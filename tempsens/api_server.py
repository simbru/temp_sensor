"""
FastAPI server for Raspberry Pi temperature sensor clients.
Exposes data from local SQLite database via REST API for remote dashboard access.
"""
import math
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import psutil
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from . import io_funcs

app = FastAPI(
    title="Temperature Sensor API",
    description="API for accessing DHT22 temperature and humidity data",
    version="1.0.0"
)


def safe_float(val):
    """Convert to float and replace NaN/Inf with None for JSON compatibility."""
    if val is None:
        return None
    f = float(val)
    return None if math.isnan(f) or math.isinf(f) else f


# Metrics caching to reduce overhead
_metrics_cache = {
    "data": None,
    "timestamp": 0,
    "cache_duration": 10  # Cache for 10 seconds
}


def get_device_info():
    """Get device name, IP address, and system info."""
    config = io_funcs.fetch_config()
    device_name = config["DEFAULT"].get("device_name", socket.gethostname())

    # Get local IP address
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
        s.close()
    except Exception:
        ip_address = "unknown"

    # Get system uptime
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime_seconds = (datetime.now() - boot_time).total_seconds()

    # Get sensor type from config and actual detected sensor
    config = io_funcs.fetch_config()
    sensor_type_config = config["DEFAULT"].get("sensor_type", "AUTO")

    # Get actual sensor name from the running sensor instance
    sensor = io_funcs._get_sensor()
    sensor_type_actual = sensor.name

    # Format as "config | actual" if config is AUTO, otherwise just show the actual sensor
    if sensor_type_config.upper() == "AUTO":
        sensor_type = f"{sensor_type_config} | {sensor_type_actual}"
    else:
        sensor_type = sensor_type_actual

    return {
        "device_name": device_name,
        "ip_address": ip_address,
        "hostname": socket.gethostname(),
        "uptime_seconds": uptime_seconds,
        "sensor_type": sensor_type
    }


def get_system_metrics():
    """
    Get system metrics (CPU, memory, database size).
    Results are cached for 10 seconds to reduce overhead.
    """
    current_time = time.time()

    # Return cached data if still fresh
    if (_metrics_cache["data"] is not None and
        current_time - _metrics_cache["timestamp"] < _metrics_cache["cache_duration"]):
        return _metrics_cache["data"]

    # Get CPU and memory usage
    cpu_percent = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory()
    memory_percent = memory.percent

    # Get database size
    config = io_funcs.fetch_config()
    db_path = Path(config["DEFAULT"].get("database_path", "templog.db"))

    try:
        db_size_bytes = db_path.stat().st_size
        db_size_mb = db_size_bytes / (1024 * 1024)
    except Exception:
        db_size_mb = 0.0

    # Get record count from database
    try:
        import sqlite3
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sensor_data")
            record_count = cursor.fetchone()[0]
    except Exception:
        record_count = 0

    # Get sensor type from config and actual detected sensor
    config = io_funcs.fetch_config()
    sensor_type_config = config["DEFAULT"].get("sensor_type", "AUTO")

    # Get actual sensor name from the running sensor instance
    sensor = io_funcs._get_sensor()
    sensor_type_actual = sensor.name

    # Format as "config | actual" if config is AUTO, otherwise just show the actual sensor
    if sensor_type_config.upper() == "AUTO":
        sensor_type = f"{sensor_type_config} | {sensor_type_actual}"
    else:
        sensor_type = sensor_type_actual

    metrics = {
        "cpu_percent": round(cpu_percent, 1),
        "memory_percent": round(memory_percent, 1),
        "database_size_mb": round(db_size_mb, 2),
        "total_records": record_count,
        "sensor_type": sensor_type
    }

    # Update cache
    _metrics_cache["data"] = metrics
    _metrics_cache["timestamp"] = current_time

    return metrics


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "message": "Temperature Sensor API",
        "version": "1.0.0",
        "endpoints": ["/status", "/data/latest", "/data/range", "/config", "/metrics"]
    }


@app.get("/status")
async def get_status():
    """
    Get current device status, including device info and latest reading.
    """
    device_info = get_device_info()

    # Get latest reading
    try:
        data = io_funcs.fetch_log_data_range(limit=1)
        if data and len(data["time"]) > 0:
            last_reading = {
                "timestamp": data["time"][0],
                "temperature_c": safe_float(data["temperature"][0]),
                "humidity_pct": safe_float(data["humidity"][0]),
            }
            # Add extended sensor data if available
            if data["pressure"][0] is not None:
                last_reading["pressure_hpa"] = safe_float(data["pressure"][0])
            if data["light"][0] is not None:
                last_reading["light_lux"] = safe_float(data["light"][0])
            if data["noise"][0] is not None:
                last_reading["noise_dba"] = safe_float(data["noise"][0])
        else:
            last_reading = None
    except Exception as e:
        last_reading = {"error": str(e)}

    # Check for hardware failure
    consecutive_failures = io_funcs._get_consecutive_failures()
    hardware_status = "ok"
    if consecutive_failures >= io_funcs.HARDWARE_FAILURE_THRESHOLD:
        hardware_status = "hardware_failure"
    elif consecutive_failures > 0:
        hardware_status = f"degraded ({consecutive_failures} failed reads)"

    # Get available sensors from sensor instance
    sensor = io_funcs._get_sensor()
    available_sensors = ["temperature", "humidity"]  # All sensors have these
    if hasattr(sensor, 'available_sensors'):
        available_sensors = sensor.available_sensors

    return {
        **device_info,
        "last_reading": last_reading,
        "log_interval_s": float(io_funcs.CONFIG["DEFAULT"]["loginterval_s"]),
        "hardware_status": hardware_status,
        "consecutive_failures": consecutive_failures,
        "available_sensors": available_sensors
    }


