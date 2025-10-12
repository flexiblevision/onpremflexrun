"""
Unit tests for retrieve_programs.py worker script
"""
import pytest
import string
from unittest.mock import Mock, patch, MagicMock


class TestFormatFilename:
    """Tests for format_filename function"""

    @pytest.mark.unit
    def test_format_filename_basic(self):
        """Test basic filename formatting"""
        from worker_scripts.retrieve_programs import format_filename

        result = format_filename('My Program Name')
        assert result == 'My_Program_Name'

    @pytest.mark.unit
    def test_format_filename_special_chars(self):
        """Test filename formatting removes special characters"""
        from worker_scripts.retrieve_programs import format_filename

        result = format_filename('Program@#$%Name!')
        assert '@' not in result
        assert '#' not in result
        assert '$' not in result
        assert '%' not in result
        assert '!' not in result

    @pytest.mark.unit
    def test_format_filename_keeps_valid_chars(self):
        """Test that valid characters are preserved"""
        from worker_scripts.retrieve_programs import format_filename

        result = format_filename('Program-Name_123.test')
        assert result == 'Program-Name_123.test'

    @pytest.mark.unit
    def test_format_filename_replaces_spaces(self):
        """Test that spaces are replaced with underscores"""
        from worker_scripts.retrieve_programs import format_filename

        result = format_filename('Program With Many Spaces')
        assert result == 'Program_With_Many_Spaces'
        assert ' ' not in result

    @pytest.mark.unit
    def test_format_filename_empty_string(self):
        """Test formatting empty string"""
        from worker_scripts.retrieve_programs import format_filename

        result = format_filename('')
        assert result == ''

    @pytest.mark.unit
    def test_format_filename_only_invalid_chars(self):
        """Test formatting string with only invalid characters"""
        from worker_scripts.retrieve_programs import format_filename

        result = format_filename('@#$%^&*!')
        assert result == ''

    @pytest.mark.unit
    def test_format_filename_preserves_case(self):
        """Test that case is preserved"""
        from worker_scripts.retrieve_programs import format_filename

        result = format_filename('MyProgram')
        assert result == 'MyProgram'
        assert result != 'myprogram'

    @pytest.mark.unit
    def test_format_filename_alphanumeric_only(self):
        """Test filename with only alphanumeric characters"""
        from worker_scripts.retrieve_programs import format_filename

        result = format_filename('Program123ABC')
        assert result == 'Program123ABC'

    @pytest.mark.unit
    def test_format_filename_parentheses(self):
        """Test that parentheses are allowed"""
        from worker_scripts.retrieve_programs import format_filename

        result = format_filename('Program(v1.0)')
        assert '(' in result
        assert ')' in result

    @pytest.mark.unit
    def test_format_filename_dots_and_dashes(self):
        """Test that dots and dashes are allowed"""
        from worker_scripts.retrieve_programs import format_filename

        result = format_filename('my-program.v1.2')
        assert result == 'my-program.v1.2'


