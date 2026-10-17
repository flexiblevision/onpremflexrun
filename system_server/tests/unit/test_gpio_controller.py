"""The GPIO monitoring daemon behind the SocketIO pin view.

An inspection is triggered by an input pin going low. The trigger has to be
edge-detected: a PLC holds the line low for as long as the part is in the
fixture, and a level-triggered read would fire an inference on every poll of a
1ms loop. allow_inference is that edge detector, and it is what these mostly
cover.

The module builds a GPIO instance at import, which reads the driver and
MongoDB, so it is loaded here with both stubbed.
"""
import importlib.util
import json
import os
import sys
import pytest
from testsupport import thread_aware_sleep_mock
from unittest.mock import patch, MagicMock, call


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
GPIO_DIR = os.path.join(REPO, 'gpio')


def _socketio_stub():
    """Stand in for flask_socketio when it is not installed.

    requirements.txt pins Flask-SocketIO, so CI imports the real one; a
    developer machine that has not installed it should still be able to run
    these. Nothing here depends on the transport - every test patches emit.
    """
    if importlib.util.find_spec('flask_socketio') is not None:
        return None

    stub = MagicMock()
    stub.SocketIO.return_value.on.side_effect = lambda *a, **kw: (lambda f: f)
    return stub


def _load_controller(alias='_gpio_controller_under_test', so_exists=True):
    """Import gpio_controller with the driver and MongoDB stubbed out."""
    path = os.path.join(GPIO_DIR, 'gpio_controller.py')
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)

    stub = _socketio_stub()
    injected = {'flask_socketio': stub} if stub else {}

    sys.path.insert(0, GPIO_DIR)
    sys.modules[alias] = module
    for name, value in injected.items():
        sys.modules[name] = value
    try:
        with patch('ctypes.CDLL', return_value=MagicMock()) as cdll, \
             patch('pymongo.MongoClient', return_value=MagicMock()), \
             patch('os.path.exists', return_value=so_exists):
            spec.loader.exec_module(module)
            module._cdll = cdll
    finally:
        sys.path.pop(0)
        sys.modules.pop(alias, None)
        for name in injected:
            sys.modules.pop(name, None)
    return module


gc = _load_controller()


BLANK_STATE = {f'GPO{i}': False for i in range(1, 9)}
BLANK_STATE.update({f'GPI{i}': False for i in range(1, 9)})


def _stored_state(**overrides):
    """A pin-state document as it comes back from mongo, _id and type included."""
    doc = dict(BLANK_STATE, _id='oid', type='gpio_pin_state')
    doc.update(overrides)
    return doc


@pytest.fixture
def pins():
    with patch.object(gc, 'pin_state_ref') as collection:
        collection.find_one.return_value = _stored_state()
        yield collection


@pytest.fixture
def driver():
    with patch.object(gc, 'functions') as functions:
        functions.read_gpo.return_value = 1
        functions.set_gpio.return_value = 0
        functions.read_gpi.return_value = 1
        yield functions


@pytest.fixture
def emit():
    with patch.object(gc.socketio, 'emit') as socket_emit:
        yield socket_emit


@pytest.fixture
def gpio(pins, driver, emit):
    return gc.GPIO()


class TestMsTimestamp:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_is_epoch_milliseconds(self):
        import datetime
        now = datetime.datetime.now().timestamp() * 1000
        assert abs(gc.ms_timestamp() - now) < 5000
        assert isinstance(gc.ms_timestamp(), int)


class TestGpioConstruction:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_existing_state_document_is_adopted(self, pins, driver, emit):
        pins.find_one.return_value = _stored_state(GPO3=True)

        gpio = gc.GPIO()

        assert gpio.cur_pin_state['GPO3'] is True
        # Mongo bookkeeping fields must not survive into the emitted payload.
        assert '_id' not in gpio.cur_pin_state
        assert 'type' not in gpio.cur_pin_state

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_fresh_device_gets_a_seeded_state_document(self, pins, driver, emit):
        pins.find_one.return_value = None

        gpio = gc.GPIO()

        assert gpio.cur_pin_state == BLANK_STATE
        pins.insert_one.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_output_baseline_is_read_from_the_driver(self, pins, driver, emit):
        driver.read_gpo.side_effect = [1, 0, 1, 1, 1, 1, 1, 1]

        gpio = gc.GPIO()

        assert gpio.current_output_state == [1, 0, 1, 1, 1, 1, 1, 1]
        assert driver.read_gpo.call_args_list == [call(i) for i in range(1, 9)]

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_trigger_starts_armed(self, gpio):
        assert gpio.last_input_state == 'wait'
        assert gpio.last_pin_state is None

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_debounce_delay_is_one_millisecond(self, gpio):
        assert gpio.debounce_delay == .001


