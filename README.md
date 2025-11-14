Will eventually contain the scripts for an autonomous RasPi unit that logs temperature and uploads it to a central database.

### Dashboard Notes

- Run with `uv run bokeh serve --show tempsens/dashboard/bokeh_app.py`
- Temperature/humidity y-ranges are driven by configurable window widths: defaults come from `config.ini`, live tweaks persist to `settings.ini`
- Manual y-axis zoom updates the window controls; adjust the spinners to recentre on the moving-average values
- The axes auto recentre at most every `temperature_range_update_s` / `humidity_range_update_s` seconds (set in `config.ini`) with a small built-in deadband to avoid jitter
