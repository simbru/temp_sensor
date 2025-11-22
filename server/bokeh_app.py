"""
Multi-sensor Bokeh Server dashboard for lab temperature monitoring.
Run with: bokeh serve --show server/bokeh_app.py
"""
import configparser
import logging
import logging.handlers
import math
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
from bokeh.models import ColumnDataSource, Button, Spinner, Range1d, Toggle, CustomJS, Select, Span
from bokeh.layouts import column, row
from bokeh.models.widgets import Div

from server.api_client import MultiSensorClient
from server.data_aggregator import DataAggregator

# Configure logging to both file and console
log_dir = _project_root / "logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / "server.log"

# Create formatter
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# File handler with rotation (10MB max, keep 5 old files)
file_handler = logging.handlers.RotatingFileHandler(
    log_file,
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

# Console handler (still show logs in terminal)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)

logger = logging.getLogger(__name__)
logger.info(f"Logging to: {log_file}")

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
        for name, value in config["SENSORS"].items():
            # Parse format: "url" or "url, poll_interval_s"
            parts = [p.strip() for p in value.split(',')]
            url = parts[0]
            poll_interval = int(parts[1]) if len(parts) > 1 else 30  # Default 30s

            sensor_configs.append({
                "name": name.replace("_", " "),
                "url": url,
                "poll_interval_s": poll_interval
            })

    if not sensor_configs:
        raise ValueError("No sensors configured in config_server.ini")

    poll_interval = int(config.get("SERVER", "poll_interval_s", fallback="30"))
    min_gap_threshold = int(config.get("SERVER", "min_gap_threshold_s", fallback="60"))
    db_path = config.get("SERVER", "database_path", fallback="sensor_data.db")
    update_ms = int(config.get("SERVER", "dashboard_update_ms", fallback="10000"))
    max_plot_points = int(config.get("SERVER", "max_plot_points", fallback="50000"))

    return sensor_configs, poll_interval, min_gap_threshold, db_path, update_ms, max_plot_points

try:
    sensor_configs, poll_interval, min_gap_threshold, db_path, dashboard_update_ms, max_plot_points = load_server_config()
    logger.info(f"Loaded {len(sensor_configs)} sensor configurations")
    logger.info(f"Max plot points: {max_plot_points:,}")
except Exception as e:
    logger.error(f"Failed to load server configuration: {e}")
    raise

# Initialize multi-sensor client and data aggregator
multi_client = MultiSensorClient(sensor_configs)
aggregator = DataAggregator(
    multi_client,
    sensor_configs=sensor_configs,
    db_path=db_path,
    poll_interval=poll_interval,
    min_gap_threshold=min_gap_threshold
)

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
TOLERANCE_MS = 500
DEFAULT_MA_WINDOW = 10
MIN_TEMP_WINDOW = 1.0
MIN_HUM_WINDOW = 1.0
MAX_HUM_WINDOW = 100.0
DEFAULT_TEMP_CENTER = 20.0
DEFAULT_HUM_CENTER = 50.0
TEMP_CENTER_DEADBAND = 0.25
HUM_CENTER_DEADBAND = 2.0
MAX_FETCH_SAMPLES = 200000
FETCH_PADDING_SAMPLES = 200

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


def _determine_fetch_limit() -> int | None:
    minutes = current_window.get("minutes")
    if minutes is None:
        return None

    seconds = max(float(minutes) * 60.0, float(SAMPLE_INTERVAL_SECONDS))
    sample_interval = max(float(SAMPLE_INTERVAL_SECONDS), 1.0)
    estimated_samples = int(math.ceil(seconds / sample_interval)) + FETCH_PADDING_SAMPLES
    estimated_samples = max(estimated_samples, 1000)
    return min(estimated_samples, MAX_FETCH_SAMPLES)

STATUS_DISPLAY = {
    "active": {"icon": "🟢", "label": "Receiving data"},
    "idle": {"icon": "🔵", "label": "Connected · waiting"},
    "syncing": {"icon": "🟡", "label": "Syncing backlog"},
    "error": {"icon": "🔴", "label": "Offline or unreachable"},
    "unknown": {"icon": "🔴", "label": "Status unavailable"},
}

DEFAULT_STATUS_DISPLAY = STATUS_DISPLAY["unknown"]


def _resolve_status_display(metadata: dict | None) -> tuple[str, str]:
    """Return emoji and label for current sensor status."""
    status_value = (metadata or {}).get("status") or "unknown"
    status_key = status_value.lower() if isinstance(status_value, str) else "unknown"
    display = STATUS_DISPLAY.get(status_key, DEFAULT_STATUS_DISPLAY)
    if metadata and metadata.get("last_error"):
        display = STATUS_DISPLAY["error"]
    return display["icon"], display["label"]


def _format_metadata_timestamp(timestamp_str: str | None) -> str:
    if not timestamp_str:
        return "—"
    try:
        return pd.to_datetime(timestamp_str).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(timestamp_str)


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
def prepare_source_data(raw_data, window_size, max_points=None, connect_points=False, log_interval_s=None):
    """
    Return CDS-compatible dict with moving-average columns added.

    Args:
        raw_data: Raw sensor data dict
        window_size: Moving average window size
        max_points: Not used (reserved for future LTTB implementation)
        connect_points: If True, only show breaks for large gaps (3+ missed readings)
                       If False, show breaks for all gaps (1.5+ missed readings)
        log_interval_s: Sensor's log interval in seconds (for smart gap detection)
    """
    if not raw_data or len(raw_data.get("time", [])) == 0:
        return {"time": [], "temperature": [], "humidity": [], "temp_ma": [], "hum_ma": []}

    time_vals = np.asarray(raw_data.get("time", []))
    temps = np.asarray(raw_data.get("temperature", []), dtype=float)
    hums = np.asarray(raw_data.get("humidity", []), dtype=float)

    if len(temps) == 0:
        return {"time": time_vals, "temperature": temps, "humidity": hums,
                "temp_ma": temps, "hum_ma": hums}

    # Note: Removed simple downsampling as it caused "snaking" artifacts
    # Smart windowing now limits data at fetch time instead
    # Future: Could implement LTTB algorithm here for "All data" view

    # Always insert NaN markers for large gaps (sensor offline), but threshold depends on connect_points
    # - connect_points=True: Only show breaks for 3+ missed readings (large outages)
    # - connect_points=False: Show breaks for 1.5+ missed readings (more sensitive)
    if log_interval_s is not None and log_interval_s > 0:
        # Smart thresholding based on sensor's actual log interval
        if connect_points:
            gap_threshold = log_interval_s * 3.0  # 3x log interval = 3 missed readings
        else:
            gap_threshold = log_interval_s * 1.5  # 1.5x log interval = 1-2 missed readings
        expected_interval_s = log_interval_s  # Use actual sensor interval
    else:
        # Fallback for unknown log interval
        gap_threshold = 180 if connect_points else 60
        expected_interval_s = 2  # Fallback typical interval

    time_vals, temps, hums = _insert_gap_markers(time_vals, temps, hums,
                                                  gap_threshold_s=gap_threshold,
                                                  expected_interval_s=expected_interval_s)

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


def _insert_gap_markers(time_vals, temps, hums, gap_threshold_s=60, expected_interval_s=2):
    """
    Insert NaN at time gaps to show missing data in plots.

    Detects where time between consecutive points exceeds threshold and
    inserts proportional NaN markers to prevent moving averages from
    falsely interpolating across gaps.

    IMPORTANT: time_vals are milliseconds since epoch (float), not ISO strings.
    This is the format returned by data_aggregator for Bokeh plotting.

    Args:
        time_vals: Millisecond timestamps (float) - NOT ISO strings
        temps: Temperature values
        hums: Humidity values
        gap_threshold_s: Time gap threshold in seconds (default 60s = 1 minute)
                        Gaps larger than this are considered client outages
        expected_interval_s: Expected sensor log interval in seconds (default 2s)
                           Used to calculate proportional NaN markers

    Returns:
        Tuple of (times, temps, hums) with NaN inserted at gaps
    """
    if len(time_vals) < 2:
        return time_vals, temps, hums

    # Calculate time gaps directly from milliseconds (avoid datetime conversion)
    time_diffs_ms = np.diff(time_vals)
    time_diffs_s = time_diffs_ms / 1000.0
    
    # Detect gaps larger than threshold (e.g., client offline)
    gap_mask = time_diffs_s > gap_threshold_s
    gap_indices = np.where(gap_mask)[0] + 1  # +1 because diff shifts indices
    
    if len(gap_indices) == 0:
        return time_vals, temps, hums
    
    # Calculate exact size needed by examining each gap
    total_nan_markers = 0
    for gap_idx in gap_indices:
        gap_size_ms = time_vals[gap_idx] - time_vals[gap_idx - 1]
        gap_size_seconds = gap_size_ms / 1000.0
        num_missing = int(gap_size_seconds / expected_interval_s)
        num_nan_markers = max(min(num_missing, 100), 5)
        total_nan_markers += num_nan_markers
    
    # Preallocate result arrays with exact size
    result_size = len(time_vals) + total_nan_markers
    result_times = np.empty(result_size, dtype=float)
    result_temps = np.empty(result_size, dtype=float)
    result_hums = np.empty(result_size, dtype=float)
    
    write_idx = 0
    prev_idx = 0
    
    for gap_idx in gap_indices:
        # Copy data up to gap
        chunk_size = gap_idx - prev_idx
        result_times[write_idx:write_idx+chunk_size] = time_vals[prev_idx:gap_idx]
        result_temps[write_idx:write_idx+chunk_size] = temps[prev_idx:gap_idx]
        result_hums[write_idx:write_idx+chunk_size] = hums[prev_idx:gap_idx]
        write_idx += chunk_size
        
        # Calculate how many NaN markers to insert based on gap size
        gap_size_ms = time_vals[gap_idx] - time_vals[gap_idx - 1]
        gap_size_seconds = gap_size_ms / 1000.0
        num_missing = int(gap_size_seconds / expected_interval_s)

        # Insert enough NaN markers to prevent moving average from bridging the gap
        # Minimum of 5 ensures even moderate MA windows show the break
        num_nan_markers = max(min(num_missing, 100), 5)  # Cap at 100 to avoid huge gaps
        
        # Insert NaN with interpolated millisecond timestamps within the gap
        # Vectorized: create all NaN markers at once instead of loop
        gap_start_ms = time_vals[gap_idx - 1]
        gap_end_ms = time_vals[gap_idx]
        nan_times = np.linspace(gap_start_ms, gap_end_ms, num_nan_markers + 2)[1:-1]  # Exclude endpoints

        result_times[write_idx:write_idx+num_nan_markers] = nan_times
        result_temps[write_idx:write_idx+num_nan_markers] = np.nan
        result_hums[write_idx:write_idx+num_nan_markers] = np.nan
        write_idx += num_nan_markers
        
        prev_idx = gap_idx
    
    # Copy remaining data
    remaining = len(time_vals) - prev_idx
    result_times[write_idx:write_idx+remaining] = time_vals[prev_idx:]
    result_temps[write_idx:write_idx+remaining] = temps[prev_idx:]
    result_hums[write_idx:write_idx+remaining] = hums[prev_idx:]
    write_idx += remaining
    
    return (result_times[:write_idx],
            result_temps[:write_idx],
            result_hums[:write_idx])


# Fetch initial data for first sensor
initial_data_raw = aggregator.get_sensor_data(current_sensor_state["name"], limit=1000)
initial_metadata = aggregator.get_sensor_metadata(current_sensor_state["name"])
initial_log_interval = (initial_metadata or {}).get('log_interval_s')
initial_data = prepare_source_data(initial_data_raw, DEFAULT_MA_WINDOW, max_plot_points,
                                   connect_points=False, log_interval_s=initial_log_interval)
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
    "minutes": 1440,  # 24 hours
    "force_update": True,
    "auto_range": True,
    "last_set_start": None,
    "last_set_end": None,
    "data_min": None,
    "data_max": None,
}