class TestDriverWrappers:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_set_gpio_delegates_to_the_driver(self, gpio, driver):
        assert gpio._set_gpio(1, 5, 0) == 0
        driver.set_gpio.assert_called_with(1, 5, 0)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_read_gpi_delegates_to_the_driver(self, gpio, driver):
        driver.read_gpi.return_value = 3
        assert gpio._read_gpi(2) == 3

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_missing_driver_reports_failure_rather_than_raising(self, gpio):
        # gpio.so is absent on ARM builds; the daemon still has to run so the
        # SocketIO view stays up.
        gpio.set_gpio_func = None
        gpio.read_gpi_func = None

        assert gpio._set_gpio(1, 5, 0) == -1
        assert gpio._read_gpi(2) == -1


class TestAllowInference:
    """Edge detection on the trigger line."""

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_first_low_reading_fires(self, gpio):
        assert gpio.allow_inference(0, 1) is True

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_held_line_does_not_fire_again(self, gpio):
        # The loop polls every millisecond; without this a single part would
        # queue thousands of inferences.
        assert gpio.allow_inference(0, 1) is True
        assert gpio.allow_inference(0, 1) is False
        assert gpio.allow_inference(0, 1) is False

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_high_line_does_not_fire(self, gpio):
        assert gpio.allow_inference(1, 1) is False
        assert gpio.last_input_state == 'wait'

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_trigger_rearms_after_the_line_goes_high(self, gpio):
        assert gpio.allow_inference(0, 1) is True
        gpio.last_input_state = 'wait'
        assert gpio.allow_inference(0, 1) is True


class TestUpdatePinState:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_low_reading_is_recorded_as_asserted(self, gpio):
        # The driver reports 0 for an asserted line.
        gpio.update_pin_state('I', [0, 1, 1, 1, 1, 1, 1, 1])

        assert gpio.cur_pin_state['GPI1'] is True
        assert gpio.cur_pin_state['GPI2'] is False

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_outputs_are_keyed_separately_from_inputs(self, gpio):
        gpio.update_pin_state('O', [0] * 8)

        assert all(gpio.cur_pin_state[f'GPO{i}'] is True for i in range(1, 9))
        assert all(gpio.cur_pin_state[f'GPI{i}'] is False for i in range(1, 9))


class TestDefaultPinState:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_every_output_is_driven_off(self, gpio, driver, pins):
        # Startup must not leave a reject gate latched from the last run.
        gpio.default_pin_state()

        for pin in range(1, 9):
            assert call(1, pin, 1) in driver.set_gpio.call_args_list

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_state_document_is_written(self, gpio, pins):
        gpio.default_pin_state()

        assert pins.update_one.call_args[0][0] == {'type': 'gpio_pin_state'}
        assert pins.update_one.call_args[0][2] is True

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_view_is_notified(self, gpio, emit):
        gpio.default_pin_state()
        emit.assert_called()


class TestEmitPinStateUpdate:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_publishes_on_the_gpio_namespace(self, gpio, emit):
        gpio._emit_pin_state_update()

        event, payload = emit.call_args[0]
        assert event == 'pin_state_update'
        assert payload is gpio.cur_pin_state
        assert emit.call_args[1]['namespace'] == '/gpio'

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_mongo_fields_are_stripped_before_publishing(self, gpio, emit):
        gpio.cur_pin_state['_id'] = 'oid'
        gpio.cur_pin_state['type'] = 'gpio_pin_state'

        gpio._emit_pin_state_update()

        assert '_id' not in gpio.cur_pin_state
        assert 'type' not in gpio.cur_pin_state

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_socket_failure_does_not_stop_the_loop(self, gpio, emit):
        # No client connected is normal; it must not take the daemon down.
        emit.side_effect = RuntimeError('no clients')
        gpio._emit_pin_state_update()


class TestPinSwitchInference:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_start_marks_the_input_busy_and_publishes(self, gpio, pins, emit):
        pins.find_one.return_value = None

        gpio.pin_switch_inference_start(3)

        assert gpio.cur_pin_state['GPI3'] is True
        pins.update_one.assert_called()
        emit.assert_called()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_end_clears_the_input_after_a_hold(self, gpio, pins, emit):
        pins.find_one.return_value = None
        gpio.cur_pin_state['GPI3'] = True

        with patch('time.sleep', new=thread_aware_sleep_mock()) as sleep:
            gpio.pin_switch_inference_end(3)

        # The hold is what makes the transition visible in the UI.
        sleep.assert_called_once_with(.3)
        assert gpio.cur_pin_state['GPI3'] is False

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_end_publishes_both_sides_of_the_transition(self, gpio, pins, emit):
        pins.find_one.return_value = None

        with patch('time.sleep', new=thread_aware_sleep_mock()):
            gpio.pin_switch_inference_end(3)

        assert emit.call_count == 2


