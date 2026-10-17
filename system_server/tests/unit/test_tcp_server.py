"""The TCP command server the PLC talks to on port 5300.

This is a socket on the plant network that accepts commands, drives output pins
and triggers inspections. Its loops used to run at import, so nothing could
load it; they are now behind main(). The cases that matter are the ones a
neighbour device can actually produce: a partial frame, a command that is not
in the preset table, and a prediction that comes back non-200.
"""
import importlib.util
import json
import os
import socket
import sys
import pytest
from testsupport import thread_aware_sleep_mock
from unittest.mock import patch, MagicMock, call


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
TCP_DIR = os.path.join(REPO, 'tcp')


def _load_tcp_server():
    """Import tcp_server with the native GPIO library stubbed out.

    tcp_server does `from gpio_helper import *`, a bare import that resolves
    only with its own directory on sys.path - true when it runs as a script,
    not when the package is imported.
    """
    path = os.path.join(TCP_DIR, 'tcp_server.py')
    spec = importlib.util.spec_from_file_location('_tcp_server_under_test', path)
    module = importlib.util.module_from_spec(spec)

    sys.path.insert(0, TCP_DIR)
    sys.modules['_tcp_server_under_test'] = module
    try:
        with patch('ctypes.CDLL', return_value=MagicMock()):
            spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
        sys.modules.pop('_tcp_server_under_test', None)
    return module


ts = _load_tcp_server()


PRESET = {
    'ioVal': 'cmd1', 'modelName': 'widgets', 'modelVersion': 3,
    'cameraId': 'cam0', 'presetId': 'p1',
}
CONFIG = {'type': 'tcp_config', 'packet_header': False,
          'predictions': True, 'image': False}


@pytest.fixture
def conn():
    """A socket double whose recv() yields a scripted set of frames."""
    sock = MagicMock()
    return sock


def _frames(*payloads):
    """recv() side effect: the given frames, then b'' to close."""
    return list(payloads) + [b'']


def _sent(conn):
    return [c[0][0] for c in conn.send.call_args_list]


class TestTakeAction:
    @pytest.mark.unit
    def test_builds_a_detection_id_query_parameter(self):
        assert ts.take_action({'did': '12345'}) == '&did=12345'

    @pytest.mark.unit
    def test_unknown_keys_are_ignored(self):
        assert ts.take_action({'other': 'x'}) == ''

    @pytest.mark.unit
    def test_no_actions_produce_no_parameters(self):
        assert ts.take_action({}) == ''

    @pytest.mark.unit
    def test_a_non_string_detection_id_is_coerced(self):
        assert ts.take_action({'did': 42}) == '&did=42'


class TestReadGpioState:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_input_request_returns_only_inputs(self):
        state = {'inputs': [1, 0], 'outputs': [0, 1]}
        with patch.object(ts, 'read_all_gpio_states_as_json', return_value=state):
            assert ts.read_gpio_state('input') == {'inputs': [1, 0]}

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_output_request_returns_only_outputs(self):
        state = {'inputs': [1, 0], 'outputs': [0, 1]}
        with patch.object(ts, 'read_all_gpio_states_as_json', return_value=state):
            assert ts.read_gpio_state('output') == {'outputs': [0, 1]}

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_unknown_type_is_an_error(self):
        with patch.object(ts, 'read_all_gpio_states_as_json', return_value={}):
            assert ts.read_gpio_state('sideways') == \
                {'error': 'Invalid state type requested'}


