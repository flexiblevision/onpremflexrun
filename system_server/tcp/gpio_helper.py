import os
import sys

# gpio_controller owns gpio.so; driving it from this process too corrupted reads
# and dropped writes. Appended, not inserted, so tcp/ modules still win.
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'gpio'))

import gpio_client
from gpio_client import read_all_gpio_states_as_json, pulse_pass_fail
from gpio_error_logger import log_gpio_error


def set_pin_state(pin_num, state):
    value = 0 if state == True else 1
    try:
        rc = gpio_client.set_output(pin_num, state)
    except Exception as e:
        log_gpio_error('set_gpio', pin_num, 1, value, -1, e)
        return 'error'
    log_gpio_error('set_gpio', pin_num, 1, value, rc)
    return 'on' if state == True else 'off'
