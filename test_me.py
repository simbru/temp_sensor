import adafruit_dht
from board import D4 as pin
import time 
import datetime
import configparser
import pathlib
import os
import random
from io_funcs import configpath
import io_funcs

# unit testing with pytest

def test_get_config(config_loc = configpath):
    # Read config
    config = configparser.ConfigParser()
    config.read(config_loc)
    assert config

def test_config_access(config_loc = configpath, diagnostic_print = True):
    config = io_funcs.fetch_config()
    if diagnostic_print is True:
        print("Running from:", os.getcwd())
        print("Other sections:", config.sections())
    io_funcs.write_config("TestWrite", random.randint(0, 9))
    assert config

# def test_csv_access():
#     return 0