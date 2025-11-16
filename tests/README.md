# Testing Guide

This directory contains the test suite for the tempsens temperature monitoring system.

## Running Tests

### Run All Tests
```bash
python3 -m pytest tests/ -v
```

### Run with Coverage Report
```bash
python3 -m pytest tests/ --cov=tempsens --cov=server --cov-report=html
```

### Run Specific Test File
```bash
python3 -m pytest tests/test_io_funcs.py -v
python3 -m pytest tests/test_api_server.py -v
```

### Run Specific Test
```bash
python3 -m pytest tests/test_io_funcs.py::TestConfigManagement::test_gen_default_config -v
```

## Test Structure

### `test_io_funcs.py`
Tests for the core I/O functionality:
- **TestConfigManagement**: Configuration file creation, reading, and management
- **TestSimulatedSensor**: Simulated sensor data generation (works without hardware)
- **TestDatabaseOperations**: SQLite database operations (init, read, write, queries)

### `test_api_server.py`
Tests for the FastAPI REST API:
- **TestAPIEndpoints**: All API endpoints (`/`, `/status`, `/data/latest`, `/data/range`, `/config`)
- Tests use simulated data, no hardware required

## Test Coverage

Current test coverage includes:
- Configuration management (100%)
- Database operations (SQLite read/write)
- Simulated sensor data generation
- API endpoints and responses
- Error handling and edge cases

## Requirements

Tests can run on any platform (Windows, macOS, Linux) without hardware:
```bash
pip install pytest pytest-cov httpx pytest-asyncio
```

## Notes

- Tests use **simulated sensor data** - no Raspberry Pi or DHT22 sensor required
- Each test uses isolated temporary databases and config files
- Tests clean up after themselves (no leftover files)
- All API tests use FastAPI's test client (no actual HTTP server needed)
