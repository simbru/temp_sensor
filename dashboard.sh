#!/bin/bash
export PYTHONPATH="/home/weatherstation/python/temp_sensor"
source /home/weatherstation/python-environments/tempsens/bin/activate
shiny run --reload --launch-browser dashboard/app.py 