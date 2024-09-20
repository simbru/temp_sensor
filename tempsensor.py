
import subprocess
import atexit
import psutil
# Local imports
import io_funcs
import os
import time
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

PID_FILE = 'tempsens_running.pid'
# Launch tempsensor.py if not already launched
def tempsensor_subprocess():
    # If file doesn't exist, create it
    io_funcs.init_data_hdf5()
    # # If file doesn't exist, create it
    # io_funcs.init_data_hdf5()
    if os.path.isfile(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            # Check if process is running
            if psutil.pid_exists(pid):
                print("SUBPROCESS: Temperature sensor is already running.")
                return
            else:
                print("SUBPROCESS: Stale PID file. Starting a new temperature sensor.")
        except (OSError, ValueError):
            print("Invalid PID or no process found.")
    # Start the temperature sensor
    process = subprocess.Popen(["python", "tempsensor.py"])
    with open(PID_FILE, 'w') as f:
        f.write(str(process.pid))
    print("SUBPROCESS: Spinning up tempsensor.")
    return process

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