# Data cache to avoid re-fetching on every button click
data_cache = {
    "sensor_name": None,
    "raw_data": None,
    "prepared_data": None,
    "last_timestamp": None,  # Track last timestamp for incremental fetch
    "last_fetch_time": 0,
    "fetch_interval_ms": 5000,  # Re-fetch every 5 seconds max
    # Cache keys for prepared data to avoid recomputation
    "prep_ma_window": None,
    "prep_max_points": None,
    "prep_raw_hash": None,  # Hash of raw data to detect changes
}

range_update_state = {
    "programmatic": False,
    "programmatic_until": 0.0
}

# Create plots
temp_plot = figure(
    title=f"Temperature - {current_sensor_state['name']}",
    x_axis_label="Time",
    y_axis_label="Temperature (°C)",
    x_axis_type="datetime",
    y_range=Range1d(start=TEMP_Y_START, end=TEMP_Y_END),
    height=200,
    sizing_mode="scale_width",
    max_width=1000,
    tools="pan,wheel_zoom,box_zoom,reset,save",
    active_drag=None,
    active_scroll=None,
    x_range=Range1d(),
    output_backend="webgl"  # Hardware-accelerated rendering
)
temp_raw_renderer = temp_plot.line('time', 'temperature', source=source, line_width=2,
                                   color='#ff7a7a', alpha=0.6)
