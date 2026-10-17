"""Pin control against the gpio.so driver.

Two near-identical copies of this module exist - system_server/gpio/ and
system_server/tcp/ - and they have drifted: only the tcp copy records driver
return codes. Both are exercised here from the same table, so a change applied
to one and not the other shows up as a failure rather than as silent drift.

Both load gpio.so with ctypes at import time, which is why nothing could
previously import them off a device. Each is loaded once here with CDLL
stubbed, under a private module name so the conftest-level gpio mock that the
route tests rely on is left alone.
"""
import importlib.util
import os
import sys
import pytest
from unittest.mock import patch, MagicMock, call


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load(alias, relative_path):
    """Import a gpio_helper copy with the native library stubbed out.

    The tcp copy does a bare `import gpio_error_logger`, which resolves only
    with its own directory on sys.path - true when tcp_server.py is run as a
    script from there, not when the package is imported. The directory is added
    for the duration of the load.
    """
    path = os.path.join(REPO, relative_path)
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)

    driver = MagicMock()
    sys.path.insert(0, os.path.dirname(path))
    sys.modules[alias] = module
    try:
        with patch('ctypes.CDLL', return_value=driver):
            spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
        sys.modules.pop(alias, None)

    module._driver = driver
    return module


GPIO_HELPER = _load('_gpio_pkg_helper', 'gpio/gpio_helper.py')
TCP_HELPER = _load('_tcp_pkg_helper', 'tcp/gpio_helper.py')

BOTH = pytest.mark.parametrize('helper', [GPIO_HELPER, TCP_HELPER],
                               ids=['gpio', 'tcp'])


@pytest.fixture(autouse=True)
def reset_drivers():
    GPIO_HELPER._driver.reset_mock()
    TCP_HELPER._driver.reset_mock()
    yield


@pytest.fixture
def pin_state():
    """Patch the pin_state collection on both copies."""
    with patch.object(GPIO_HELPER, 'pin_state_ref') as a, \
         patch.object(TCP_HELPER, 'pin_state_ref') as b:
        yield {GPIO_HELPER: a, TCP_HELPER: b}


class TestReadPin:
    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_a_driver_value_differing_from_the_pin_reads_high(self, helper):
        helper.functions.read_gpi.return_value = 0
        assert helper.read_pin(3) is True

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_a_driver_value_equal_to_the_pin_reads_low(self, helper):
        # read_gpi echoes the pin number back when the input is not asserted.
        helper.functions.read_gpi.return_value = 3
        assert helper.read_pin(3) is False

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_a_string_pin_number_is_coerced(self, helper):
        helper.functions.read_gpi.return_value = 0
        assert helper.read_pin('3') is True
        helper.functions.read_gpi.assert_called_once_with(3)


class TestTogglePin:
    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_an_on_pin_is_driven_low(self, helper, pin_state):
        collection = pin_state[helper]
        collection.find_one.return_value = {'GPO4': True}

        helper.toggle_pin(4)

        # value 1 is LOW for this driver.
        helper.functions.set_gpio.assert_called_once_with(1, 4, 1)
        assert collection.update_one.call_args[0][1]['$set']['GPO4'] is False

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_an_off_pin_is_driven_high(self, helper, pin_state):
        collection = pin_state[helper]
        collection.find_one.return_value = {'GPO4': False}

        helper.toggle_pin(4)

        helper.functions.set_gpio.assert_called_once_with(1, 4, 0)
        assert collection.update_one.call_args[0][1]['$set']['GPO4'] is True

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_the_state_document_is_upserted(self, helper, pin_state):
        collection = pin_state[helper]
        collection.find_one.return_value = {'GPO1': False}

        helper.toggle_pin(1)

        assert collection.update_one.call_args[0][0] == {'type': 'gpio_pin_state'}
        assert collection.update_one.call_args[0][2] is True

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_other_pins_are_carried_through_untouched(self, helper, pin_state):
        # The whole document is written back, so a partial read would clear
        # every other pin's recorded state.
        collection = pin_state[helper]
        collection.find_one.return_value = {'GPO1': False, 'GPO2': True, 'GPO3': False}

        helper.toggle_pin(1)

        written = collection.update_one.call_args[0][1]['$set']
        assert written['GPO2'] is True
        assert written['GPO3'] is False

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_a_pin_missing_from_the_state_document_raises(self, helper, pin_state):
        pin_state[helper].find_one.return_value = {'GPO1': False}

        with pytest.raises(KeyError):
            helper.toggle_pin(7)


