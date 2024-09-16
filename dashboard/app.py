import pandas as pd
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
# Local imports
import io_funcs
import tempsensor
import datetime

_ = subprocess.Popen(["python", "tempsensor.py"]) #assign to _ to mitigate Shiny express type error 
time.sleep(2) # give time for tempsensor to spin up

CONFIG = io_funcs.fetch_config()
print(dict(CONFIG["DEFAULT"]))
SAVEPATH = pathlib.Path(CONFIG["DEFAULT"]["outputfile"])
print("Save path:", SAVEPATH)
print("Exists:", SAVEPATH.exists())

with ui.card():
    @reactive.file_reader(SAVEPATH, interval_secs=2)
    def fetch_data():
        # Force pandas to re-read the file without caching
        df = pd.read_csv(SAVEPATH, engine='python')
        return df

    p = figure(title = "Test", x_axis_label = "Time", y_axis_label = "Temperature",
            x_axis_type = "datetime",)
    df = pd.read_csv(SAVEPATH, engine='python') # first read the file once to get start state
    df["time"] = pd.to_datetime(df["time"])
    source = ColumnDataSource(data=df)
    # temperature, time = df["temperature"], df["time"]
    plot_line = p.line(x = "time", y = "temperature", source = source, color = "red")

    @render_bokeh
    def init_plot():
        return p

    @render_bokeh
    @reactive.file_reader(SAVEPATH, interval_secs=2)
    def update_plot():
        df = pd.read_csv(SAVEPATH, engine='python')
        temperature, time = df["temperature"], df["time"]
        df["time"] = pd.to_datetime(df["time"])
        source.data = df

    @render.text
    @reactive.file_reader(SAVEPATH)
    def time():
        with open(SAVEPATH, 'r', newline='') as f:
            reader = csv.reader(f)
            content = [i for i in reader]
            curr_string = content[-1][0]
            parsed_date = datetime.datetime.strptime(curr_string, '%Y-%m-%d %H:%M:%S.%f')
            formatted_date = parsed_date.strftime('%B %d, %Y at %I:%M:%S %p')
            return f"Time: {formatted_date}"