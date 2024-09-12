import pandas as pd
import os
import csv
import pathlib
from shiny import App, Inputs, Outputs, Session, reactive, ui, render
from shinywidgets import output_widget, render_plotly
import plotly.express as px
import plotly.graph_objects as go
from shiny import reactive
import subprocess


# Local imports
import io_funcs
import tempsensor
import datetime

# SHINYDIR = pathlib.Path(os.getcwd())
# TEMPSENSDIR = SHINYDIR.parent
# # os.chdir(TEMPSENSDIR)

CONFIG = io_funcs.fetch_config()
print(dict(CONFIG["DEFAULT"]))
SAVEPATH = pathlib.Path(CONFIG["DEFAULT"]["outputfile"])
print("Save path:", SAVEPATH)
print("Exists:", SAVEPATH.exists())

#
def plot_temp_timeseries(df, window_size=10, leading_s=20, update_view=True):
    # Ensure 'time' is in datetime format
    df['time'] = pd.to_datetime(df['time'])

    fig = px.line(
        df,
        x="time",
        y="temperature",
    ).update_layout(
        yaxis_title="Temperature",
        xaxis_title="Date time",
    )
    fig.update_yaxes(rangemode="tozero")
    if update_view:
        #Get the max time and set that as the focus point of scrolling
        max_time = df['time'].max() + pd.to_timedelta(leading_s, unit='s')
        min_time = max_time - pd.Timedelta(minutes=window_size)
        fig.update_xaxes(range=[min_time, max_time])
    return fig

def plot_hume_timeseries(df, window_size=10, leading_s=20, 
        yspan_percent = .10, ymax_recency_h = 6, update_view=True):
    fig = px.line(
        df,
        x="time",
        y="humidity",
    ).update_layout(
        yaxis_title="Humiditiy",
        xaxis_title="Date time",
    )
    # max_hume_recent = df['humidity'].max()
    # # Calculate the y-axis range
    # fig.update_yaxes(range = )
    # Calculate the x-axis range
    if update_view is True:
        #Get the max time and set that as the focus point of scrolling
        max_time = df['time'].max() + pd.to_timedelta(leading_s, unit='s')
        min_time = max_time - pd.Timedelta(minutes=window_size)
        fig.update_xaxes(range=[min_time, max_time])
    return fig

def server(input: Inputs, output: Outputs, session: Session):

    auto_scroll = reactive.Value(True) 

    @render_plotly
    def init_temp_plot():
        return go.FigureWidget()

    @render_plotly
    def init_hum_plot():
        return go.FigureWidget()

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

    @reactive.file_reader(SAVEPATH, interval_secs=2)
    def fetch_data():
        # Force pandas to re-read the file without caching
        df = pd.read_csv(SAVEPATH, engine='python')
        return df
        
    @reactive.effect
    def update_temp_plot():
        df = fetch_data()
        #Generate figure
        fig = plot_temp_timeseries(df, update_view=auto_scroll.get())
        if auto_scroll.get():
            with init_temp_plot.widget.batch_animate():
                #Update the plot layout and its data
                init_temp_plot.widget.update(layout=fig.layout, data=fig.data)
        else:
            init_temp_plot.widget.update(data=fig.data)
    
    @reactive.effect
    def update_hum_plot():
        df = fetch_data()
        #Generate figure
        fig = plot_hume_timeseries(df, update_view=auto_scroll.get())
        if auto_scroll.get():
            with init_hum_plot.widget.batch_animate():
                #Update the plot layout and its data
                init_hum_plot.widget.update(layout=fig.layout, data=fig.data)
        else:
            init_hum_plot.widget.update(data=fig.data)

    @reactive.Effect
    @reactive.event(input.pause_scroll)
    def pause_scroll():
        auto_scroll.set(False)  # Stop auto-scrolling when the button is pressed

    @reactive.Effect
    @reactive.event(input.resume_scroll)
    def resume_scroll():
        auto_scroll.set(True)  # Resume auto-scrolling when the button is pressed

    
app_ui = ui.page_fluid(
    ui.card(
            ui.card_header(f"Temperature over time ({SAVEPATH})"),
            output_widget("init_temp_plot")
            ),
    ui.card(
        ui.card_header(f"Humiditiy over time ({SAVEPATH})"),
        output_widget("init_hum_plot")
        ), 
    ui.output_text("time", inline = True),
    ui.input_action_button("pause_scroll", "Pause Auto-Scroll"),
    ui.input_action_button("resume_scroll", "Resume Auto-Scroll")
)

subprocess.Popen(["python", "tempsensor.py"], )
app = App(app_ui, server)