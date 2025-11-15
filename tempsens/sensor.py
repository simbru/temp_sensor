
import os

# Local imports
from . import io_funcs

os.environ['HDF5_USE_FILE_LOCKING'] = 'FALSE'

def run_tempsensor_test():
    """Run the temperature sensor logging loop."""
    # Initialize HDF5 file if it doesn't exist
    io_funcs.init_data_hdf5()
    print("LOGGING INITIATED: tempsensor.py running.")
    # Schedule the initial run
    io_funcs.schedule.enter(io_funcs.LOGINTERVAL, 1, io_funcs.log_data)
    while True:
        io_funcs.schedule.run()

if __name__ == "__main__":
    run_tempsensor_test()