import os
import threading
import time
import requests
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
    if state == True:
        functions.set_gpio(1, int(pin_num), 0)
    else:
        functions.set_gpio(1, int(pin_num), 1)

    pin_state_ref.update_one(query, {'$set': {[pin_key]: state}}, True)
    return cur_pin_state[pin_key]



