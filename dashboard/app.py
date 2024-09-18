import h5py
import os
import csv
import pathlib
from shiny import reactive
import subprocess
from shiny.express import input, ui, render
from shinywidgets import render_bokeh
from bokeh.plotting import figure, show
from bokeh.models import ColumnDataSource
import time
import datetime
import atexit

# Local imports
import io_funcs

# # Launch tempsensor.py if not already launched
# PID_FILE = 'tempsens_running.pid'
# def start_temp_sensor():
#     # Check if the PID file exists
#     if os.path.isfile(PID_FILE):
#         try:
#             with open(PID_FILE, 'r') as f:
#                 pid = int(f.read().strip())
#             # Check if the process is running
#             os.kill(pid, 0)  # This will not actually kill the process, just check if it's running
#             print("Temperature sensor is already running.")
#             return
#         except (OSError, ValueError):
#             # OSError means the process is not running, ValueError means bad PID
#             print("Stale PID file. Starting a new temperature sensor.")
#     # Start the temperature sensor
#     process = subprocess.Popen(["python", "tempsensor.py"])
#     with open(PID_FILE, 'w') as f:
#         f.write(str(process.pid))
#     print("Temperature sensor started.")
#     return process

# temp_sensor_process = start_temp_sensor()
CONFIG = io_funcs.fetch_config()
RENDER_INTERVAL = int(CONFIG["DEFAULT"]["loginterval_s"]) + 1
SAVEPATH = pathlib.Path(CONFIG["DEFAULT"]["outputfile"])

with ui.card():
    # Create initial plot with immediate data
    p = figure(title = "Test", x_axis_label = "Time", y_axis_label = "Temperature",
            x_axis_type = "datetime",)
    # Fetch the data from H5 file 
    source = ColumnDataSource(data=io_funcs.fetch_log_data())
    plot_line = p.line(x = "time", y = "temperature", source = source, color = "red")

    @render_bokeh
    def init_plot():
        return p

    @render_bokeh
    @reactive.file_reader(SAVEPATH, interval_secs=RENDER_INTERVAL)
    def update_plot():
        source.data = io_funcs.fetch_log_data()

    # @render.text
    # @reactive.file_reader(SAVEPATH)
    # def time():
    #     with open(SAVEPATH, 'r', newline='') as f:
    #         reader = csv.reader(f)
    #         content = [i for i in reader]
    #         curr_string = content[-1][0]
    #         parsed_date = datetime.datetime.strptime(curr_string, '%Y-%m-%d %H:%M:%S.%f')
    #         formatted_date = parsed_date.strftime('%B %d, %Y at %I:%M:%S %p')
    #         return f"Time: {formatted_date}"

# def cleanup():
#     temp_sensor_process.wait()
#     temp_sensor_process.terminate()
#     if os.path.isfile(PID_FILE):
#         os.remove(PID_FILE)

# atexit.register(cleanup)