temp_ma_renderer = temp_plot.line('time', 'temp_ma', source=source, line_width=3,
                                  color='red', alpha=0.9)

# Configure gridlines: automatic ticks with minor gridlines
temp_plot.xgrid.grid_line_color = "gray"
temp_plot.xgrid.grid_line_alpha = 0.3
temp_plot.xgrid.grid_line_width = 1
temp_plot.xgrid.minor_grid_line_color = "lightgray"
temp_plot.xgrid.minor_grid_line_alpha = 0.15
temp_plot.xaxis.ticker.num_minor_ticks = 5

# Store day boundary spans for dynamic updates
day_boundary_spans_temp = []

humidity_plot = figure(
    title=f"Humidity - {current_sensor_state['name']}",
    x_axis_label="Time",
    y_axis_label="Humidity (%)",
    x_axis_type="datetime",
    y_range=Range1d(start=HUM_Y_START, end=HUM_Y_END),
    height=200,
    sizing_mode="scale_width",
    max_width=1000,
    tools="pan,wheel_zoom,box_zoom,reset,save",
    active_drag=None,
    active_scroll=None,
    x_range=temp_plot.x_range,
    output_backend="webgl"  # Hardware-accelerated rendering
)
hum_raw_renderer = humidity_plot.line('time', 'humidity', source=source, line_width=2,
                                      color='#6fa8ff', alpha=0.6)
hum_ma_renderer = humidity_plot.line('time', 'hum_ma', source=source, line_width=3,
                                     color='navy', alpha=0.9)

# Configure gridlines: automatic ticks with minor gridlines
humidity_plot.xgrid.grid_line_color = "gray"
humidity_plot.xgrid.grid_line_alpha = 0.3
humidity_plot.xgrid.grid_line_width = 1
humidity_plot.xgrid.minor_grid_line_color = "lightgray"
humidity_plot.xgrid.minor_grid_line_alpha = 0.15
humidity_plot.xaxis.ticker.num_minor_ticks = 5

# Store day boundary spans for dynamic updates
day_boundary_spans_hum = []


def _update_day_boundaries(data_times):
    """Add vertical lines at midnight (day boundaries) within the data range."""
    try:
        if len(data_times) == 0:
            return

        from datetime import datetime, timedelta
        import pandas as pd

        # Convert milliseconds to datetime
        start_dt = pd.Timestamp(data_times[0], unit='ms')
        end_dt = pd.Timestamp(data_times[-1], unit='ms')

        # Find all midnight boundaries in range
        current_date = start_dt.normalize() + timedelta(days=1)  # Next midnight after start
        midnight_times = []

        while current_date <= end_dt:
            midnight_ms = current_date.value / 1e6  # Convert to milliseconds
            midnight_times.append(midnight_ms)
            current_date += timedelta(days=1)

        # Check if boundaries changed - skip expensive renderer updates if same
        global day_boundary_spans_temp, day_boundary_spans_hum, _last_midnight_times
        if '_last_midnight_times' not in globals():
            _last_midnight_times = None

        if _last_midnight_times == midnight_times:
            return  # No change, skip update

        _last_midnight_times = midnight_times

        # Remove old spans
        for span in day_boundary_spans_temp:
            try:
                temp_plot.renderers.remove(span)
            except Exception:
                pass
        for span in day_boundary_spans_hum:
            try:
                humidity_plot.renderers.remove(span)
            except Exception:
                pass

        day_boundary_spans_temp.clear()
        day_boundary_spans_hum.clear()

        # Add new spans at midnight boundaries
        for midnight_ms in midnight_times:
            span_temp = Span(location=midnight_ms, dimension='height',
                             line_color='navy', line_width=2, line_alpha=0.4)
            span_hum = Span(location=midnight_ms, dimension='height',
                            line_color='navy', line_width=2, line_alpha=0.4)

            temp_plot.add_layout(span_temp)
            humidity_plot.add_layout(span_hum)

            day_boundary_spans_temp.append(span_temp)
            day_boundary_spans_hum.append(span_hum)
    except Exception as e:
        logger.warning(f"Failed to update day boundaries: {e}")
        # Non-critical - continue without day boundaries


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
    range_update_state["programmatic"] = True
    range_update_state["programmatic_until"] = time.time() + 0.5  # 500ms window
    current_window["last_set_start"] = start_ms
    current_window["last_set_end"] = end_ms
    temp_plot.x_range.start = start_ms
    temp_plot.x_range.end = end_ms


