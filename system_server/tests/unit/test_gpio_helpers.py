"""The two gpio_helper modules.

gpio/gpio_helper.py is the ctypes layer over gpio.so, loaded only by
gpio_controller. tcp/gpio_helper.py no longer touches gpio.so at all: two
processes driving the driver at once corrupted reads and dropped writes, so
tcp_server's pin access is proxied through the controller.
"""
import ctypes
import importlib.util
import os
import sys
import pytest
from unittest.mock import patch, MagicMock


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load(alias, relative_path):
    """Import a module from a script directory, with CDLL stubbed out."""
    path = os.path.join(REPO, relative_path)
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)

    driver = MagicMock()
    sys.path.insert(0, os.path.dirname(path))
    sys.modules[alias] = module
    try:
        with patch('ctypes.CDLL', return_value=driver) as cdll:
            spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
        sys.modules.pop(alias, None)

    module._driver = driver
    module._cdll = cdll
    return module


GPIO_HELPER = _load('_gpio_pkg_helper', 'gpio/gpio_helper.py')
TCP_HELPER = _load('_tcp_pkg_helper', 'tcp/gpio_helper.py')


@pytest.fixture(autouse=True)
def reset_driver():
    GPIO_HELPER._driver.reset_mock()
    yield


class TestReadAllGpioStates:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_bytes_are_unpacked_lsb_first_into_eight_bits(self):
        GPIO_HELPER.functions.read_all_gpio_states.return_value = \
            GPIO_HELPER.GPIO_State(inputs=0b00000101, outputs=0b10000000)

        result = GPIO_HELPER.read_all_gpio_states_as_json()

        assert result['inputs'] == [1, 0, 1, 0, 0, 0, 0, 0]
        assert result['outputs'] == [0, 0, 0, 0, 0, 0, 0, 1]

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_return_type_is_declared_before_the_call(self):
        # Without restype ctypes truncates the struct to an int and every pin
        # reads as garbage.
        GPIO_HELPER.functions.read_all_gpio_states.return_value = \
            GPIO_HELPER.GPIO_State(inputs=0, outputs=0)

        GPIO_HELPER.read_all_gpio_states_as_json()

        assert GPIO_HELPER.functions.read_all_gpio_states.restype is GPIO_HELPER.GPIO_State

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_missing_driver_is_reported_not_raised(self):
        GPIO_HELPER.functions.read_all_gpio_states.side_effect = OSError('no such file')

        result = GPIO_HELPER.read_all_gpio_states_as_json()

        assert result['error'] == 'Failed to load libgpio.so'
        assert 'no such file' in result['details']

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_struct_is_two_unsigned_bytes(self):
        assert GPIO_HELPER.GPIO_State._fields_ == [
            ('inputs', ctypes.c_ubyte), ('outputs', ctypes.c_ubyte)]
        assert ctypes.sizeof(GPIO_HELPER.GPIO_State) == 2


class TestGpioHelperIsReadOnly:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_no_unlocked_pin_writers_remain(self):
        # Writers here would bypass gpio_controller's lock.
        for name in ('set_pin_state', 'toggle_pin', 'read_pin'):
            assert not hasattr(GPIO_HELPER, name)


class TestTcpHelper:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_never_loads_the_driver(self):
        TCP_HELPER._cdll.assert_not_called()
        assert not hasattr(TCP_HELPER, 'functions')

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_setting_on_goes_through_the_controller(self):
        with patch.object(TCP_HELPER.gpio_client, 'set_output', return_value=0) as set_output, \
             patch.object(TCP_HELPER, 'log_gpio_error'):
            assert TCP_HELPER.set_pin_state('5', True) == 'on'

        set_output.assert_called_once_with('5', True)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_setting_off_reports_off(self):
        with patch.object(TCP_HELPER.gpio_client, 'set_output', return_value=0), \
             patch.object(TCP_HELPER, 'log_gpio_error'):
            assert TCP_HELPER.set_pin_state(5, False) == 'off'

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_driver_return_code_is_logged(self):
        with patch.object(TCP_HELPER.gpio_client, 'set_output', return_value=-1), \
             patch.object(TCP_HELPER, 'log_gpio_error') as log:
            TCP_HELPER.set_pin_state(5, True)

        log.assert_called_once_with('set_gpio', 5, 1, 0, -1)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_unreachable_controller_is_logged_and_reported(self):
        # A raise here would drop the TCP connection mid-inspection, and
        # answering 'on' would tell the PLC a pin moved when it did not.
        error = ConnectionError('refused')
        with patch.object(TCP_HELPER.gpio_client, 'set_output', side_effect=error), \
             patch.object(TCP_HELPER, 'log_gpio_error') as log:
            assert TCP_HELPER.set_pin_state(5, False) == 'error'

        log.assert_called_once_with('set_gpio', 5, 1, 1, -1, error)