class TestSetPassFailPins:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_pass_pulses_pin_five(self):
        with patch.object(ts, 'functions') as driver, \
             patch.object(ts, 'pin_state_ref') as pins, \
             patch('time.sleep', new=thread_aware_sleep_mock()):
            assert ts.set_pass_fail_pins({'pass_fail': 'PASS'}) == 'PASS'

        # Pin driven low (asserted), then high again (released).
        assert call(1, 5, 0) in driver.set_gpio.call_args_list
        assert call(1, 5, 1) in driver.set_gpio.call_args_list
        assert pins.update_one.call_count == 2

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_fail_pulses_pin_six(self):
        with patch.object(ts, 'functions') as driver, \
             patch.object(ts, 'pin_state_ref'), \
             patch('time.sleep', new=thread_aware_sleep_mock()):
            assert ts.set_pass_fail_pins({'pass_fail': 'FAIL'}) == 'FAIL'

        assert call(1, 6, 0) in driver.set_gpio.call_args_list

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_both_pins_are_always_released(self):
        # A pin left asserted latches the reject gate open on the next part.
        with patch.object(ts, 'functions') as driver, \
             patch.object(ts, 'pin_state_ref') as pins, \
             patch('time.sleep', new=thread_aware_sleep_mock()):
            ts.set_pass_fail_pins({'pass_fail': 'PASS'})

        released = pins.update_one.call_args[0][1]['$set']
        assert released['GPO5'] is False
        assert released['GPO6'] is False
        assert driver.set_gpio.call_args_list[-2:] == [call(1, 5, 1), call(1, 6, 1)]

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_pulse_is_held_before_release(self):
        with patch.object(ts, 'functions'), \
             patch.object(ts, 'pin_state_ref'), \
             patch('time.sleep', new=thread_aware_sleep_mock()) as sleep:
            ts.set_pass_fail_pins({'pass_fail': 'FAIL'})

        sleep.assert_called_once_with(.5)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_result_without_a_verdict_touches_nothing(self):
        with patch.object(ts, 'functions') as driver, \
             patch.object(ts, 'pin_state_ref') as pins:
            assert ts.set_pass_fail_pins({'other': 1}) is None

        driver.set_gpio.assert_not_called()
        pins.update_one.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_unrecognised_verdict_still_releases_both_pins(self):
        with patch.object(ts, 'functions') as driver, \
             patch.object(ts, 'pin_state_ref'), \
             patch('time.sleep', new=thread_aware_sleep_mock()):
            assert ts.set_pass_fail_pins({'pass_fail': 'UNKNOWN'}) == 'UNKNOWN'

        assert driver.set_gpio.call_args_list == [call(1, 5, 1), call(1, 6, 1)]


class TestHelpPayload:
    @pytest.mark.unit
    def test_lists_the_configured_prediction_commands(self):
        payload = ts.help_payload({'cmd1': PRESET, 'cmd2': PRESET})
        run = payload['commands']['Run Prediction']

        assert sorted(run['Valid Commands (based on your presets)']) == ['cmd1', 'cmd2']

    @pytest.mark.unit
    def test_documents_the_pin_read_commands(self):
        commands = ts.help_payload({})['commands']
        assert commands['Read Input Pins'] == 'GPIread'
        assert commands['Read Output Pins'] == 'GPOread'

    @pytest.mark.unit
    def test_is_json_serialisable(self):
        assert json.loads(json.dumps(ts.help_payload({'cmd1': PRESET})))


class TestLoadValidCommands:
    @pytest.mark.unit
    def test_indexes_tcp_presets_by_their_command_string(self):
        with patch.object(ts.io_ref, 'find', return_value=[PRESET]) as find:
            assert ts.load_valid_commands() == {'cmd1': PRESET}
        find.assert_called_once_with({'ioType': 'TCP'})

    @pytest.mark.unit
    def test_no_presets_is_an_empty_table(self):
        with patch.object(ts.io_ref, 'find', return_value=[]):
            assert ts.load_valid_commands() == {}


class TestHandleConnectionReadCommands:
    @pytest.mark.unit
    def test_help_returns_the_command_reference(self, conn):
        conn.recv.side_effect = _frames(b'help')

        ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        assert json.loads(_sent(conn)[0])['commands']['Read Input Pins'] == 'GPIread'

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_gpiread_returns_the_input_pins(self, conn):
        conn.recv.side_effect = _frames(b'GPIread')
        with patch.object(ts, 'read_all_gpio_states_as_json',
                          return_value={'inputs': [1, 0], 'outputs': [0, 0]}):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {}, CONFIG)

        assert json.loads(_sent(conn)[0]) == {'inputs': [1, 0]}

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_gporead_returns_the_output_pins(self, conn):
        conn.recv.side_effect = _frames(b'GPOread')
        with patch.object(ts, 'read_all_gpio_states_as_json',
                          return_value={'inputs': [0, 0], 'outputs': [1, 1]}):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {}, CONFIG)

        assert json.loads(_sent(conn)[0]) == {'outputs': [1, 1]}


