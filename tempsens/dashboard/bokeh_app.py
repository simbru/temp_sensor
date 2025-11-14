"""
Bokeh Server dashboard for live temperature/humidity monitoring.
Run with: bokeh serve --show tempsens/dashboard/bokeh_app.py
"""
import configparser
import pathlib
import socket
import sys
import time

import numpy as np
import pandas as pd

# Add project root to path
_project_root = pathlib.Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from bokeh.plotting import figure, curdoc
from bokeh.models import ColumnDataSource, Button, Spinner, Range1d, Toggle, CustomJS
from bokeh.layouts import column, row
from bokeh.models.widgets import Div, TextInput

from tempsens import io_funcs, sensor as tempsensor


def get_device_ip():
    """Get the device's primary IP address."""
    try:
        # Create a socket to determine the primary network interface IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # Doesn't actually connect, just determines routing
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "Unknown"


def sanitize_filename(name):
    """Sanitize device name for use in filenames by replacing special characters with underscores."""
    import re
    # Replace spaces, commas, and other special characters with underscores
    sanitized = re.sub(r'[^\w\-]', '_', name)
    # Replace multiple consecutive underscores with a single underscore
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')
    return sanitized if sanitized else "Unnamed_Device"


# Constants
TOLERANCE_MS = 2000  # 2 seconds tolerance for range comparison
DEFAULT_MA_WINDOW = 10
MIN_TEMP_WINDOW = 1.0
MIN_HUM_WINDOW = 1.0
MAX_HUM_WINDOW = 100.0
DEFAULT_TEMP_CENTER = 20.0
DEFAULT_HUM_CENTER = 50.0
TEMP_CENTER_DEADBAND = 0.25
HUM_CENTER_DEADBAND = 2.0

SETTINGS_FILE = _project_root / "settings.ini"
SETTINGS_SECTION = "DISPLAY"
SETTINGS_DEFAULTS = {
    "temperature_window_c": "10",
    "humidity_window_pct": "50",
    "device_name": "Unnamed Device",
}


# Configuration
CONFIG = io_funcs.fetch_config()
UPDATE_INTERVAL = int(float(CONFIG["DEFAULT"]["loginterval_s"]) * 1000)  # Convert to milliseconds
SAMPLE_INTERVAL_SECONDS = UPDATE_INTERVAL / 1000.0
if SAMPLE_INTERVAL_SECONDS:
    samples_per_min = 60.0 / SAMPLE_INTERVAL_SECONDS
    SAMPLE_RATE_TEXT = f"dt≈{SAMPLE_INTERVAL_SECONDS:.1f}s (~{samples_per_min:.0f}/min)"
else:
    SAMPLE_RATE_TEXT = "dt≈0s"


def _read_config_float(key: str, fallback: float) -> float:
    try:
        raw_value = CONFIG["DEFAULT"].get(key, str(fallback))
        return float(raw_value)
    except (TypeError, ValueError):
        return fallback


TEMP_RANGE_COOLDOWN = max(_read_config_float("temperature_range_update_s", 10.0), 0.0)
HUM_RANGE_COOLDOWN = max(_read_config_float("humidity_range_update_s", 10.0), 0.0)


def _coerce_window(value: object, fallback: float, minimum: float, maximum: float | None = None) -> float:
    try:
        window = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        window = fallback
    if window < minimum:
        window = minimum
    if maximum is not None and window > maximum:
        window = maximum
    return window


def _load_settings() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.optionxform = str  # type: ignore[assignment]
    if SETTINGS_FILE.exists():
        cfg.read(SETTINGS_FILE)
    if SETTINGS_SECTION not in cfg:
        cfg[SETTINGS_SECTION] = {}
    section = cfg[SETTINGS_SECTION]
    changed = False
    for key, default in SETTINGS_DEFAULTS.items():
        if key not in section:
            section[key] = default
            changed = True
    if changed:
        with SETTINGS_FILE.open("w") as fh:
            cfg.write(fh)
    return cfg


def _persist_settings(cfg: configparser.ConfigParser) -> None:
    with SETTINGS_FILE.open("w") as fh:
        cfg.write(fh)


TEMP_WINDOW_DEFAULT = _read_config_float("temperature_window_c", 10.0)
HUM_WINDOW_DEFAULT = _read_config_float("humidity_window_pct", 50.0)

