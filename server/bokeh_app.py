"""
Multi-sensor Bokeh Server dashboard for lab temperature monitoring.
Run with: bokeh serve --show server/bokeh_app.py
"""
import configparser
import logging
import pathlib
import sys
import time

import numpy as np
import pandas as pd

# Add project root to path
_project_root = pathlib.Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from bokeh.plotting import figure, curdoc
from bokeh.models import ColumnDataSource, Button, Spinner, Range1d, Toggle, CustomJS, Select
from bokeh.layouts import column, row
from bokeh.models.widgets import Div

from server.api_client import MultiSensorClient
from server.data_aggregator import DataAggregator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load server configuration
CONFIG_PATH = _project_root / "server" / "config_server.ini"

def load_server_config():
    """Load server configuration from config_server.ini."""
    config = configparser.ConfigParser()
    config.optionxform = str  # type: ignore[attr-defined]

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Server configuration not found at {CONFIG_PATH}")

    config.read(CONFIG_PATH)

    # Parse sensor configurations
    sensor_configs = []
    if "SENSORS" in config:
        for name, url in config["SENSORS"].items():
            sensor_configs.append({"name": name.replace("_", " "), "url": url})

    if not sensor_configs:
        raise ValueError("No sensors configured in config_server.ini")

    poll_interval = int(config.get("SERVER", "poll_interval_s", fallback="30"))
    db_path = config.get("SERVER", "database_path", fallback="sensor_data.db")
    update_ms = int(config.get("SERVER", "dashboard_update_ms", fallback="10000"))

    return sensor_configs, poll_interval, db_path, update_ms

try:
    sensor_configs, poll_interval, db_path, dashboard_update_ms = load_server_config()
    logger.info(f"Loaded {len(sensor_configs)} sensor configurations")
except Exception as e:
    logger.error(f"Failed to load server configuration: {e}")
    raise

# Initialize multi-sensor client and data aggregator
multi_client = MultiSensorClient(sensor_configs)
aggregator = DataAggregator(multi_client, db_path=db_path, poll_interval=poll_interval)

# Do initial poll to populate database
logger.info("Performing initial data poll...")
try:
    aggregator.poll_once()
    logger.info("Initial poll completed successfully")
except Exception as e:
    logger.error(f"Initial poll failed: {e}", exc_info=True)

# Start background polling
aggregator.start_polling()
logger.info("Started background polling thread")

# Constants (adapted from original dashboard)
TOLERANCE_MS = 2000
DEFAULT_MA_WINDOW = 10
MIN_TEMP_WINDOW = 1.0
MIN_HUM_WINDOW = 1.0
MAX_HUM_WINDOW = 100.0
DEFAULT_TEMP_CENTER = 20.0
DEFAULT_HUM_CENTER = 50.0
TEMP_CENTER_DEADBAND = 0.25
HUM_CENTER_DEADBAND = 2.0

UPDATE_INTERVAL = dashboard_update_ms
SAMPLE_INTERVAL_SECONDS = poll_interval  # Approximate
SAMPLE_RATE_TEXT = f"~{poll_interval}s/sample"

# State for current sensor selection
current_sensor_state = {
    "name": sensor_configs[0]["name"] if sensor_configs else "No sensors",
    "data": None
}

status_state = {
    "sample_text": SAMPLE_RATE_TEXT,
    "ma_text": "",
}


def _coerce_window(value, fallback: float, minimum: float, maximum: float | None = None) -> float:
    try:
        window = float(value)
    except (TypeError, ValueError):
        window = fallback
    if window < minimum:
        window = minimum
    if maximum is not None and window > maximum:
        window = maximum
    return window


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


