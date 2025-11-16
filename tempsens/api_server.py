"""
FastAPI server for Raspberry Pi temperature sensor clients.
Exposes data from local SQLite database via REST API for remote dashboard access.
"""
import math
import socket
from datetime import datetime
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

    return {
        "device_name": device_name,
        "ip_address": ip_address,
        "hostname": socket.gethostname(),
        "uptime_seconds": uptime_seconds,
        "sensor_type": "DHT22" if io_funcs.sensor_found else "simulated"
    }


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "message": "Temperature Sensor API",
        "version": "1.0.0",
        "endpoints": ["/status", "/data/latest", "/data/range", "/config"]
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
                "temperature_c": float(data["temperature"][0]),
                "humidity_pct": float(data["humidity"][0])
            }
        else:
            last_reading = None
    except Exception as e:
        last_reading = {"error": str(e)}

    return {
        **device_info,
        "last_reading": last_reading,
        "log_interval_s": float(io_funcs.CONFIG["DEFAULT"]["loginterval_s"])
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
        JSON with arrays of time (ISO format), temperature (°C), and humidity (%)
    """
    try:
        data = io_funcs.fetch_log_data_range(limit=limit)
        device_info = get_device_info()

        return {
            "device_name": device_info["device_name"],
            "device_ip": device_info["ip_address"],
            "data": {
                "time": data["time"],
                "temperature": [safe_float(t) for t in data["temperature"]],
                "humidity": [safe_float(h) for h in data["humidity"]]
            },
            "metadata": {
                "count": len(data["time"]),
                "log_interval_s": float(io_funcs.CONFIG["DEFAULT"]["loginterval_s"])
            }
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch data: {str(e)}"}
        )


@app.get("/data/range")
async def get_data_range(
    start: Optional[str] = Query(default=None, description="Start timestamp (ISO format: YYYY-MM-DD HH:MM:SS)"),
    end: Optional[str] = Query(default=None, description="End timestamp (ISO format: YYYY-MM-DD HH:MM:SS)")
):
    """
    Get readings within a specific time range.

    Args:
        start: Start timestamp in ISO format (optional)
        end: End timestamp in ISO format (optional)

    Returns:
        JSON with arrays of time (ISO format), temperature (°C), and humidity (%)
    """
    try:
        data = io_funcs.fetch_log_data_range(start_time=start, end_time=end)
        device_info = get_device_info()

        return {
            "device_name": device_info["device_name"],
            "device_ip": device_info["ip_address"],
            "data": {
                "time": data["time"],
                "temperature": [safe_float(t) for t in data["temperature"]],
                "humidity": [safe_float(h) for h in data["humidity"]]
            },
            "metadata": {
                "count": len(data["time"]),
                "start": start,
                "end": end,
                "log_interval_s": float(io_funcs.CONFIG["DEFAULT"]["loginterval_s"])
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

    return {
        "device_name": device_info["device_name"],
        "log_interval_s": float(config["DEFAULT"]["loginterval_s"]),
        "output_file": config["DEFAULT"]["outputfile"],
        "sensor_type": "DHT22" if io_funcs.sensor_found else "simulated"
    }


if __name__ == "__main__":
    import uvicorn

    config = io_funcs.fetch_config()
    api_port = int(config["DEFAULT"].get("api_port", 5000))

    print(f"Starting Temperature Sensor API on port {api_port}")
    uvicorn.run(app, host="0.0.0.0", port=api_port)
