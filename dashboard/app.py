import pathlib
from shiny import reactive
from shiny.express import input, ui, render
from shinywidgets import render_bokeh
from bokeh.plotting import figure, show
from bokeh.models import ColumnDataSource, RangeTool
# os.environ['HDF5_USE_FILE_LOCKING'] = 'True'
# Local imports
import io_funcs
import tempsensor 

temp_sensor_process = tempsensor.tempsensor_subprocess()

CONFIG = io_funcs.fetch_config()
RENDER_INTERVAL = float(CONFIG["DEFAULT"]["loginterval_s"])
SAVEPATH = pathlib.Path(CONFIG["DEFAULT"]["outputfile"])

with ui.card():

    # Fetch initial data from H5 file 
    source = ColumnDataSource(data=io_funcs.fetch_log_data())
    # Create initial plot with immediate data
    p = figure(title = "Test", x_axis_label = "Time", y_axis_label = "Temperature",
            x_axis_type = "datetime", toolbar_location="below")

    plot_line = p.line(x = "time", y = "temperature", source = source, color = "red")

    # select = figure(title="Drag the middle and edges of the selection box to change the range above",
    #             height=130, width=800, y_range=p.y_range,
    #             x_axis_type="datetime", y_axis_type=None,
    #             tools="", toolbar_location=None, background_fill_color="#efefef")

    # range_tool = RangeTool(x_range=p.x_range)

    # TODO: https://docs.bokeh.org/en/latest/docs/user_guide/topics/timeseries.html#range-tool

    # select.line('date', 'close', source=source)
    # select.ygrid.grid_line_color = None
    # select.add_tools(range_tool)

    @render_bokeh
    def init_plot():
        return p

    @render_bokeh
    @reactive.file_reader(SAVEPATH, interval_secs=RENDER_INTERVAL)
    def update_plot():
        source.data = io_funcs.fetch_log_data()
        # figure.x_range = (source.data["time"][-100], source.data["time"][-1])

    @render.text
    @reactive.file_reader(SAVEPATH, interval_secs=RENDER_INTERVAL)
    def display_time():
        curr_datetime = io_funcs.fetch_log_data()["time"][-1].astype(str)
        date = curr_datetime[:10]
        time = curr_datetime[11:-4]
        return f"Time: {time} Date: {date}"