def range_change_callback(attr, old, new):
    """Detect if range change was from user or programmatic."""
    # Check if we're in programmatic window (500ms after set_programmatic_range)
    if range_update_state["programmatic"] and time.time() < range_update_state["programmatic_until"]:
        current_window[f"last_set_{attr}"] = new
        return

    # Clear programmatic flag if window expired
    range_update_state["programmatic"] = False

    # Check tolerance against expected value
    expected_key = f"last_set_{attr}"
    expected = current_window.get(expected_key)

    if expected is not None:
        diff = abs(new - expected)
        if diff <= TOLERANCE_MS:
            current_window[expected_key] = new
            return

    # Check against data boundaries
    data_min = current_window.get("data_min")
    data_max = current_window.get("data_max")
    if attr == "start" and data_min is not None and abs(new - data_min) <= TOLERANCE_MS:
        current_window[expected_key] = new
        return
    if attr == "end" and data_max is not None and abs(new - data_max) <= TOLERANCE_MS:
        current_window[expected_key] = new
        return

    # Genuine user interaction detected - disable auto-range
    if current_window["auto_range"]:
        current_window["auto_range"] = False
        logger.info("Auto-scroll disabled: user zoomed/panned the plot")


temp_plot.x_range.on_change('start', range_change_callback)
temp_plot.x_range.on_change('end', range_change_callback)

# UI widgets
current_readings = Div(text="<h3>Loading...</h3>", sizing_mode="stretch_width")

# Sensor selection dropdown
sensor_names = [cfg["name"] for cfg in sensor_configs]
sensor_selector = Select(
    title="Select Sensor:",
    value=current_sensor_state["name"],
    options=sensor_names,
    width=300
)

# Time window buttons
btn_10min = Button(label="10 min", button_type="default", width=100)
btn_1h = Button(label="1 hour", button_type="default", width=100)
btn_3h = Button(label="3 hours", button_type="default", width=100)
btn_12h = Button(label="12 hours", button_type="default", width=100)
btn_24h = Button(label="24 hours", button_type="success", width=100)
btn_1week = Button(label="1 week", button_type="default", width=100)
btn_all = Button(label="All data", button_type="default", width=100)

time_buttons = [btn_10min, btn_1h, btn_3h, btn_12h, btn_24h, btn_1week, btn_all]

# Custom window inputs (days/hours/minutes/seconds)
# Note: No hard limits - you can enter any value (e.g., 120 minutes, 48 hours, etc.)
time_input_state = {"updating": False}
window_days = Spinner(title="Days", low=0, step=1, value=0, width=90)
window_hours = Spinner(title="Hours", low=0, step=1, value=24, width=90)
window_minutes = Spinner(title="Minutes", low=0, step=1, value=0, width=90)
window_seconds = Spinner(title="Seconds", low=0, step=1, value=0, width=90)

ma_spinner = Spinner(title="Average samples", low=1, high=9999, step=1,
                     value=DEFAULT_MA_WINDOW, width=90)

# Data display selector (raw, average, or both)
data_display_select = Select(
    title="Data display:",
    value="Both",
    options=["Both", "Average only", "Raw only"],
    width=140
)

# Auto-scroll toggle
auto_scroll_toggle = Toggle(label="Auto-scroll: ON", button_type="success", active=True, width=140)

# CSV download button
btn_download_csv = Button(label="Download CSV", button_type="success", width=150, height=40)

# Window width spinners
temp_window_spinner = Spinner(title="Temperature range (°C)", low=MIN_TEMP_WINDOW,
                            high=None, step=0.5,
                            value=temp_window_state["value"], width=120)
hum_window_spinner = Spinner(title="Humidity range (%)", low=MIN_HUM_WINDOW,
                            high=MAX_HUM_WINDOW, step=1,
                            value=hum_window_state["value"], width=120)
# temp_window_range_display = Div(text="", width=20, height=40)
# hum_window_range_display = Div(text="", width=20, height=40)

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
    """
    Update the time window.
    Triggers data refetch if window expanded beyond cached data.
    """
    logger.info(f"update_time_window called with minutes={minutes}")

    # Check if we need to refetch data (window changed significantly)
    old_minutes = current_window.get("minutes")
    needs_refetch = False

    if minutes != old_minutes:
        # Switching between "All data" and windowed, or vice versa
        if (minutes is None) != (old_minutes is None):
            needs_refetch = True
            logger.info(f"Window type changed (all data <-> windowed), refetching")
        # Expanding window beyond cached range
        elif minutes is not None and old_minutes is not None and minutes > old_minutes * 1.5:
            needs_refetch = True
            logger.info(f"Window expanded significantly ({old_minutes}m → {minutes}m), refetching")

    current_window["minutes"] = minutes
    current_window["force_update"] = True
    current_window["auto_range"] = True

    if sync_inputs:
        set_time_inputs_from_minutes(minutes)

    if needs_refetch:
        # Refetch data with new window
        sensor_name = current_sensor_state["name"]
        fetch_initial_data(sensor_name)

    update_view()  # Update view with (possibly new) data


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


