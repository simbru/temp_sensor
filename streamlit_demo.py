import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import time

# Initial chart data
chart_data = pd.DataFrame(np.random.randn(100, 1), columns=["a"])

# Create initial line chart
chart = st.line_chart(chart_data)

# Create initial Matplotlib plot
fig, ax = plt.subplots()

# Loop to update both Streamlit chart and Matplotlib plot 
for tick in range(100):
    add_df = pd.DataFrame(np.random.randn(1, 1), columns=(["a"]))
    chart_data = pd.concat([chart_data, add_df], ignore_index=True)
    # Update Streamlit line chart
    chart.add_rows(add_df)
    # Pause for a second
    time.sleep(1)