settings_config = _load_settings()
settings_section = settings_config[SETTINGS_SECTION]
temp_window_initial = _coerce_window(
    settings_section.get("temperature_window_c", str(TEMP_WINDOW_DEFAULT)),
    TEMP_WINDOW_DEFAULT,
    MIN_TEMP_WINDOW,
)
hum_window_initial = _coerce_window(
    settings_section.get("humidity_window_pct", str(HUM_WINDOW_DEFAULT)),
    HUM_WINDOW_DEFAULT,
    MIN_HUM_WINDOW,
    MAX_HUM_WINDOW,
)
device_name_initial = settings_section.get("device_name", "Unnamed Device")
device_ip = get_device_ip()

# Normalise stored settings so they match clamped values
normalised = False
if settings_section.get("temperature_window_c") != str(temp_window_initial):
    settings_section["temperature_window_c"] = str(temp_window_initial)
    normalised = True
if settings_section.get("humidity_window_pct") != str(hum_window_initial):
    settings_section["humidity_window_pct"] = str(hum_window_initial)
    normalised = True
if normalised:
    _persist_settings(settings_config)

status_state = {
    "sample_text": SAMPLE_RATE_TEXT,
    "ma_text": "",
}


def _compute_window_bounds(center: float, width: float, *, minimum: float | None = None,
                           maximum: float | None = None) -> tuple[float, float]:
    width = max(width, 0.0)
    if width == 0.0:
        return center, center

    half = width / 2.0
    start = center - half
    end = center + half

    if minimum is not None and maximum is not None and width >= (maximum - minimum):
        return minimum, maximum

    if minimum is not None and start < minimum:
        start = minimum
        end = start + width
    if maximum is not None and end > maximum:
        end = maximum
        start = end - width

    if minimum is not None and start < minimum:
        start = minimum
    if maximum is not None and end > maximum:
        end = maximum

    return start, end

# Start the temperature sensor if not running
temp_sensor_process = tempsensor.tempsensor_subprocess()

# Initialize data
def prepare_source_data(raw_data, window_size):
    """Return CDS-compatible dict with moving-average columns added."""
    if not raw_data:
        return {"time": [], "temperature": [], "humidity": [], "temp_ma": [], "hum_ma": []}

    time_vals = np.asarray(raw_data.get("time", []))
    temps = np.asarray(raw_data.get("temperature", []), dtype=float)
    hums = np.asarray(raw_data.get("humidity", []), dtype=float)

    if len(temps) == 0:
        return {"time": time_vals, "temperature": temps, "humidity": hums,
                "temp_ma": temps, "hum_ma": hums}

    window = max(int(window_size), 1)
    temp_ma = pd.Series(temps).rolling(window=window, min_periods=1).mean().to_numpy()
    hum_ma = pd.Series(hums).rolling(window=window, min_periods=1).mean().to_numpy()

    return {
        "time": time_vals,
        "temperature": temps,
        "humidity": hums,
        "temp_ma": temp_ma,
        "hum_ma": hum_ma,
    }


initial_data_raw = io_funcs.fetch_log_data()
initial_data = prepare_source_data(initial_data_raw, DEFAULT_MA_WINDOW)
source = ColumnDataSource(data=initial_data)

initial_temp_ma = float(initial_data["temp_ma"][-1]) if len(initial_data["temp_ma"]) else None
if initial_temp_ma is not None and np.isnan(initial_temp_ma):
    initial_temp_ma = None
initial_hum_ma = float(initial_data["hum_ma"][-1]) if len(initial_data["hum_ma"]) else None
if initial_hum_ma is not None and np.isnan(initial_hum_ma):
    initial_hum_ma = None

latest_values: dict[str, float | None] = {
    "temp": initial_temp_ma,
    "hum": initial_hum_ma,
}

initial_temp_center = latest_values["temp"] if latest_values["temp"] is not None else DEFAULT_TEMP_CENTER
initial_hum_center = latest_values["hum"] if latest_values["hum"] is not None else DEFAULT_HUM_CENTER

now_monotonic = time.monotonic()

temp_window_state = {
    "value": temp_window_initial,
    "auto": True,
    "pending": 0,
    "last_start": None,
    "last_end": None,
    "manual_center": None,
    "last_auto_center": initial_temp_center,
    "last_auto_update": now_monotonic - TEMP_RANGE_COOLDOWN,
}
hum_window_state = {
    "value": hum_window_initial,
    "auto": True,
    "pending": 0,
    "last_start": None,
    "last_end": None,
    "manual_center": None,
    "last_auto_center": initial_hum_center,
    "last_auto_update": now_monotonic - HUM_RANGE_COOLDOWN,
}

TEMP_Y_START, TEMP_Y_END = _compute_window_bounds(initial_temp_center, temp_window_state["value"])
HUM_Y_START, HUM_Y_END = _compute_window_bounds(initial_hum_center, hum_window_state["value"],
                                                minimum=0.0, maximum=100.0)