# def _update_window_displays():
#     temp_start = temp_plot.y_range.start
#     temp_end = temp_plot.y_range.end
#     hum_start = humidity_plot.y_range.start
#     hum_end = humidity_plot.y_range.end

#     if temp_start is None or temp_end is None:
#         temp_html = "<p style='margin:0;font-size:8px;'>Range: —</p>"
#     else:
#         temp_html = f"<p style='margin:0;font-size:8px;'>Range: {temp_start:.1f}°C – {temp_end:.1f}°C</p>"

#     if hum_start is None or hum_end is None:
#         hum_html = "<p style='margin:0;font-size:8px;'>Range: —</p>"
#     else:
#         hum_html = f"<p style='margin:0;font-size:8px;'>Range: {hum_start:.0f}% – {hum_end:.0f}%</p>"

#     temp_window_range_display.text = temp_html
#     hum_window_range_display.text = hum_html


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
    # _update_window_displays()


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
    # _update_window_displays()


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

# Initialize window displays with current values
# _update_window_displays()

# Wire up CSV download callback
btn_download_csv.js_on_event("button_click", build_download_callback())


def on_ma_change(attr, old, new):
    """Recompute moving averages when the window size changes."""
    try:
        int(new)
    except (TypeError, ValueError):
        return
    current_window["force_update"] = True
    update_view()  # Recalculate moving average from cached data


ma_spinner.on_change("value", on_ma_change)


def on_data_display_change(attr, old, new):
    """Handle data display mode changes (Both/Average only/Raw only)."""
    if new == "Both":
        temp_raw_renderer.visible = True
        hum_raw_renderer.visible = True
        temp_ma_renderer.visible = True
        hum_ma_renderer.visible = True
    elif new == "Average only":
        temp_raw_renderer.visible = False
        hum_raw_renderer.visible = False
        temp_ma_renderer.visible = True
        hum_ma_renderer.visible = True
    elif new == "Raw only":
        temp_raw_renderer.visible = True
        hum_raw_renderer.visible = True
        temp_ma_renderer.visible = False
        hum_ma_renderer.visible = False


data_display_select.on_change("value", on_data_display_change)


def on_auto_scroll_toggle(attr, old, new):
    """Handle auto-scroll toggle changes."""
    current_window["auto_range"] = bool(new)
    auto_scroll_toggle.button_type = "success" if new else "default"
    auto_scroll_toggle.label = f"Auto-scroll: {'ON' if new else 'OFF'}"
    if new:
        logger.info("Auto-scroll re-enabled by user")
        update_view()  # Force immediate view update to current time


auto_scroll_toggle.on_change("active", on_auto_scroll_toggle)


def on_sensor_change(attr, old, new):
    """Handle sensor selection change."""
    current_sensor_state["name"] = new
    temp_plot.title.text = f"Temperature - {new}"
    humidity_plot.title.text = f"Humidity - {new}"
    current_window["force_update"] = True

    # Sensor changed - need to fetch all historical data for new sensor
    fetch_initial_data(new)
    update_view()

    logger.info(f"Switched to sensor: {new}")


sensor_selector.on_change("value", on_sensor_change)


def fetch_initial_data(sensor_name):
    """
    Fetch data for a sensor from database with smart windowing.
    - If viewing specific time window: fetches that window + margin
    - If "All data": fetches everything
    Called on startup, sensor change, or window change.
    """
    from datetime import datetime, timedelta
    import pandas as pd

    # Determine fetch strategy based on time window
    window_minutes = current_window.get("minutes")

    if window_minutes is None:
        # "All data" mode - fetch everything
        logger.info(f"Fetching ALL historical data for {sensor_name}")
        try:
            new_data = aggregator.get_sensor_data(sensor_name)
            fetch_type = "all"
        except Exception as e:
            logger.error(f"Error fetching all data: {e}")
            return None
    else:
        # Smart windowing - fetch only visible window + 2x margin
        margin_factor = 2.0  # Fetch 2x the visible window for smooth panning
        fetch_window_minutes = window_minutes * margin_factor

        logger.info(f"Fetching last {fetch_window_minutes:.0f} minutes of data for {sensor_name} (window: {window_minutes:.0f}m)")

        try:
            # Calculate time range (from now back to fetch_window_minutes ago)
            end_time = datetime.now()
            start_time = end_time - timedelta(minutes=fetch_window_minutes)

            # Convert to INTEGER milliseconds for database query
            start_ms = int(start_time.timestamp() * 1000)
            end_ms = int(end_time.timestamp() * 1000)

            new_data = aggregator.get_sensor_data(sensor_name, start_time=start_ms, end_time=end_ms)
            fetch_type = "windowed"
        except Exception as e:
            logger.error(f"Error fetching windowed data: {e}")
            return None

    try:
        if not new_data or len(new_data.get("time", [])) == 0:
            logger.warning(f"No data available for sensor: {sensor_name}")
            return None

        record_count = len(new_data.get('time', []))
        logger.info(f"Fetched {record_count:,} records ({fetch_type})")

        # Fetch metadata to get log_interval for smart gap detection
        metadata = aggregator.get_sensor_metadata(sensor_name)
        log_interval_s = (metadata or {}).get('log_interval_s')

        # Update cache
        data_cache["sensor_name"] = sensor_name
        data_cache["raw_data"] = new_data
        data_cache["last_timestamp"] = new_data["time"][-1] if len(new_data["time"]) > 0 else None
        data_cache["last_fetch_time"] = time.time() * 1000
        data_cache["log_interval_s"] = log_interval_s  # Store for gap detection

        return new_data
    except Exception as e:
        logger.error(f"Error processing fetched data: {e}")
        return None


