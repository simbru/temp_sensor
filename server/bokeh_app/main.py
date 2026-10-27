"""
Multi-sensor Bokeh Server dashboard for lab temperature monitoring.
Run with: bokeh serve server/bokeh_app --port 8000
"""
import logging
import math
import pathlib
import sys
import time
from functools import partial

import numpy as np
import pandas as pd

# Add project root to path so 'server.*' imports work
_project_root = pathlib.Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from bokeh.plotting import figure, curdoc
from bokeh.events import ConnectionLost
from bokeh.models import ColumnDataSource, Button, Spinner, Range1d, Toggle, CustomJS, Select
from bokeh.layouts import column, row
from bokeh.models.widgets import Div

from server.lttb import lttb_downsample
from server.bokeh_app._startup import (
    get_aggregator, sensor_configs, poll_interval, dashboard_update_ms, max_plot_points,
    display_points, load_executor,
)

logger = logging.getLogger(__name__)

# Get the singleton aggregator instance (already created by app_hooks.on_server_loaded)
aggregator = get_aggregator()

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
CUSTOM_WINDOW_DEBOUNCE_MS = 400  # Wait for spinner clicks to settle before reloading
ZOOM_DETAIL_DEBOUNCE_MS = 300  # Wait for zoom/pan to settle before re-downsampling the view
ZOOM_DETAIL_MARGIN = 0.5  # Also prepare half a view width either side, so short pans stay filled

UPDATE_INTERVAL = dashboard_update_ms
SAMPLE_INTERVAL_SECONDS = poll_interval  # Approximate
SAMPLE_RATE_TEXT = f"~{poll_interval}s/sample"

def _pick_initial_sensor():
    """Pick the sensor with the most recent data as the default selection.
    Falls back to the first configured sensor if no metadata exists (fresh DB)."""
    if not sensor_configs:
        return "No sensors"
    best_sensor = sensor_configs[0]["name"]
    best_time = 0
    for cfg in sensor_configs:
        name = cfg["name"]
        try:
            meta = aggregator.get_sensor_metadata(name)
            if meta and meta.get("last_update"):
                from datetime import datetime as _dt
                last_update = _dt.fromisoformat(meta["last_update"])
                ts = last_update.timestamp()
                if ts > best_time and meta.get("status") != "error":
                    best_time = ts
                    best_sensor = name
        except Exception:
            logger.debug("Failed to read metadata for sensor '%s' when selecting default sensor.", name, exc_info=True)
    logger.info(f"Default sensor: {best_sensor}")
    return best_sensor

# State for current sensor selection
current_sensor_state = {
    "name": _pick_initial_sensor(),
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
    "hardware_failure": {"icon": "⛓️‍💥", "label": "Sensor hardware failure"},
    "error": {"icon": "🔴", "label": "Offline or unreachable"},
    "unknown": {"icon": "🔴", "label": "Status unavailable"},
    "waiting": {"icon": "⏳", "label": "Waiting for first data"},
}

DEFAULT_STATUS_DISPLAY = STATUS_DISPLAY["unknown"]


