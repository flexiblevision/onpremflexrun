"""Pin access from processes other than gpio_controller."""
import importlib.util
import os
import pytest
from unittest.mock import patch, MagicMock


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load_client():
    # conftest replaces the `gpio` package with a mock, so load by path.
    path = os.path.join(REPO, 'gpio', 'gpio_client.py')
    spec = importlib.util.spec_from_file_location('_gpio_client_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client = _load_client()


def _response(body):
    resp = MagicMock()
    resp.json.return_value = body
    return resp


class TestGpioClient:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_set_output_posts_the_pin_and_returns_the_driver_code(self):
        with patch('requests.post', return_value=_response({'state': 'on', 'rc': 0})) as post:
            assert client.set_output('3', True) == 0

        assert post.call_args[0][0] == 'http://127.0.0.1:1817/api/gpio/set'
        assert post.call_args[1]['json'] == {'pin': 3, 'state': True}
        assert post.call_args[1]['timeout'] == client.TIMEOUT

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_only_an_exact_true_turns_a_pin_on(self):
        with patch('requests.post', return_value=_response({'rc': 0})) as post:
            assert client.set_pin_state(3, 'on') == 'off'

        assert post.call_args[1]['json']['state'] is False

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_http_error_raises(self):
        resp = _response({})
        resp.raise_for_status.side_effect = Exception('403')
        with patch('requests.post', return_value=resp):
            with pytest.raises(Exception):
                client.set_output(3, True)

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_toggle_returns_the_new_state(self):
        with patch('requests.post', return_value=_response({'state': True})) as post:
            assert client.toggle_pin(4) is True

        assert post.call_args[0][0].endswith('/api/gpio/toggle')

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_read_pin_reports_whether_the_input_is_asserted(self):
        with patch('requests.get', return_value=_response({'asserted': True})) as get:
            assert client.read_pin('2') is True

        assert get.call_args[0][0].endswith('/api/gpio/input/2')

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_read_all_returns_the_controller_snapshot(self):
        state = {'inputs': [1] * 8, 'outputs': [0] * 8}
        with patch('requests.get', return_value=_response(state)):
            assert client.read_all_gpio_states_as_json() == state

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_pass_fail_waits_out_the_pulse(self):
        with patch('requests.post', return_value=_response({'pass_fail': 'PASS'})) as post:
            assert client.pulse_pass_fail('PASS') == 'PASS'

        assert post.call_args[1]['timeout'] > client.TIMEOUT