# Time window settings (in minutes)
current_window = {
    "minutes": 10,
    "force_update": True,
    "auto_range": True,
    "last_set_start": None,  # Track the last programmatic range start (ms)
    "last_set_end": None,
    "data_min": None,
    "data_max": None,
}

# Guard flag so programmatic range changes do not disable auto-range
range_update_state = {"pending": 0}

# Create temperature plot with sensible y-range (0-40°C)
temp_plot = figure(
    title="Temperature Over Time",
    x_axis_label="Time",
    y_axis_label="Temperature (°C)",
    x_axis_type="datetime",
    y_range=Range1d(start=TEMP_Y_START, end=TEMP_Y_END),
    height=400,
    sizing_mode="stretch_width",
    tools="pan,wheel_zoom,box_zoom,reset,save",
    active_drag=None,
    active_scroll=None,
    x_range=Range1d()
)
temp_raw_renderer = temp_plot.line('time', 'temperature', source=source, line_width=2,
                                   color='#ff7a7a', alpha=0.6)
temp_ma_renderer = temp_plot.line('time', 'temp_ma', source=source, line_width=3,
                                  color='red', alpha=0.9)

# Create humidity plot with 0-100% range
humidity_plot = figure(
    title="Humidity Over Time",
    x_axis_label="Time",
    y_axis_label="Humidity (%)",
    x_axis_type="datetime",
    y_range=Range1d(start=HUM_Y_START, end=HUM_Y_END),
    height=400,
    sizing_mode="stretch_width",
    tools="pan,wheel_zoom,box_zoom,reset,save",
    active_drag=None,
    active_scroll=None,
    x_range=temp_plot.x_range  # Link x-axis with temperature plot
)
hum_raw_renderer = humidity_plot.line('time', 'humidity', source=source, line_width=2,
                                      color='#6fa8ff', alpha=0.6)
hum_ma_renderer = humidity_plot.line('time', 'hum_ma', source=source, line_width=3,
                                     color='navy', alpha=0.9)


def _set_temp_y_range(center: float | None, window: float, *, record_auto: bool = False) -> None:
    if center is None:
        center = DEFAULT_TEMP_CENTER
    start, end = _compute_window_bounds(center, window)
    temp_window_state["pending"] = 2
    temp_window_state["last_start"] = start
    temp_window_state["last_end"] = end
    temp_window_state["manual_center"] = None
    temp_plot.y_range.start = start
    temp_plot.y_range.end = end
    if record_auto:
        temp_window_state["last_auto_center"] = (start + end) / 2.0
        temp_window_state["last_auto_update"] = time.monotonic()


def _set_hum_y_range(center: float | None, window: float, *, record_auto: bool = False) -> None:
    if center is None:
        center = DEFAULT_HUM_CENTER
    start, end = _compute_window_bounds(center, window, minimum=0.0, maximum=100.0)
    hum_window_state["pending"] = 2
    hum_window_state["last_start"] = start
    hum_window_state["last_end"] = end
    hum_window_state["manual_center"] = None
    humidity_plot.y_range.start = start
    humidity_plot.y_range.end = end
    if record_auto:
        hum_window_state["last_auto_center"] = (start + end) / 2.0
        hum_window_state["last_auto_update"] = time.monotonic()


_set_temp_y_range(initial_temp_center, temp_window_state["value"], record_auto=True)
_set_hum_y_range(initial_hum_center, hum_window_state["value"], record_auto=True)
temp_window_state["last_auto_update"] -= TEMP_RANGE_COOLDOWN
hum_window_state["last_auto_update"] -= HUM_RANGE_COOLDOWN


def _should_auto_update_temp(center: float | None) -> bool:
    if center is None:
        return False
    last_center = temp_window_state.get("last_auto_center")
    if last_center is not None and abs(center - last_center) < TEMP_CENTER_DEADBAND:
        return False
    now = time.monotonic()
    last_ts = temp_window_state.get("last_auto_update", 0.0)
    if now - last_ts < TEMP_RANGE_COOLDOWN:
        return False
    return True


def _should_auto_update_hum(center: float | None) -> bool:
    if center is None:
        return False
    last_center = hum_window_state.get("last_auto_center")
    if last_center is not None and abs(center - last_center) < HUM_CENTER_DEADBAND:
        return False
    now = time.monotonic()
    last_ts = hum_window_state.get("last_auto_update", 0.0)
    if now - last_ts < HUM_RANGE_COOLDOWN:
        return False
    return True