def _resolve_status_display(metadata: dict | None) -> tuple[str, str]:
    """Return emoji and label for current sensor status."""
    status_value = (metadata or {}).get("status") or "unknown"
    status_key = status_value.lower() if isinstance(status_value, str) else "unknown"
    display = STATUS_DISPLAY.get(status_key, DEFAULT_STATUS_DISPLAY)
    # Only override with error if it's not already a specific error type (like hardware_failure)
    if metadata and metadata.get("last_error") and status_key not in ["hardware_failure"]:
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
    Supports extended sensor data (pressure, light, noise) when available.

    Pipeline: raw → gap markers → moving averages → LTTB downsample (if needed)

    Args:
        raw_data: Raw sensor data dict (may include pressure, light, noise)
        window_size: Moving average window size
        max_points: Maximum points to send to browser (enforced via LTTB)
        connect_points: If True, only show breaks for large gaps (3+ missed readings)
                       If False, show breaks for all gaps (1.5+ missed readings)
        log_interval_s: Sensor's log interval in seconds (for smart gap detection)
    """
    if not raw_data or len(raw_data.get("time", [])) == 0:
        return {"time": [], "temperature": [], "humidity": [], "temp_ma": [], "hum_ma": []}

    time_vals = np.asarray(raw_data.get("time", []))
    temps = np.asarray(raw_data.get("temperature", []), dtype=float)
    hums = np.asarray(raw_data.get("humidity", []), dtype=float)

    # Check for extended sensor data
    has_pressure = "pressure" in raw_data and len(raw_data["pressure"]) > 0
    has_light = "light" in raw_data and len(raw_data["light"]) > 0
    has_noise = "noise" in raw_data and len(raw_data["noise"]) > 0

    pressures = np.asarray(raw_data.get("pressure", []), dtype=float) if has_pressure else None
    lights = np.asarray(raw_data.get("light", []), dtype=float) if has_light else None
    noises = np.asarray(raw_data.get("noise", []), dtype=float) if has_noise else None

    if len(temps) == 0:
        result = {"time": time_vals, "temperature": temps, "humidity": hums,
                "temp_ma": temps, "hum_ma": hums}
        if has_pressure:
            result["pressure"] = pressures
            result["pressure_ma"] = pressures
        if has_light:
            result["light"] = lights
            result["light_ma"] = lights
        if has_noise:
            result["noise"] = noises
            result["noise_ma"] = noises
        return result

    # Always insert NaN markers for large gaps (sensor offline), but threshold depends on connect_points
    # - connect_points=True: Only show breaks for 3+ missed readings (large outages)
    # - connect_points=False: Show breaks for 1.5+ missed readings (more sensitive)
    if log_interval_s is not None and log_interval_s > 0:
        # Smart thresholding based on sensor's actual log interval
        if connect_points:
            gap_threshold = log_interval_s * 3.0  # 3x log interval = 3 missed readings
        else:
            gap_threshold = log_interval_s * 1.5  # 1.5x log interval = 1-2 missed readings
    else:
        # Fallback for unknown log interval
        gap_threshold = 180 if connect_points else 60

    # Build list of all arrays to process together (ensures same gap markers for all)
    arrays_to_process = [temps, hums]
    if has_pressure and pressures is not None:
        # Ensure pressure array matches length of temps/hums
        if len(pressures) == len(temps):
            arrays_to_process.append(pressures)
        else:
            has_pressure = False
    if has_light and lights is not None:
        if len(lights) == len(temps):
            arrays_to_process.append(lights)
        else:
            has_light = False
    if has_noise and noises is not None:
        if len(noises) == len(temps):
            arrays_to_process.append(noises)
        else:
            has_noise = False

    # Insert gap markers for ALL sensor arrays in a single pass
    # This ensures all arrays get the same gap markers at the same positions
    result_arrays, marker_mask = _insert_gap_markers_multi(time_vals, arrays_to_process,
                                                            gap_threshold_s=gap_threshold)

    # Unpack results
    time_vals = result_arrays[0]
    temps = result_arrays[1]
    hums = result_arrays[2]
    array_idx = 3
    if has_pressure:
        pressures = result_arrays[array_idx]
        array_idx += 1
    if has_light:
        lights = result_arrays[array_idx]
        array_idx += 1
    if has_noise:
        noises = result_arrays[array_idx]
        array_idx += 1

    window = max(int(window_size), 1)

    # Moving averages restart after each gap marker so they never bridge an outage
    temp_ma = _segment_rolling_mean(temps, marker_mask, window)
    hum_ma = _segment_rolling_mean(hums, marker_mask, window)

    result = {
        "time": time_vals,
        "temperature": temps,
        "humidity": hums,
        "temp_ma": temp_ma,
        "hum_ma": hum_ma,
    }

    # ALWAYS include extended sensor columns for ColumnDataSource consistency
    # Use actual data if available, otherwise empty arrays of matching length
    n_points = len(time_vals)
    
    if has_pressure and pressures is not None and len(pressures) == n_points:
        result["pressure"] = pressures
        result["pressure_ma"] = _segment_rolling_mean(pressures, marker_mask, window)
    else:
        result["pressure"] = np.full(n_points, np.nan)
        result["pressure_ma"] = np.full(n_points, np.nan)

    if has_light and lights is not None and len(lights) == n_points:
        result["light"] = lights
        result["light_ma"] = _segment_rolling_mean(lights, marker_mask, window)
    else:
        result["light"] = np.full(n_points, np.nan)
        result["light_ma"] = np.full(n_points, np.nan)

    if has_noise and noises is not None and len(noises) == n_points:
        result["noise"] = noises
        result["noise_ma"] = _segment_rolling_mean(noises, marker_mask, window)
    else:
        result["noise"] = np.full(n_points, np.nan)
        result["noise_ma"] = np.full(n_points, np.nan)

    # LTTB downsampling: cap points sent to browser while preserving visual shape
    if max_points is not None and max_points > 0 and len(result["time"]) > max_points:
        pre_lttb_count = len(result["time"])
        t0 = time.perf_counter()
        data_arrays = {k: v for k, v in result.items() if k != "time"}
        result["time"], data_arrays = lttb_downsample(result["time"], data_arrays, max_points)
        result.update(data_arrays)
        elapsed = time.perf_counter() - t0
        logger.info(f"LTTB downsampling: {pre_lttb_count:,} → {len(result['time']):,} points in {elapsed:.3f}s")

    return _to_wire_dtypes(result)


def _to_wire_dtypes(columns):
    """float64 time (ms epoch needs the precision), float32 for everything else (halves payload)."""
    return {
        key: np.asarray(values, dtype=np.float64 if key == "time" else np.float32)
        for key, values in columns.items()
    }


GAP_INTERVAL_MULTIPLE = 3.0  # A gap is a step > 3x the local sampling interval
GAP_MEDIAN_WINDOW = 51  # Neighbouring steps used to estimate the local sampling interval


def _insert_gap_markers_multi(time_vals, data_arrays, gap_threshold_s=60):
    """
    Insert one NaN marker into every time gap, for multiple aligned arrays at once.

    The expected sampling interval is estimated locally (rolling median of the
    neighbouring steps), so data mixing resolutions - raw readings, 1-min and
    15-min retention tiers - only breaks at real outages, not at every coarse bucket.

    Args:
        time_vals: Millisecond timestamps (float)
        data_arrays: List of data arrays to process (all must have same length as time_vals)
        gap_threshold_s: Minimum step (seconds) that can count as a gap

    Returns:
        (arrays, marker_mask): arrays is [time_vals, array1, array2, ...] with NaN
        inserted at gaps; marker_mask is True at the inserted positions.
    """
    time_vals = np.asarray(time_vals, dtype=float)
    if len(time_vals) < 2:
        return [time_vals] + list(data_arrays), np.zeros(len(time_vals), dtype=bool)

    steps_s = np.diff(time_vals) / 1000.0
    local_interval_s = (
        pd.Series(steps_s)
        .rolling(GAP_MEDIAN_WINDOW, center=True, min_periods=1)
        .median()
        .to_numpy()
    )
    is_gap = steps_s > np.maximum(local_interval_s * GAP_INTERVAL_MULTIPLE, gap_threshold_s)
    gap_indices = np.flatnonzero(is_gap) + 1  # position of the first point after each gap

    if len(gap_indices) == 0:
        return [time_vals] + list(data_arrays), np.zeros(len(time_vals), dtype=bool)

    marker_times = (time_vals[gap_indices - 1] + time_vals[gap_indices]) / 2.0
    result = [np.insert(time_vals, gap_indices, marker_times)]
    result += [np.insert(np.asarray(arr, dtype=float), gap_indices, np.nan) for arr in data_arrays]

    # np.insert places the k-th marker at gap_indices[k] + k in the output
    marker_mask = np.zeros(len(result[0]), dtype=bool)
    marker_mask[gap_indices + np.arange(len(gap_indices))] = True
    return result, marker_mask


def _segment_rolling_mean(values, marker_mask, window):
    """
    Trailing moving average (NaN-skipping, min_periods=1) that restarts after
    every gap marker, so the average never bridges an outage. NaN at markers.
    """
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n == 0:
        return values

    valid = ~np.isnan(values)
    csum = np.concatenate(([0.0], np.cumsum(np.where(valid, values, 0.0))))
    ccount = np.concatenate(([0], np.cumsum(valid)))

    idx = np.arange(n)
    # First index of the segment each point belongs to (point after the latest marker)
    segment_start = np.maximum.accumulate(np.where(marker_mask, idx + 1, 0))
    lo = np.maximum(idx - window + 1, segment_start)
    hi = idx + 1

    counts = ccount[hi] - ccount[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(counts > 0, (csum[hi] - csum[lo]) / counts, np.nan)


# Fetch initial data for first sensor
initial_data_raw = aggregator.get_sensor_data(current_sensor_state["name"], limit=1000)
initial_metadata = aggregator.get_sensor_metadata(current_sensor_state["name"])
initial_log_interval = (initial_metadata or {}).get('log_interval_s')
initial_data = prepare_source_data(initial_data_raw, DEFAULT_MA_WINDOW, display_points,
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
    "log_interval_s": None,
    # Info about data availability outside current window
    "total_records": 0,
    "newest_timestamp_ms": None,  # Most recent data timestamp in entire DB
    # Earliest timestamp raw_data is guaranteed to cover (None = from the beginning)
    "fetch_type": None,
    "fetched_start_ms": None,
    # (start_ms, end_ms) the plot is zoomed to, prepared at full detail; None = whole dataset
    "view_range": None,
    "prep_view_range": None,
}

# Background loads: each request bumps the generation; a finished load is only
# applied if no newer request has been made since, so rapid clicks never queue up.
load_state = {
    "generation": 0,
    "busy": False,
}

# Session document, captured at session creation for use from worker threads
session_doc = curdoc()

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

# Create pressure plot (shown only if sensor provides pressure data)
pressure_plot = figure(
    title=f"Pressure - {current_sensor_state['name']}",
    x_axis_label="Time",
    y_axis_label="Pressure (hPa)",
    x_axis_type="datetime",
    height=200,
    sizing_mode="scale_width",
    max_width=1000,
    tools="pan,wheel_zoom,box_zoom,reset,save",
    active_drag=None,
    active_scroll=None,
    x_range=temp_plot.x_range,
    output_backend="webgl",
    visible=False  # Hidden by default, shown when pressure data available
)
pressure_raw_renderer = pressure_plot.line('time', 'pressure', source=source, line_width=2,
                                          color='#90EE90', alpha=0.6)
pressure_ma_renderer = pressure_plot.line('time', 'pressure_ma', source=source, line_width=3,
                                         color='green', alpha=0.9)
pressure_plot.xgrid.grid_line_color = "gray"
pressure_plot.xgrid.grid_line_alpha = 0.3
pressure_plot.xgrid.minor_grid_line_alpha = 0.15
pressure_plot.xaxis.ticker.num_minor_ticks = 5

# Create light plot (shown only if sensor provides light data)
light_plot = figure(
    title=f"Light - {current_sensor_state['name']}",
    x_axis_label="Time",
    y_axis_label="Light (lux)",
    x_axis_type="datetime",
    height=200,
    sizing_mode="scale_width",
    max_width=1000,
    tools="pan,wheel_zoom,box_zoom,reset,save",
    active_drag=None,
    active_scroll=None,
    x_range=temp_plot.x_range,
    output_backend="webgl",
    visible=False  # Hidden by default, shown when light data available
)
light_raw_renderer = light_plot.line('time', 'light', source=source, line_width=2,
                                    color='#FFD700', alpha=0.6)
light_ma_renderer = light_plot.line('time', 'light_ma', source=source, line_width=3,
                                   color='orange', alpha=0.9)
light_plot.xgrid.grid_line_color = "gray"
light_plot.xgrid.grid_line_alpha = 0.3
light_plot.xgrid.minor_grid_line_alpha = 0.15
light_plot.xaxis.ticker.num_minor_ticks = 5

# Create noise plot (shown only if sensor provides noise data)
noise_plot = figure(
    title=f"Noise - {current_sensor_state['name']}",
    x_axis_label="Time",
    y_axis_label="Noise (dBA)",
    x_axis_type="datetime",
    height=200,
    sizing_mode="scale_width",
    max_width=1000,
    tools="pan,wheel_zoom,box_zoom,reset,save",
    active_drag=None,
    active_scroll=None,
    x_range=temp_plot.x_range,
    output_backend="webgl",
    visible=False  # Hidden by default, shown when noise data available
)
noise_raw_renderer = noise_plot.line('time', 'noise', source=source, line_width=2,
                                    color='#DDA0DD', alpha=0.6)
noise_ma_renderer = noise_plot.line('time', 'noise_ma', source=source, line_width=3,
                                   color='purple', alpha=0.9)
noise_plot.xgrid.grid_line_color = "gray"
noise_plot.xgrid.grid_line_alpha = 0.3
noise_plot.xgrid.minor_grid_line_alpha = 0.15
noise_plot.xaxis.ticker.num_minor_ticks = 5


# Day boundary lines: one vspan renderer per plot, all fed by a single shared source.
# Updating the source replaces the lines in place, so they can never stack up.
day_boundary_source = ColumnDataSource(data={"x": []})
day_boundary_renderers = [
    plot.vspan(x="x", source=day_boundary_source, line_color="navy", line_width=2, line_alpha=0.4)
    for plot in (temp_plot, humidity_plot, pressure_plot, light_plot, noise_plot)
]


def _update_day_boundaries(data_times):
    """Place vertical lines at each midnight within the data range."""
    if len(data_times) == 0:
        return

    day_ms = 86_400_000
    first_midnight = (int(data_times[0]) // day_ms + 1) * day_ms
    midnight_times = list(range(first_midnight, int(data_times[-1]) + 1, day_ms))

    if day_boundary_source.data["x"] != midnight_times:
        day_boundary_source.data = {"x": midnight_times}
        # Over long ranges the lines sit a few pixels apart; keep them faint so the data stays readable
        many = len(midnight_times) > 45
        for renderer in day_boundary_renderers:
            renderer.glyph.line_width = 1 if many else 2
            renderer.glyph.line_alpha = 0.15 if many else 0.4


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
    _schedule_zoom_detail()


zoom_state = {"pending_timeout": None}


def _schedule_zoom_detail():
    """Debounced: re-downsample the visible range once zooming/panning stops."""
    pending = zoom_state["pending_timeout"]
    if pending is not None:
        try:
            session_doc.remove_timeout_callback(pending)
        except ValueError:
            pass  # Already fired
    zoom_state["pending_timeout"] = session_doc.add_timeout_callback(
        _apply_zoom_detail, ZOOM_DETAIL_DEBOUNCE_MS
    )


def _apply_zoom_detail():
    zoom_state["pending_timeout"] = None
    raw_data = data_cache["raw_data"]
    start, end = temp_plot.x_range.start, temp_plot.x_range.end
    if current_window["auto_range"] or raw_data is None or start is None or end is None or end <= start:
        return

    previous = data_cache["view_range"]
    span = end - start
    if (previous is not None and previous[0] <= start and previous[1] >= end
            and previous[1] - previous[0] <= span * (1 + 2 * ZOOM_DETAIL_MARGIN) * 1.5):
        return  # Small pan within the already-detailed range

    times = raw_data["time"]
    view_range = (start - span * ZOOM_DETAIL_MARGIN, end + span * ZOOM_DETAIL_MARGIN)
    if view_range[0] <= times[0] and view_range[1] >= times[-1]:
        view_range = None  # Zoomed out to everything
    if view_range == previous:
        return

    data_cache["view_range"] = view_range
    _request_load(None, refetch=False)


temp_plot.x_range.on_change('start', range_change_callback)
temp_plot.x_range.on_change('end', range_change_callback)

# UI widgets
_initial_loading_msg = "Loading..."
if initial_metadata and initial_metadata.get("status") == "syncing":
    _initial_loading_msg = f"Syncing data for {current_sensor_state['name']} — this may take a moment…"
current_readings = Div(text=f"<h3>{_initial_loading_msg}</h3>", sizing_mode="stretch_width")

# Sensor selection dropdown
sensor_names = [cfg["name"] for cfg in sensor_configs]

# Default to first sensor
initial_sensor = current_sensor_state["name"] if current_sensor_state["name"] else (sensor_names[0] if sensor_names else "No sensors")

sensor_selector = Select(
    title="Select Sensor:",
    value=initial_sensor,
    options=sensor_names,
    width=300
)

# JavaScript callback to save sensor selection to localStorage when changed
save_sensor_js = CustomJS(args=dict(sensor_selector=sensor_selector), code="""
    // Save the selected sensor to localStorage whenever it changes
    localStorage.setItem('selected_sensor', sensor_selector.value);
