import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objs as go
import time

# Function to generate random temperature data
def gen_temp(baseline=20, variance=5):
    return baseline + np.random.randint(-variance, variance)

# Initialize the session state if not already initialized
if 'chart_data' not in st.session_state:
    st.session_state.chart_data = pd.DataFrame({"index": [0], "a": [gen_temp()]})
    st.session_state.fig = go.Figure()
    st.session_state.fig.add_trace(go.Scatter(x=st.session_state.chart_data['index'], 
                                              y=st.session_state.chart_data['a'], 
                                              mode='lines', name='Temperature'))
    st.session_state.fig.update_layout(
        xaxis_title="Index",
        yaxis_title="Temperature",
        xaxis=dict(range=[0, 20]),  # Initial range for x-axis
        yaxis=dict(range=[15, 25])  # Initial range for y-axis
    )

# Display the figure in Streamlit (only once)
st.plotly_chart(st.session_state.fig, use_container_width=True)

# Main loop to update the chart
i = st.session_state.chart_data['index'].iloc[-1] + 1

while True:
    # Generate new data point
    new_data = pd.DataFrame({"index": [i], "a": [gen_temp()]})
    
    # Update session state data
    st.session_state.chart_data = pd.concat([st.session_state.chart_data, new_data], ignore_index=True)

    # Update the Plotly figure with the new data
    st.session_state.fig.data[0].x = st.session_state.chart_data['index']
    st.session_state.fig.data[0].y = st.session_state.chart_data['a']
    
    # Optionally adjust the axis ranges dynamically
    st.session_state.fig.update_xaxes(range=[0, st.session_state.chart_data['index'].max() + 5])
    st.session_state.fig.update_yaxes(range=[st.session_state.chart_data['a'].min() - 5, st.session_state.chart_data['a'].max() + 5])

    # Refresh the chart in Streamlit
    st.plotly_chart(st.session_state.fig, use_container_width=True, update=True)

    i += 1
    time.sleep(1)