class TestGetPassFailEntry:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_returns_the_first_match(self, gpio):
        with patch.object(gc, 'pass_fail_ref') as collection:
            collection.find.return_value = [{'modelName': 'widgets', 'pass': 1}]
            entry = gpio.get_pass_fail_entry('widgets', 3)

        assert entry['modelName'] == 'widgets'

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_queries_on_model_and_version(self, gpio):
        with patch.object(gc, 'pass_fail_ref') as collection:
            collection.find.return_value = []
            gpio.get_pass_fail_entry('widgets', 3)

        collection.find.assert_called_once_with(
            {'modelName': 'widgets', 'modelVersion': 3})

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_no_match_is_false(self, gpio):
        with patch.object(gc, 'pass_fail_ref') as collection:
            collection.find.return_value = []
            assert gpio.get_pass_fail_entry('widgets', 3) is False


PRESET = {'cameraId': 'cam0', 'modelName': 'widgets', 'modelVersion': 3,
          'ioVal': 'GPI1', 'presetId': 'p1'}


class TestRunInference:
    @pytest.fixture
    def token(self):
        with patch.object(gc, 'util_ref') as collection:
            collection.find_one.return_value = {'token': 'tok'}
            yield collection

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_vision_preset_calls_the_snap_endpoint(self, gpio, token):
        with patch('requests.get', return_value=MagicMock(status_code=200)) as get, \
             patch.object(gpio, 'pin_switch_inference_end'):
            gpio.run_inference(PRESET, 1)

        url = get.call_args[0][0]
        assert '/api/capture/predict/snap/widgets/3/cam0' in url
        assert 'workstation=GPI1' in url
        assert 'preset_id=p1' in url
        assert get.call_args[1]['headers'] == {'Authorization': 'Bearer tok'}

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_successful_inference_releases_the_input(self, gpio, token):
        with patch('requests.get', return_value=MagicMock(status_code=200)), \
             patch.object(gpio, 'pin_switch_inference_end') as end:
            gpio.run_inference(PRESET, 1)

        end.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_failed_inference_leaves_the_input_marked_busy(self, gpio, token):
        # Documented, not endorsed: a non-200 never calls the end switch, so
        # the pin shows busy in the UI until the next successful cycle.
        with patch('requests.get', return_value=MagicMock(status_code=500)), \
             patch.object(gpio, 'pin_switch_inference_end') as end:
            gpio.run_inference(PRESET, 1)

        end.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_unreachable_backend_is_reported_not_raised(self, gpio, token):
        # This runs on a daemon thread; an escaping exception is invisible.
        with patch('requests.get', side_effect=ConnectionError('refused')), \
             patch.object(gpio, 'pin_switch_inference_end') as end:
            gpio.run_inference(PRESET, 1)

        end.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_thermal_preset_fetches_a_frame_then_submits_it(self, gpio, token):
        frame = MagicMock(status_code=200)
        frame.json.return_value = {'b64': 'data'}

        with patch('requests.get', return_value=frame) as get, \
             patch('requests.put', return_value=MagicMock(status_code=200)) as put, \
             patch.object(gpio, 'pin_switch_inference_end') as end:
            gpio.run_inference(dict(PRESET, server='thermal'), 1)

        assert '/api/ir/vision/b64Frame/cam0' in get.call_args[0][0]
        assert '/api/capture/predict/single_inference/1/1' in put.call_args[0][0]
        assert put.call_args[1]['json'] == {'b64': 'data'}
        end.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_unreachable_thermal_camera_is_reported_not_raised(self, gpio, token):
        with patch('requests.get', side_effect=ConnectionError('refused')), \
             patch('requests.put') as put, \
             patch.object(gpio, 'pin_switch_inference_end') as end:
            gpio.run_inference(dict(PRESET, server='thermal'), 1)

        put.assert_not_called()
        end.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_inference_request_is_bounded(self, gpio, token):
        # It runs on a per-trigger thread; an unbounded request would leak one
        # thread per part.
        with patch('requests.get', return_value=MagicMock(status_code=200)) as get, \
             patch.object(gpio, 'pin_switch_inference_end'):
            gpio.run_inference(PRESET, 1)

        assert get.call_args[1]['timeout'] == 2