# Initialize data
def prepare_source_data(raw_data, window_size):
    """Return CDS-compatible dict with moving-average columns added."""
    if not raw_data or len(raw_data.get("time", [])) == 0:
        return {"time": [], "temperature": [], "humidity": [], "temp_ma": [], "hum_ma": []}

    time_vals = np.asarray(raw_data.get("time", []))
    temps = np.asarray(raw_data.get("temperature", []), dtype=float)
    hums = np.asarray(raw_data.get("humidity", []), dtype=float)

    if len(temps) == 0:
        return {"time": time_vals, "temperature": temps, "humidity": hums,
                "temp_ma": temps, "hum_ma": hums}

    # Insert NaN markers at time gaps for visualization
    # This shows gaps in plots without storing NaN in HDF5 (reduces lock contention)
    time_vals, temps, hums = _insert_gap_markers(time_vals, temps, hums)

    window = max(int(window_size), 1)
    # Use min_periods=1 so moving average smoothly interpolates across small gaps
    # Raw data (with gap detection) shows the truth, moving average shows the trend
    temp_ma = pd.Series(temps).rolling(window=window, min_periods=1).mean().to_numpy()
    hum_ma = pd.Series(hums).rolling(window=window, min_periods=1).mean().to_numpy()

    return {
        "time": time_vals,
        "temperature": temps,
        "humidity": hums,
        "temp_ma": temp_ma,
        "hum_ma": hum_ma,
    }


def _insert_gap_markers(time_vals, temps, hums, gap_threshold_s=60):
    """
    Insert NaN at time gaps to show missing data in plots.

    Detects where time between consecutive points exceeds threshold and
    inserts proportional NaN markers to prevent moving averages from
    falsely interpolating across gaps.

    Args:
        time_vals: ISO timestamp strings
        temps: Temperature values
        hums: Humidity values
        gap_threshold_s: Time gap threshold in seconds (default 60s = 1 minute)
                        Gaps larger than this are considered client outages

    Returns:
        Tuple of (times, temps, hums) with NaN inserted at gaps
    """
    if len(time_vals) < 2:
        return time_vals, temps, hums

    # Convert to datetime and calculate time gaps
    times_dt = pd.to_datetime(time_vals)
    time_diffs = times_dt.diff().total_seconds().to_numpy()

    # Detect gaps larger than threshold (e.g., client offline)
    gap_mask = time_diffs > gap_threshold_s
    gap_indices = np.where(gap_mask)[0]

    if len(gap_indices) == 0:
        return time_vals, temps, hums

    # Build arrays with NaN inserted at each gap
    # Insert multiple NaN proportional to gap size to ensure even large moving
    # average windows show breaks (prevents false interpolation across gaps)
    result_times = []
    result_temps = []
    result_hums = []

    prev_idx = 0
    for gap_idx in gap_indices:
        # Add data up to gap
        result_times.extend(time_vals[prev_idx:gap_idx])
        result_temps.extend(temps[prev_idx:gap_idx])
        result_hums.extend(hums[prev_idx:gap_idx])

        # Calculate how many NaN markers to insert based on gap size
        # For a 2-second interval sensor, insert one NaN per missing interval
        gap_size_seconds = time_diffs[gap_idx]
        expected_interval_s = 2  # Typical sensor log interval
        num_missing = int(gap_size_seconds / expected_interval_s)

        # Insert enough NaN markers to prevent moving average from bridging the gap
        # Minimum of 5 ensures even moderate MA windows show the break
        num_nan_markers = max(num_missing, 5)

        for _ in range(num_nan_markers):
            result_times.append(time_vals[gap_idx - 1])
            result_temps.append(np.nan)
            result_hums.append(np.nan)

        prev_idx = gap_idx

    # Add remaining data
    result_times.extend(time_vals[prev_idx:])
    result_temps.extend(temps[prev_idx:])
    result_hums.extend(hums[prev_idx:])

    return (np.array(result_times),
            np.array(result_temps, dtype=float),
            np.array(result_hums, dtype=float))