class TestHandleConnectionPinCommands:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_single_character_key_sets_an_output_pin(self, conn):
        conn.recv.side_effect = _frames(b'{"1": true}')
        with patch.object(ts, 'set_pin_state', return_value='on') as set_pin, \
             patch.object(ts, 'log_signal') as log:
            ts.handle_connection(conn, ('10.0.0.9', 4000), {}, CONFIG)

        set_pin.assert_called_once_with('1', True)
        assert _sent(conn) == [b'on\n']
        log.assert_called_once_with(b'{"1": true}')

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_turning_a_pin_off_is_acknowledged(self, conn):
        conn.recv.side_effect = _frames(b'{"2": false}')
        with patch.object(ts, 'set_pin_state', return_value='off'), \
             patch.object(ts, 'log_signal'):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {}, CONFIG)

        assert _sent(conn) == [b'off\n']

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_raw_frame_is_recorded_before_the_pin_moves(self, conn):
        # gpio_csv_logger reconstructs cycle timing from these frames; logging
        # after the driver call would attribute the wrong timestamp.
        order = []
        conn.recv.side_effect = _frames(b'{"1": true}')
        with patch.object(ts, 'set_pin_state',
                          side_effect=lambda *a: order.append('set') or 'on'), \
             patch.object(ts, 'log_signal', side_effect=lambda d: order.append('log')):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {}, CONFIG)

        assert order == ['log', 'set']