def fetch_incremental_data(sensor_name):
    """
    Fetch only NEW data since last known timestamp.
    Called by periodic refresh to get latest readings.
    """
    # If no cache or sensor changed, do full fetch
    if data_cache["sensor_name"] != sensor_name or data_cache["last_timestamp"] is None:
        return fetch_initial_data(sensor_name)

    try:
        from datetime import datetime
        import pandas as pd

        # Use INTEGER milliseconds directly for query (no conversion needed!)
        last_timestamp_ms = data_cache["last_timestamp"]
        now_ms = int(datetime.now().timestamp() * 1000)

        # Fetch only new records
        new_records = aggregator.get_sensor_data(
            sensor_name,
            start_time=last_timestamp_ms,
            end_time=now_ms
        )

        if not new_records or len(new_records.get("time", [])) == 0:
            logger.debug(f"No new data for {sensor_name}")
            return data_cache["raw_data"]  # Return cached data

        # Append new records to cache
        if len(new_records["time"]) > 0:
            logger.debug(f"Fetched {len(new_records['time'])} new records for {sensor_name}")

            # Convert to lists for extension
            data_cache["raw_data"]["time"] = list(data_cache["raw_data"]["time"]) + list(new_records["time"])
            data_cache["raw_data"]["temperature"] = list(data_cache["raw_data"]["temperature"]) + list(new_records["temperature"])
            data_cache["raw_data"]["humidity"] = list(data_cache["raw_data"]["humidity"]) + list(new_records["humidity"])

            # Trim old data to prevent endless accumulation (only for windowed mode)
            window_minutes = current_window.get("minutes")
            if window_minutes is not None:
                # Keep 3x the window size to allow for smooth panning
                keep_window_minutes = window_minutes * 3
                keep_window_ms = keep_window_minutes * 60 * 1000

                # Find cutoff time
                latest_time = data_cache["raw_data"]["time"][-1]
                cutoff_time = latest_time - keep_window_ms

                # Find index of first point to keep
                times = np.array(data_cache["raw_data"]["time"])
                keep_indices = times >= cutoff_time
                first_keep_idx = np.argmax(keep_indices)

                if first_keep_idx > 0:
                    # Trim old data
                    data_cache["raw_data"]["time"] = data_cache["raw_data"]["time"][first_keep_idx:]
                    data_cache["raw_data"]["temperature"] = data_cache["raw_data"]["temperature"][first_keep_idx:]
                    data_cache["raw_data"]["humidity"] = data_cache["raw_data"]["humidity"][first_keep_idx:]
                    logger.debug(f"Trimmed {first_keep_idx} old points from cache")

            # Update last timestamp
            data_cache["last_timestamp"] = new_records["time"][-1]
            data_cache["last_fetch_time"] = time.time() * 1000

        return data_cache["raw_data"]
    except Exception as e:
        logger.error(f"Error fetching incremental data: {e}")
        return data_cache["raw_data"]  # Fall back to cached data


def update_view():
    """
    Update plot view and UI elements using cached data.
    Does NOT fetch from database - instant response.
    """
    try:
        sensor_name = current_sensor_state["name"]

        if data_cache["raw_data"] is None or data_cache["sensor_name"] != sensor_name:
            logger.warning("No cached data available, triggering initial fetch")
            fetch_initial_data(sensor_name)
            if data_cache["raw_data"] is None:
                current_readings.text = f"<h3>⚠️ No data available for {sensor_name}</h3>"
                return

        # Use cached data
        raw_data = data_cache["raw_data"]

        # Check if we need to recompute prepared data
        window = max(int(ma_spinner.value), 1)

        # Create a simple hash of the data (length + first/last timestamps)
        # Avoids expensive hashing of large arrays
        if raw_data and raw_data.get("time") is not None and len(raw_data.get("time", [])) > 0:
            times = raw_data["time"]
            raw_hash = (len(times), times[0] if len(times) > 0 else 0, times[-1] if len(times) > 0 else 0)
        else:
            raw_hash = None

        # Only recompute if parameters or data changed
        cache_valid = (
            data_cache["prepared_data"] is not None and
            data_cache["prep_ma_window"] == window and
            data_cache["prep_max_points"] == max_plot_points and
            data_cache["prep_raw_hash"] == raw_hash
        )

        if cache_valid:
            # Use cached prepared data - no recomputation needed!
            logger.debug("Using cached prepared data")
            prepared = data_cache["prepared_data"]
        else:
            # Recompute and cache
            logger.debug(f"Recomputing prepared data (MA window: {window}, max points: {max_plot_points})")
            log_interval_s = data_cache.get("log_interval_s")  # Get from cache for smart gap detection
            prepared = prepare_source_data(raw_data, window, max_plot_points,
                                          connect_points=False,  # Use sensitive gap detection (1.5x log interval)
                                          log_interval_s=log_interval_s)
            data_cache["prepared_data"] = prepared
            data_cache["prep_ma_window"] = window
            data_cache["prep_max_points"] = max_plot_points
            data_cache["prep_raw_hash"] = raw_hash

        # Replace dataset
        source.data = prepared

        # Update day boundary markers
        _update_day_boundaries(prepared["time"])

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
        current_window["data_min"] = prepared["time"][0] if len(prepared["time"]) > 0 else None
        current_window["data_max"] = prepared["time"][-1] if len(prepared["time"]) > 0 else None

        if current_window.get("force_update", False):
            current_window["force_update"] = False

        # Update time window (sliding window) - use LATEST data
        if len(prepared["time"]) == 0:
            logger.warning("No time data in prepared dataset")
            return

        latest_time_ms = prepared["time"][-1]

        if current_window["auto_range"]:
            if current_window["minutes"] is not None:
                window_duration_ms = current_window["minutes"] * 60 * 1000
                window_start_ms = latest_time_ms - window_duration_ms
                set_programmatic_range(window_start_ms, latest_time_ms)
            else:
                start_ms = prepared["time"][0]
                end_ms = latest_time_ms
                set_programmatic_range(start_ms, end_ms)

        # Sync toggle state with auto-range flag
        if not current_window["auto_range"] and auto_scroll_toggle.active:
            auto_scroll_toggle.active = False
            auto_scroll_toggle.button_type = "default"
            auto_scroll_toggle.label = "Auto-scroll: OFF"

        # Update current readings display
        _update_status_display(sensor_name, prepared, latest_time_ms)

    except Exception as e:
        logger.error(f"Error updating view: {e}")


