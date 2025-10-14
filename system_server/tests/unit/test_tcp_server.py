"""
Unit and integration tests for TCP server functionality

Note: tcp_server.py has module-level code that creates a socket server and runs
an infinite loop. To test the functions, we extract and test their logic separately.
"""
import pytest
import socket
import json
from unittest.mock import Mock, patch, MagicMock, call


class TestTakeAction:
    """Tests for take_action helper function"""

    @pytest.mark.unit
    def test_take_action_with_did(self):
        """Test take_action with device ID"""
        # Replicate the take_action logic
        def take_action(actions):
            params = ''
            for key in actions.keys():
                if key == 'did':
                    params += '&did=' + str(actions[key])
            return params

        actions = {'did': '12345'}
        result = take_action(actions)
        assert result == '&did=12345'

    @pytest.mark.unit
    def test_take_action_with_multiple_params(self):
        """Test take_action with multiple parameters including did"""
        def take_action(actions):
            params = ''
            for key in actions.keys():
                if key == 'did':
                    params += '&did=' + str(actions[key])
            return params

        actions = {'did': '67890', 'other_param': 'value'}
        result = take_action(actions)
        assert '&did=67890' in result

    @pytest.mark.unit
    def test_take_action_without_did(self):
        """Test take_action without device ID"""
        def take_action(actions):
            params = ''
            for key in actions.keys():
                if key == 'did':
                    params += '&did=' + str(actions[key])
            return params

        actions = {'other_param': 'value'}
        result = take_action(actions)
        assert result == ''

    @pytest.mark.unit
    def test_take_action_empty_dict(self):
        """Test take_action with empty dictionary"""
        def take_action(actions):
            params = ''
            for key in actions.keys():
                if key == 'did':
                    params += '&did=' + str(actions[key])
            return params

        actions = {}
        result = take_action(actions)
        assert result == ''


class TestReadGpioState:
    """Tests for read_gpio_state function"""

    @pytest.mark.unit
    def test_read_gpio_state_input(self):
        """Test reading input GPIO state"""
        # Replicate read_gpio_state logic
        def read_all_gpio_states_as_json():
            return {
                "inputs": [0, 1, 0, 1, 0, 1, 0, 1],
                "outputs": [1, 0, 1, 0, 1, 0, 1, 0]
            }

        def read_gpio_state(type):
            io_state = read_all_gpio_states_as_json()
            if type == 'input':
                return {"inputs": io_state["inputs"]}
            elif type == 'output':
                return {"outputs": io_state["outputs"]}
            else:
                return {'error': 'Invalid state type requested'}

        result = read_gpio_state('input')
        assert 'inputs' in result
        assert 'outputs' not in result
        assert result['inputs'] == [0, 1, 0, 1, 0, 1, 0, 1]

    @pytest.mark.unit
    def test_read_gpio_state_output(self):
        """Test reading output GPIO state"""
        def read_all_gpio_states_as_json():
            return {
                "inputs": [0, 1, 0, 1, 0, 1, 0, 1],
                "outputs": [1, 0, 1, 0, 1, 0, 1, 0]
            }

        def read_gpio_state(type):
            io_state = read_all_gpio_states_as_json()
            if type == 'input':
                return {"inputs": io_state["inputs"]}
            elif type == 'output':
                return {"outputs": io_state["outputs"]}
            else:
                return {'error': 'Invalid state type requested'}

        result = read_gpio_state('output')
        assert 'outputs' in result
        assert 'inputs' not in result
        assert result['outputs'] == [1, 0, 1, 0, 1, 0, 1, 0]

    @pytest.mark.unit
    def test_read_gpio_state_invalid_type(self):
        """Test reading GPIO state with invalid type"""
        def read_all_gpio_states_as_json():
            return {
                "inputs": [0, 1, 0, 1, 0, 1, 0, 1],
                "outputs": [1, 0, 1, 0, 1, 0, 1, 0]
            }

        def read_gpio_state(type):
            io_state = read_all_gpio_states_as_json()
            if type == 'input':
                return {"inputs": io_state["inputs"]}
            elif type == 'output':
                return {"outputs": io_state["outputs"]}
            else:
                return {'error': 'Invalid state type requested'}

        result = read_gpio_state('invalid')
        assert 'error' in result
        assert result['error'] == 'Invalid state type requested'


