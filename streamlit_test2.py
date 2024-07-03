import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import time

# Initial chart data
chart_data = pd.DataFrame(np.random.randn(100, 1), columns=["a"])

# Create initial line chart
chart = st.line_chart(chart_data[-50:])  # Initialize with the last 50 rows

# Create initial Matplotlib plot
fig, ax = plt.subplots()
line, = ax.plot(chart_data.index, chart_data['a'])
placeholder = st.empty()
placeholder.pyplot(fig)

# Function to update Matplotlib plot with a sliding window of 50
def update_plot(new_data, window_size=50):
    line.set_xdata(new_data.index)
    line.set_ydata(new_data['a'])
    ax.relim()
    ax.autoscale_view()
    # Set x-axis limits to create a sliding window effect
    if len(new_data) > window_size:
        ax.set_xlim(len(new_data) - window_size, len(new_data))
    else:
        ax.set_xlim(0, window_size)

# Loop to update both Streamlit chart and Matplotlib plot
window_size = 50
for tick in range(100):
    add_df = pd.DataFrame(np.random.randn(1, 1), columns=["a"])
    chart_data = pd.concat([chart_data, add_df], ignore_index=True)
    # Update Streamlit line chart
    chart.line_chart(chart_data[-window_size:])  # Only pass the last 50 rows
    # Update Matplotlib plot
    update_plot(chart_data, window_size=window_size)
    placeholder.pyplot(fig)
    # Pause for a second
    time.sleep(1)