class TestHandleConnectionPredictions:
    def _response(self, status=200, body=None):
        resp = MagicMock(status_code=status)
        resp.json.return_value = body if body is not None else {'pass_fail': 'PASS'}
        return resp

    @pytest.mark.unit
    def test_a_known_command_triggers_a_prediction(self, conn):
        conn.recv.side_effect = _frames(b'{"cmd1": {}}')
        with patch.object(ts.util_ref, 'find_one', return_value={'token': 'tok'}), \
             patch('requests.get', return_value=self._response()) as get, \
             patch.object(ts, 'set_pass_fail_pins'):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        url = get.call_args[0][0]
        assert '/api/capture/predict/snap/widgets/3/cam0' in url
        assert get.call_args[1]['headers'] == {'Authorization': 'Bearer tok'}

    @pytest.mark.unit
    def test_the_workstation_records_the_caller_address(self, conn):
        conn.recv.side_effect = _frames(b'{"cmd1": {}}')
        with patch.object(ts.util_ref, 'find_one', return_value={'token': 'tok'}), \
             patch('requests.get', return_value=self._response()) as get, \
             patch.object(ts, 'set_pass_fail_pins'):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        assert 'workstation=TCP: 10.0.0.9:cmd1' in get.call_args[0][0]

    @pytest.mark.unit
    def test_a_detection_id_is_forwarded(self, conn):
        conn.recv.side_effect = _frames(b'{"cmd1": {"did": "12345"}}')
        with patch.object(ts.util_ref, 'find_one', return_value={'token': 'tok'}), \
             patch('requests.get', return_value=self._response()) as get, \
             patch.object(ts, 'set_pass_fail_pins'):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        assert '&did=12345' in get.call_args[0][0]

    @pytest.mark.unit
    def test_the_result_is_returned_to_the_neighbour(self, conn):
        conn.recv.side_effect = _frames(b'{"cmd1": {}}')
        body = {'pass_fail': 'PASS', 'predictions': [1]}
        with patch.object(ts.util_ref, 'find_one', return_value={'token': 'tok'}), \
             patch('requests.get', return_value=self._response(body=body)), \
             patch.object(ts, 'set_pass_fail_pins'):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        assert json.loads(conn.sendall.call_args[0][0]) == body

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_verdict_drives_the_pass_fail_pins(self, conn):
        conn.recv.side_effect = _frames(b'{"cmd1": {}}')
        with patch.object(ts.util_ref, 'find_one', return_value={'token': 'tok'}), \
             patch('requests.get', return_value=self._response()), \
             patch.object(ts, 'set_pass_fail_pins') as pins:
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        pins.assert_called_once_with({'pass_fail': 'PASS'})

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_gpio_failure_does_not_stop_the_reply(self, conn):
        # The neighbour is waiting on the socket; a driver fault must not hang
        # it.
        conn.recv.side_effect = _frames(b'{"cmd1": {}}')
        with patch.object(ts.util_ref, 'find_one', return_value={'token': 'tok'}), \
             patch('requests.get', return_value=self._response()), \
             patch.object(ts, 'set_pass_fail_pins', side_effect=OSError('no driver')):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        conn.sendall.assert_called_once()

    @pytest.mark.unit
    def test_fields_disabled_in_config_are_stripped(self, conn):
        conn.recv.side_effect = _frames(b'{"cmd1": {}}')
        body = {'pass_fail': 'PASS', 'predictions': [1], 'image': 'base64...'}
        with patch.object(ts.util_ref, 'find_one', return_value={'token': 'tok'}), \
             patch('requests.get', return_value=self._response(body=body)), \
             patch.object(ts, 'set_pass_fail_pins'):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        sent = json.loads(conn.sendall.call_args[0][0])
        assert 'image' not in sent
        assert sent['predictions'] == [1]

    @pytest.mark.unit
    def test_the_packet_header_frames_the_payload_when_enabled(self, conn):
        # STX/ETX framing with a length prefix, for neighbours that cannot
        # read a bare JSON stream.
        conn.recv.side_effect = _frames(b'{"cmd1": {}}')
        config = dict(CONFIG, packet_header=True)
        with patch.object(ts.util_ref, 'find_one', return_value={'token': 'tok'}), \
             patch('requests.get', return_value=self._response(body={'a': 1})), \
             patch.object(ts, 'set_pass_fail_pins'):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, config)

        framed = conn.sendall.call_args[0][0]
        payload = json.dumps({'a': 1}).encode()
        assert framed == b'\x01' + str(len(payload)).encode() + \
            b'\x02' + payload + b'\x03' + b'\x0d'

    @pytest.mark.unit
    def test_a_failed_send_falls_back_to_an_error_marker(self, conn):
        conn.recv.side_effect = _frames(b'{"cmd1": {}}')
        conn.sendall.side_effect = [socket.error('broken pipe'), None]
        with patch.object(ts.util_ref, 'find_one', return_value={'token': 'tok'}), \
             patch('requests.get', return_value=self._response()), \
             patch.object(ts, 'set_pass_fail_pins'):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        assert conn.sendall.call_args_list[-1] == call(b'-1')

    @pytest.mark.unit
    def test_a_non_200_prediction_reports_failure(self, conn):
        conn.recv.side_effect = _frames(b'{"cmd1": {}}')
        with patch.object(ts.util_ref, 'find_one', return_value={'token': 'tok'}), \
             patch('requests.get', return_value=self._response(status=500)):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        assert _sent(conn) == [b'request failed\n']
        conn.sendall.assert_not_called()