""")

# JavaScript callback to restore sensor selection from localStorage on page load
restore_sensor_js = CustomJS(args=dict(sensor_selector=sensor_selector, sensor_names=sensor_names), code="""
    // Try to restore the last selected sensor from localStorage
    const stored_sensor = localStorage.getItem('selected_sensor');
    if (stored_sensor && sensor_names.includes(stored_sensor)) {
        sensor_selector.value = stored_sensor;
    }
""")

# Time window buttons
btn_10min = Button(label="10 min", button_type="default", width=100)
btn_1h = Button(label="1 hour", button_type="default", width=100)
btn_3h = Button(label="3 hours", button_type="default", width=100)
btn_12h = Button(label="12 hours", button_type="default", width=100)
btn_24h = Button(label="24 hours", button_type="success", width=100)
btn_1week = Button(label="1 week", button_type="default", width=100)
btn_30d = Button(label="30 days", button_type="default", width=100)
btn_60d = Button(label="60 days", button_type="default", width=100)
btn_180d = Button(label="180 days", button_type="default", width=100)
btn_all = Button(label="All data", button_type="default", width=100)

time_buttons = [btn_10min, btn_1h, btn_3h, btn_12h, btn_24h, btn_1week, btn_30d, btn_60d, btn_180d, btn_all]

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

# Day line toggle - handled entirely in the browser, no server round-trip
day_lines_toggle = Toggle(label="Day lines: ON", button_type="success", active=True, width=120)
day_lines_toggle.js_on_change("active", CustomJS(
    args=dict(toggle=day_lines_toggle, renderers=day_boundary_renderers),
    code="""
        for (const r of renderers) { r.visible = toggle.active; }
        toggle.label = toggle.active ? "Day lines: ON" : "Day lines: OFF";
        toggle.button_type = toggle.active ? "success" : "default";
    """,
))

# CSV download button
btn_download_csv = Button(label="Download CSV", button_type="success", width=150, height=40)

# Window width spinners
temp_window_spinner = Spinner(title="Temperature range (°C)", low=MIN_TEMP_WINDOW,
                            high=None, step=0.5,
                            value=temp_window_state["value"], width=120)
hum_window_spinner = Spinner(title="Humidity range (%)", low=MIN_HUM_WINDOW,
                            high=MAX_HUM_WINDOW, step=1,
                            value=hum_window_state["value"], width=120)

# Auto-fit toggles for Y-axis
temp_auto_toggle = Toggle(label="Auto-fit", button_type="success", active=True, width=80)
hum_auto_toggle = Toggle(label="Auto-fit", button_type="success", active=True, width=80)

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


def _cache_covers_window(minutes):
    """True if the cached raw data already spans the requested window."""
    if data_cache["raw_data"] is None or data_cache["sensor_name"] != current_sensor_state["name"]:
        return False
    fetched_start_ms = data_cache.get("fetched_start_ms")
    if fetched_start_ms is None:
        return data_cache.get("fetch_type") == "all"
    if minutes is None:
        return False
    window_start_ms = time.time() * 1000 - minutes * 60 * 1000
    return window_start_ms >= fetched_start_ms


def update_time_window(minutes, sync_inputs=True):
    """
    Update the time window.
    Loads data in the background if the window reaches beyond the cached data.
    """
    logger.info(f"update_time_window called with minutes={minutes}")

    current_window["minutes"] = minutes
    current_window["force_update"] = True
    current_window["auto_range"] = True
    data_cache["view_range"] = None

    if sync_inputs:
        set_time_inputs_from_minutes(minutes)

    if not _cache_covers_window(minutes):
        _request_load(_loading_message(current_sensor_state["name"], f"Loading {format_minutes(minutes)}…"))
        return
    if not _prepared_is_current(data_cache["raw_data"], max(int(ma_spinner.value), 1)):
        _request_load(f"Loading {format_minutes(minutes)}…", refetch=False)  # e.g. leaving a zoomed view
        return

    update_view()
    if not load_state["busy"]:
        _clear_busy()  # The browser showed the indicator on click


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
    """Debounced: apply the custom window once the spinners stop changing."""
    if time_input_state["updating"]:
        return

    _set_busy("Updating time window…")
    pending = time_input_state.get("pending_timeout")
    if pending is not None:
        try:
            session_doc.remove_timeout_callback(pending)
        except ValueError:
            pass  # Already fired
    time_input_state["pending_timeout"] = session_doc.add_timeout_callback(
        _apply_custom_time_window, CUSTOM_WINDOW_DEBOUNCE_MS
    )


def _apply_custom_time_window():
    time_input_state["pending_timeout"] = None
    minutes = get_minutes_from_inputs()
    if minutes is None:
        if not load_state["busy"]:
            _clear_busy()
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
    # User manually adjusted spinner - disable auto mode
    temp_window_state["auto"] = False
    temp_auto_toggle.active = False  # Sync toggle state
    temp_window_state["manual_center"] = None
    temp_window_state["pending"] = 2
    window_control_state["temp_syncing"] = True
    try:
        temp_window_spinner.value = value
    finally:
        window_control_state["temp_syncing"] = False
    _set_temp_y_range(latest_values["temp"], value, record_auto=False)
    # _update_window_displays()


def _on_hum_window_change(attr, old, new):
    if window_control_state["hum_syncing"]:
        return
    value = _coerce_window(new, hum_window_state["value"], MIN_HUM_WINDOW, MAX_HUM_WINDOW)
    hum_window_state["value"] = value
    # User manually adjusted spinner - disable auto mode
    hum_window_state["auto"] = False
    hum_auto_toggle.active = False  # Sync toggle state
    hum_window_state["manual_center"] = None
    hum_window_state["pending"] = 2
    window_control_state["hum_syncing"] = True
    try:
        hum_window_spinner.value = value
    finally:
        window_control_state["hum_syncing"] = False
    _set_hum_y_range(latest_values["hum"], value, record_auto=False)
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
btn_30d.on_click(lambda: (set_button_active(btn_30d), update_time_window(30 * 1440)))
btn_60d.on_click(lambda: (set_button_active(btn_60d), update_time_window(60 * 1440)))
btn_180d.on_click(lambda: (set_button_active(btn_180d), update_time_window(180 * 1440)))
btn_all.on_click(lambda: (set_button_active(btn_all), update_time_window(None)))

def on_temp_auto_toggle(attr, old, new):
    """Handle temperature auto-fit toggle."""
    temp_window_state["auto"] = bool(new)
    if new:
        # Re-enable auto mode - trigger immediate update
        update_view()

def on_hum_auto_toggle(attr, old, new):
    """Handle humidity auto-fit toggle."""
    hum_window_state["auto"] = bool(new)
    if new:
        # Re-enable auto mode - trigger immediate update
        update_view()

temp_window_spinner.on_change("value", _on_temp_window_change)
hum_window_spinner.on_change("value", _on_hum_window_change)
temp_auto_toggle.on_change("active", on_temp_auto_toggle)
hum_auto_toggle.on_change("active", on_hum_auto_toggle)

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
    _request_load("Recalculating averages…", refetch=False)


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
        if data_cache["view_range"] is not None:
            data_cache["view_range"] = None  # Leave the zoomed view: replot the whole window
            _request_load(None, refetch=False)
        else:
            update_view()  # Force immediate view update to current time


auto_scroll_toggle.on_change("active", on_auto_scroll_toggle)


def on_sensor_change(attr, old, new):
    """Handle sensor selection change."""
    current_sensor_state["name"] = new
    temp_plot.title.text = f"Temperature - {new}"
    humidity_plot.title.text = f"Humidity - {new}"
    pressure_plot.title.text = f"Pressure - {new}"
    light_plot.title.text = f"Light - {new}"
    noise_plot.title.text = f"Noise - {new}"
    current_window["force_update"] = True
    data_cache["view_range"] = None
    logger.info(f"Switching to sensor: {new}")
    _request_load(_loading_message(new, f"Loading {new}…"))


sensor_selector.on_change("value", on_sensor_change)
# Also save to localStorage on client-side when changed
sensor_selector.js_on_change("value", save_sensor_js)


def _query_window_data(sensor_name, window_minutes):
    """
    Query a sensor's data from the database with smart windowing.
    - If viewing specific time window: fetches that window + margin
    - If "All data": fetches everything

    Touches no Bokeh models or session state, so it is safe to run on a worker thread.
    Returns a dict for _install_fetched_data().
    """
    fetched_start_ms = None
    if window_minutes is None:
        # "All data" mode - fetch everything
        logger.info(f"Fetching ALL historical data for {sensor_name}")
        new_data = aggregator.get_sensor_data(sensor_name)
        fetch_type = "all"
    else:
        # Smart windowing - fetch only visible window + 2x margin
        margin_factor = 2.0  # Fetch 2x the visible window for smooth panning
        fetch_window_minutes = window_minutes * margin_factor

        logger.info(f"Fetching last {fetch_window_minutes:.0f} minutes of data for {sensor_name} (window: {window_minutes:.0f}m)")

        # INTEGER milliseconds for database query
        end_ms = int(time.time() * 1000)
        fetched_start_ms = end_ms - int(fetch_window_minutes * 60 * 1000)

        new_data = aggregator.get_sensor_data(sensor_name, start_time=fetched_start_ms, end_time=end_ms)
        fetch_type = "windowed"

        # Historical fallback: if time window returns nothing (sensor dead/stale),
        # fall back to showing the most recent data regardless of age
        if not new_data or len(new_data.get("time", [])) == 0:
            logger.info(f"No data in time window for {sensor_name}, falling back to latest historical records")
            new_data = aggregator.get_sensor_data(sensor_name, limit=1000)
            fetch_type = "historical_fallback"

    fetched = {
        "data": new_data,
        "fetch_type": fetch_type,
        "fetched_start_ms": fetched_start_ms,
        "log_interval_s": None,
        "total_records": None,
        "newest_timestamp_ms": None,
    }

    if not new_data or len(new_data.get("time", [])) == 0:
        logger.warning(f"No data available for sensor: {sensor_name}")
        # Check if there's data outside the current time window
        fetched["data"] = None
        fetched["total_records"] = aggregator.get_total_records(sensor_name)
        fetched["newest_timestamp_ms"] = aggregator.get_last_timestamp(sensor_name)
        return fetched

    logger.info(f"Fetched {len(new_data['time']):,} records ({fetch_type})")

    # Fetch metadata to get log_interval for smart gap detection
    metadata = aggregator.get_sensor_metadata(sensor_name)
    fetched["log_interval_s"] = (metadata or {}).get('log_interval_s')
    fetched["total_records"] = len(new_data["time"])
    fetched["newest_timestamp_ms"] = new_data["time"][-1]
    return fetched


def _install_fetched_data(sensor_name, fetched):
    """Store a _query_window_data() result in the session cache. Returns the raw data."""
    new_data = fetched["data"]
    data_cache["sensor_name"] = sensor_name
    data_cache["raw_data"] = new_data
    data_cache["fetch_type"] = fetched["fetch_type"]
    data_cache["fetched_start_ms"] = fetched["fetched_start_ms"]
    data_cache["total_records"] = fetched["total_records"]
    data_cache["newest_timestamp_ms"] = fetched["newest_timestamp_ms"]

    if new_data is not None:
        data_cache["last_timestamp"] = new_data["time"][-1]
        data_cache["last_fetch_time"] = time.time() * 1000
        data_cache["log_interval_s"] = fetched["log_interval_s"]  # Store for gap detection

    # Invalidate prepared data cache to force recalculation
    # This ensures moving averages are recalculated with the new data
    data_cache["prepared_data"] = None
    data_cache["prep_raw_hash"] = None
    return new_data


def fetch_initial_data(sensor_name):
    """
    Fetch data for the current window synchronously (startup and fallback paths).
    Interactive loads go through _request_load() instead.
    """
    try:
        fetched = _query_window_data(sensor_name, current_window.get("minutes"))
    except Exception as e:
        logger.error(f"Error fetching data for {sensor_name}: {e}")
        return None
    return _install_fetched_data(sensor_name, fetched)


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

        # Fetch only new records (start_time + 1 to exclude the already-cached point)
        new_records = aggregator.get_sensor_data(
            sensor_name,
            start_time=last_timestamp_ms + 1,
            end_time=now_ms
        )

        if not new_records or len(new_records.get("time", [])) == 0:
            logger.debug(f"No new data for {sensor_name}")
            data_cache["new_point_count"] = 0  # No change
            return data_cache["raw_data"]  # Return cached data

        # Append new records to cache
        new_count = len(new_records["time"])
        if new_count > 0:
            logger.debug(f"Fetched {new_count} new records for {sensor_name}")
            data_cache["new_point_count"] = new_count

            # Append new records using numpy concatenation (avoids copying entire dataset as Python lists)
            for key in ("time", "temperature", "humidity"):
                data_cache["raw_data"][key] = np.concatenate([
                    np.asarray(data_cache["raw_data"][key]),
                    np.asarray(new_records[key])
                ])

            # Handle extended sensor data if present
            for key in ("pressure", "light", "noise"):
                if key in new_records:
                    if key not in data_cache["raw_data"]:
                        data_cache["raw_data"][key] = np.array([])
                    data_cache["raw_data"][key] = np.concatenate([
                        np.asarray(data_cache["raw_data"][key]),
                        np.asarray(new_records[key])
                    ])

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
                    # Trim old data (including extended fields if present)
                    data_cache["raw_data"]["time"] = data_cache["raw_data"]["time"][first_keep_idx:]
                    data_cache["raw_data"]["temperature"] = data_cache["raw_data"]["temperature"][first_keep_idx:]
                    data_cache["raw_data"]["humidity"] = data_cache["raw_data"]["humidity"][first_keep_idx:]
                    if "pressure" in data_cache["raw_data"]:
                        data_cache["raw_data"]["pressure"] = data_cache["raw_data"]["pressure"][first_keep_idx:]
                    if "light" in data_cache["raw_data"]:
                        data_cache["raw_data"]["light"] = data_cache["raw_data"]["light"][first_keep_idx:]
                    if "noise" in data_cache["raw_data"]:
                        data_cache["raw_data"]["noise"] = data_cache["raw_data"]["noise"][first_keep_idx:]
                    data_cache["fetched_start_ms"] = max(data_cache.get("fetched_start_ms") or cutoff_time,
                                                         cutoff_time)
                    logger.debug(f"Trimmed {first_keep_idx} old points from cache")

            # Update last timestamp
            data_cache["last_timestamp"] = new_records["time"][-1]
            data_cache["last_fetch_time"] = time.time() * 1000

        return data_cache["raw_data"]
    except Exception as e:
        logger.error(f"Error fetching incremental data: {e}")
        return data_cache["raw_data"]  # Fall back to cached data


def _prepare_stream_update(raw_data, window_size, new_count):
    """
    Prepare only the NEW points for source.stream(), including gap markers
    at the junction and correct moving averages using trailing context.

    Returns a dict suitable for source.stream(), or None if fallback needed.
    """
    try:
        cached = data_cache["prepared_data"]
        if cached is None or len(cached.get("time", [])) == 0:
            return None

        # Get the new raw points (tail of raw_data)
        raw_times = raw_data["time"]
        raw_temps = raw_data["temperature"]
        raw_hums = raw_data["humidity"]
        n_raw = len(raw_times)
        new_start = n_raw - new_count

        new_times = np.asarray(raw_times[new_start:], dtype=float)
        new_temps = np.asarray(raw_temps[new_start:], dtype=float)
        new_hums = np.asarray(raw_hums[new_start:], dtype=float)

        # Check for gap at junction (between last cached point and first new point)
        last_cached_time = cached["time"][-1]
        # Find last non-NaN cached time for gap detection
        cached_times_arr = np.asarray(cached["time"])
        valid_mask = ~np.isnan(cached_times_arr)
        if not np.any(valid_mask):
            return None
        last_valid_time = cached_times_arr[valid_mask][-1]

        gap_threshold_s = 60  # Default
        log_interval_s = data_cache.get("log_interval_s")
        if log_interval_s and log_interval_s > 0:
            gap_threshold_s = log_interval_s * 1.5

        # Check if there's a gap between last cached data and new data
        time_diff_s = (new_times[0] - last_valid_time) / 1000.0
        gap_points = []
        if time_diff_s > gap_threshold_s:
            # Insert a single NaN marker at the gap
            gap_time = (last_valid_time + new_times[0]) / 2.0
            gap_points = [gap_time]

        # Build the stream arrays
        n_gap = len(gap_points)
        n_total = n_gap + len(new_times)

        stream_times = np.empty(n_total, dtype=float)
        stream_temps = np.empty(n_total, dtype=float)
        stream_hums = np.empty(n_total, dtype=float)

        if n_gap > 0:
            stream_times[0] = gap_points[0]
            stream_temps[0] = np.nan
            stream_hums[0] = np.nan

        stream_times[n_gap:] = new_times
        stream_temps[n_gap:] = new_temps
        stream_hums[n_gap:] = new_hums

        # Compute moving averages for new points using trailing context from cache
        window = max(int(window_size), 1)

        # MA context: the last (window-1) cached points. Gap markers (NaN in the cached
        # temperature, as LTTB keeps them) and a junction gap restart the average.
        n_context = min(window - 1, len(cached["time"]))
        context_markers = np.isnan(np.asarray(cached["temperature"][len(cached["time"]) - n_context:], dtype=float))
        marker_mask = np.zeros(n_context + n_total, dtype=bool)
        marker_mask[:n_context] = context_markers
        marker_mask[n_context:n_context + n_gap] = True

        def _stream_ma(column, stream_vals):
            context = np.asarray(cached[column][len(cached["time"]) - n_context:], dtype=float)
            if len(context) != n_context:
                context = np.full(n_context, np.nan)
            full = np.concatenate([context, stream_vals])
            return _segment_rolling_mean(full, marker_mask, window)[n_context:]

        result = {
            "time": stream_times,
            "temperature": stream_temps,
            "humidity": stream_hums,
            "temp_ma": _stream_ma("temperature", stream_temps),
            "hum_ma": _stream_ma("humidity", stream_hums),
        }

        # Handle extended sensor data
        for field in ("pressure", "light", "noise"):
            if field in raw_data and len(raw_data[field]) >= n_raw:
                new_vals = np.asarray(raw_data[field][new_start:], dtype=float)
                if len(new_vals) != new_count:
                    # Length mismatch — fill with NaN instead of risking array misalignment
                    result[field] = np.full(n_total, np.nan)
                    result[f"{field}_ma"] = np.full(n_total, np.nan)
                    continue
                stream_vals = np.empty(n_total, dtype=float)
                if n_gap > 0:
                    stream_vals[0] = np.nan
                stream_vals[n_gap:] = new_vals

                result[field] = stream_vals
                result[f"{field}_ma"] = (_stream_ma(field, stream_vals) if field in cached
                                         else np.full(n_total, np.nan))
            else:
                result[field] = np.full(n_total, np.nan)
                result[f"{field}_ma"] = np.full(n_total, np.nan)

        return _to_wire_dtypes(result)

    except Exception as e:
        logger.warning(f"Stream update preparation failed, falling back to full update: {e}")
        return None


def _extend_prepared_cache(stream_dict):
    """
    Extend the cached prepared_data with streamed points so that
    y-axis calculations and status display use up-to-date data.
    """
    cached = data_cache.get("prepared_data")
    if cached is None:
        return

    # Verify stream_dict has consistent lengths before extending
    stream_len = len(stream_dict.get("time", []))
    if stream_len == 0:
        return

    for key in list(cached.keys()):
        if key in stream_dict:
            new_arr = np.asarray(stream_dict[key])
            if len(new_arr) != stream_len:
                # Length mismatch in stream — fall back to full refresh next cycle
                logger.warning(f"Stream length mismatch for '{key}': {len(new_arr)} vs {stream_len}")
                data_cache["prepared_data"] = None
                data_cache["prep_raw_hash"] = None
                return
            cached_arr = np.asarray(cached[key])
            cached[key] = np.concatenate([cached_arr, new_arr])

    # Trim to max_plot_points to keep cache bounded (trim all arrays uniformly)
    n = len(cached.get("time", []))
    if max_plot_points and n > max_plot_points:
        trim = n - max_plot_points
        for key in cached:
            arr = cached[key]
            if hasattr(arr, '__len__') and len(arr) == n:
                cached[key] = arr[trim:]

    # The prepared cache now reflects the current raw data (compared against _raw_hash in update_view)
    data_cache["prep_raw_hash"] = _raw_hash(data_cache["raw_data"])


def _raw_hash(raw_data):
    """Cheap identity of the raw data: length + first/last timestamps (avoids hashing large arrays)."""
    if not raw_data or raw_data.get("time") is None or len(raw_data["time"]) == 0:
        return None
    times = raw_data["time"]
    return (len(times), times[0], times[-1])


def _slice_to_view(raw_data, view_range):
    """Raw data restricted to view_range (start_ms, end_ms); everything if view_range is None."""
    if view_range is None or not raw_data:
        return raw_data
    times = np.asarray(raw_data["time"])
    lo, hi = np.searchsorted(times, view_range[0]), np.searchsorted(times, view_range[1], side="right")
    return {key: np.asarray(values)[lo:hi] if len(values) == len(times) else values
            for key, values in raw_data.items()}


def _prepared_is_current(raw_data, window):
    """True if the cached prepared data matches the raw data, MA window and zoomed view."""
    return (
        data_cache["prepared_data"] is not None and
        data_cache["prep_ma_window"] == window and
        data_cache["prep_max_points"] == display_points and
        data_cache["prep_view_range"] == data_cache["view_range"] and
        data_cache["prep_raw_hash"] == _raw_hash(raw_data)
    )


def _store_prepared(prepared, raw_data, window, view_range):
    data_cache["prepared_data"] = prepared
    data_cache["prep_ma_window"] = window
    data_cache["prep_max_points"] = display_points
    data_cache["prep_view_range"] = view_range
    data_cache["prep_raw_hash"] = _raw_hash(raw_data)


def update_view():
    """
    Update plot view and UI elements using cached data.
    Does NOT fetch from database - instant response.
    """
    try:
        sensor_name = current_sensor_state["name"]

        if data_cache["raw_data"] is None or data_cache["sensor_name"] != sensor_name:
            if load_state["busy"]:
                return  # A background load for this sensor will refresh the view when done
            logger.debug("No cached data available, triggering initial fetch")
            fetch_initial_data(sensor_name)
            if data_cache["raw_data"] is None:
                _update_status_display_no_data(sensor_name)
                return

        # Use cached data
        raw_data = data_cache["raw_data"]

        # Check if we need to recompute prepared data
        window = max(int(ma_spinner.value), 1)

        # Decide between streaming (fast incremental) vs full replacement
        new_point_count = data_cache.get("new_point_count", -1)

        # Streaming eligibility: small incremental update, same sensor, same settings
        can_stream = (
            new_point_count > 0 and
            new_point_count <= 100 and
            data_cache["prepared_data"] is not None and
            data_cache["prep_ma_window"] == window and
            data_cache["prep_max_points"] == display_points
        )

        if can_stream:
            # FAST PATH: Stream only new points instead of replacing entire dataset
            logger.debug(f"Streaming {new_point_count} new points")
            prepared = _prepare_stream_update(raw_data, window, new_point_count)
            if prepared is not None:
                source.stream(prepared, rollover=max_plot_points if max_plot_points else None)
                # Update the full prepared cache for y-axis and status calculations
                # (lightweight: just extend the cached arrays)
                _extend_prepared_cache(prepared)
                prepared = data_cache["prepared_data"]
            else:
                # Fallback to full replacement if stream prep fails
                can_stream = False

        if not can_stream:
            # FULL PATH: Recompute everything (sensor change, window change, etc.)
            if _prepared_is_current(raw_data, window):
                logger.debug("Using cached prepared data")
                prepared = data_cache["prepared_data"]
            else:
                logger.debug(f"Recomputing prepared data (MA window: {window}, display points: {display_points})")
                view_range = data_cache["view_range"]
                prepared = prepare_source_data(_slice_to_view(raw_data, view_range), window, display_points,
                                              connect_points=False,
                                              log_interval_s=data_cache.get("log_interval_s"))
                _store_prepared(prepared, raw_data, window, view_range)

            # Replace entire dataset
            source.data = prepared

        # Reset new_point_count after processing
        data_cache["new_point_count"] = 0

        # Show/hide extended sensor plots based on available data
        # (prepared always carries these columns - NaN-filled when the sensor lacks them)
        pressure_plot.visible = bool(np.isfinite(prepared.get("pressure", [])).any())
        light_plot.visible = bool(np.isfinite(prepared.get("light", [])).any())
        noise_plot.visible = bool(np.isfinite(prepared.get("noise", [])).any())

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

        # AUTO Y-AXIS RANGING: Fit to visible data instead of just latest value
        # This ensures extreme values (like temperature drops) are visible
        if temp_window_state["auto"] or hum_window_state["auto"]:
            # Calculate min/max of VISIBLE data (will be used after X-range is set)
            # We'll recalculate after setting X-range to get accurate visible bounds
            pass  # Moved to after X-range update below

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

            # AUTO Y-AXIS FITTING: After setting X-range, fit Y-axis to visible data
            # Skip expensive numpy ops if visible window hasn't changed
            x_start = temp_plot.x_range.start
            x_end = temp_plot.x_range.end
            n_pts = len(prepared["time"])
            last_ts = prepared["time"][-1] if n_pts > 0 else 0
            yaxis_key = (n_pts, last_ts, round(x_start, 1), round(x_end, 1))

            if yaxis_key != data_cache.get("_yaxis_key"):
                data_cache["_yaxis_key"] = yaxis_key

                # Only convert to numpy once for y-axis calculation
                times = np.asarray(prepared["time"])
                visible_mask = (times >= x_start) & (times <= x_end)

                if temp_window_state["auto"]:
                    visible_temps = np.asarray(prepared["temperature"])[visible_mask]
                    valid_temps = visible_temps[~np.isnan(visible_temps)]
                    if len(valid_temps) > 0:
                        temp_min = np.min(valid_temps)
                        temp_max = np.max(valid_temps)
                        temp_range = temp_max - temp_min
                        temp_padding = max(temp_range * 0.1, 0.5)
                        temp_plot.y_range.start = temp_min - temp_padding
                        temp_plot.y_range.end = temp_max + temp_padding
                        temp_window_state["last_auto_update"] = time.monotonic()

                if hum_window_state["auto"]:
                    visible_hums = np.asarray(prepared["humidity"])[visible_mask]
                    valid_hums = visible_hums[~np.isnan(visible_hums)]
                    if len(valid_hums) > 0:
                        hum_min = np.min(valid_hums)
                        hum_max = np.max(valid_hums)
                        hum_range = hum_max - hum_min
                        hum_padding = max(hum_range * 0.1, 2.0)
                        humidity_plot.y_range.start = max(0.0, hum_min - hum_padding)
                        humidity_plot.y_range.end = min(100.0, hum_max + hum_padding)
                        hum_window_state["last_auto_update"] = time.monotonic()

        # Sync toggle state with auto-range flag
        if not current_window["auto_range"] and auto_scroll_toggle.active:
            auto_scroll_toggle.active = False
            auto_scroll_toggle.button_type = "default"
            auto_scroll_toggle.label = "Auto-scroll: OFF"

        # Update current readings display
        _update_status_display(sensor_name, prepared, latest_time_ms)

    except Exception as e:
        logger.error(f"Error updating view: {e}")


def _build_status_panel_html(
    sensor_name, status_icon, status_label, sensor_type_str,
    reading_html, device_ip, cpu_str, mem_str, client_db_str,
    client_records_str, uptime_str, last_sync_str, server_db_size,
    records_str, error_html
):
    """Build the 4-column status panel HTML. Shared by both data and no-data paths."""
    return f"""
    <div style="background-color:#f0f0f0;padding:14px;border-radius:5px;margin-bottom:18px;box-shadow:0 2px 4px rgba(0,0,0,0.1);display:flex;flex-wrap:wrap;gap:2px;align-items:flex-start;max-width:1200px;">
        <div style="flex:1 1 180px;min-width:180px;">
            <h3 style="margin:0 0 5px 0;font-size:14px;font-weight:600;">Status</h3>
            <p style="font-size:16px;margin:0;">{status_icon} {sensor_name}</p>
            <p style="font-size:12px;margin:4px 0 0;color:#555;">{status_label}</p>
            <p style="font-size:12px;margin:4px 0 0;color:#555;">{sensor_type_str}</p>
        </div>
        <div style="flex:1 1 200px;min-width:200px;">
            <h3 style="margin:0 0 5px 0;font-size:14px;font-weight:600;">Last reading</h3>
            {reading_html}
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