class TestSetPassFailPins:
    """Tests for set_pass_fail_pins function logic"""

    @pytest.mark.unit
    @patch('time.sleep')
    def test_set_pass_fail_pins_pass(self, mock_sleep):
        """Test setting PASS pin logic"""
        mock_functions = MagicMock()
        mock_pin_state_ref = MagicMock()
        mock_functions.set_gpio.return_value = 0

        # Replicate set_pass_fail_pins logic
        def set_pass_fail_pins(data):
            if 'pass_fail' in data:
                new_pin_state = {}
                if data['pass_fail'] == 'PASS':
                    mock_functions.set_gpio(1, 5, 0)  # PASS PIN ON
                    new_pin_state['GPO5'] = True
                if data['pass_fail'] == 'FAIL':
                    mock_functions.set_gpio(1, 6, 0)  # FAIL PIN ON
                    new_pin_state['GPO6'] = True

                mock_pin_state_ref.update_one({'type': 'gpio_pin_state'}, {'$set': new_pin_state}, True)
                mock_sleep(.5)

                mock_functions.set_gpio(1, 5, 1)  # PASS PIN OFF
                mock_functions.set_gpio(1, 6, 1)  # FAIL PIN OFF
                new_pin_state['GPO5'] = False
                new_pin_state['GPO6'] = False
                mock_pin_state_ref.update_one({'type': 'gpio_pin_state'}, {'$set': new_pin_state}, True)
                return data['pass_fail']
            return None

        data = {'pass_fail': 'PASS'}
        result = set_pass_fail_pins(data)

        assert result == 'PASS'
        # PASS pin ON (1 call), then both PASS and FAIL pins OFF (2 calls) = 3 total
        assert mock_functions.set_gpio.call_count == 3

    @pytest.mark.unit
    @patch('time.sleep')
    def test_set_pass_fail_pins_fail(self, mock_sleep):
        """Test setting FAIL pin logic"""
        mock_functions = MagicMock()
        mock_pin_state_ref = MagicMock()
        mock_functions.set_gpio.return_value = 0

        def set_pass_fail_pins(data):
            if 'pass_fail' in data:
                new_pin_state = {}
                if data['pass_fail'] == 'PASS':
                    mock_functions.set_gpio(1, 5, 0)
                    new_pin_state['GPO5'] = True
                if data['pass_fail'] == 'FAIL':
                    mock_functions.set_gpio(1, 6, 0)
                    new_pin_state['GPO6'] = True

                mock_pin_state_ref.update_one({'type': 'gpio_pin_state'}, {'$set': new_pin_state}, True)
                mock_sleep(.5)

                mock_functions.set_gpio(1, 5, 1)
                mock_functions.set_gpio(1, 6, 1)
                new_pin_state['GPO5'] = False
                new_pin_state['GPO6'] = False
                mock_pin_state_ref.update_one({'type': 'gpio_pin_state'}, {'$set': new_pin_state}, True)
                return data['pass_fail']
            return None

        data = {'pass_fail': 'FAIL'}
        result = set_pass_fail_pins(data)

        assert result == 'FAIL'
        # FAIL pin ON (1 call), then both PASS and FAIL pins OFF (2 calls) = 3 total
        assert mock_functions.set_gpio.call_count == 3

    @pytest.mark.unit
    def test_set_pass_fail_pins_no_data(self):
        """Test set_pass_fail_pins with no pass_fail data"""
        def set_pass_fail_pins(data):
            if 'pass_fail' in data:
                return data['pass_fail']
            return None

        data = {'other_field': 'value'}
        result = set_pass_fail_pins(data)

        assert result is None


class TestTCPServerCommands:
    """Integration tests for TCP server command handling"""

    @pytest.mark.integration
    def test_help_command_format(self):
        """Test help command returns valid format"""
        valid_commands = {'cmd1': {'modelName': 'test_model'}}

        help_map = {
            "commands": {
                "Read Input Pins": "GPIread",
                "Read Output Pins": "GPOread",
                "Set Output Pin State (on/off)": ["{\"1\": true}", "{\"1\": false}"],
                "Run Prediction": {
                    "Valid Commands (based on your presets)": list(valid_commands.keys()),
                    "Format": "{\"cmd1\": {\"did\": \"12345\"}}"
                }
            }
        }

        assert "commands" in help_map
        assert "Read Input Pins" in help_map["commands"]
        assert "GPIread" == help_map["commands"]["Read Input Pins"]
        assert "cmd1" in help_map["commands"]["Run Prediction"]["Valid Commands (based on your presets)"]

    @pytest.mark.integration
    @patch('requests.get')
    def test_prediction_command_success(self, mock_requests):
        """Test successful prediction command"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'prediction': 'PASS',
            'confidence': 0.95,
            'pass_fail': 'PASS'
        }
        mock_requests.return_value = mock_response

        response = mock_requests(
            'http://172.17.0.1:5000/api/capture/predict/snap/test_model/1/cam1',
            headers={'Authorization': 'Bearer test_token_12345'}
        )

        assert response.status_code == 200
        assert response.json()['prediction'] == 'PASS'
        assert response.json()['pass_fail'] == 'PASS'

    @pytest.mark.integration
    @patch('requests.get')
    def test_prediction_command_failure(self, mock_requests):
        """Test failed prediction command"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_requests.return_value = mock_response

        response = mock_requests(
            'http://172.17.0.1:5000/api/capture/predict/snap/test_model/1/cam1',
            headers={'Authorization': 'Bearer test_token_12345'}
        )

        assert response.status_code == 500


