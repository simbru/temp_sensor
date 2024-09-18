
import time 
import datetime
import numpy as np
import pathlib
import h5py
import sched, time
# Local imports
import io_funcs

def run_tempsensor_test():
    print("Running tempsensor")
    # If file doesn't exist, create it
    io_funcs.init_data_hdf5()
    # Schedule the initial run
    io_funcs.schedule.enter(io_funcs.LOGINTERVAL, 0, io_funcs.log_data)
    io_funcs.schedule.enter(io_funcs.LOGINTERVAL, 1, io_funcs.fetch_log_data)
    # io_funcs.schedule.run()
    while True:
    #     # Run the scheduler -> this will keep the program running
        io_funcs.schedule.run(blocking=False)
        time.sleep(.1)

if __name__ == "__main__":
    run_tempsensor_test()