def _get_metadata_display_vars(sensor_name):
    """Fetch and format metadata + server info for the status panel.
    Returns a dict of formatted strings ready for the HTML template."""
    metadata = aggregator.get_sensor_metadata(sensor_name)
    status_icon, status_label = _resolve_status_display(metadata)
    metadata_safe = metadata or {}
    device_ip = metadata_safe.get("device_ip") or "unknown"
    last_sync_str = _format_metadata_timestamp(metadata_safe.get("last_update"))
    last_error = metadata_safe.get("last_error")
    error_html = ""
    if last_error:
        error_html = f"<p style='font-size:12px;margin:6px 0 0;color:#c0392b;background-color:#fdecea;padding:4px 8px;border-radius:3px;'>Last error: {last_error}</p>"

    # Server uptime
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

    # Server metrics
    server_metrics = aggregator.get_server_metrics()
    server_db_size = server_metrics.get("database_size_mb", 0)
    total_records = server_metrics.get("total_records", 0)
    if total_records >= 1000000:
        records_str = f"{total_records / 1000000:.1f}M"
    elif total_records >= 1000:
        records_str = f"{total_records / 1000:.0f}K"
    else:
        records_str = str(total_records)

    # Client metrics
    cpu_str = f"{metadata_safe.get('cpu_percent', 0):.0f}%" if metadata_safe.get('cpu_percent') is not None else "—"
    mem_str = f"{metadata_safe.get('memory_percent', 0):.0f}%" if metadata_safe.get('memory_percent') is not None else "—"
    client_db_str = f"{metadata_safe.get('client_db_size_mb', 0):.1f} MB" if metadata_safe.get('client_db_size_mb') is not None else "—"
    sensor_type_str = metadata_safe.get('sensor_type', 'Unknown')

    # Client record count
    client_total_records = metadata_safe.get('client_total_records', 0)
    if client_total_records is not None and client_total_records >= 1000000:
        client_records_str = f"{client_total_records / 1000000:.1f}M"
    elif client_total_records is not None and client_total_records >= 1000:
        client_records_str = f"{client_total_records / 1000:.0f}K"
    elif client_total_records is not None:
        client_records_str = str(client_total_records)
    else:
        client_records_str = "—"

    return {
        "metadata_safe": metadata_safe,
        "status_icon": status_icon,
        "status_label": status_label,
        "sensor_type_str": sensor_type_str,
        "device_ip": device_ip,
        "last_sync_str": last_sync_str,
        "error_html": error_html,
        "uptime_str": uptime_str,
        "server_db_size": server_db_size,
        "records_str": records_str,
        "cpu_str": cpu_str,
        "mem_str": mem_str,
        "client_db_str": client_db_str,
        "client_records_str": client_records_str,
    }