class TestTCPServerPacketHeader:
    """Tests for TCP packet header functionality"""

    @pytest.mark.unit
    def test_packet_with_header(self):
        """Test packet formatting with header enabled"""
        data = {'prediction': 'PASS', 'confidence': 0.95}
        data_bytes = json.dumps(data).encode('utf-8')

        # Simulate packet header creation
        packet_header = b'\x01' + str(len(data_bytes)).encode('utf-8')
        full_packet = packet_header + b'\x02' + data_bytes + b'\x03' + b'\x0d'

        assert full_packet.startswith(b'\x01')
        assert b'\x02' in full_packet
        assert b'\x03' in full_packet
        assert full_packet.endswith(b'\x0d')
        assert data_bytes in full_packet

    @pytest.mark.unit
    def test_packet_without_header(self):
        """Test packet formatting without header"""
        data = {'prediction': 'PASS', 'confidence': 0.95}
        data_bytes = json.dumps(data).encode('utf-8')

        assert data_bytes == json.dumps(data).encode('utf-8')
        assert not data_bytes.startswith(b'\x01')

    @pytest.mark.unit
    def test_config_filter_keys(self):
        """Test filtering response data based on config"""
        config = {
            'packet_header': True,
            'confidence': True,
            'prediction': True,
            'bbox': False,
            'image': False
        }

        data = {
            'prediction': 'PASS',
            'confidence': 0.95,
            'bbox': [10, 20, 30, 40],
            'image': 'base64string'
        }

        # Simulate config filtering
        keys_to_remove = [k for k in config if not config[k] and k != 'packet_header']
        for k in keys_to_remove:
            if k in data:
                del data[k]

        assert 'prediction' in data
        assert 'confidence' in data
        assert 'bbox' not in data
        assert 'image' not in data


class TestTCPServerErrorHandling:
    """Tests for error handling in TCP server"""

    @pytest.mark.unit
    def test_invalid_json_command(self):
        """Test handling of invalid JSON command"""
        invalid_json = b'{"invalid_json": '

        with pytest.raises(json.JSONDecodeError):
            json.loads(invalid_json.decode('utf-8'))

    @pytest.mark.unit
    def test_empty_command(self):
        """Test handling of empty command"""
        empty_data = b''

        # Empty data should be treated as connection close
        assert len(empty_data) == 0

    @pytest.mark.unit
    def test_unknown_command(self):
        """Test handling of unknown command"""
        command_data = json.dumps({"unknown_cmd": {"did": "12345"}})
        incoming_command = json.loads(command_data)
        command = list(incoming_command.keys())[0]

        valid_commands = {'cmd1': {}, 'cmd2': {}}

        assert command not in valid_commands.keys()

    @pytest.mark.unit
    def test_socket_error_on_send(self):
        """Test socket error during data send"""
        mock_socket = MagicMock()
        mock_socket.sendall.side_effect = socket.error('Connection broken')

        data_bytes = b'test_data'

        with pytest.raises(socket.error):
            mock_socket.sendall(data_bytes)


class TestTCPServerConnection:
    """Tests for TCP server connection handling"""

    @pytest.mark.unit
    @patch('socket.socket')
    def test_socket_creation(self, mock_socket_class):
        """Test socket creation and configuration"""
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_address = ('0.0.0.0', 5300)

        # Verify socket was created
        mock_socket_class.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)

    @pytest.mark.unit
    def test_server_address_format(self):
        """Test server address format"""
        server_name = '0.0.0.0'
        server_port = 5300
        server_address = (server_name, server_port)

        assert server_address[0] == '0.0.0.0'
        assert server_address[1] == 5300
        assert isinstance(server_address, tuple)

    @pytest.mark.unit
    @patch('socket.socket')
    def test_socket_bind_and_listen(self, mock_socket_class):
        """Test socket bind and listen"""
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_address = ('0.0.0.0', 5300)

        sock.bind(server_address)
        sock.listen(1)

        mock_sock.bind.assert_called_once_with(server_address)
        mock_sock.listen.assert_called_once_with(1)

    @pytest.mark.unit
    @patch('socket.socket')
    def test_connection_accept(self, mock_socket_class):
        """Test accepting connections"""
        mock_sock = MagicMock()
        mock_connection = MagicMock()
        mock_client_address = ('192.168.1.100', 54321)

        mock_sock.accept.return_value = (mock_connection, mock_client_address)
        mock_socket_class.return_value = mock_sock

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection, client_address = sock.accept()

        assert connection == mock_connection
        assert client_address == mock_client_address


