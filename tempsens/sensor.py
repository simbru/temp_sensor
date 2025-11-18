
# Local imports
from . import io_funcs


def run_tempsensor_test():
    """Run the temperature sensor logging loop."""
    # Initialize SQLite database if it doesn't exist
    io_funcs.init_database()
    print("LOGGING INITIATED: tempsensor.py running.")
    # Take an immediate reading on startup (no delay)
    io_funcs.schedule.enter(0, 1, io_funcs.log_data)
    while True:
        io_funcs.schedule.run()

if __name__ == "__main__":
    run_tempsensor_test()