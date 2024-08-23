import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import time
import altair 

# Initial chart data


# Create altair line chart
chart_row = st.empty()

def gen_temp(baseline = 20, variance = 5, seed = 42):
    return baseline + np.random.randint(variance)

# Loop to update both Streamlit chart and Matplotlib plot 
chart_data = pd.DataFrame(np.ma.masked_array(data = [0], mask = False), columns=["a"])
chart_data["index"] = chart_data.index
chart = altair.Chart(chart_data).mark_line().encode(
    x=altair.X("index"), 
    y=altair.Y("a").scale(bins = [0, 5, 10, 15, 20])).interactive()

chart.configure_range(y = [10, chart_data["index"].max()])
chart_row.altair_chart(chart, use_container_width=True, on_select="ignore")

runme = True
i = 0
while runme is True:
    i += 1
    new_data = pd.DataFrame([gen_temp()], columns=["a"])
    new_data["index"] = len(chart_data) + i
    chart_data = pd.concat([chart_data, new_data], ignore_index=True, copy = False)
    chart_row.add_rows(chart_data)
    
    time.sleep(1)