def _handle_temp_y_change(attr, old, new):
    if temp_window_state["pending"] > 0:
        temp_window_state["pending"] = max(temp_window_state["pending"] - 1, 0)
        temp_window_state[f"last_{attr}"] = new
        return

    temp_window_state[f"last_{attr}"] = new
    if attr == "start":
        return

    start = temp_plot.y_range.start
    end = temp_plot.y_range.end
    if start is None or end is None:
        return

    span = end - start
    if span <= 0:
        return

    width = max(round(span, 2), MIN_TEMP_WINDOW)
    temp_window_state["value"] = width
    temp_window_state["auto"] = False
    temp_window_state["manual_center"] = (start + end) / 2.0
    _persist_window_settings()

    if not window_control_state["temp_syncing"]:
        window_control_state["temp_syncing"] = True
        try:
            temp_window_spinner.value = width
        finally:
            window_control_state["temp_syncing"] = False

    _update_window_displays()


def _handle_hum_y_change(attr, old, new):
    if hum_window_state["pending"] > 0:
        hum_window_state["pending"] = max(hum_window_state["pending"] - 1, 0)
        hum_window_state[f"last_{attr}"] = new
        return

    hum_window_state[f"last_{attr}"] = new
    if attr == "start":
        return

    start = humidity_plot.y_range.start
    end = humidity_plot.y_range.end
    if start is None or end is None:
        return

    span = end - start
    if span <= 0:
        return

    width = max(round(span, 0), MIN_HUM_WINDOW)
    width = min(width, MAX_HUM_WINDOW)

    hum_window_state["value"] = width
    hum_window_state["auto"] = False
    hum_window_state["manual_center"] = (start + end) / 2.0
    _persist_window_settings()

    if not window_control_state["hum_syncing"]:
        window_control_state["hum_syncing"] = True
        try:
            hum_window_spinner.value = width
        finally:
            window_control_state["hum_syncing"] = False

    _update_window_displays()

# Apply sliding window updates without triggering the range-change callback
def set_programmatic_range(start_ms, end_ms):
    """Set the x-range while suppressing user-interaction detection."""
    range_update_state["pending"] = 2  # expect callbacks for start and end
    current_window["last_set_start"] = start_ms
    current_window["last_set_end"] = end_ms
    temp_plot.x_range.start = start_ms
    temp_plot.x_range.end = end_ms

# Disable auto-range when user interacts with plots
def range_change_callback(attr, old, new):
    """Detect if range change was from user or programmatic."""
    if range_update_state["pending"] > 0:
        range_update_state["pending"] = max(range_update_state["pending"] - 1, 0)
        current_window[f"last_set_{attr}"] = new
        return

    expected_key = f"last_set_{attr}"
    expected = current_window.get(expected_key)
    diff = None

    if expected is not None:
        diff = abs(new - expected)
        if diff <= TOLERANCE_MS:
            current_window[expected_key] = new
            return

    # Allow Bokeh to clamp to available data without disabling auto-range
    data_min = current_window.get("data_min")
    data_max = current_window.get("data_max")
    if attr == "start" and data_min is not None and abs(new - data_min) <= TOLERANCE_MS:
        current_window[expected_key] = new
        return
    if attr == "end" and data_max is not None and abs(new - data_max) <= TOLERANCE_MS:
        current_window[expected_key] = new
        return

    if current_window["auto_range"]:
        current_window["auto_range"] = False
        if diff is not None:
            print(f"Auto-range disabled - user changed {attr} (diff: {diff:.1f}ms)")
        else:
            print(f"Auto-range disabled - user changed {attr} (no programmatic baseline)")

    current_window[expected_key] = new

# Add event listeners to detect user interaction with x-axis
temp_plot.x_range.on_change('start', range_change_callback)
temp_plot.x_range.on_change('end', range_change_callback)
temp_plot.y_range.on_change('start', _handle_temp_y_change)
temp_plot.y_range.on_change('end', _handle_temp_y_change)
humidity_plot.y_range.on_change('start', _handle_hum_y_change)
humidity_plot.y_range.on_change('end', _handle_hum_y_change)

# Current readings display
current_readings = Div(text="<h3>Loading...</h3>", sizing_mode="stretch_width", height=120)

window_control_state = {
    "temp_syncing": False,
    "hum_syncing": False,
}

temp_window_spinner = Spinner(title="Temperature window (°C)", low=MIN_TEMP_WINDOW,
                              high=None, step=0.5,
                              value=temp_window_state["value"], width=180)
hum_window_spinner = Spinner(title="Humidity window (%)", low=MIN_HUM_WINDOW,
                             high=MAX_HUM_WINDOW, step=1,
                             value=hum_window_state["value"], width=180)