@app.get("/data/latest")
async def get_latest_data(
    limit: int = Query(default=100, ge=1, le=50000, description="Number of most recent readings to return")
):
    """
    Get the most recent N readings from the sensor.

    Args:
        limit: Number of readings to return (1-50000, default 100)

    Returns:
        JSON with arrays of time (INTEGER milliseconds since epoch), temperature (°C), humidity (%),
        and optionally pressure (hPa), light (lux), noise (dBA) if available from sensor
    """
    try:
        data = io_funcs.fetch_log_data_range(limit=limit)
        device_info = get_device_info()

        # Build response with available sensor data
        response_data = {
            "time": data["time"],
            "temperature": [safe_float(t) for t in data["temperature"]],
            "humidity": [safe_float(h) for h in data["humidity"]]
        }

        # Add extended sensor data if present (filter out all-None arrays)
        if any(p is not None for p in data["pressure"]):
            response_data["pressure"] = [safe_float(p) for p in data["pressure"]]
        if any(light_val is not None for light_val in data["light"]):
            response_data["light"] = [safe_float(light_val) for light_val in data["light"]]
        if any(n is not None for n in data["noise"]):
            response_data["noise"] = [safe_float(n) for n in data["noise"]]

        return {
            "device_name": device_info["device_name"],
            "device_ip": device_info["ip_address"],
            "data": response_data,
            "metadata": {
                "count": len(data["time"]),
                "log_interval_s": float(io_funcs.CONFIG["DEFAULT"]["loginterval_s"]),
                "available_sensors": list(response_data.keys())
            }
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch data: {str(e)}"}
        )


@app.get("/data/range")
async def get_data_range(
    start: Optional[int] = Query(default=None, description="Start timestamp (milliseconds since epoch)"),
    end: Optional[int] = Query(default=None, description="End timestamp (milliseconds since epoch)"),
    limit: Optional[int] = Query(default=None, ge=1, le=50000, description="Optional cap on returned rows")
):
    """
    Get readings within a specific time range.

    Args:
        start: Start timestamp as INTEGER milliseconds since epoch (optional)
        end: End timestamp as INTEGER milliseconds since epoch (optional)
        limit: Maximum number of readings to return (optional)

    Returns:
        JSON with arrays of time (INTEGER milliseconds since epoch), temperature (°C), humidity (%),
        and optionally pressure (hPa), light (lux), noise (dBA) if available from sensor
    """
    try:
        data = io_funcs.fetch_log_data_range(start_time=start, end_time=end, limit=limit)
        device_info = get_device_info()

        # Build response with available sensor data
        response_data = {
            "time": data["time"],
            "temperature": [safe_float(t) for t in data["temperature"]],
            "humidity": [safe_float(h) for h in data["humidity"]]
        }

        # Add extended sensor data if present (filter out all-None arrays)
        if any(p is not None for p in data["pressure"]):
            response_data["pressure"] = [safe_float(p) for p in data["pressure"]]
        if any(light_val is not None for light_val in data["light"]):
            response_data["light"] = [safe_float(light_val) for light_val in data["light"]]
        if any(n is not None for n in data["noise"]):
            response_data["noise"] = [safe_float(n) for n in data["noise"]]

        return {
            "device_name": device_info["device_name"],
            "device_ip": device_info["ip_address"],
            "data": response_data,
            "metadata": {
                "count": len(data["time"]),
                "start": start,
                "end": end,
                "log_interval_s": float(io_funcs.CONFIG["DEFAULT"]["loginterval_s"]),
                "limit": limit,
                "available_sensors": list(response_data.keys())
            }
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch data: {str(e)}"}
        )


@app.get("/config")
async def get_config():
    """
    Get current configuration parameters.
    """
    config = io_funcs.fetch_config()
    device_info = get_device_info()

    # Get sensor type from config and actual detected sensor
    sensor_type_config = config["DEFAULT"].get("sensor_type", "AUTO")

    # Get actual sensor name from the running sensor instance
    sensor = io_funcs._get_sensor()
    sensor_type_actual = sensor.name

    # Format as "config | actual" if config is AUTO, otherwise just show the actual sensor
    if sensor_type_config.upper() == "AUTO":
        sensor_type = f"{sensor_type_config} | {sensor_type_actual}"
    else:
        sensor_type = sensor_type_actual

    return {
        "device_name": device_info["device_name"],
        "log_interval_s": float(config["DEFAULT"]["loginterval_s"]),
        "output_file": config["DEFAULT"]["outputfile"],
        "sensor_type": sensor_type
    }


@app.get("/metrics")
async def get_metrics():
    """
    Get system metrics including CPU usage, memory usage, and database size.
    Results are cached for 10 seconds to reduce overhead on the Raspberry Pi.
    """
    try:
        metrics = get_system_metrics()
        return metrics
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch metrics: {str(e)}"}
        )


if __name__ == "__main__":
    import uvicorn

    config = io_funcs.fetch_config()
    api_port = int(config["DEFAULT"].get("api_port", 5000))

    print(f"Starting Temperature Sensor API on port {api_port}")
    uvicorn.run(app, host="0.0.0.0", port=api_port)
