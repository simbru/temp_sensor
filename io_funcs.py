import configparser
import pathlib
import os

#CONFIGPATH = r"/home/weatherstation/.config.ini"
CONFIGPATH = "config.ini" # need to get from Raspi and copy to somewhere in repo

def gen_default_config(config_loc = CONFIGPATH):
    """
    Initialises a default config.ini file for parameters
    """
    config = configparser.ConfigParser()
    config["DEFAULT"] = {'LogInterval_s' : '1',
                        'RemoteInterval_S' : '86400',
                        'OutputFile' : 'templog.csv',
                        'TestWrite' : '8'}
    if not os.path.exists(CONFIGPATH):
        with open(CONFIGPATH, 'w') as configfile:
            config.write(configfile)
    else:
        print(f"File already exists at {CONFIGPATH}")
        print("Do you want to overwrite it?")
        overwrite = input("y/n:")
        if overwrite == "y":
            with open(CONFIGPATH, 'w') as configfile:
                config.write(configfile)
        else:
            print("Aborting... No default config generated.")


def fetch_config(config_loc = CONFIGPATH):
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

def write_to_config(param_str, value, config_loc = CONFIGPATH):
    # Get the config file
    config = fetch_config(CONFIGPATH)
    config.optionxform = str
    # Get contents DEFAULT section
    current_content = list(config["DEFAULT"].keys())
    if param_str not in current_content:
        raise KeyError(f"Passed str '{param_str}, which is not a parameter in config file at {CONFIGPATH}'") 
    else:
        print(f"{param_str} set to {value} in {config_loc}")
        config["DEFAULT"][param_str] = f"{value}"
        with open(CONFIGPATH, 'w') as configfile:
            config.write(configfile)

def fetch_csv():
    # Get config
    config = fetch_config()
    # Get CSV location
    output_file = config["DEFAULT"]["OutputFile"]
    return output_file    