# Fetch initial data for first sensor
initial_data_raw = aggregator.get_sensor_data(current_sensor_state["name"], limit=1000)
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
    "value": 10.0,
    "auto": True,
    "pending": 0,
    "last_start": None,
    "last_end": None,
    "manual_center": None,
    "last_auto_center": initial_temp_center,
    "last_auto_update": now_monotonic - 10.0,
}
hum_window_state = {
    "value": 50.0,
    "auto": True,
    "pending": 0,
    "last_start": None,
    "last_end": None,
    "manual_center": None,
    "last_auto_center": initial_hum_center,
    "last_auto_update": now_monotonic - 10.0,
}

TEMP_Y_START, TEMP_Y_END = _compute_window_bounds(initial_temp_center, temp_window_state["value"])
HUM_Y_START, HUM_Y_END = _compute_window_bounds(initial_hum_center, hum_window_state["value"],
                                                minimum=0.0, maximum=100.0)

# Time window settings (in minutes)
current_window = {
    "minutes": 10,
    "force_update": True,
    "auto_range": True,
    "last_set_start": None,
    "last_set_end": None,
    "data_min": None,
    "data_max": None,
}

range_update_state = {"pending": 0}

# Create plots
temp_plot = figure(
    title=f"Temperature - {current_sensor_state['name']}",
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

humidity_plot = figure(
    title=f"Humidity - {current_sensor_state['name']}",
    x_axis_label="Time",
    y_axis_label="Humidity (%)",
    x_axis_type="datetime",
    y_range=Range1d(start=HUM_Y_START, end=HUM_Y_END),
    height=400,
    sizing_mode="stretch_width",
    tools="pan,wheel_zoom,box_zoom,reset,save",
    active_drag=None,
    active_scroll=None,
    x_range=temp_plot.x_range
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


def _should_auto_update_temp(center: float | None) -> bool:
    if center is None:
        return False
    last_center = temp_window_state.get("last_auto_center")
    if last_center is not None and abs(center - last_center) < TEMP_CENTER_DEADBAND:
        return False
    now = time.monotonic()
    last_ts = temp_window_state.get("last_auto_update", 0.0)
    if now - last_ts < 10.0:
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
    if now - last_ts < 10.0:
        return False
    return True


def set_programmatic_range(start_ms, end_ms):
    """Set the x-range while suppressing user-interaction detection."""
    range_update_state["pending"] = 2
    current_window["last_set_start"] = start_ms
    current_window["last_set_end"] = end_ms
    temp_plot.x_range.start = start_ms
    temp_plot.x_range.end = end_ms


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


temp_plot.x_range.on_change('start', range_change_callback)
temp_plot.x_range.on_change('end', range_change_callback)

# UI widgets
current_readings = Div(text="<h3>Loading...</h3>", sizing_mode="stretch_width", height=120)

# Sensor selection dropdown
sensor_names = [cfg["name"] for cfg in sensor_configs]
sensor_selector = Select(
    title="Select Sensor:",
    value=current_sensor_state["name"],
    options=sensor_names,
    width=300
)

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

ma_spinner = Spinner(title="Moving average window (samples)", low=1, high=500, step=1,
                     value=DEFAULT_MA_WINDOW, width=180)
show_raw_toggle = Toggle(label="Raw data: ON", button_type="success", active=True, width=140)

# CSV download button
btn_download_csv = Button(label="📥 Download CSV", button_type="success", width=230)

# Window width spinners
temp_window_spinner = Spinner(title="Temperature window (°C)", low=MIN_TEMP_WINDOW,
                              high=None, step=0.5,
                              value=temp_window_state["value"], width=180)
hum_window_spinner = Spinner(title="Humidity window (%)", low=MIN_HUM_WINDOW,
                             high=MAX_HUM_WINDOW, step=1,
                             value=hum_window_state["value"], width=180)
temp_window_range_display = Div(text="", width=200, height=40)
hum_window_range_display = Div(text="", width=200, height=40)

window_control_state = {
    "temp_syncing": False,
    "hum_syncing": False,
}


def build_download_callback():
    """Create CustomJS callback for CSV download with sensor name and timestamp."""
    return CustomJS(args=dict(source=source, sensor_selector=sensor_selector), code="""
        // Sanitize sensor name for filename
        function sanitizeFilename(name) {
            var sanitized = name.replace(/[^\\w\\-]/g, '_');
            sanitized = sanitized.replace(/_+/g, '_');
            sanitized = sanitized.replace(/^_+|_+$/g, '');
            return sanitized || 'Unnamed_Sensor';
        }

        var columns = ['time', 'temperature', 'humidity'];
        var data = source.data;
        if (!data || !data.time || !data.time.length) {
            return;
        }

        var sensorName = sensor_selector.value || 'Unnamed Sensor';
        var lines = [['sensor_name'].concat(columns).join(',')];
        var nrows = data.time.length;

        for (var i = 0; i < nrows; i++) {
            var row = [sensorName];

            // Handle time column
            var timeVal = data.time[i];
            var dt = new Date(timeVal);
            if (!isNaN(dt.getTime())) {
                var y = dt.getFullYear();
                var m = ('0' + (dt.getMonth() + 1)).slice(-2);
                var d = ('0' + dt.getDate()).slice(-2);
                var hh = ('0' + dt.getHours()).slice(-2);
                var mm = ('0' + dt.getMinutes()).slice(-2);
                var ss = ('0' + dt.getSeconds()).slice(-2);
                row.push(y + '-' + m + '-' + d + ' ' + hh + ':' + mm + ':' + ss);
            } else {
                row.push('');
            }

            // Handle temperature
            var temp = data.temperature[i];
            if (typeof temp === 'number') {
                row.push(temp.toFixed(2));
            } else if (temp == null) {
                row.push('');
            } else {
                row.push(String(temp));
            }

            // Handle humidity
            var hum = data.humidity[i];
            if (typeof hum === 'number') {
                row.push(hum.toFixed(2));
            } else if (hum == null) {
                row.push('');
            } else {
                row.push(String(hum));
            }

            lines.push(row.join(','));
        }

        var csv = lines.join(String.fromCharCode(10));
        var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        var timestamp = new Date().toISOString().replace(/[:.-]/g, '').slice(0, 15);
        var sanitizedSensorName = sanitizeFilename(sensorName);
        var filename = sanitizedSensorName + '_' + timestamp + '.csv';
        var link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
    """)


def set_button_active(active_btn):
    """Highlight the active preset button."""
    for btn in time_buttons:
        btn.button_type = "success" if btn == active_btn else "default"


def update_time_window(minutes, sync_inputs=True):
    """Update the time window."""
    current_window["minutes"] = minutes
    current_window["force_update"] = True
    current_window["auto_range"] = True
    if sync_inputs:
        set_time_inputs_from_minutes(minutes)
    update_data()


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


def set_time_inputs_from_minutes(minutes):
    """Sync the custom inputs with a minutes value."""
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


def on_custom_time_change(attr, old, new):
    if time_input_state["updating"]:
        return

    minutes = get_minutes_from_inputs()
    if minutes is None:
        return

    set_button_active(None)
    update_time_window(minutes, sync_inputs=False)


def _update_window_displays():
    temp_start = temp_plot.y_range.start
    temp_end = temp_plot.y_range.end
    hum_start = humidity_plot.y_range.start
    hum_end = humidity_plot.y_range.end

    if temp_start is None or temp_end is None:
        temp_html = "<p style='margin:0;font-size:14px;'>Range: —</p>"
    else:
        temp_html = f"<p style='margin:0;font-size:14px;'>Range: {temp_start:.1f}°C – {temp_end:.1f}°C</p>"

    if hum_start is None or hum_end is None:
        hum_html = "<p style='margin:0;font-size:14px;'>Range: —</p>"
    else:
        hum_html = f"<p style='margin:0;font-size:14px;'>Range: {hum_start:.0f}% – {hum_end:.0f}%</p>"

    temp_window_range_display.text = temp_html
    hum_window_range_display.text = hum_html


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
    _set_hum_y_range(latest_values["hum"], value, record_auto=True)
    _update_window_displays()


# Initialize custom inputs to current preset
set_time_inputs_from_minutes(current_window["minutes"])

# Wire up callbacks
for spinner in (window_days, window_hours, window_minutes, window_seconds):
    spinner.on_change("value", on_custom_time_change)

btn_10min.on_click(lambda: (set_button_active(btn_10min), update_time_window(10)))
btn_1h.on_click(lambda: (set_button_active(btn_1h), update_time_window(60)))
btn_3h.on_click(lambda: (set_button_active(btn_3h), update_time_window(180)))
btn_12h.on_click(lambda: (set_button_active(btn_12h), update_time_window(720)))
btn_24h.on_click(lambda: (set_button_active(btn_24h), update_time_window(1440)))
btn_1week.on_click(lambda: (set_button_active(btn_1week), update_time_window(10080)))
btn_all.on_click(lambda: (set_button_active(btn_all), update_time_window(None)))

temp_window_spinner.on_change("value", _on_temp_window_change)
hum_window_spinner.on_change("value", _on_hum_window_change)

# Wire up CSV download callback
btn_download_csv.js_on_event("button_click", build_download_callback())


def on_ma_change(attr, old, new):
    """Recompute moving averages when the window size changes."""
    try:
        int(new)
    except (TypeError, ValueError):
        return
    current_window["force_update"] = True
    update_data()


ma_spinner.on_change("value", on_ma_change)


def on_raw_toggle_change(attr, old, new):
    visible = bool(new)
    temp_raw_renderer.visible = visible
    hum_raw_renderer.visible = visible
    show_raw_toggle.button_type = "success" if visible else "default"
    show_raw_toggle.label = f"Raw data: {'ON' if visible else 'OFF'}"


show_raw_toggle.on_change("active", on_raw_toggle_change)
on_raw_toggle_change("active", True, show_raw_toggle.active)


def on_sensor_change(attr, old, new):
    """Handle sensor selection change."""
    current_sensor_state["name"] = new
    temp_plot.title.text = f"Temperature - {new}"
    humidity_plot.title.text = f"Humidity - {new}"
    current_window["force_update"] = True
    update_data()
    logger.info(f"Switched to sensor: {new}")


sensor_selector.on_change("value", on_sensor_change)


def update_data():
    """Update the data source with new readings from selected sensor."""
    try:
        sensor_name = current_sensor_state["name"]
        logger.debug(f"update_data() called for sensor: {sensor_name}")

        # Fetch data from aggregator
        new_data = aggregator.get_sensor_data(sensor_name, limit=10000)

        if not new_data or len(new_data.get("time", [])) == 0:
            logger.warning(f"No data available for sensor: {sensor_name}")
            current_readings.text = f"<h3>⚠️ No data available for {sensor_name}</h3>"
            return

        logger.debug(f"Fetched {len(new_data.get('time', []))} data points")

        window = max(int(ma_spinner.value), 1)
        prepared = prepare_source_data(new_data, window)

        # Replace dataset
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

        # Track current data extents
        current_window["data_min"] = prepared["time"][0]
        current_window["data_max"] = prepared["time"][-1]

        if current_window.get("force_update", False):
            current_window["force_update"] = False

        # Update time window (sliding window) - use LATEST data
        if len(prepared["time"]) == 0:
            logger.warning("No time data in prepared dataset")
            return

        latest_time_ms = prepared["time"][-1]  # Get the LAST (most recent) timestamp

        if current_window["auto_range"]:
            if current_window["minutes"] is not None:
                window_duration_ms = current_window["minutes"] * 60 * 1000
                window_start_ms = latest_time_ms - window_duration_ms
                set_programmatic_range(window_start_ms, latest_time_ms)
            else:
                start_ms = prepared["time"][0]
                end_ms = latest_time_ms
                set_programmatic_range(start_ms, end_ms)

        # Update current readings display - use LATEST values
        curr_datetime = pd.Timestamp(latest_time_ms, unit='ms')
        curr_time_str = curr_datetime.strftime('%Y-%m-%d %H:%M:%S')

        # Make sure we're getting the last values
        if len(prepared["temp_ma"]) > 0:
            curr_temp = prepared["temp_ma"][-1]
        else:
            curr_temp = 0.0

        if len(prepared["hum_ma"]) > 0:
            curr_hum = prepared["hum_ma"][-1]
        else:
            curr_hum = 0.0

        logger.debug(f"Latest reading: {curr_time_str}, {curr_temp:.1f}°C, {curr_hum:.1f}%")

        # Get sensor metadata
        metadata = aggregator.get_sensor_metadata(sensor_name)
        status_indicator = "🟢" if metadata and metadata["status"] == "active" else "🔴"
        device_ip = metadata["device_ip"] if metadata else "unknown"

        current_readings.text = f"""
        <div style="background-color:#f0f0f0;padding:18px;border-radius:5px;margin-bottom:18px;display:flex;flex-wrap:wrap;gap:24px;align-items:center;">
            <div style="min-width:150px;">
                <h3 style="margin:0 0 6px 0;font-size:16px;">Sensor</h3>
                <p style="font-size:18px;margin:0;">{status_indicator} {sensor_name}</p>
            </div>
            <div style="min-width:150px;">
                <h3 style="margin:0 0 6px 0;font-size:16px;">IP Address</h3>
                <p style="font-size:16px;margin:0;">{device_ip}</p>
            </div>
            <div style="min-width:150px;">
                <h3 style="margin:0 0 6px 0;font-size:16px;">Time</h3>
                <p style="font-size:18px;margin:0;">{curr_time_str}</p>
            </div>
            <div style="min-width:150px;">
                <h3 style="margin:0 0 6px 0;font-size:16px;">Readings</h3>
                <p style="font-size:18px;margin:0;">{curr_temp:.1f}°C · {curr_hum:.1f}%</p>
            </div>
        </div>
        """
    except Exception as e:
        logger.error(f"Error updating data: {e}")


# Initial update
update_data()

# Schedule periodic updates
curdoc().add_periodic_callback(update_data, UPDATE_INTERVAL)

# Layout
sensor_selection_row = row(sensor_selector, sizing_mode="scale_width")
time_button_row = row(btn_10min, btn_1h, btn_3h, btn_12h, btn_24h, btn_1week, btn_all,
                      sizing_mode="scale_width")
custom_time_row = row(window_days, window_hours, window_minutes, window_seconds,
                      sizing_mode="scale_width")
ma_controls_row = row(ma_spinner, show_raw_toggle, btn_download_csv, sizing_mode="scale_width")
display_range_row = row(
    temp_window_spinner,
    temp_window_range_display,
    hum_window_spinner,
    hum_window_range_display,
    sizing_mode="scale_width"
)

layout = column(
    Div(text="<h1>🌡️ Multi-Sensor Temperature Monitor</h1>", sizing_mode="stretch_width", height=60),
    Div(text="<h3>Sensor Selection</h3>", sizing_mode="stretch_width", height=30),
    sensor_selection_row,
    Div(text="<br>", sizing_mode="stretch_width", height=10),
    current_readings,
    Div(text="<h4>Time Window:</h4>", sizing_mode="stretch_width", height=30),
    time_button_row,
    Div(text="<h4>Custom Window:</h4>", sizing_mode="stretch_width", height=30),
    custom_time_row,
    Div(text="<h4>Moving Average:</h4>", sizing_mode="stretch_width", height=30),
    ma_controls_row,
    Div(text="<h4>Display Range:</h4>", sizing_mode="stretch_width", height=30),
    display_range_row,
    Div(text="<br>", sizing_mode="stretch_width", height=10),
    temp_plot,
    humidity_plot,
    sizing_mode="scale_width"
)

curdoc().add_root(layout)
curdoc().title = "Multi-Sensor Temperature Monitor"