class TestSetPinState:
    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_setting_on_drives_the_pin_low_and_reports_on(self, helper, pin_state):
        assert helper.set_pin_state(5, True) == 'on'
        helper.functions.set_gpio.assert_called_once_with(1, 5, 0)

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_setting_off_drives_the_pin_high_and_reports_off(self, helper, pin_state):
        assert helper.set_pin_state(5, False) == 'off'
        helper.functions.set_gpio.assert_called_once_with(1, 5, 1)

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_only_the_named_pin_is_written(self, helper, pin_state):
        helper.set_pin_state(5, True)

        collection = pin_state[helper]
        assert collection.update_one.call_args[0][1] == {'$set': {'GPO5': True}}
        assert collection.update_one.call_args[0][2] is True

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_only_an_exact_true_turns_a_pin_on(self, helper, pin_state):
        # `if state == True` - a truthy non-boolean takes the off branch, so a
        # caller passing 1 or 'on' silently turns the pin off.
        assert helper.set_pin_state(5, 'on') == 'off'
        helper.functions.set_gpio.assert_called_once_with(1, 5, 1)

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_a_string_pin_number_is_coerced(self, helper, pin_state):
        helper.set_pin_state('5', True)
        helper.functions.set_gpio.assert_called_once_with(1, 5, 0)


class TestSetPinStateErrorLogging:
    """Only the tcp copy records what the driver returned."""

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_tcp_copy_logs_the_driver_return_code(self, pin_state):
        TCP_HELPER.functions.set_gpio.return_value = -1

        with patch.object(TCP_HELPER, 'log_gpio_error') as log:
            TCP_HELPER.set_pin_state(5, True)

        log.assert_called_once_with('set_gpio', 5, 1, 0, -1)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_driver_exception_is_logged_and_swallowed(self, pin_state):
        # A raise here would drop the TCP connection mid-inspection.
        TCP_HELPER.functions.set_gpio.side_effect = OSError('device busy')

        with patch.object(TCP_HELPER, 'log_gpio_error') as log:
            assert TCP_HELPER.set_pin_state(5, False) == 'off'

        assert log.call_args[0][:5] == ('set_gpio', 5, 1, 1, -1)
        assert isinstance(log.call_args[0][5], OSError)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_state_is_still_recorded_after_a_driver_failure(self, pin_state):
        TCP_HELPER.functions.set_gpio.side_effect = OSError('device busy')

        with patch.object(TCP_HELPER, 'log_gpio_error'):
            TCP_HELPER.set_pin_state(5, True)

        pin_state[TCP_HELPER].update_one.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_gpio_copy_has_no_error_logging(self):
        # Documented drift: a failed set_gpio on this copy leaves no trace.
        assert not hasattr(GPIO_HELPER, 'log_gpio_error')

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_gpio_copy_propagates_a_driver_exception(self, pin_state):
        GPIO_HELPER.functions.set_gpio.side_effect = OSError('device busy')

        with pytest.raises(OSError):
            GPIO_HELPER.set_pin_state(5, True)


class TestReadAllGpioStates:
    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_bytes_are_unpacked_lsb_first_into_eight_bits(self, helper):
        state = helper.GPIO_State(inputs=0b00000101, outputs=0b10000000)
        helper.functions.read_all_gpio_states.return_value = state

        result = helper.read_all_gpio_states_as_json()

        assert result['inputs'] == [1, 0, 1, 0, 0, 0, 0, 0]
        assert result['outputs'] == [0, 0, 0, 0, 0, 0, 0, 1]

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_all_pins_low(self, helper):
        helper.functions.read_all_gpio_states.return_value = \
            helper.GPIO_State(inputs=0, outputs=0)

        result = helper.read_all_gpio_states_as_json()

        assert result == {'inputs': [0] * 8, 'outputs': [0] * 8}

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_all_pins_high(self, helper):
        helper.functions.read_all_gpio_states.return_value = \
            helper.GPIO_State(inputs=0xFF, outputs=0xFF)

        result = helper.read_all_gpio_states_as_json()

        assert result == {'inputs': [1] * 8, 'outputs': [1] * 8}

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_the_return_type_is_declared_before_the_call(self, helper):
        # Without restype ctypes truncates the struct to an int and every pin
        # reads as garbage.
        helper.functions.read_all_gpio_states.return_value = \
            helper.GPIO_State(inputs=0, outputs=0)

        helper.read_all_gpio_states_as_json()

        assert helper.functions.read_all_gpio_states.restype is helper.GPIO_State

    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_a_missing_driver_is_reported_not_raised(self, helper):
        helper.functions.read_all_gpio_states.side_effect = OSError('no such file')

        result = helper.read_all_gpio_states_as_json()

        assert result['error'] == 'Failed to load libgpio.so'
        assert 'no such file' in result['details']


class TestGpioStateStruct:
    @pytest.mark.unit
    @pytest.mark.gpio
    @BOTH
    def test_the_struct_is_two_unsigned_bytes(self, helper):
        import ctypes
        assert helper.GPIO_State._fields_ == [
            ('inputs', ctypes.c_ubyte), ('outputs', ctypes.c_ubyte)]
        assert ctypes.sizeof(helper.GPIO_State) == 2


class TestCopiesHaveNotDrifted:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_both_copies_expose_the_same_pin_api(self):
        api = {'read_pin', 'toggle_pin', 'set_pin_state',
               'read_all_gpio_states_as_json', 'GPIO_State'}
        assert api <= set(dir(GPIO_HELPER))
        assert api <= set(dir(TCP_HELPER))

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_both_load_the_same_shared_object(self):
        assert GPIO_HELPER.so_file == TCP_HELPER.so_file
        assert GPIO_HELPER.so_file.endswith('/flex-run/system_server/gpio/gpio.so')
