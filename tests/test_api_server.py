"""
Tests for API server endpoints.
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from tempsens import io_funcs
from tempsens.api_server import app


@pytest.fixture
def temp_db_for_api(monkeypatch):
    """Create a temporary database with test data for API testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    # Initialize database and add test data
    io_funcs.init_database(db_path)
    for i in range(10):
        timestamp = f"2023-11-16 12:00:{i:02d}.000000"
        io_funcs.write_data(timestamp, 20.0 + i, 50.0 + i, db_path)
    
    # Use monkeypatch to override FILENAME in io_funcs module
    monkeypatch.setattr(io_funcs, 'FILENAME', db_path)
    monkeypatch.setitem(io_funcs.CONFIG["DEFAULT"], "outputfile", db_path)
    
    yield db_path
    
    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.mark.asyncio
class TestAPIEndpoints:
    """Test FastAPI endpoints."""
    
    async def test_root_endpoint(self):
        """Test root endpoint returns API information."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/")
        
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "version" in data
        assert "endpoints" in data
    
    async def test_status_endpoint(self, temp_db_for_api):
        """Test status endpoint returns device information."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/status")
        
        assert response.status_code == 200
        data = response.json()
        assert "device_name" in data
        assert "sensor_type" in data
        assert "last_reading" in data
        assert "log_interval_s" in data
    
    async def test_latest_data_endpoint(self, temp_db_for_api):
        """Test getting latest data from API."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/data/latest?limit=5")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "device_name" in data
        assert "data" in data
        assert "metadata" in data
        
        # Check data structure
        assert "time" in data["data"]
        assert "temperature" in data["data"]
        assert "humidity" in data["data"]
        
        # Should return 5 readings
        assert data["metadata"]["count"] == 5
        assert len(data["data"]["time"]) == 5
    
    async def test_latest_data_default_limit(self, temp_db_for_api):
        """Test latest data endpoint with default limit."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/data/latest")
        
        assert response.status_code == 200
        data = response.json()
        
        # Should return all available (10) readings since default is 100
        assert data["metadata"]["count"] == 10
    
    async def test_range_endpoint(self, temp_db_for_api):
        """Test data range endpoint with time filters."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/data/range?start=2023-11-16%2012:00:03.000000&end=2023-11-16%2012:00:06.000000"
            )
        
        assert response.status_code == 200
        data = response.json()
        
        # Should get readings at 03, 04, 05, 06 (4 readings)
        assert data["metadata"]["count"] == 4
        assert len(data["data"]["time"]) == 4
    
    async def test_config_endpoint(self, temp_db_for_api):
        """Test config endpoint returns configuration."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/config")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "device_name" in data
        assert "log_interval_s" in data
        assert "output_file" in data
        assert "sensor_type" in data
    
    async def test_safe_float_function(self):
        """Test safe_float utility function handles special values."""
        from tempsens.api_server import safe_float
        import math
        
        # Normal values
        assert safe_float(1.5) == 1.5
        assert safe_float(0) == 0.0
        
        # Special values should return None
        assert safe_float(None) is None
        assert safe_float(float('nan')) is None
        assert safe_float(float('inf')) is None
        assert safe_float(float('-inf')) is None