class TestRetrievePrograms:
    """Tests for retrieve_programs function"""

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_basic(self, mock_get, mock_programs_collection):
        """Test basic program retrieval"""
        from worker_scripts.retrieve_programs import retrieve_programs

        # Mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'records': [
                {'id': 'prog1', 'model': 'Model One', 'name': 'Program 1'},
                {'id': 'prog2', 'model': 'Model Two', 'name': 'Program 2'}
            ]
        }
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {},
                'project2': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Should make request for each project
        assert mock_get.call_count == 2
        # Should update programs collection for each program (2 programs * 2 projects)
        assert mock_programs_collection.update_one.call_count == 4

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_correct_url(self, mock_get, mock_programs_collection):
        """Test that correct URL is constructed"""
        from worker_scripts.retrieve_programs import retrieve_programs, CLOUD_DOMAIN

        mock_response = MagicMock()
        mock_response.json.return_value = {'records': []}
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project123': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Verify URL construction with pagination and use_latest flag
        expected_url = f'{CLOUD_DOMAIN}/api/capture/programs/project123/0/9999?use_latest=true'
        mock_get.assert_called_once()
        call_args = mock_get.call_args[0]
        assert expected_url in str(call_args)

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_with_auth_headers(self, mock_get, mock_programs_collection):
        """Test that authorization headers are included"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {'records': []}
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }
        token = 'test_bearer_token'

        retrieve_programs(resp_data, token)

        # Check headers
        call_kwargs = mock_get.call_args[1]
        assert 'headers' in call_kwargs
        assert call_kwargs['headers']['Authorization'] == f'Bearer {token}'
        assert call_kwargs['headers']['Content-Type'] == 'application/json'

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_with_timeout(self, mock_get, mock_programs_collection):
        """Test that request includes timeout"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {'records': []}
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        call_kwargs = mock_get.call_args[1]
        assert 'timeout' in call_kwargs
        assert call_kwargs['timeout'] == 5

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_formats_model_name(self, mock_get, mock_programs_collection):
        """Test that model name is formatted"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'records': [
                {'id': 'prog1', 'model': 'Model With Spaces', 'name': 'Program 1'}
            ]
        }
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Check that model name was formatted
        update_call = mock_programs_collection.update_one.call_args[0]
        program_data = update_call[1]['$set']
        assert program_data['model'] == 'Model_With_Spaces'

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_updates_by_id(self, mock_get, mock_programs_collection):
        """Test that programs are updated/upserted by id"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'records': [
                {'id': 'unique_prog_id', 'model': 'Test Model', 'name': 'Test Program'}
            ]
        }
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Check query and update
        update_call = mock_programs_collection.update_one.call_args[0]
        query = update_call[0]
        update_data = update_call[1]
        upsert = mock_programs_collection.update_one.call_args[0][2]

        assert query == {'id': 'unique_prog_id'}
        assert '$set' in update_data
        assert upsert is True  # Should be upsert

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_empty_records(self, mock_get, mock_programs_collection):
        """Test handling of empty records"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {'records': []}
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Should not update collection if no programs
        mock_programs_collection.update_one.assert_not_called()

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_no_records_key(self, mock_get, mock_programs_collection):
        """Test handling when records key is missing"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {}  # No 'records' key
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        # Should raise KeyError or handle gracefully
        with pytest.raises(KeyError):
            retrieve_programs(resp_data, 'test_token')

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_multiple_projects(self, mock_get, mock_programs_collection):
        """Test retrieving programs for multiple projects"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'records': [
                {'id': 'prog1', 'model': 'Model 1', 'name': 'Program 1'}
            ]
        }
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {},
                'project2': {},
                'project3': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Should make 3 requests, one per project
        assert mock_get.call_count == 3
        # Should update 3 times (1 program per project)
        assert mock_programs_collection.update_one.call_count == 3

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_preserves_all_fields(self, mock_get, mock_programs_collection):
        """Test that all program fields are preserved"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'records': [
                {
                    'id': 'prog1',
                    'name': 'Complex Program',
                    'model': 'Test Model',
                    'version': 'v1.0',
                    'steps': ['step1', 'step2'],
                    'settings': {'key': 'value'},
                    'created_at': '2024-01-01'
                }
            ]
        }
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Check that all fields are in the update
        update_call = mock_programs_collection.update_one.call_args[0]
        program_data = update_call[1]['$set']

        assert program_data['id'] == 'prog1'
        assert program_data['name'] == 'Complex Program'
        assert program_data['model'] == 'Test_Model'  # Formatted
        assert program_data['version'] == 'v1.0'
        assert program_data['steps'] == ['step1', 'step2']
        assert program_data['settings'] == {'key': 'value'}
        assert program_data['created_at'] == '2024-01-01'

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_request_exception(self, mock_get, mock_programs_collection):
        """Test handling of request exceptions"""
        from worker_scripts.retrieve_programs import retrieve_programs

        # Simulate a request exception
        mock_get.side_effect = Exception('Network error')

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        # Should raise the exception (function doesn't handle it)
        with pytest.raises(Exception):
            retrieve_programs(resp_data, 'test_token')

    @pytest.mark.unit
    def test_cloud_domain_constant(self):
        """Test CLOUD_DOMAIN constant"""
        from worker_scripts.retrieve_programs import CLOUD_DOMAIN

        # Should have a valid cloud domain
        assert CLOUD_DOMAIN is not None
        assert 'http' in CLOUD_DOMAIN or 'flexiblevision' in CLOUD_DOMAIN