class TestHandleConnectionErrors:
    @pytest.mark.unit
    def test_an_unknown_command_is_rejected(self, conn):
        conn.recv.side_effect = _frames(b'{"nope": {}}')
        ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        assert _sent(conn) == [b'Invalid Command\n']

    @pytest.mark.unit
    def test_unparseable_json_is_rejected_without_dropping_the_connection(self, conn):
        conn.recv.side_effect = _frames(b'not json at all', b'{"nope": {}}')
        ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        # Both frames answered; the neighbour is not disconnected on a typo.
        assert _sent(conn) == [b'Invalid Command\n', b'Invalid Command\n']

    @pytest.mark.unit
    def test_an_empty_frame_closes_the_connection(self, conn):
        conn.recv.side_effect = [b'']
        ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        conn.send.assert_not_called()

    @pytest.mark.unit
    def test_a_socket_error_does_not_end_the_session(self, conn):
        conn.recv.side_effect = [socket.error('reset'), b'{"nope": {}}', b'']
        ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        assert _sent(conn) == [b'Invalid Command\n']

    @pytest.mark.unit
    def test_invalid_utf8_on_the_first_frame_crashes_the_handler(self, conn):
        # `command` is assigned only after data.decode() succeeds, so a frame
        # that is not valid UTF-8 leaves it unbound and the `command in
        # valid_commands` check below raises. A neighbour with a mismatched
        # encoding takes the connection handler down.
        conn.recv.side_effect = _frames(b'\xff\xfe')

        with pytest.raises(NameError):
            ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

    @pytest.mark.unit
    def test_a_later_invalid_frame_is_answered_with_the_previous_command(self, conn):
        # Once `command` is bound by an earlier frame it survives the decode
        # failure, so the bad frame is answered rather than crashing.
        conn.recv.side_effect = _frames(b'{"nope": {}}', b'\xff\xfe')
        ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)

        assert _sent(conn) == [b'Invalid Command\n', b'Invalid Command\n']

    @pytest.mark.unit
    def test_a_missing_id_token_crashes_the_handler(self, conn):
        # An unauthorised device has no token document, and the lookup result
        # is subscripted without a guard.
        conn.recv.side_effect = _frames(b'{"cmd1": {}}')
        with patch.object(ts.util_ref, 'find_one', return_value=None):
            with pytest.raises(TypeError):
                ts.handle_connection(conn, ('10.0.0.9', 4000), {'cmd1': PRESET}, CONFIG)


class TestHandleConnectionDefaults:
    @pytest.mark.unit
    def test_presets_and_config_are_loaded_per_connection(self, conn):
        # Reloading on each connect is what lets a preset edit take effect
        # without restarting the daemon.
        conn.recv.side_effect = [b'']
        with patch.object(ts, 'load_valid_commands', return_value={}) as commands, \
             patch.object(ts, 'load_tcp_config', return_value=CONFIG) as config:
            ts.handle_connection(conn, ('10.0.0.9', 4000))

        commands.assert_called_once()
        config.assert_called_once()


class TestServerLifecycle:
    @pytest.mark.unit
    def test_binds_and_listens_on_the_documented_port(self):
        sock = MagicMock()
        with patch('socket.socket', return_value=sock):
            assert ts.create_server() is sock

        sock.bind.assert_called_once_with(('0.0.0.0', 5300))
        sock.listen.assert_called_once_with(1)

    @pytest.mark.unit
    def test_the_socket_is_a_tcp_stream(self):
        with patch('socket.socket', return_value=MagicMock()) as factory:
            ts.create_server()
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)

    @pytest.mark.unit
    def test_each_connection_is_closed_after_being_served(self):
        sock = MagicMock()
        conn = MagicMock()
        sock.accept.side_effect = [(conn, ('10.0.0.9', 4000)), KeyboardInterrupt]

        with patch.object(ts, 'handle_connection'):
            with pytest.raises(KeyboardInterrupt):
                ts.serve_forever(sock)

        conn.close.assert_called_once()

    @pytest.mark.unit
    def test_a_handler_that_raises_still_closes_the_socket(self):
        # A leaked descriptor per crash exhausts the process over a shift.
        sock = MagicMock()
        conn = MagicMock()
        sock.accept.return_value = (conn, ('10.0.0.9', 4000))

        with patch.object(ts, 'handle_connection', side_effect=RuntimeError('boom')):
            with pytest.raises(RuntimeError):
                ts.serve_forever(sock)

        conn.close.assert_called_once()

    @pytest.mark.unit
    def test_main_creates_the_server_and_serves(self):
        sock = MagicMock()
        with patch.object(ts, 'create_server', return_value=sock) as create, \
             patch.object(ts, 'serve_forever') as serve:
            ts.main()

        create.assert_called_once()
        serve.assert_called_once_with(sock)

    @pytest.mark.unit
    def test_importing_does_not_bind_a_listening_socket(self):
        # The bind and the accept loop used to run at module scope, which is
        # why this module sat at 0% coverage and could not be imported at all
        # off a device. Asserting on socket.socket directly is not usable here
        # - the MongoDB monitor threads open sockets of their own - so the
        # check is that no module-level server socket survives the import.
        module = _load_tcp_server()

        assert not hasattr(module, 'sock')
        assert not hasattr(module, 'connections')
        assert callable(module.main)