def _update_status_display(sensor_name, prepared, latest_time_ms):
    """Helper function to update the status display."""
    import pandas as pd

    # Find last NON-NaN raw data point (not moving average)
    # This ensures we show the actual last sensor reading, not an averaged value
    temps = np.array(prepared["temperature"])
    hums = np.array(prepared["humidity"])
    times = np.array(prepared["time"])

    # Find last non-NaN indices
    valid_temp_indices = np.where(~np.isnan(temps))[0]
    valid_hum_indices = np.where(~np.isnan(hums))[0]

    if len(valid_temp_indices) > 0:
        last_temp_idx = valid_temp_indices[-1]
        curr_temp = temps[last_temp_idx]
        curr_time_temp_ms = times[last_temp_idx]
    else:
        curr_temp = 0.0
        curr_time_temp_ms = latest_time_ms

    if len(valid_hum_indices) > 0:
        last_hum_idx = valid_hum_indices[-1]
        curr_hum = hums[last_hum_idx]
        curr_time_hum_ms = times[last_hum_idx]
    else:
        curr_hum = 0.0
        curr_time_hum_ms = latest_time_ms

    # Use the most recent timestamp for display (usually same for both)
    latest_reading_ms = max(curr_time_temp_ms, curr_time_hum_ms)
    curr_datetime = pd.Timestamp(latest_reading_ms, unit='ms')
    curr_time_str = curr_datetime.strftime('%Y-%m-%d %H:%M:%S')

    logger.debug(f"Latest reading: {curr_time_str}, {curr_temp:.1f}°C, {curr_hum:.1f}%")

    # Get sensor metadata
    metadata = aggregator.get_sensor_metadata(sensor_name)
    status_icon, status_label = _resolve_status_display(metadata)
    metadata_safe = metadata or {}
    device_ip = metadata_safe.get("device_ip") or "unknown"
    last_sync_str = _format_metadata_timestamp(metadata_safe.get("last_update"))
    last_error = metadata_safe.get("last_error")
    error_html = ""
    if last_error:
        error_html = f"<p style='font-size:12px;margin:6px 0 0;color:#c0392b;background-color:#fdecea;padding:4px 8px;border-radius:3px;'>Last error: {last_error}</p>"

    # Get server uptime
    uptime = aggregator.get_uptime()
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        uptime_str = f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        uptime_str = f"{hours}h {minutes}m {seconds}s"
    else:
        uptime_str = f"{minutes}m {seconds}s"

    # Get server metrics
    server_metrics = aggregator.get_server_metrics()
    server_db_size = server_metrics.get("database_size_mb", 0)
    total_records = server_metrics.get("total_records", 0)

    # Format records count
    if total_records >= 1000000:
        records_str = f"{total_records / 1000000:.1f}M"
    elif total_records >= 1000:
        records_str = f"{total_records / 1000:.0f}K"
    else:
        records_str = str(total_records)

    # Get client metrics
    cpu_str = f"{metadata_safe.get('cpu_percent', 0):.0f}%" if metadata_safe.get('cpu_percent') is not None else "—"
    mem_str = f"{metadata_safe.get('memory_percent', 0):.0f}%" if metadata_safe.get('memory_percent') is not None else "—"
    client_db_str = f"{metadata_safe.get('client_db_size_mb', 0):.1f} MB" if metadata_safe.get('client_db_size_mb') is not None else "—"
    sensor_type_str = metadata_safe.get('sensor_type', 'Unknown')

    # Calculate time since last reading (use actual last reading time, not plot end time)
    now_ms = int(pd.Timestamp.now().timestamp() * 1000)
    time_ago_ms = now_ms - latest_reading_ms
    time_ago_s = time_ago_ms / 1000

    # Format time ago
    if time_ago_s < 60:
        time_ago_part = f"{int(time_ago_s)}s"
    elif time_ago_s < 3600:
        time_ago_part = f"{int(time_ago_s / 60)}m"
    else:
        time_ago_part = f"{int(time_ago_s / 3600)}h"

    # Format with log interval if available: "60s | 7s ago"
    log_interval_s = metadata_safe.get('log_interval_s')
    if log_interval_s is not None:
        if log_interval_s < 60:
            log_interval_part = f"{int(log_interval_s)}s"
        elif log_interval_s < 3600:
            log_interval_part = f"{int(log_interval_s / 60)}m"
        else:
            log_interval_part = f"{int(log_interval_s / 3600)}h"
        time_ago_str = f"{log_interval_part} | {time_ago_part} ago"
    else:
        time_ago_str = f"{time_ago_part} ago"

    # Format client record count
    client_total_records = metadata_safe.get('client_total_records', 0)
    if client_total_records is not None and client_total_records >= 1000000:
        client_records_str = f"{client_total_records / 1000000:.1f}M"
    elif client_total_records is not None and client_total_records >= 1000:
        client_records_str = f"{client_total_records / 1000:.0f}K"
    elif client_total_records is not None:
        client_records_str = str(client_total_records)
    else:
        client_records_str = "—"

    current_readings.text = f"""
    <div style="background-color:#f0f0f0;padding:14px;border-radius:5px;margin-bottom:18px;box-shadow:0 2px 4px rgba(0,0,0,0.1);display:flex;flex-wrap:wrap;gap:2px;align-items:flex-start;max-width:1200px;">
        <div style="flex:1 1 180px;min-width:180px;">
            <h3 style="margin:0 0 5px 0;font-size:14px;font-weight:600;">Status</h3>
            <p style="font-size:16px;margin:0;">{status_icon} {sensor_name}</p>
            <p style="font-size:12px;margin:4px 0 0;color:#555;">{status_label}</p>
            <p style="font-size:12px;margin:4px 0 0;color:#555;">{sensor_type_str}</p>
        </div>
        <div style="flex:1 1 200px;min-width:200px;">
            <h3 style="margin:0 0 5px 0;font-size:14px;font-weight:600;">Last reading</h3>
            <p style="font-size:16px;margin:0;">{curr_temp:.1f}°C · {curr_hum:.1f}%</p>
            <p style="font-size:12px;margin:4px 0 0;color:#555;">{time_ago_str}</p>
            <p style="font-size:11px;margin:2px 0 0;color:#777;">{curr_time_str}</p>
        </div>
        <div style="flex:1 1 200px;min-width:200px;">
            <h3 style="margin:0 0 5px 0;font-size:14px;font-weight:600;">Client Info</h3>
            <p style="font-size:16px;margin:0;font-family:monospace;">{device_ip}</p>
            <p style="font-size:12px;margin:4px 0 0;color:#555;">CPU: {cpu_str} | Memory: {mem_str}</p>
            <p style="font-size:12px;margin:4px 0 0;color:#555;">Database: {client_db_str} | Records: {client_records_str}</p>
        </div>
        <div style="flex:1 1 220px;min-width:220px;">
            <h3 style="margin:0 0 5px 0;font-size:14px;font-weight:600;">Server Info</h3>
            <p style="font-size:16px;margin:0;">Uptime: {uptime_str}</p>
            <p style="font-size:12px;margin:4px 0 0;color:#555;">Last Sync: {last_sync_str}</p>
            <p style="font-size:12px;margin:4px 0 0;color:#555;">Database: {server_db_size:.1f} MB | Records: {records_str}</p>
            {error_html}
        </div>
    </div>
    """


