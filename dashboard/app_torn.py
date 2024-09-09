import pandas as pd
import os
import csv
import pathlib
from shiny import App, Inputs, Outputs, Session, reactive, ui
from shiny.express import input, render, ui
from shinywidgets import render_altair, render_widget, render_plotly
import plotly.express as px
import plotly.graph_objects as go
from shiny import reactive
import matplotlib.pyplot as plt
# Local imports
import io_funcs
import tempsensor 

# SHINYDIR = pathlib.Path(os.getcwd())
# TEMPSENSDIR = SHINYDIR.parent
# # os.chdir(TEMPSENSDIR)

CONFIG = io_funcs.fetch_config()
print(dict(CONFIG["DEFAULT"]))
SAVEPATH = pathlib.Path(CONFIG["DEFAULT"]["outputfile"])
print("Save path:", SAVEPATH)
print("Exists:", SAVEPATH.exists())

# @render_widget  
# def plot():  
#     fig = px.line(
#         data_frame= pd.read_csv(SAVEPATH),
#         x="time",
#         y="temperature",
#     )
#     return fig

def server():

    # Create an empty plotly figure on page load
    @render_plotly
    def plot():
        return go.FigureWidget() 


def plot_timeseries(d):
    fig = px.line(
        d,
        x="time",
        y="temperature",
        labels=dict(score="accuracy"),
        color="model",
        color_discrete_sequence=px.colors.qualitative.Set2,
        template="simple_white",
    ).update_layout(
        # title={"temperature"},
        yaxis_title="temperature",
        xaxis_title="Date time",
    )

    fig.update_yaxes(rangemode="tozero")
    
    return fig

# @render.plot
# @reactive.file_reader(SAVEPATH)
# def line():
#     tempsensor_df = pd.read_csv(SAVEPATH)
#     fig, ax = plt.subplots(1,1)
#     ax.plot(tempsensor_df['time'], tempsensor_df['temperature'])
#     ax.set_ylim(0)
#     return fig

@render.ui
@reactive.file_reader(SAVEPATH)
def time():
    with open(SAVEPATH, 'r', newline='') as f:
        reader = csv.reader(f)
        content = [i for i in reader]
        return f"{content[0][0]}: {content[-1][0]}"
    
app_ui = ui.page_fillable(ui.card())

app = App(app_ui, server)