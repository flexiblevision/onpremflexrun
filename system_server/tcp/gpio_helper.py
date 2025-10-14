import os
import threading
import time
import requests
import ctypes
import json
from ctypes import *
from pymongo import MongoClient
import datetime
import string

client   = MongoClient("172.17.0.1")
io_ref   = client["fvonprem"]["io_presets"]
util_ref = client["fvonprem"]["utils"]
pin_state_ref = client["fvonprem"]["pin_state"]

so_file = os.environ['HOME']+"/flex-run/system_server/gpio/gpio.so"
functions = CDLL(so_file)

#(<direction>,<pin_index>,<value>)
# direction - IN  = 0
# direction - OUT = 1

# value - HIGH = 1
# value - LOW  = 0

def read_pin(pin_num):
    state = functions.read_gpi(int(pin_num))
    return int(state) != int(pin_num)

def toggle_pin(pin_num):
    query = {'type':'gpio_pin_state'}
    cur_pin_state = pin_state_ref.find_one(query)
    pin_key       = 'GPO'+str(pin_num)
    if cur_pin_state[pin_key]:
        cur_pin_state[pin_key] = False
        functions.set_gpio(1, int(pin_num), 1)
    else:
        cur_pin_state[pin_key] = True
        functions.set_gpio(1, int(pin_num), 0)
    pin_state_ref.update_one(query, {'$set': cur_pin_state}, True)


def set_pin_state(pin_num, state):
    query = {'type':'gpio_pin_state'}
    pin_key       = 'GPO'+str(pin_num)
    res = -1
    if state == True:
        functions.set_gpio(1, int(pin_num), 0)
        res = 'on'
    else:
        functions.set_gpio(1, int(pin_num), 1)
        res = 'off'

    pin_state_ref.update_one(query, {'$set': {pin_key: state}}, True)
    return res


class GPIO_State(ctypes.Structure):
    _fields_ = [
        ("inputs", ctypes.c_ubyte),
        ("outputs", ctypes.c_ubyte)
    ]


def read_all_gpio_states_as_json():
    try:
        functions.read_all_gpio_states.restype = GPIO_State
        c_gpio_state = functions.read_all_gpio_states()

        inputs = [(c_gpio_state.inputs >> i) & 1 for i in range(8)]
        outputs = [(c_gpio_state.outputs >> i) & 1 for i in range(8)]

        gpio_data = {
            "inputs": inputs,
            "outputs": outputs
        }

        return gpio_data

    except OSError as e:
        error_message = {
            "error": "Failed to load libgpio.so",
            "details": str(e)
        }
        return error_message

