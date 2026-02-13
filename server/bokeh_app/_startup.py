"""
Shared startup code for the Bokeh dashboard app.

Handles logging setup, config loading, and the aggregator singleton.
Imported by both app_hooks.py (server startup) and main.py (per-session).
"""
import configparser
import logging
import logging.handlers
import pathlib
import sys
import threading

from server.api_client import MultiSensorClient
from server.data_aggregator import DataAggregator

# ── Paths ──────────────────────────────────────────────────────────────────────
_project_root = pathlib.Path(__file__).parent.parent.parent
CONFIG_PATH = _project_root / "server" / "config_server.ini"

# ── Logging (one-time setup) ──────────────────────────────────────────────────
# Guard: only configure logging once per process, even if this module is
# re-imported or the Bokeh app re-executes main.py for a new session.
if not hasattr(sys, '_temp_sensor_logging_configured'):
    log_dir = _project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "server.log"

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # On Windows, RotatingFileHandler fails to rename open log files when multiple
    # threads are logging simultaneously (WinError 32). Use a safe wrapper.
    class _SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
        """RotatingFileHandler that silently skips rotation on Windows file lock errors."""
        def doRollover(self):
            try:
                super().doRollover()
            except PermissionError:
                pass

    file_handler = _SafeRotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    setattr(sys, '_temp_sensor_logging_configured', True)

logger = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────
def load_server_config():
    """Load server configuration from config_server.ini."""
    config = configparser.ConfigParser()
    config.optionxform = str  # type: ignore[attr-defined]

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Server configuration not found at {CONFIG_PATH}")

    config.read(CONFIG_PATH)

    sensor_configs = []
    if "SENSORS" in config:
        for name, value in config["SENSORS"].items():
            parts = [p.strip() for p in value.split(',')]
            url = parts[0]
            poll_interval = int(parts[1]) if len(parts) > 1 else 30

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

    retention_config = {
        "hot_retention_days": int(config.get("RETENTION", "hot_retention_days", fallback="7")),
        "warm_retention_days": int(config.get("RETENTION", "warm_retention_days", fallback="90")),
        "warm_resolution_s": int(config.get("RETENTION", "warm_resolution_s", fallback="60")),
        "cold_resolution_s": int(config.get("RETENTION", "cold_resolution_s", fallback="900")),
    }

    return sensor_configs, poll_interval, min_gap_threshold, db_path, update_ms, max_plot_points, retention_config


# Load config once at import time
sensor_configs, poll_interval, min_gap_threshold, db_path, dashboard_update_ms, max_plot_points, retention_config = load_server_config()
logger.info(f"Loaded {len(sensor_configs)} sensor configurations")
logger.info(f"Max plot points: {max_plot_points:,}")
logger.info(f"Retention: hot={retention_config['hot_retention_days']}d, warm={retention_config['warm_retention_days']}d")


# ── Aggregator singleton ─────────────────────────────────────────────────────
# Store in sys module so it survives Bokeh's per-session module reloads.
if not hasattr(sys, '_temp_sensor_singletons'):
    setattr(sys, '_temp_sensor_singletons', {'lock': threading.Lock()})


def get_aggregator() -> DataAggregator:
    """Get or create the global aggregator instance (thread-safe singleton)."""
    store = getattr(sys, '_temp_sensor_singletons')

    existing = store.get('aggregator')
    if existing is not None:
        logger.debug("Reusing existing aggregator instance")
        return existing

    with store['lock']:
        existing = store.get('aggregator')
        if existing is not None:
            return existing

        logger.info("Creating global data aggregator instance...")
        multi_client = MultiSensorClient(sensor_configs)
        instance = DataAggregator(
            multi_client,
            sensor_configs=sensor_configs,
            db_path=db_path,
            poll_interval=poll_interval,
            min_gap_threshold=min_gap_threshold,
            retention_config=retention_config
        )

        # Start background polling (non-blocking — threads populate data asynchronously)
        instance.start_polling()
        logger.info("Started background polling threads")

        store['aggregator'] = instance
        return instance