def _update_status_display_no_data(sensor_name):
    """Show the full status panel even when no sensor data is available.
    Displays metadata (IP, status, errors) with 'No data yet' for readings."""
    mv = _get_metadata_display_vars(sensor_name)

    reading_html = """<p style="font-size:16px;margin:0;color:#888;">No data yet</p>
            <p style="font-size:12px;margin:4px 0 0;color:#999;">Waiting for sensor data...</p>"""

    current_readings.text = _build_status_panel_html(
        sensor_name=sensor_name, reading_html=reading_html, **{
            k: mv[k] for k in (
                "status_icon", "status_label", "sensor_type_str", "device_ip",
                "cpu_str", "mem_str", "client_db_str", "client_records_str",
                "uptime_str", "last_sync_str", "server_db_size", "records_str",
                "error_html"
            )
        }
    )


def _update_status_display(sensor_name, prepared, latest_time_ms):
    """Helper function to update the status display with sensor data."""
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

    # Get shared metadata/server display variables
    mv = _get_metadata_display_vars(sensor_name)
    metadata_safe = mv["metadata_safe"]

    # Calculate time since last reading (use actual last reading time, not plot end time)
    now_ms = int(pd.Timestamp.now().timestamp() * 1000)
    time_ago_ms = now_ms - latest_reading_ms
    time_ago_s = time_ago_ms / 1000

    # Format time ago
    if time_ago_s < 60:
        time_ago_part = f"{int(time_ago_s)}s"
    elif time_ago_s < 3600:
        time_ago_part = f"{int(time_ago_s / 60)}m"
    elif time_ago_s < 86400:
        time_ago_part = f"{int(time_ago_s / 3600)}h"
    else:
        days = int(time_ago_s / 86400)
        time_ago_part = f"{days}d"

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

    reading_html = f"""<p style="font-size:16px;margin:0;">{curr_temp:.1f}°C · {curr_hum:.1f}%</p>
            <p style="font-size:12px;margin:4px 0 0;color:#555;">{time_ago_str}</p>
            <p style="font-size:11px;margin:2px 0 0;color:#777;">{curr_time_str}</p>"""

    current_readings.text = _build_status_panel_html(
        sensor_name=sensor_name, reading_html=reading_html, **{
            k: mv[k] for k in (
                "status_icon", "status_label", "sensor_type_str", "device_ip",
                "cpu_str", "mem_str", "client_db_str", "client_records_str",
                "uptime_str", "last_sync_str", "server_db_size", "records_str",
                "error_html"
            )
        }
    )