temp_window_range_display = Div(text="", width=200, height=40)
hum_window_range_display = Div(text="", width=200, height=40)

# Time window buttons
btn_10min = Button(label="10 min", button_type="success", width=100)
btn_1h = Button(label="1 hour", button_type="default", width=100)
btn_3h = Button(label="3 hours", button_type="default", width=100)
btn_12h = Button(label="12 hours", button_type="default", width=100)
btn_24h = Button(label="24 hours", button_type="default", width=100)
btn_1week = Button(label="1 week", button_type="default", width=100)
btn_all = Button(label="All data", button_type="default", width=100)

time_buttons = [btn_10min, btn_1h, btn_3h, btn_12h, btn_24h, btn_1week, btn_all]

# Custom window inputs (days/hours/minutes/seconds)
# Note: No hard limits - you can enter any value (e.g., 120 minutes, 48 hours, etc.)
time_input_state = {"updating": False}
window_days = Spinner(title="Days", low=0, step=1, value=0, width=90)
window_hours = Spinner(title="Hours", low=0, step=1, value=0, width=90)
window_minutes = Spinner(title="Minutes", low=0, step=1, value=10, width=90)
window_seconds = Spinner(title="Seconds", low=0, step=1, value=0, width=90)


def format_minutes(minutes):
    if minutes is None:
        return "ALL data"

    total_seconds = int(round(float(minutes) * 60))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    if secs:
        parts.append(f"{secs}s")

    return " ".join(parts) if parts else "0s"


def format_duration_seconds(total_seconds):
    total_seconds = int(round(total_seconds))
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    if secs or not parts:
        parts.append(f"{secs}s")

    return "".join(parts)


