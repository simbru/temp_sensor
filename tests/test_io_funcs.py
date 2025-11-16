"""
Tests for io_funcs module - configuration and data management.
"""
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from tempsens import io_funcs


@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False) as f:
        config_path = f.name
    yield config_path
    # Cleanup
    if os.path.exists(config_path):
        os.remove(config_path)


@pytest.fixture
def temp_db_file():
    """Create a temporary database file for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)


class TestConfigManagement:
    """Test configuration file operations."""
    
    def test_gen_default_config(self, temp_config_file):
        """Test generating default config file."""
        config = io_funcs.gen_default_config(temp_config_file, force=True)
        
        assert config is not None
        assert "DEFAULT" in config
        assert config["DEFAULT"]["loginterval_s"] == "2"
        assert config["DEFAULT"]["device_name"] == "Temperature Sensor"
        assert Path(temp_config_file).exists()
    
    def test_fetch_config_creates_if_missing(self, temp_config_file):
        """Test that fetch_config creates config if it doesn't exist."""
        # Remove file if it exists
        if os.path.exists(temp_config_file):
            os.remove(temp_config_file)
        
        config = io_funcs.fetch_config(temp_config_file)
        
        assert config is not None
        assert Path(temp_config_file).exists()
        assert "DEFAULT" in config
    
    def test_fetch_config_reads_existing(self, temp_config_file):
        """Test reading an existing config file."""
        # Create a config first
        io_funcs.gen_default_config(temp_config_file, force=True)
        
        config = io_funcs.fetch_config(temp_config_file)
        
        assert config is not None
        assert config["DEFAULT"]["device_name"] == "Temperature Sensor"
    
    def test_config_has_all_defaults(self, temp_config_file):
        """Test that all default values are present in config."""
        config = io_funcs.fetch_config(temp_config_file)
        
        for key in io_funcs.DEFAULT_CONFIG_VALUES:
            assert key in config["DEFAULT"]


class TestSimulatedSensor:
    """Test simulated sensor data generation."""
    
    def test_simulate_tempsens_returns_values(self):
        """Test that simulated sensor returns temperature and humidity."""
        temp, hum = io_funcs.simulate_tempsens()
        
        # Check return types
        assert isinstance(temp, (int, float)) or temp is None
        assert isinstance(hum, (int, float)) or hum is None
    
    def test_simulate_tempsens_baseline_params(self):
        """Test simulated sensor with custom baseline parameters."""
        temp, hum = io_funcs.simulate_tempsens(
            tempbaseline=25, 
            tempvar=2, 
            humbaseline=60, 
            humvar=3
        )
        
        # Values should be in expected ranges when not None
        if temp is not None:
            assert 23 <= temp <= 29  # 25 ± 4 (generous range)
        if hum is not None:
            assert 57 <= hum <= 66  # 60 ± 6 (generous range)


class TestDatabaseOperations:
    """Test database reading/writing operations."""
    
    def test_init_database_creates_table(self, temp_db_file):
        """Test that database initialization creates proper table structure."""
        io_funcs.init_database(temp_db_file)
        
        conn = sqlite3.connect(temp_db_file)
        cursor = conn.cursor()
        
        # Check table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sensor_data'")
        assert cursor.fetchone() is not None
        
        # Check table structure
        cursor.execute("PRAGMA table_info(sensor_data)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        
        assert 'timestamp' in columns
        assert 'temperature' in columns
        assert 'humidity' in columns
        
        conn.close()
    
    def test_write_and_read_data(self, temp_db_file):
        """Test writing and reading data from database."""
        io_funcs.init_database(temp_db_file)
        
        # Write test data
        test_timestamp = "2023-11-16 12:00:00.000000"
        test_temp = 22.5
        test_hum = 55.0
        io_funcs.write_data(test_timestamp, test_temp, test_hum, temp_db_file)
        
        # Read data back
        data = io_funcs.fetch_log_data_range(limit=1, filename=temp_db_file)
        
        assert len(data["time"]) == 1
        assert len(data["temperature"]) == 1
        assert len(data["humidity"]) == 1
        assert abs(data["temperature"][0] - test_temp) < 0.01
        assert abs(data["humidity"][0] - test_hum) < 0.01
    
    def test_fetch_log_data_range_with_limit(self, temp_db_file):
        """Test fetching limited number of records."""
        io_funcs.init_database(temp_db_file)
        
        # Write multiple records
        for i in range(10):
            timestamp = f"2023-11-16 12:00:{i:02d}.000000"
            io_funcs.write_data(timestamp, 20.0 + i, 50.0 + i, temp_db_file)
        
        # Fetch with limit
        data = io_funcs.fetch_log_data_range(limit=5, filename=temp_db_file)
        
        assert len(data["time"]) == 5
        assert len(data["temperature"]) == 5
        assert len(data["humidity"]) == 5
    
    def test_fetch_log_data_range_with_time_filter(self, temp_db_file):
        """Test fetching data within a time range."""
        io_funcs.init_database(temp_db_file)
        
        # Write multiple records across time
        for i in range(10):
            timestamp = f"2023-11-16 12:{i:02d}:00.000000"
            io_funcs.write_data(timestamp, 20.0 + i, 50.0 + i, temp_db_file)
        
        # Fetch with time range
        data = io_funcs.fetch_log_data_range(
            filename=temp_db_file,
            start_time="2023-11-16 12:03:00.000000",
            end_time="2023-11-16 12:06:00.000000"
        )
        
        # Should get readings at 03, 04, 05, 06 (4 readings)
        assert len(data["time"]) == 4
        assert data["temperature"][0] == 23.0  # 20 + 3
        assert data["temperature"][-1] == 26.0  # 20 + 6