_stream_cycle_count = 0
_FULL_REFRESH_EVERY = 60  # Force full LTTB refresh every ~60 cycles (~5 min at 5s interval)

def update_data():
    """
    Periodic refresh - fetch new data and update view.
    Called by bokeh periodic callback.
    """
    global _stream_cycle_count
    try:
        if load_state["busy"]:
            return  # A background load is about to replace the data anyway

        sensor_name = current_sensor_state["name"]

        _stream_cycle_count += 1
        if _stream_cycle_count >= _FULL_REFRESH_EVERY and data_cache["sensor_name"] == sensor_name:
            # Periodic full refresh to maintain LTTB quality after many streaming updates
            _stream_cycle_count = 0
            logger.debug("Periodic full LTTB refresh triggered")
            _request_load(None, refetch=False)
            return

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
time_button_row = row(btn_10min, btn_3h, btn_12h, btn_24h, btn_1week, btn_30d, btn_60d, btn_180d, btn_all,
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
    column(temp_window_spinner, temp_auto_toggle),
    column(hum_window_spinner, hum_auto_toggle),
    data_display_select,  # Dropdown to select data display mode
    column(Div(text="&nbsp;", height=10), day_lines_toggle),  # Empty title space for alignment
    sizing_mode="scale_width"
)

_LOADING_HTML = (
    "<div style='padding:8px 16px;background:#e3f2fd;border-radius:4px;"
    "color:#1565c0;font-weight:bold;text-align:center;'>⏳ {msg}</div>"
)
loading_indicator = Div(
    text="",
    visible=False,
    sizing_mode="stretch_width",
    height=40,
)