def set_time_inputs_from_minutes(minutes):
    """Sync the custom inputs with a minutes value (None -> zeros)."""
    if minutes is None or minutes <= 0:
        values = (0, 0, 0, 0)
    else:
        total_seconds = int(round(float(minutes) * 60))
        days, rem = divmod(total_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        values = (days, hours, mins, secs)

    time_input_state["updating"] = True
    try:
        window_days.value = values[0]
        window_hours.value = values[1]
        window_minutes.value = values[2]
        window_seconds.value = values[3]
    finally:
        time_input_state["updating"] = False


def get_minutes_from_inputs():
    try:
        total_seconds = (
            int(window_days.value) * 86400
            + int(window_hours.value) * 3600
            + int(window_minutes.value) * 60
            + int(window_seconds.value)
        )
    except (TypeError, ValueError):
        return None

    if total_seconds <= 0:
        return None

    return total_seconds / 60

def set_button_active(active_btn):
    """Highlight the active preset button (or clear selection with None)."""
    for btn in time_buttons:
        btn.button_type = "success" if btn == active_btn else "default"

def update_time_window(minutes, sync_inputs=True):
    """Update the time window and optionally sync custom inputs."""
    current_window["minutes"] = minutes
    current_window["force_update"] = True
    current_window["auto_range"] = True  # Re-enable auto-range

    if sync_inputs:
        set_time_inputs_from_minutes(minutes)

    print(f"Time window changed to: {format_minutes(minutes)}, auto-range re-enabled")
    update_data()


def on_custom_time_change(attr, old, new):
    if time_input_state["updating"]:
        return

    minutes = get_minutes_from_inputs()
    if minutes is None:
        return

    set_button_active(None)
    update_time_window(minutes, sync_inputs=False)


for spinner in (window_days, window_hours, window_minutes, window_seconds):
    spinner.on_change("value", on_custom_time_change)

# Initialize custom inputs to current preset (60 minutes)
set_time_inputs_from_minutes(current_window["minutes"])

btn_10min.on_click(lambda: (set_button_active(btn_10min), update_time_window(10)))
btn_1h.on_click(lambda: (set_button_active(btn_1h), update_time_window(60)))
btn_3h.on_click(lambda: (set_button_active(btn_3h), update_time_window(180)))
btn_12h.on_click(lambda: (set_button_active(btn_12h), update_time_window(720)))
btn_24h.on_click(lambda: (set_button_active(btn_24h), update_time_window(1440)))
btn_1week.on_click(lambda: (set_button_active(btn_1week), update_time_window(10080)))
btn_all.on_click(lambda: (set_button_active(btn_all), update_time_window(None)))

# CSV download buttons
btn_download_temp = Button(label="📥 Download Temperature CSV", button_type="primary", width=230)
btn_download_hum = Button(label="📥 Download Humidity CSV", button_type="primary", width=230)
btn_download_both = Button(label="📥 Download Both CSV", button_type="success", width=230)
ma_spinner = Spinner(title="Moving average window (samples)", low=1, high=500, step=1,
                     value=DEFAULT_MA_WINDOW, width=180)
show_raw_toggle = Toggle(label="Raw data: ON", button_type="success", active=True, width=140)

# Device info widgets
device_name_input = TextInput(value=device_name_initial, title="Device Name:", width=400)
device_ip_display = Div(text=f"<strong>IP Address:</strong> {device_ip}", width=300, height=50)


def build_download_callback(columns, prefix, device_name_widget):
    return CustomJS(args=dict(source=source, columns=columns, prefix=prefix, device_name_widget=device_name_widget), code="""
        // Sanitize device name for filename
        function sanitizeFilename(name) {
            var sanitized = name.replace(/[^\\w\\-]/g, '_');
            sanitized = sanitized.replace(/_+/g, '_');
            sanitized = sanitized.replace(/^_+|_+$/g, '');
            return sanitized || 'Unnamed_Device';
        }

        var cols = ['device_name'].concat(columns);
        var data = source.data;
        if (!columns.length || !data) {
            return;
        }
        var first = data[columns[0]];
        if (!first || !first.length) {
            return;
        }

        var deviceName = device_name_widget.value || 'Unnamed Device';
        var lines = [cols.join(',')];
        var nrows = first.length;
        for (var i = 0; i < nrows; i++) {
            var row = [deviceName];
            for (var j = 0; j < columns.length; j++) {
                var col = columns[j];
                var value = data[col][i];
                if (col === 'time') {
                    var dt = new Date(value);
                    if (!isNaN(dt.getTime())) {
                        var y = dt.getFullYear();
                        var m = ('0' + (dt.getMonth() + 1)).slice(-2);
                        var d = ('0' + dt.getDate()).slice(-2);
                        var hh = ('0' + dt.getHours()).slice(-2);
                        var mm = ('0' + dt.getMinutes()).slice(-2);
                        var ss = ('0' + dt.getSeconds()).slice(-2);
                        row.push(y + '-' + m + '-' + d + ' ' + hh + ':' + mm + ':' + ss);
                        continue;
                    }
                }
                if (typeof value === 'number') {
                    row.push(value.toFixed(2));
                } else if (value == null) {
                    row.push('');
                } else {
                    row.push(String(value));
                }
            }
            lines.push(row.join(','));
        }

        var csv = lines.join(String.fromCharCode(10));
        var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        var timestamp = new Date().toISOString().replace(/[:.-]/g, '').slice(0, 15);
        var sanitizedDeviceName = sanitizeFilename(deviceName);
        var filename = sanitizedDeviceName + '_' + prefix + '_' + timestamp + '.csv';
        var link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
    """)


btn_download_temp.js_on_event("button_click", build_download_callback(["time", "temperature"], "temperature", device_name_input))
btn_download_hum.js_on_event("button_click", build_download_callback(["time", "humidity"], "humidity", device_name_input))
btn_download_both.js_on_event("button_click", build_download_callback(["time", "temperature", "humidity"], "temp_humidity", device_name_input))


def on_ma_change(attr, old, new):
    """Recompute moving averages when the window size changes."""
    try:
        int(new)
    except (TypeError, ValueError):
        return
    update_ma_info(new)
    current_window["force_update"] = True
    update_data()


ma_spinner.on_change("value", on_ma_change)


def _on_temp_window_change(attr, old, new):
    if window_control_state["temp_syncing"]:
        return
    value = _coerce_window(new, temp_window_state["value"], MIN_TEMP_WINDOW)
    temp_window_state["value"] = value
    temp_window_state["auto"] = True
    temp_window_state["manual_center"] = None
    temp_window_state["pending"] = 2
    window_control_state["temp_syncing"] = True
    try:
        temp_window_spinner.value = value
    finally:
        window_control_state["temp_syncing"] = False
    _persist_window_settings()
    _set_temp_y_range(latest_values["temp"], value, record_auto=True)
    _update_window_displays()


def _on_hum_window_change(attr, old, new):
    if window_control_state["hum_syncing"]:
        return
    value = _coerce_window(new, hum_window_state["value"], MIN_HUM_WINDOW, MAX_HUM_WINDOW)
    hum_window_state["value"] = value
    hum_window_state["auto"] = True
    hum_window_state["manual_center"] = None
    hum_window_state["pending"] = 2
    window_control_state["hum_syncing"] = True
    try:
        hum_window_spinner.value = value
    finally:
        window_control_state["hum_syncing"] = False
    _persist_window_settings()
    _set_hum_y_range(latest_values["hum"], value, record_auto=True)
    _update_window_displays()


temp_window_spinner.on_change("value", _on_temp_window_change)
hum_window_spinner.on_change("value", _on_hum_window_change)


def on_raw_toggle_change(attr, old, new):
    visible = bool(new)
    temp_raw_renderer.visible = visible
    hum_raw_renderer.visible = visible
    show_raw_toggle.button_type = "success" if visible else "default"
    show_raw_toggle.label = f"Raw data: {'ON' if visible else 'OFF'}"


show_raw_toggle.on_change("active", on_raw_toggle_change)
on_raw_toggle_change("active", True, show_raw_toggle.active)


def on_device_name_change(attr, old, new):
    """Persist device name to settings.ini when changed."""
    settings_section["device_name"] = new
    _persist_settings(settings_config)
    print(f"Device name updated to: {new}")


device_name_input.on_change("value", on_device_name_change)


def update_ma_info(window_size):
    window_size = max(int(window_size), 1)
    approx_duration = window_size * SAMPLE_INTERVAL_SECONDS
    status_state["ma_text"] = f"{window_size} samp ≈ {format_duration_seconds(approx_duration)}"


update_ma_info(DEFAULT_MA_WINDOW)


def _update_window_displays():
    temp_start = temp_plot.y_range.start
    temp_end = temp_plot.y_range.end
    hum_start = humidity_plot.y_range.start
    hum_end = humidity_plot.y_range.end

    if temp_start is None or temp_end is None:
        temp_html = "<p style='margin:0;font-size:14px;'>Range: —</p>"
    else:
        temp_html = (
            f"<p style='margin:0;font-size:14px;'>Range: {temp_start:.1f}°C – {temp_end:.1f}°C</p>"
        )

    if hum_start is None or hum_end is None:
        hum_html = "<p style='margin:0;font-size:14px;'>Range: —</p>"
    else:
        hum_html = (
            f"<p style='margin:0;font-size:14px;'>Range: {hum_start:.0f}% – {hum_end:.0f}%</p>"
        )

    temp_window_range_display.text = temp_html
    hum_window_range_display.text = hum_html


def _persist_window_settings():
    settings_section["temperature_window_c"] = f"{temp_window_state['value']:.2f}"
    settings_section["humidity_window_pct"] = f"{hum_window_state['value']:.2f}"
    _persist_settings(settings_config)


_update_window_displays()

def update_data():
    """Update the data source with new readings."""
    try:
        new_data = io_funcs.fetch_log_data()

        if not new_data or len(new_data.get("time", [])) == 0:
            return

        window = max(int(ma_spinner.value), 1)
        update_ma_info(window)

        prepared = prepare_source_data(new_data, window)

        # Replace dataset to keep moving averages in sync
        source.data = prepared

        latest_temp_ma = float(prepared["temp_ma"][-1]) if len(prepared["temp_ma"]) else None
        if latest_temp_ma is not None and np.isnan(latest_temp_ma):
            latest_temp_ma = None
        latest_hum_ma = float(prepared["hum_ma"][-1]) if len(prepared["hum_ma"]) else None
        if latest_hum_ma is not None and np.isnan(latest_hum_ma):
            latest_hum_ma = None
        latest_values["temp"] = latest_temp_ma
        latest_values["hum"] = latest_hum_ma

        if temp_window_state["auto"] and _should_auto_update_temp(latest_temp_ma):
            _set_temp_y_range(latest_temp_ma, temp_window_state["value"], record_auto=True)
        if hum_window_state["auto"] and _should_auto_update_hum(latest_hum_ma):
            _set_hum_y_range(latest_hum_ma, hum_window_state["value"], record_auto=True)
        _update_window_displays()

        # Track current data extents for range-change handling
        current_window["data_min"] = prepared["time"][0]
        current_window["data_max"] = prepared["time"][-1]

        if current_window.get("force_update", False):
            current_window["force_update"] = False
            current_window["force_update_was_true"] = True

        # Update time window (sliding window) - only if auto_range is enabled
        latest_time_ms = prepared["time"][-1]  # Already in milliseconds

        if current_window["auto_range"]:
            if current_window["minutes"] is not None:
                # Calculate window boundaries (all in milliseconds)
                window_duration_ms = current_window["minutes"] * 60 * 1000
                window_start_ms = latest_time_ms - window_duration_ms

                # Update x-axis range for sliding window
                set_programmatic_range(window_start_ms, latest_time_ms)

                # Debug output
                if current_window.get("force_update_was_true", False):
                    print(f"Setting window to {format_minutes(current_window['minutes'])}")
                    current_window["force_update_was_true"] = False
            else:
                # Show all data
                start_ms = prepared["time"][0]
                end_ms = latest_time_ms

                set_programmatic_range(start_ms, end_ms)

                if current_window.get("force_update_was_true", False):
                    print("Setting window to ALL data")
                    current_window["force_update_was_true"] = False

        # Update current readings display with human-readable time
        # Convert milliseconds back to datetime for display
        curr_datetime = pd.Timestamp(latest_time_ms, unit='ms')
        curr_time_str = curr_datetime.strftime('%Y-%m-%d %H:%M:%S')
        curr_temp = prepared["temp_ma"][-1]
        curr_hum = prepared["hum_ma"][-1]

        # Show auto-range status
        auto_status = "🟢 Auto-following" if current_window["auto_range"] else "🔴 Manual (click time button to re-enable)"

        smoothing_text = status_state.get("ma_text", "-" )
        sample_text = status_state.get("sample_text", "-")

        current_readings.text = f"""
        <div style="background-color:#f0f0f0;padding:18px;border-radius:5px;margin-bottom:18px;display:flex;flex-wrap:wrap;gap:24px;align-items:center;">
            <div style="min-width:150px;">
                <h3 style="margin:0 0 6px 0;font-size:16px;">Time</h3>
                <p style="font-size:18px;margin:0;">{curr_time_str}</p>
            </div>
            <div style="min-width:150px;">
                <h3 style="margin:0 0 6px 0;font-size:16px;">Readings</h3>
                <p style="font-size:18px;margin:0;">{curr_temp:.1f}°C · {curr_hum:.1f}%</p>
            </div>
            <div style="min-width:150px;">
                <h3 style="margin:0 0 6px 0;font-size:16px;">Smoothing</h3>
                <p style="font-size:16px;margin:0;">{smoothing_text}</p>
            </div>
            <div style="min-width:150px;">
                <h3 style="margin:0 0 6px 0;font-size:16px;">Cadence</h3>
                <p style="font-size:16px;margin:0;">{sample_text}</p>
            </div>
            <div style="min-width:150px;">
                <h3 style="margin:0 0 6px 0;font-size:16px;">Mode</h3>
                <p style="font-size:16px;margin:0;color:#333;">{auto_status}</p>
            </div>
        </div>
        """
    except Exception as e:
        print(f"Error updating data: {e}")

# Initial update
update_data()

# Schedule periodic updates
curdoc().add_periodic_callback(update_data, UPDATE_INTERVAL)

# Layout
time_button_row = row(btn_10min, btn_1h, btn_3h, btn_12h, btn_24h, btn_1week, btn_all,
                      sizing_mode="scale_width")

download_button_row = row(btn_download_temp, btn_download_hum, btn_download_both,
                          sizing_mode="scale_width")

custom_time_row = row(window_days, window_hours, window_minutes, window_seconds,
                      sizing_mode="scale_width")
ma_controls_row = row(ma_spinner, show_raw_toggle, sizing_mode="scale_width")

display_range_row = row(
    temp_window_spinner,
    temp_window_range_display,
    hum_window_spinner,
    hum_window_range_display,
    sizing_mode="scale_width"
)

device_info_row = row(device_name_input, device_ip_display, sizing_mode="scale_width")

layout = column(
    Div(text="<h3>Device Information</h3>", sizing_mode="stretch_width", height=40),
    device_info_row,
    Div(text="<br>", sizing_mode="stretch_width", height=10),  # Spacer
    Div(text="<h1>🌡️ Temperature & Humidity Monitor</h1>", sizing_mode="stretch_width", height=60),
    current_readings,
    Div(text="<h4>Time Window:</h4>", sizing_mode="stretch_width", height=30),
    time_button_row,
    Div(text="<h4>Custom Window:</h4>", sizing_mode="stretch_width", height=30),
    custom_time_row,
    Div(text="<h4>Moving Average:</h4>", sizing_mode="stretch_width", height=30),
    ma_controls_row,
    Div(text="<h4>Display Range:</h4>", sizing_mode="stretch_width", height=30),
    display_range_row,
    Div(text="<br>", sizing_mode="stretch_width", height=10),  # Spacer
    temp_plot,
    humidity_plot,
    Div(text="<br>", sizing_mode="stretch_width", height=10),
    Div(text="<h4>Download Data:</h4>", sizing_mode="stretch_width", height=30),
    download_button_row,
    sizing_mode="scale_width"
)

curdoc().add_root(layout)
curdoc().title = "Temperature Monitor"
