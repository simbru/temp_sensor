"""
Largest Triangle Three Buckets (LTTB) downsampling algorithm.

Reduces a time series to N points while preserving visual shape — peaks,
troughs, and trends are kept; only "boring" middle points are dropped.

Reference: Sveinn Steinarsson, "Downsampling Time Series for Visual
Representation" (MSc thesis, University of Iceland, 2013).
"""

import numpy as np


def lttb_downsample(time_vals, data_arrays, threshold):
    """
    Downsample aligned arrays using LTTB on the *first* data array.

    The first data array (typically temperature) drives the index selection;
    all other arrays are sliced to the same indices so they stay aligned.

    NaN markers (gap indicators) are preserved: any index where the primary
    data array is NaN is automatically kept, and LTTB runs on the non-NaN
    segments independently.

    Args:
        time_vals:    1-D array of timestamps (any numeric type, e.g. ms epoch).
        data_arrays:  Dict mapping column names to 1-D arrays, all same length
                      as time_vals.  The *first* float-valued column is used as
                      the LTTB reference signal.
        threshold:    Target number of output points.  If the input is already
                      ≤ threshold, all data is returned unchanged.

    Returns:
        (time_out, data_out) where time_out is the downsampled time array
        and data_out is a dict with the same keys as data_arrays, each
        downsampled to the same indices.
    """
    n = len(time_vals)
    if n <= threshold or threshold < 3:
        return time_vals, data_arrays

    # Find the primary signal for LTTB (first float array that isn't all-NaN)
    primary_key = None
    for key, arr in data_arrays.items():
        if key == "time":
            continue
        arr_np = np.asarray(arr, dtype=float)
        if not np.all(np.isnan(arr_np)):
            primary_key = key
            break

    if primary_key is None:
        # All data is NaN — just return evenly spaced indices
        indices = np.linspace(0, n - 1, threshold, dtype=int)
        time_out = np.asarray(time_vals)[indices]
        data_out = {k: np.asarray(v)[indices] for k, v in data_arrays.items()}
        return time_out, data_out

    primary = np.asarray(data_arrays[primary_key], dtype=float)
    times = np.asarray(time_vals, dtype=float)

    # Identify NaN positions (gap markers) — these must be preserved
    nan_mask = np.isnan(primary)

    if not np.any(nan_mask):
        # Simple case: no gaps, run LTTB on full array
        indices = _lttb_core(times, primary, threshold)
    else:
        # Complex case: preserve all NaN markers, run LTTB on non-NaN segments
        nan_indices = set(np.where(nan_mask)[0])
        non_nan_indices = np.where(~nan_mask)[0]

        # Budget: we need to keep all NaN indices, distribute remaining budget
        # across non-NaN data proportionally
        nan_budget = len(nan_indices)
        remaining_budget = max(threshold - nan_budget, 3)

        if len(non_nan_indices) <= remaining_budget:
            # Enough budget to keep everything
            indices = np.arange(n)
        else:
            # Run LTTB on non-NaN data only
            sub_times = times[non_nan_indices]
            sub_primary = primary[non_nan_indices]
            sub_selected = _lttb_core(sub_times, sub_primary, remaining_budget)
            # Map sub-indices back to original indices
            selected_non_nan = non_nan_indices[sub_selected]
            # Merge with NaN indices
            indices = np.sort(np.concatenate([
                selected_non_nan,
                np.array(list(nan_indices), dtype=int)
            ]))

    time_out = times[indices]
    data_out = {k: np.asarray(v)[indices] for k, v in data_arrays.items()}
    return time_out, data_out


def _lttb_core(times, values, threshold):
    """
    Core LTTB algorithm — no NaN handling, pure downsampling.

    Args:
        times:     1-D float array of timestamps.
        values:    1-D float array of data values (no NaN).
        threshold: Number of output points (≥ 3).

    Returns:
        1-D int array of selected indices into the input arrays.
    """
    n = len(times)
    if n <= threshold:
        return np.arange(n)

    # Always keep first and last points
    selected = np.empty(threshold, dtype=int)
    selected[0] = 0
    selected[threshold - 1] = n - 1

    # Bucket size (first and last buckets have 1 point each)
    bucket_size = (n - 2) / (threshold - 2)

    prev_selected_idx = 0

    for i in range(1, threshold - 1):
        # Current bucket range
        bucket_start = int(np.floor((i - 1) * bucket_size)) + 1
        bucket_end = int(np.floor(i * bucket_size)) + 1
        bucket_end = min(bucket_end, n - 1)

        # Next bucket range (for computing average)
        next_bucket_start = int(np.floor(i * bucket_size)) + 1
        next_bucket_end = int(np.floor((i + 1) * bucket_size)) + 1
        next_bucket_end = min(next_bucket_end, n)

        # Average of next bucket
        avg_time = np.mean(times[next_bucket_start:next_bucket_end])
        avg_val = np.mean(values[next_bucket_start:next_bucket_end])

        # Previous selected point
        prev_time = times[prev_selected_idx]
        prev_val = values[prev_selected_idx]

        # Find point in current bucket with max triangle area
        best_idx = bucket_start
        best_area = -1.0

        for j in range(bucket_start, bucket_end):
            # Triangle area = 0.5 * |x_a(y_b - y_c) + x_b(y_c - y_a) + x_c(y_a - y_b)|
            area = abs(
                (prev_time - avg_time) * (values[j] - prev_val)
                - (prev_time - times[j]) * (avg_val - prev_val)
            )
            if area > best_area:
                best_area = area
                best_idx = j

        selected[i] = best_idx
        prev_selected_idx = best_idx

    return selected
