import os
import ctypes
from ctypes import *

# Loaded only by gpio_controller, which serialises every call. Other processes
# go through gpio_client: gpio.so is not safe to drive from two at once.
so_file = os.environ['HOME']+"/flex-run/system_server/gpio/gpio.so"
functions = CDLL(so_file)

#(<direction>,<pin_index>,<value>)
# direction - IN  = 0
# direction - OUT = 1

# value - HIGH = 1
# value - LOW  = 0


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
