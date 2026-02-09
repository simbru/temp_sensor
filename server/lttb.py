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

    NaN markers (gap indicators) are preserved where budget allows.
    If there are more NaN markers than the threshold, a subset of evenly-
    spaced NaN indices is kept so output never exceeds the threshold.

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
        # Complex case: preserve NaN markers, run LTTB on non-NaN segments
        nan_idx_arr = np.where(nan_mask)[0]
        non_nan_indices = np.where(~nan_mask)[0]

        nan_count = len(nan_idx_arr)

        # Clamp NaN budget so total output never exceeds threshold
        if nan_count >= threshold - 2:
            # Too many NaN markers — keep an evenly-spaced subset
            nan_budget = threshold - 2  # reserve 2 slots minimum for real data
            keep = np.linspace(0, nan_count - 1, nan_budget, dtype=int)
            nan_idx_arr = nan_idx_arr[keep]
            remaining_budget = 3  # minimum for LTTB
        else:
            remaining_budget = max(threshold - nan_count, 3)

        if len(non_nan_indices) <= remaining_budget:
            # Enough budget to keep all real data
            indices = np.sort(np.concatenate([non_nan_indices, nan_idx_arr]))
        else:
            # Run LTTB on non-NaN data only
            sub_times = times[non_nan_indices]
            sub_primary = primary[non_nan_indices]
            sub_selected = _lttb_core(sub_times, sub_primary, remaining_budget)
            # Map sub-indices back to original indices
            selected_non_nan = non_nan_indices[sub_selected]
            # Merge with NaN indices
            indices = np.sort(np.concatenate([selected_non_nan, nan_idx_arr]))

    time_out = times[indices]
    data_out = {k: np.asarray(v)[indices] for k, v in data_arrays.items()}
    return time_out, data_out


def _lttb_core(times, values, threshold):
    """
    Core LTTB algorithm — no NaN handling, pure downsampling.

    Uses vectorized bucket boundary + cumulative sum precomputation for
    fast range averages, keeping only the small inner-bucket max-area
    selection as a Python loop.

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

    # ── Vectorized precomputation ────────────────────────────────────
    # Precompute ALL bucket boundaries at once (eliminates per-iteration np.floor)
    indices = np.arange(threshold - 2)
    bucket_starts = np.floor(indices * bucket_size).astype(int) + 1
    bucket_ends = np.floor((indices + 1) * bucket_size).astype(int) + 1
    bucket_ends = np.minimum(bucket_ends, n - 1)

    # Next-bucket boundaries for average computation
    next_starts = bucket_ends.copy()
    next_ends = np.floor((indices + 2) * bucket_size).astype(int) + 1
    next_ends = np.minimum(next_ends, n)

    # Cumulative sum trick: O(1) range average instead of O(k) np.mean per bucket
    cumsum_t = np.empty(n + 1)
    cumsum_t[0] = 0.0
    np.cumsum(times, out=cumsum_t[1:])

    cumsum_v = np.empty(n + 1)
    cumsum_v[0] = 0.0
    np.cumsum(values, out=cumsum_v[1:])

    counts = next_ends - next_starts
    # Guard against zero-length buckets (shouldn't happen, but be safe)
    safe_counts = np.maximum(counts, 1)
    avg_times = (cumsum_t[next_ends] - cumsum_t[next_starts]) / safe_counts
    avg_vals = (cumsum_v[next_ends] - cumsum_v[next_starts]) / safe_counts

    # ── Inner loop (only iterates within small buckets, ~6 pts each) ─
    prev_selected_idx = 0

    for i in range(threshold - 2):
        bs = bucket_starts[i]
        be = bucket_ends[i]
        at = avg_times[i]
        av = avg_vals[i]

        prev_time = times[prev_selected_idx]
        prev_val = values[prev_selected_idx]

        # Vectorized area computation within the bucket
        bucket_times = times[bs:be]
        bucket_vals = values[bs:be]
        areas = np.abs(
            (prev_time - at) * (bucket_vals - prev_val)
            - (prev_time - bucket_times) * (av - prev_val)
        )
        best_idx = bs + int(np.argmax(areas))

        selected[i + 1] = best_idx
        prev_selected_idx = best_idx

    return selected
