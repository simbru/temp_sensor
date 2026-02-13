"""
Bokeh server lifecycle hooks.

on_server_loaded() runs once when 'bokeh serve' starts, BEFORE any browser
sessions connect. This is where we create the data aggregator and start
polling threads, so backfills begin immediately — not when someone opens
the dashboard.
"""
import pathlib
import sys

# Ensure project root is on path so 'server.*' imports work
_project_root = pathlib.Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from server.bokeh_app._startup import get_aggregator, logger


def on_server_loaded(server_context):
    """Called when the Bokeh server starts, before any sessions are created."""
    logger.info("Bokeh server loaded — initializing aggregator and polling threads...")
    aggregator = get_aggregator()
    logger.info(f"Aggregator ready, polling {len(aggregator._poll_threads)} sensors")


def on_server_unloaded(server_context):
    """Called when the Bokeh server shuts down."""
    logger.info("Bokeh server shutting down — stopping aggregator...")
    store = getattr(sys, '_temp_sensor_singletons', {})
    aggregator = store.get('aggregator')
    if aggregator is not None:
        aggregator.stop_polling()
        logger.info("Aggregator stopped")