def update_data():
    """
    Periodic refresh - fetch new data and update view.
    Called by bokeh periodic callback.
    """
    try:
        sensor_name = current_sensor_state["name"]

        # Fetch incremental data (only new records since last timestamp)
        fetch_incremental_data(sensor_name)

        # Update view with latest data
        update_view()

    except Exception as e:
        logger.error(f"Error in periodic update: {e}")


# Initial update
update_data()

# Schedule periodic updates
curdoc().add_periodic_callback(update_data, UPDATE_INTERVAL)

# Layout
sensor_selection_row = row(sensor_selector, btn_download_csv, sizing_mode="scale_width")
time_button_row = row(btn_10min, btn_3h, btn_12h, btn_24h, btn_1week, btn_all,
                    sizing_mode="scale_width")#btn_1h,
# custom_time_row = row(
#                     sizing_mode="scale_width")
window_control_row = row(
    window_days,
    window_hours,
    window_minutes,
    window_seconds,
    column(Div(text="&nbsp;", height=10), auto_scroll_toggle),  # Empty title space for alignment
    sizing_mode="scale_width"
)
display_range_row = row(
    ma_spinner,
    temp_window_spinner,
    #temp_window_range_display,
    hum_window_spinner,
    #hum_window_range_display,
    data_display_select,  # Dropdown to select data display mode
    sizing_mode="scale_width"
)

layout = column(
    Div(text="<h1> Multi-sensor temperature monitor</h1>", sizing_mode="stretch_width", height=60),
    Div(text="<h3>Sensor Selection</h3>", sizing_mode="stretch_width", height=25),
    sensor_selection_row,
    Div(text="<br>", sizing_mode="stretch_width", height=10),
    current_readings,
    Div(text="<h4>Quick set time window:</h4>", sizing_mode="stretch_width", height=25),
    time_button_row,
    # Div(text="<h4>Custom Window:</h4>", sizing_mode="stretch_width", height=25),
    # custom_time_row,
    Div(text="<h4>Window control:</h4>", sizing_mode="stretch_width", height=25),
    window_control_row,
    Div(text="<h4>Display control:</h4>", sizing_mode="stretch_width", height=25),
    display_range_row,
    Div(text="<br>", sizing_mode="stretch_width", height=10),
    temp_plot,
    humidity_plot,
    sizing_mode="scale_width"
)

curdoc().add_root(layout)
curdoc().title = "Multi-Sensor Temperature Monitor"
