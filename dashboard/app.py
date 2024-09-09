import pandas as pd
import os
import csv
import pathlib
from shiny import App, Inputs, Outputs, Session, reactive, ui, render
from shinywidgets import output_widget, render_plotly
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

#
def plot_timeseries(df):
    fig = px.line(
        df,
        x="time",
        y="temperature",
        # template="simple_white",
    ).update_layout(
        # title={"temperature"},
        yaxis_title="temperature",
        xaxis_title="Date time",
    )
    fig.update_yaxes(rangemode="tozero")
    return fig

def server(input: Inputs, output: Outputs, session: Session):

    @render_plotly
    def init_plot():
        return go.FigureWidget()

    @render.text
    @reactive.file_reader(SAVEPATH)
    def time():
        with open(SAVEPATH, 'r', newline='') as f:
            reader = csv.reader(f)
            content = [i for i in reader]
            return f"{content[0][0]}: {content[-1][0]}"

    @reactive.file_reader(SAVEPATH, interval_secs=)
    def fetch_data():
        df = pd.read_csv(SAVEPATH)
        return df
        
    @reactive.effect
    def update_plot():
        df = fetch_data()
        print("Updating plot")
        if not df.empty:
            with init_plot.widget.batch_animate():
                fig = plot_timeseries(df)
                init_plot.widget.update(layout=fig.layout, data=fig.data)

app_ui = ui.page_fluid(
    ui.card(
            ui.card_header("This is the header"),
            output_widget("init_plot")
            ), 
    ui.output_text("time", inline = True),
)

app = App(app_ui, server)