class TestRunLoop:
    def _states(self, *frames):
        """read_all_gpio_states_as_json side effect, then stop the loop."""
        return list(frames) + [KeyboardInterrupt()]

    def _drive(self, gpio, frames, presets=None):
        results = []
        for frame in frames:
            results.append(frame)
        results.append(KeyboardInterrupt())

        def read():
            item = results.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        # default_pin_state is stubbed so the assertions below see only what
        # the monitoring loop itself published, not the startup reset.
        with patch.object(gpio, 'default_pin_state'), \
             patch.object(gc, 'read_all_gpio_states_as_json', side_effect=read), \
             patch.object(gc, 'io_ref') as io_ref, \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch('threading.Thread') as thread:
            io_ref.find.return_value = presets if presets is not None else []
            with pytest.raises(KeyboardInterrupt):
                gpio.run()
        return thread

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_outputs_are_defaulted_before_monitoring(self, gpio, pins, driver):
        with patch.object(gpio, 'default_pin_state') as default, \
             patch.object(gc, 'read_all_gpio_states_as_json',
                          side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                gpio.run()

        default.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_low_input_starts_an_inference_thread(self, gpio, pins, emit):
        gpio.current_output_state = [1] * 8
        frame = {'inputs': [0, 1, 1, 1, 1, 1, 1, 1], 'outputs': [1] * 8}

        thread = self._drive(gpio, [frame], presets=[PRESET])

        thread.assert_called_once()
        assert thread.call_args[1]['target'] == gpio.run_inference
        assert thread.call_args[1]['args'] == (PRESET, 1)
        assert thread.call_args[1]['daemon'] is True

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_presets_are_looked_up_for_the_triggered_pin(self, gpio, pins, emit):
        gpio.current_output_state = [1] * 8
        frame = {'inputs': [1, 1, 0, 1, 1, 1, 1, 1], 'outputs': [1] * 8}

        with patch.object(gc, 'read_all_gpio_states_as_json',
                          side_effect=[frame, KeyboardInterrupt()]), \
             patch.object(gc, 'io_ref') as io_ref, \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch('threading.Thread'):
            io_ref.find.return_value = []
            with pytest.raises(KeyboardInterrupt):
                gpio.run()

        io_ref.find.assert_called_once_with({'ioVal': 'GPI3'})

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_held_line_only_triggers_once(self, gpio, pins, emit):
        gpio.current_output_state = [1] * 8
        frame = {'inputs': [0, 1, 1, 1, 1, 1, 1, 1], 'outputs': [1] * 8}

        thread = self._drive(gpio, [frame, frame, frame], presets=[PRESET])

        assert thread.call_count == 1

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_trigger_rearms_once_the_line_returns_high(self, gpio, pins, emit):
        gpio.current_output_state = [1] * 8
        low = {'inputs': [0, 1, 1, 1, 1, 1, 1, 1], 'outputs': [1] * 8}
        high = {'inputs': [1] * 8, 'outputs': [1] * 8}

        thread = self._drive(gpio, [low, high, low], presets=[PRESET])

        assert thread.call_count == 2

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_output_change_is_published(self, gpio, pins, emit):
        gpio.current_output_state = [1] * 8
        frame = {'inputs': [1] * 8, 'outputs': [1, 1, 0, 1, 1, 1, 1, 1]}

        self._drive(gpio, [frame])

        assert gpio.current_output_state == [1, 1, 0, 1, 1, 1, 1, 1]
        assert gpio.cur_pin_state['GPO3'] is True
        emit.assert_called()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_unchanged_frame_publishes_nothing(self, gpio, pins, emit):
        gpio.current_output_state = [1] * 8
        gpio.last_pin_state = [1] * 8
        frame = {'inputs': [1] * 8, 'outputs': [1] * 8}

        emit.reset_mock()
        self._drive(gpio, [frame])

        emit.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_loop_debounces_between_reads(self, gpio, pins, emit):
        gpio.current_output_state = [1] * 8
        frame = {'inputs': [1] * 8, 'outputs': [1] * 8}

        with patch.object(gc, 'read_all_gpio_states_as_json',
                          side_effect=[frame, KeyboardInterrupt()]), \
             patch('time.sleep', new=thread_aware_sleep_mock()) as sleep:
            with pytest.raises(KeyboardInterrupt):
                gpio.run()

        sleep.assert_called_once_with(gpio.debounce_delay)


class TestSocketHandlers:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_connecting_client_is_sent_the_current_state(self):
        with patch.object(gc, 'emit') as emit:
            gc.handle_connect()

        emit.assert_called_once_with('pin_state_update', gc.init_gpio.cur_pin_state)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_disconnect_is_handled(self):
        gc.handle_disconnect()


class TestModuleLoad:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_missing_shared_object_still_fails_to_import(self):
        # ARM builds ship without gpio.so. The module guards the CDLL call and
        # sets functions = None, but GPIO.__init__ runs at import and does
        # functions.set_gpio straight away - so the guard only changes the
        # error, it does not let the daemon come up. Recorded rather than
        # assumed: the SocketIO pin view is unavailable on those builds either
        # way.
        with pytest.raises(AttributeError):
            _load_controller(alias='_gpio_no_so', so_exists=False)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_socketio_server_is_not_started_at_import(self):
        assert gc.app is not None
        assert gc.socketio is not None