# One line under the time buttons summarising the last load
load_status = Div(text="", sizing_mode="stretch_width", height=20,
                  styles={"font-size": "12px", "color": "#666"})

connection_banner = Div(
    text=("<div style='padding:8px 16px;background:#fdecea;border-radius:4px;color:#b71c1c;"
          "font-weight:bold;text-align:center;'>Connection to the server was lost. "
          "Reload the page to reconnect.</div>"),
    visible=False,
    sizing_mode="stretch_width",
)

# Plots fade while their data is being replaced
all_plots = [temp_plot, humidity_plot, pressure_plot, light_plot, noise_plot]
_PLOT_IDLE_STYLE = {"opacity": "1", "transition": "opacity 0.15s"}
_PLOT_BUSY_STYLE = {"opacity": "0.45", "transition": "opacity 0.15s"}
for _plot in all_plots:
    _plot.styles = _PLOT_IDLE_STYLE

# Client-side loading indicator: shows instantly in the browser, before the Python round-trip.
# Python replaces the message with a specific one and clears it when the update is done.
_loading_js_show = CustomJS(
    args=dict(indicator=loading_indicator, plots=all_plots,
              html=_LOADING_HTML.format(msg="Loading…"), busy_style=_PLOT_BUSY_STYLE),
    code="""
        indicator.text = html;
        indicator.visible = true;
        for (const p of plots) { p.styles = busy_style; }
    """,
)