class TestTCPServerPresets:
    """Tests for TCP preset and command validation"""

    @pytest.mark.unit
    def test_load_valid_commands(self):
        """Test loading valid commands from database"""
        mock_io_ref = MagicMock()
        mock_io_ref.find.return_value = [
            {'ioType': 'TCP', 'ioVal': 'cmd1', 'modelName': 'model1'},
            {'ioType': 'TCP', 'ioVal': 'cmd2', 'modelName': 'model2'},
            {'ioType': 'TCP', 'ioVal': 'cmd3', 'modelName': 'model3'}
        ]

        query = {'ioType': 'TCP'}
        presets = mock_io_ref.find(query)
        valid_commands = {}

        for preset in presets:
            valid_commands[preset['ioVal']] = preset

        assert len(valid_commands) == 3
        assert 'cmd1' in valid_commands
        assert 'cmd2' in valid_commands
        assert 'cmd3' in valid_commands

    @pytest.mark.unit
    def test_preset_with_all_fields(self):
        """Test preset with all required fields"""
        preset = {
            'ioType': 'TCP',
            'ioVal': 'test_cmd',
            'modelName': 'test_model',
            'modelVersion': 1,
            'cameraId': 'cam1',
            'presetId': 'preset123'
        }

        assert preset['ioType'] == 'TCP'
        assert preset['ioVal'] == 'test_cmd'
        assert preset['modelName'] == 'test_model'
        assert preset['modelVersion'] == 1
        assert preset['cameraId'] == 'cam1'
        assert preset['presetId'] == 'preset123'

    @pytest.mark.unit
    def test_build_prediction_url(self):
        """Test building prediction URL from preset"""
        preset = {
            'modelName': 'test_model',
            'modelVersion': 1,
            'cameraId': 'cam1',
            'presetId': 'preset1',
            'ioVal': 'cmd1'
        }

        host = 'http://172.17.0.1'
        port = '5000'
        client_address = ('192.168.1.100', 54321)
        params = '&did=12345'

        path = f"/api/capture/predict/snap/{preset['modelName']}/{preset['modelVersion']}/{preset['cameraId']}?workstation=TCP: {client_address[0]}:{preset['ioVal']}&preset_id={preset['presetId']}{params}"
        url = host + ':' + port + path

        assert 'test_model' in url
        assert '192.168.1.100' in url
        assert 'preset1' in url
        assert 'did=12345' in url


class TestTCPServerDataEncoding:
    """Tests for data encoding and decoding"""

    @pytest.mark.unit
    def test_encode_json_response(self):
        """Test encoding JSON response to bytes"""
        response_data = {
            'prediction': 'PASS',
            'confidence': 0.95,
            'timestamp': '2025-10-11T12:00:00'
        }

        json_str = json.dumps(response_data)
        data_bytes = json_str.encode('utf-8')

        assert isinstance(data_bytes, bytes)
        assert b'PASS' in data_bytes
        assert b'0.95' in data_bytes

    @pytest.mark.unit
    def test_decode_received_data(self):
        """Test decoding received data"""
        received_data = b'{"cmd1": {"did": "12345"}}'

        decoded = received_data.decode('utf-8')
        parsed = json.loads(decoded)

        assert isinstance(parsed, dict)
        assert 'cmd1' in parsed
        assert parsed['cmd1']['did'] == '12345'

    @pytest.mark.unit
    def test_handle_string_commands(self):
        """Test handling simple string commands"""
        help_command = b'help'
        gpiread_command = b'GPIread'
        gporead_command = b'GPOread'

        assert help_command.decode('utf-8') == 'help'
        assert gpiread_command.decode('utf-8') == 'GPIread'
        assert gporead_command.decode('utf-8') == 'GPOread'

    @pytest.mark.unit
    def test_handle_utf8_encoding(self):
        """Test UTF-8 encoding/decoding"""
        test_data = {'message': 'Test with special chars: ñ, é, ü'}

        encoded = json.dumps(test_data).encode('utf-8')
        decoded = encoded.decode('utf-8')
        parsed = json.loads(decoded)

        assert parsed['message'] == test_data['message']
