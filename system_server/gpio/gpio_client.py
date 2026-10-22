"""Pin access for every process except gpio_controller, which owns gpio.so."""
import os
import requests

CONTROLLER_URL = os.environ.get('GPIO_CONTROLLER_URL', 'http://127.0.0.1:1817')
TIMEOUT = 2


def _get(path):
    resp = requests.get(CONTROLLER_URL + path, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _post(path, payload, timeout=TIMEOUT):
    resp = requests.post(CONTROLLER_URL + path, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def set_output(pin_num, state):
    """Drive an output pin; returns the driver's return code."""
    return _post('/api/gpio/set', {'pin': int(pin_num), 'state': state == True})['rc']


def set_pin_state(pin_num, state):
    set_output(pin_num, state)
    return 'on' if state == True else 'off'


def toggle_pin(pin_num):
    return _post('/api/gpio/toggle', {'pin': int(pin_num)})['state']


def read_pin(pin_num):
    return _get(f'/api/gpio/input/{int(pin_num)}')['asserted']


def read_all_gpio_states_as_json():
    return _get('/api/gpio/state')


def pulse_pass_fail(verdict):
    # The controller holds the pin for half a second before answering.
    return _post('/api/gpio/pass_fail', {'pass_fail': verdict}, timeout=TIMEOUT + 1)['pass_fail']