# Wire JS loading to user-triggered reloads (fires before the Python callback). Spinners are
# left out: Python also sets their values, which would re-show the indicator after it cleared.
sensor_selector.js_on_change("value", _loading_js_show)
for _btn in time_buttons:
    _btn.js_on_click(_loading_js_show)

curdoc().js_on_event(ConnectionLost, CustomJS(args=dict(banner=connection_banner),
                                              code="banner.visible = true;"))


def _set_busy(msg):
    loading_indicator.text = _LOADING_HTML.format(msg=msg)
    loading_indicator.visible = True
    for plot in all_plots:
        plot.styles = _PLOT_BUSY_STYLE


def _clear_busy():
    loading_indicator.visible = False
    loading_indicator.text = ""
    for plot in all_plots:
        plot.styles = _PLOT_IDLE_STYLE


def _loading_message(sensor_name, default):
    meta = aggregator.get_sensor_metadata(sensor_name)
    if meta and meta.get("status") == "syncing":
        return f"Syncing data for {sensor_name} — this may take a moment…"
    return default


def _load_job(sensor_name, window_minutes, ma_window, raw_snapshot, log_interval_s, view_range):
    """Worker thread: fetch (unless given a snapshot) and prepare plot data. No Bokeh access."""
    t0 = time.perf_counter()
    fetched = None
    raw = raw_snapshot
    if raw is None:
        fetched = _query_window_data(sensor_name, window_minutes)
        raw = fetched["data"]
        log_interval_s = fetched["log_interval_s"]
    prepared = None
    if raw is not None:
        prepared = prepare_source_data(_slice_to_view(raw, view_range), ma_window, display_points,
                                       connect_points=False, log_interval_s=log_interval_s)
    return {"fetched": fetched, "prepared": prepared, "raw": raw, "elapsed_s": time.perf_counter() - t0}


def _request_load(message, refetch=True):
    """
    Fetch and prepare data on a worker thread, then apply it to the session.
    message=None runs quietly (no indicator). A newer request supersedes older ones.
    """
    load_state["generation"] += 1
    generation = load_state["generation"]
    load_state["busy"] = True
    if message is not None:
        _set_busy(message)

    sensor_name = current_sensor_state["name"]
    window_minutes = current_window.get("minutes")
    ma_window = max(int(ma_spinner.value), 1)
    reuse_raw = (not refetch and data_cache["raw_data"] is not None
                 and data_cache["sensor_name"] == sensor_name)
    raw_snapshot = dict(data_cache["raw_data"]) if reuse_raw else None
    view_range = data_cache["view_range"] if reuse_raw else None

    future = load_executor.submit(_load_job, sensor_name, window_minutes, ma_window,
                                  raw_snapshot, data_cache.get("log_interval_s"), view_range)

    def _on_done(fut):
        try:
            session_doc.add_next_tick_callback(partial(
                _apply_load, generation, fut, sensor_name, window_minutes, ma_window, view_range,
                message is not None))
        except Exception:
            logger.debug("Session closed before background load finished", exc_info=True)

    future.add_done_callback(_on_done)


def _apply_load(generation, future, sensor_name, window_minutes, ma_window, view_range, report):
    """Event loop: install a finished load, unless a newer request superseded it."""
    if generation != load_state["generation"]:
        return
    try:
        result = future.result()
        if result["fetched"] is not None:
            _install_fetched_data(sensor_name, result["fetched"])
            data_cache["view_range"] = None  # New data: back to the whole-dataset view
        if result["prepared"] is not None:
            _store_prepared(result["prepared"], result["raw"], ma_window, view_range)
        data_cache["new_point_count"] = -1  # Replace the plotted data, don't stream
        load_state["busy"] = False
        update_view()
        if report:
            readings = len(result["raw"]["time"]) if result["raw"] is not None else 0
            load_status.text = (f"{sensor_name} · {format_minutes(window_minutes)} · "
                                f"{readings:,} readings · loaded in {result['elapsed_s']:.1f} s")
    except Exception as e:
        logger.error(f"Background load failed for {sensor_name}: {e}", exc_info=True)
        load_status.text = f"Loading {sensor_name} failed. Try again, or reload the page."
    finally:
        load_state["busy"] = False
        _clear_busy()


layout = column(
    Div(text="<h1> Multi-sensor temperature monitor</h1>", sizing_mode="stretch_width", height=60),
    connection_banner,
    Div(text="<h3>Sensor Selection</h3>", sizing_mode="stretch_width", height=25),
    sensor_selection_row,
    loading_indicator,
    Div(text="<br>", sizing_mode="stretch_width", height=10),
    current_readings,
    Div(text="<h4>Quick set time window:</h4>", sizing_mode="stretch_width", height=25),
    time_button_row,
    load_status,
    # Div(text="<h4>Custom Window:</h4>", sizing_mode="stretch_width", height=25),
    # custom_time_row,
    Div(text="<h4>Window control:</h4>", sizing_mode="stretch_width", height=25),
    window_control_row,
    Div(text="<h4>Display control:</h4>", sizing_mode="stretch_width", height=25),
    display_range_row,
    Div(text="<br>", sizing_mode="stretch_width", height=10),
    temp_plot,
    humidity_plot,
    pressure_plot,  # Dynamically shown/hidden based on sensor data
    light_plot,     # Dynamically shown/hidden based on sensor data
    noise_plot,     # Dynamically shown/hidden based on sensor data
    sizing_mode="scale_width"
)

curdoc().add_root(layout)
curdoc().title = "Multi-Sensor Temperature Monitor"

# Restore saved sensor selection from localStorage on page load
curdoc().js_on_event('document_ready', restore_sensor_js)
