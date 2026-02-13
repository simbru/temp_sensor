"""
Multi-sensor Bokeh Server dashboard for lab temperature monitoring.
Run with: bokeh serve server/bokeh_app --port 8000
"""
import logging
import math
import pathlib
import sys
import time

import numpy as np
import pandas as pd

# Add project root to path so 'server.*' imports work
_project_root = pathlib.Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from bokeh.plotting import figure, curdoc
from bokeh.models import ColumnDataSource, Button, Spinner, Range1d, Toggle, CustomJS, Select, Span
from bokeh.layouts import column, row
from bokeh.models.widgets import Div

from server.lttb import lttb_downsample
from server.bokeh_app._startup import (
    get_aggregator, sensor_configs, poll_interval, dashboard_update_ms, max_plot_points,
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
    result_arrays = _insert_gap_markers_multi(time_vals, arrays_to_process,
                                               gap_threshold_s=gap_threshold,
                                               expected_interval_s=expected_interval_s)

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

    # Calculate rolling average, properly handling NaN gaps
    # pandas rolling().mean() automatically skips NaN values when calculating the mean,
    # so gaps (NaN markers) won't corrupt the moving average on either side.
    # min_periods=1 ensures MA starts immediately after a gap with available data.
    temp_ma = pd.Series(temps).rolling(window=window, min_periods=1).mean().to_numpy()
    hum_ma = pd.Series(hums).rolling(window=window, min_periods=1).mean().to_numpy()

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
        result["pressure_ma"] = pd.Series(pressures).rolling(window=window, min_periods=1).mean().to_numpy()
    else:
        result["pressure"] = np.full(n_points, np.nan)
        result["pressure_ma"] = np.full(n_points, np.nan)
    
    if has_light and lights is not None and len(lights) == n_points:
        result["light"] = lights
        result["light_ma"] = pd.Series(lights).rolling(window=window, min_periods=1).mean().to_numpy()
    else:
        result["light"] = np.full(n_points, np.nan)
        result["light_ma"] = np.full(n_points, np.nan)
    
    if has_noise and noises is not None and len(noises) == n_points:
        result["noise"] = noises
        result["noise_ma"] = pd.Series(noises).rolling(window=window, min_periods=1).mean().to_numpy()
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

    return result


def _insert_gap_markers(time_vals, temps, hums, gap_threshold_s=60, expected_interval_s=2):
    """
    Insert NaN at time gaps to show missing data in plots.

    Uses adaptive gap detection that auto-detects the actual logging interval
    from the data itself, making it robust to changes in sensor logging rate.

    IMPORTANT: time_vals are milliseconds since epoch (float), not ISO strings.
    This is the format returned by data_aggregator for Bokeh plotting.

    Args:
        time_vals: Millisecond timestamps (float) - NOT ISO strings
        temps: Temperature values
        hums: Humidity values
        gap_threshold_s: Fallback gap threshold if auto-detection fails (default 60s)
        expected_interval_s: Fallback interval if auto-detection fails (default 2s)

    Returns:
        Tuple of (times, temps, hums) with NaN inserted at gaps
    """
    if len(time_vals) < 2:
        return time_vals, temps, hums

    # Calculate time gaps directly from milliseconds (avoid datetime conversion)
    time_diffs_ms = np.diff(time_vals)
    time_diffs_s = time_diffs_ms / 1000.0

    # ADAPTIVE GAP DETECTION:
    # Calculate global median interval to understand typical logging rate
    # Filter out extreme outliers (> 10x median) to avoid skewing by sensor outages
    median_interval = np.median(time_diffs_s)

    # Filter time_diffs for baseline calculation (remove outliers)
    reasonable_diffs = time_diffs_s[time_diffs_s < median_interval * 10]

    if len(reasonable_diffs) > 0:
        # Use 95th percentile of reasonable intervals as baseline
        baseline_interval = np.percentile(reasonable_diffs, 95)
    else:
        # Fallback if all data is outliers (shouldn't happen)
        baseline_interval = expected_interval_s

    import logging
    logger = logging.getLogger(__name__)
    logger.debug(f"Gap detection - Median interval: {median_interval:.1f}s, Baseline (95th percentile): {baseline_interval:.1f}s")

    # Detect gaps using adaptive threshold
    # A gap is significant if it's 3x the baseline interval
    adaptive_gap_threshold = max(baseline_interval * 3, gap_threshold_s)
    gap_mask = time_diffs_s > adaptive_gap_threshold
    gap_indices = np.where(gap_mask)[0] + 1  # +1 because diff shifts indices

    if len(gap_indices) == 0:
        return time_vals, temps, hums
    
    # Calculate exact size needed by examining each gap
    total_nan_markers = 0
    for gap_idx in gap_indices:
        gap_size_ms = time_vals[gap_idx] - time_vals[gap_idx - 1]
        gap_size_seconds = gap_size_ms / 1000.0

        # LOCAL ADAPTIVE INTERVAL:
        # Check local interval around this gap for more accurate NaN marker calculation
        local_window_start = max(0, gap_idx - 50)
        local_window_end = min(len(time_diffs_s), gap_idx + 50)
        local_diffs = time_diffs_s[local_window_start:local_window_end]

        # Filter out other large gaps in local window
        local_reasonable = local_diffs[local_diffs < adaptive_gap_threshold]

        if len(local_reasonable) > 5:
            local_interval = np.median(local_reasonable)
        else:
            # Not enough local data, use global baseline
            local_interval = baseline_interval

        # Use the larger of baseline or local interval to be conservative
        effective_interval = max(baseline_interval, local_interval)

        num_missing = int(gap_size_seconds / effective_interval)
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

        # Calculate how many NaN markers to insert using adaptive interval
        gap_size_ms = time_vals[gap_idx] - time_vals[gap_idx - 1]
        gap_size_seconds = gap_size_ms / 1000.0

        # LOCAL ADAPTIVE INTERVAL (same logic as above):
        local_window_start = max(0, gap_idx - 50)
        local_window_end = min(len(time_diffs_s), gap_idx + 50)
        local_diffs = time_diffs_s[local_window_start:local_window_end]
        local_reasonable = local_diffs[local_diffs < adaptive_gap_threshold]

        if len(local_reasonable) > 5:
            local_interval = np.median(local_reasonable)
        else:
            local_interval = baseline_interval

        effective_interval = max(baseline_interval, local_interval)
        num_missing = int(gap_size_seconds / effective_interval)

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


def _insert_gap_markers_multi(time_vals, data_arrays, gap_threshold_s=60, expected_interval_s=2):
    """
    Insert NaN at time gaps for multiple data arrays simultaneously.
    
    This ensures all arrays get gap markers at exactly the same positions,
    preventing shape mismatches when sensors have extended data (pressure, light, noise).

    Args:
        time_vals: Millisecond timestamps (float)
        data_arrays: List of data arrays to process (all must have same length as time_vals)
        gap_threshold_s: Fallback gap threshold if auto-detection fails
        expected_interval_s: Fallback interval if auto-detection fails

    Returns:
        List of arrays: [time_vals, array1, array2, ...] with NaN inserted at gaps
    """
    if len(time_vals) < 2:
        return [time_vals] + list(data_arrays)

    # Calculate time gaps directly from milliseconds
    time_diffs_ms = np.diff(time_vals)
    time_diffs_s = time_diffs_ms / 1000.0

    # ADAPTIVE GAP DETECTION
    median_interval = np.median(time_diffs_s)
    reasonable_diffs = time_diffs_s[time_diffs_s < median_interval * 10]

    if len(reasonable_diffs) > 0:
        baseline_interval = np.percentile(reasonable_diffs, 95)
    else:
        baseline_interval = expected_interval_s

    # Detect gaps using adaptive threshold
    adaptive_gap_threshold = max(baseline_interval * 3, gap_threshold_s)
    gap_mask = time_diffs_s > adaptive_gap_threshold
    gap_indices = np.where(gap_mask)[0] + 1

    if len(gap_indices) == 0:
        return [time_vals] + list(data_arrays)

    # Calculate exact size needed
    total_nan_markers = 0
    gap_info = []  # Store (gap_idx, num_nan_markers, nan_times) for each gap

    for gap_idx in gap_indices:
        gap_size_ms = time_vals[gap_idx] - time_vals[gap_idx - 1]
        gap_size_seconds = gap_size_ms / 1000.0

        # Local adaptive interval
        local_window_start = max(0, gap_idx - 50)
        local_window_end = min(len(time_diffs_s), gap_idx + 50)
        local_diffs = time_diffs_s[local_window_start:local_window_end]
        local_reasonable = local_diffs[local_diffs < adaptive_gap_threshold]

        if len(local_reasonable) > 5:
            local_interval = np.median(local_reasonable)
        else:
            local_interval = baseline_interval

        effective_interval = max(baseline_interval, local_interval)
        num_missing = int(gap_size_seconds / effective_interval)
        num_nan_markers = max(min(num_missing, 100), 5)

        # Pre-calculate NaN timestamps
        gap_start_ms = time_vals[gap_idx - 1]
        gap_end_ms = time_vals[gap_idx]
        nan_times = np.linspace(gap_start_ms, gap_end_ms, num_nan_markers + 2)[1:-1]

        gap_info.append((gap_idx, num_nan_markers, nan_times))
        total_nan_markers += num_nan_markers

    # Preallocate result arrays
    result_size = len(time_vals) + total_nan_markers
    result_times = np.empty(result_size, dtype=float)
    result_arrays = [np.empty(result_size, dtype=float) for _ in data_arrays]

    write_idx = 0
    prev_idx = 0

    for gap_idx, num_nan_markers, nan_times in gap_info:
        # Copy data up to gap
        chunk_size = gap_idx - prev_idx
        result_times[write_idx:write_idx+chunk_size] = time_vals[prev_idx:gap_idx]
        for i, arr in enumerate(data_arrays):
            result_arrays[i][write_idx:write_idx+chunk_size] = arr[prev_idx:gap_idx]
        write_idx += chunk_size

        # Insert NaN markers
        result_times[write_idx:write_idx+num_nan_markers] = nan_times
        for result_arr in result_arrays:
            result_arr[write_idx:write_idx+num_nan_markers] = np.nan
        write_idx += num_nan_markers

        prev_idx = gap_idx

    # Copy remaining data
    remaining = len(time_vals) - prev_idx
    result_times[write_idx:write_idx+remaining] = time_vals[prev_idx:]
    for i, arr in enumerate(data_arrays):
        result_arrays[i][write_idx:write_idx+remaining] = arr[prev_idx:]
    write_idx += remaining

    # Trim to actual size and return
    return [result_times[:write_idx]] + [arr[:write_idx] for arr in result_arrays]


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
    "log_interval_s": None,
    # Info about data availability outside current window
    "total_records": 0,
    "newest_timestamp_ms": None,  # Most recent data timestamp in entire DB
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
day_boundary_spans_pressure = []

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
day_boundary_spans_light = []

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
day_boundary_spans_noise = []


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
        # Refetch data with new window — show loading indicator
        sensor_name = current_sensor_state["name"]
        _show_loading("Loading time range...")

        def _do_refetch():
            try:
                fetch_initial_data(sensor_name)
                update_view()
            finally:
                _hide_loading()

        curdoc().add_next_tick_callback(_do_refetch)
        return  # update_view will be called in the callback

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
    pressure_plot.title.text = f"Pressure - {new}"
    light_plot.title.text = f"Light - {new}"
    noise_plot.title.text = f"Noise - {new}"
    current_window["force_update"] = True

    # Show loading indicator, then fetch data on next tick so UI updates first
    _show_loading(f"Loading {new}...")

    def _do_sensor_load():
        try:
            fetch_initial_data(new)
            update_view()
            logger.info(f"Switched to sensor: {new}")
        finally:
            _hide_loading()

    curdoc().add_next_tick_callback(_do_sensor_load)


sensor_selector.on_change("value", on_sensor_change)
# Also save to localStorage on client-side when changed
sensor_selector.js_on_change("value", save_sensor_js)


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

            # Historical fallback: if time window returns nothing (sensor dead/stale),
            # fall back to showing the most recent data regardless of age
            if not new_data or len(new_data.get("time", [])) == 0:
                logger.info(f"No data in time window for {sensor_name}, falling back to latest historical records")
                new_data = aggregator.get_sensor_data(sensor_name, limit=1000)
                fetch_type = "historical_fallback"
        except Exception as e:
            logger.error(f"Error fetching windowed data: {e}")
            return None

    try:
        if not new_data or len(new_data.get("time", [])) == 0:
            logger.warning(f"No data available for sensor: {sensor_name}")
            # Check if there's data outside the current time window
            total_records = aggregator.get_total_records(sensor_name)
            newest_ts = aggregator.get_last_timestamp(sensor_name)
            data_cache["sensor_name"] = sensor_name
            data_cache["raw_data"] = None
            data_cache["total_records"] = total_records
            data_cache["newest_timestamp_ms"] = newest_ts
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
        data_cache["total_records"] = len(new_data.get("time", []))
        data_cache["newest_timestamp_ms"] = new_data["time"][-1] if len(new_data["time"]) > 0 else None

        # Invalidate prepared data cache to force recalculation
        # This ensures moving averages are recalculated with the new data
        data_cache["prepared_data"] = None
        data_cache["prep_raw_hash"] = None

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

        # Get the last (window-1) valid values from cached data for MA context
        cached_temps = np.asarray(cached["temperature"])
        cached_hums = np.asarray(cached["humidity"])

        # Concatenate context + new data, compute MA, then take only the new part
        context_temps = cached_temps[-(window - 1):] if window > 1 else np.array([])
        context_hums = cached_hums[-(window - 1):] if window > 1 else np.array([])

        full_temps = np.concatenate([context_temps, stream_temps])
        full_hums = np.concatenate([context_hums, stream_hums])

        temp_ma_full = pd.Series(full_temps).rolling(window=window, min_periods=1).mean().to_numpy()
        hum_ma_full = pd.Series(full_hums).rolling(window=window, min_periods=1).mean().to_numpy()

        # Take only the new portion (after the context window)
        stream_temp_ma = temp_ma_full[len(context_temps):]
        stream_hum_ma = hum_ma_full[len(context_hums):]

        result = {
            "time": stream_times,
            "temperature": stream_temps,
            "humidity": stream_hums,
            "temp_ma": stream_temp_ma,
            "hum_ma": stream_hum_ma,
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

                context_vals = np.asarray(cached.get(field, []))
                context_slice = context_vals[-(window - 1):] if window > 1 and len(context_vals) > 0 else np.array([])
                full_vals = np.concatenate([context_slice, stream_vals])
                ma_full = pd.Series(full_vals).rolling(window=window, min_periods=1).mean().to_numpy()

                result[field] = stream_vals
                result[f"{field}_ma"] = ma_full[len(context_slice):]
            else:
                result[field] = np.full(n_total, np.nan)
                result[f"{field}_ma"] = np.full(n_total, np.nan)

        return result

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

    # Update the raw hash to match new data state
    times = cached.get("time", [])
    if len(times) > 0:
        data_cache["prep_raw_hash"] = (len(times), times[0], times[-1])


def update_view():
    """
    Update plot view and UI elements using cached data.
    Does NOT fetch from database - instant response.
    """
    try:
        sensor_name = current_sensor_state["name"]

        if data_cache["raw_data"] is None or data_cache["sensor_name"] != sensor_name:
            logger.debug("No cached data available, triggering initial fetch")
            fetch_initial_data(sensor_name)
            if data_cache["raw_data"] is None:
                _update_status_display_no_data(sensor_name)
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

        # Decide between streaming (fast incremental) vs full replacement
        new_point_count = data_cache.get("new_point_count", -1)

        # Streaming eligibility: small incremental update, same sensor, same settings
        can_stream = (
            new_point_count > 0 and
            new_point_count <= 100 and
            data_cache["prepared_data"] is not None and
            data_cache["prep_ma_window"] == window and
            data_cache["prep_max_points"] == max_plot_points
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
            cache_valid = (
                data_cache["prepared_data"] is not None and
                data_cache["prep_ma_window"] == window and
                data_cache["prep_max_points"] == max_plot_points and
                data_cache["prep_raw_hash"] == raw_hash
            )

            if cache_valid:
                logger.debug("Using cached prepared data")
                prepared = data_cache["prepared_data"]
            else:
                logger.debug(f"Recomputing prepared data (MA window: {window}, max points: {max_plot_points})")
                log_interval_s = data_cache.get("log_interval_s")
                prepared = prepare_source_data(raw_data, window, max_plot_points,
                                              connect_points=False,
                                              log_interval_s=log_interval_s)
                data_cache["prepared_data"] = prepared
                data_cache["prep_ma_window"] = window
                data_cache["prep_max_points"] = max_plot_points
                data_cache["prep_raw_hash"] = raw_hash

            # Replace entire dataset
            source.data = prepared

        # Reset new_point_count after processing
        data_cache["new_point_count"] = 0

        # Show/hide extended sensor plots based on available data
        pressure_plot.visible = "pressure" in prepared and len(prepared.get("pressure", [])) > 0
        light_plot.visible = "light" in prepared and len(prepared.get("light", [])) > 0
        noise_plot.visible = "noise" in prepared and len(prepared.get("noise", [])) > 0

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
        sensor_name = current_sensor_state["name"]

        _stream_cycle_count += 1
        if _stream_cycle_count >= _FULL_REFRESH_EVERY:
            # Periodic full refresh to maintain LTTB quality after many streaming updates
            _stream_cycle_count = 0
            data_cache["new_point_count"] = -1  # Force full replacement path
            data_cache["prepared_data"] = None
            data_cache["prep_raw_hash"] = None
            logger.debug("Periodic full LTTB refresh triggered")

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
    column(temp_window_spinner, temp_auto_toggle),
    column(hum_window_spinner, hum_auto_toggle),
    data_display_select,  # Dropdown to select data display mode
    sizing_mode="scale_width"
)

loading_indicator = Div(
    text="",
    visible=False,
    sizing_mode="stretch_width",
    height=40,
)

# Client-side JS loading indicator — shows instantly on browser before Python round-trip.
# The Python _show_loading is kept as fallback for non-widget-triggered loads.
_loading_js_show = CustomJS(args=dict(indicator=loading_indicator), code="""
    indicator.visible = true;
    indicator.text = '<div style="padding:8px 16px;background:#e3f2fd;border-radius:4px;color:#1565c0;font-weight:bold;text-align:center;">⏳ Loading...</div>';
""")

# Wire JS loading to sensor selector (fires on browser before Python on_change)
sensor_selector.js_on_change("value", _loading_js_show)

# Wire JS loading to time window buttons
for _btn in [btn_10min, btn_3h, btn_12h, btn_24h, btn_1week, btn_all]:
    _btn.js_on_click(_loading_js_show)

def _show_loading(msg="Loading data..."):
    """Show loading indicator (Python-side fallback for non-widget triggers)."""
    loading_indicator.text = (
        f"<div style='padding:8px 16px;background:#e3f2fd;border-radius:4px;"
        f"color:#1565c0;font-weight:bold;text-align:center;'>"
        f"⏳ {msg}</div>"
    )
    loading_indicator.visible = True

def _hide_loading():
    """Hide loading indicator after data is ready."""
    loading_indicator.visible = False
    loading_indicator.text = ""

layout = column(
    Div(text="<h1> Multi-sensor temperature monitor</h1>", sizing_mode="stretch_width", height=60),
    Div(text="<h3>Sensor Selection</h3>", sizing_mode="stretch_width", height=25),
    sensor_selection_row,
    loading_indicator,
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
    pressure_plot,  # Dynamically shown/hidden based on sensor data
    light_plot,     # Dynamically shown/hidden based on sensor data
    noise_plot,     # Dynamically shown/hidden based on sensor data
    sizing_mode="scale_width"
)

curdoc().add_root(layout)
curdoc().title = "Multi-Sensor Temperature Monitor"

# Restore saved sensor selection from localStorage on page load
curdoc().js_on_event('document_ready', restore_sensor_js)
