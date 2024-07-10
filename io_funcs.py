import configparser
import pathlib
import os

configpath = r"/home/weatherstation/.config.ini"

def fetch_config(config_loc = configpath):
    """
    Generates a configparser object and reads the .config.ini into it,
    to fetch user paramters.
    """
    # Read config_loc into pathlib.Path for sanity
    config_loc = pathlib.Path(config_loc)
    # Read config
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(config_loc)
    return config

def write_config(param_str, value, config_loc = configpath):
    # Get the config file
    config = fetch_config(configpath)
    config.optionxform = str
    # Get contents DEFAULT section
    current_content = list(config["Parameters"].keys())
    if param_str not in current_content:
        raise KeyError(f"Passed str '{param_str}, which is not a parameter in config file at {configpath}'") 
    else:
        print(f"{param_str} set to {value} in {config_loc}")
        config["Parameters"][param_str] = f"{value}"
        with open(configpath, 'w') as configfile:
            config.write(configfile)

def fetch_csv():
    # Get config
    config = fetch_config()
    # Get CSV location
    output_file = config["Parameters"]["OutputFile"]
    