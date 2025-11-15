
import subprocess
import atexit
import psutil
import os
import time

# Local imports
from . import io_funcs

os.environ['HDF5_USE_FILE_LOCKING'] = 'FALSE'

PID_FILE = 'tempsens_running.pid'

def run_tempsensor_test():
    # If file doesn't exist, create it
    io_funcs.init_data_hdf5()
    print("LOGGING INITIATED: tempsensor.py running.")
    # Schedule the initial run
    io_funcs.schedule.enter(io_funcs.LOGINTERVAL, 1, io_funcs.log_data)
    while True:
        io_funcs.schedule.run()

def end_tempsensor():
    print("Shutting down temperature sensor logging.")
    try:
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        if psutil.pid_exists(pid):
            print("PID file found, terminating process.")
            process = psutil.Process(pid)
            process.terminate()
        if os.path.isfile(PID_FILE):
            os.remove(PID_FILE)
            print("Removed PID file.")
    except FileNotFoundError:
        print("PID file not found, could not clean.")

atexit.register(end_tempsensor)

if __name__ == "__main__":
    run_tempsensor_test()