class TestPaginationAndFiltering:
    """Tests for pagination and filtering logic"""

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_pagination_params(self, mock_get, mock_programs_collection):
        """Test that correct pagination parameters are used"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {'records': []}
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Check URL includes pagination (0/9999)
        call_args = str(mock_get.call_args)
        assert '/0/9999' in call_args

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_retrieve_programs_use_latest_flag(self, mock_get, mock_programs_collection):
        """Test that use_latest=true flag is included"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {'records': []}
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Check URL includes use_latest=true
        call_args = str(mock_get.call_args)
        assert 'use_latest=true' in call_args


class TestModelNameFormatting:
    """Tests for model name formatting during program retrieval"""

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_model_name_special_chars_removed(self, mock_get, mock_programs_collection):
        """Test that special characters are removed from model names"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'records': [
                {'id': 'prog1', 'model': 'Model@#$Name', 'name': 'Program 1'}
            ]
        }
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        update_call = mock_programs_collection.update_one.call_args[0]
        program_data = update_call[1]['$set']

        assert '@' not in program_data['model']
        assert '#' not in program_data['model']
        assert '$' not in program_data['model']

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_model_name_spaces_to_underscores(self, mock_get, mock_programs_collection):
        """Test that spaces in model names are converted to underscores"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'records': [
                {'id': 'prog1', 'model': 'My Great Model', 'name': 'Program 1'}
            ]
        }
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        update_call = mock_programs_collection.update_one.call_args[0]
        program_data = update_call[1]['$set']

        assert program_data['model'] == 'My_Great_Model'
        assert ' ' not in program_data['model']

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_multiple_programs_each_formatted(self, mock_get, mock_programs_collection):
        """Test that each program's model name is formatted"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'records': [
                {'id': 'prog1', 'model': 'Model One', 'name': 'Program 1'},
                {'id': 'prog2', 'model': 'Model Two', 'name': 'Program 2'},
                {'id': 'prog3', 'model': 'Model Three', 'name': 'Program 3'}
            ]
        }
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Check all three updates
        calls = mock_programs_collection.update_one.call_args_list
        assert len(calls) == 3

        for call in calls:
            program_data = call[0][1]['$set']
            # All should have underscores instead of spaces
            assert ' ' not in program_data['model']
            assert '_' in program_data['model']


class TestDataIntegrity:
    """Tests for data integrity during program retrieval"""

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_program_id_used_as_key(self, mock_get, mock_programs_collection):
        """Test that program id is consistently used as the query key"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'records': [
                {'id': 'unique_program_id', 'model': 'Model', 'name': 'Program'}
            ]
        }
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Verify query uses program id
        query = mock_programs_collection.update_one.call_args[0][0]
        assert 'id' in query
        assert query['id'] == 'unique_program_id'

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_upsert_flag_is_true(self, mock_get, mock_programs_collection):
        """Test that upsert flag is set to True"""
        from worker_scripts.retrieve_programs import retrieve_programs

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'records': [
                {'id': 'prog1', 'model': 'Model', 'name': 'Program'}
            ]
        }
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Check upsert parameter
        upsert = mock_programs_collection.update_one.call_args[0][2]
        assert upsert is True

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_programs.programs_collection')
    @patch('requests.get')
    def test_program_data_preserved_except_model(self, mock_get, mock_programs_collection):
        """Test that program data is preserved, only model name is formatted"""
        from worker_scripts.retrieve_programs import retrieve_programs

        original_program = {
            'id': 'prog123',
            'name': 'My Program',
            'model': 'Original Model',
            'data': 'important_data',
            'config': {'setting': 'value'}
        }
        mock_response = MagicMock()
        mock_response.json.return_value = {'records': [original_program.copy()]}
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_programs(resp_data, 'test_token')

        # Check that data in update matches original (except model)
        update_data = mock_programs_collection.update_one.call_args[0][1]['$set']
        assert update_data['id'] == original_program['id']
        assert update_data['name'] == original_program['name']
        assert update_data['model'] == 'Original_Model'  # Formatted
        assert update_data['data'] == original_program['data']
        assert update_data['config'] == original_program['config']
