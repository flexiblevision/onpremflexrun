"""
Unit tests for retrieve_models.py worker script
"""
import pytest
import os
import zipfile
import string
from unittest.mock import Mock, patch, MagicMock, mock_open, call
from io import BytesIO


class TestBasePath:
    """Tests for base_path function"""

    @pytest.mark.unit
    @patch('os.path.exists')
    def test_base_path_xavier_exists(self, mock_exists):
        """Test base_path when xavier_ssd exists"""
        from worker_scripts.retrieve_models import base_path

        mock_exists.return_value = True
        result = base_path()

        assert result == '/xavier_ssd/'
        # Check that exists was called with the correct path (may be called multiple times due to module-level calls)
        assert any(call[0][0] == '/xavier_ssd/' for call in mock_exists.call_args_list)

    @pytest.mark.unit
    @patch('os.path.exists')
    def test_base_path_xavier_not_exists(self, mock_exists):
        """Test base_path when xavier_ssd doesn't exist"""
        from worker_scripts.retrieve_models import base_path

        mock_exists.return_value = False
        result = base_path()

        assert result == '/'


class TestCreateConfigFile:
    """Tests for create_config_file function"""

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    def test_create_config_file_single_model(self, mock_file):
        """Test config file creation with a single model"""
        from worker_scripts.retrieve_models import create_config_file

        data = [{'type': 'test_model'}]
        create_config_file(data)

        mock_file.assert_called_once()
        write_calls = [call[0][0] for call in mock_file().write.call_args_list]
        combined_output = ''.join(write_calls)

        assert 'model_config_list {' in combined_output
        assert 'name: \'test_model\'' in combined_output
        assert 'base_path: \'/models/test_model/\'' in combined_output
        assert 'model_platform: \'tensorflow\'' in combined_output
        assert 'model_version_policy: {all {}}' in combined_output

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    def test_create_config_file_multiple_models(self, mock_file):
        """Test config file creation with multiple models"""
        from worker_scripts.retrieve_models import create_config_file

        data = [
            {'type': 'model_one'},
            {'type': 'model_two'},
            {'type': 'model_three'}
        ]
        create_config_file(data)

        write_calls = [call[0][0] for call in mock_file().write.call_args_list]
        combined_output = ''.join(write_calls)

        assert 'name: \'model_one\'' in combined_output
        assert 'name: \'model_two\'' in combined_output
        assert 'name: \'model_three\'' in combined_output


class TestDownloadByLink:
    """Tests for download_by_link function"""

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('requests.get')
    def test_download_by_link_success(self, mock_get, mock_file):
        """Test successful download by link"""
        from worker_scripts.retrieve_models import download_by_link

        # Mock the signed link response
        mock_link_response = MagicMock()
        mock_link_response.json.return_value = 'https://signed-url.com/model.zip'

        # Mock the download response
        mock_download_response = MagicMock()
        mock_download_response.headers = {'Content-length': '1024'}
        mock_download_response.iter_content.return_value = [b'chunk1', b'chunk2']
        mock_download_response.__enter__ = Mock(return_value=mock_download_response)
        mock_download_response.__exit__ = Mock(return_value=False)

        mock_get.side_effect = [mock_link_response, mock_download_response]

        download_by_link('test_token', 'project123', 'v1', '/tmp/model.zip')

        assert mock_get.call_count == 2
        # Verify headers include authorization
        first_call_kwargs = mock_get.call_args_list[0][1]
        assert 'headers' in first_call_kwargs
        assert 'Authorization' in first_call_kwargs['headers']

    @pytest.mark.unit
    @patch('requests.get')
    def test_download_by_link_with_correct_url(self, mock_get):
        """Test that download_by_link constructs correct URL"""
        from worker_scripts.retrieve_models import download_by_link, CLOUD_DOMAIN

        mock_link_response = MagicMock()
        mock_link_response.json.return_value = 'https://signed-url.com/model.zip'

        mock_download_response = MagicMock()
        mock_download_response.headers = {'Content-length': '1024'}
        mock_download_response.iter_content.return_value = []
        mock_download_response.__enter__ = Mock(return_value=mock_download_response)
        mock_download_response.__exit__ = Mock(return_value=False)

        mock_get.side_effect = [mock_link_response, mock_download_response]

        download_by_link('test_token', 'proj456', 'v2', '/tmp/model.zip')

        # Check first call for signed link
        expected_url = f'{CLOUD_DOMAIN}/api/capture/models/download_link/proj456/v2'
        first_call = mock_get.call_args_list[0]
        assert expected_url in str(first_call)


class TestFormatFilename:
    """Tests for format_filename function"""

    @pytest.mark.unit
    def test_format_filename_basic(self):
        """Test basic filename formatting"""
        from worker_scripts.retrieve_models import format_filename

        result = format_filename('My Model Name')
        assert result == 'My_Model_Name'

    @pytest.mark.unit
    def test_format_filename_special_chars(self):
        """Test filename formatting with special characters"""
        from worker_scripts.retrieve_models import format_filename

        result = format_filename('Model@#$%^&*Name')
        assert '@' not in result
        assert '#' not in result
        assert '$' not in result

    @pytest.mark.unit
    def test_format_filename_keeps_valid_chars(self):
        """Test that valid characters are kept"""
        from worker_scripts.retrieve_models import format_filename

        result = format_filename('Model-Name_123.test')
        assert 'Model-Name_123.test' == result

    @pytest.mark.unit
    def test_format_filename_empty_string(self):
        """Test formatting empty string"""
        from worker_scripts.retrieve_models import format_filename

        result = format_filename('')
        assert result == ''

    @pytest.mark.unit
    def test_format_filename_only_invalid_chars(self):
        """Test formatting string with only invalid characters"""
        from worker_scripts.retrieve_models import format_filename

        result = format_filename('@#$%^&*')
        assert result == ''


class TestRetrieveModels:
    """Tests for retrieve_models function"""

    @pytest.mark.unit
    def test_retrieve_models_no_models(self):
        """Test retrieve_models with no models in data"""
        from worker_scripts.retrieve_models import retrieve_models

        data = {'exclude_models': {}}
        result = retrieve_models(data, 'token123')

        assert result is False

    @pytest.mark.unit
    def test_retrieve_models_empty_models(self):
        """Test retrieve_models with empty models dict"""
        from worker_scripts.retrieve_models import retrieve_models

        data = {'models': {}, 'exclude_models': {}}
        result = retrieve_models(data, 'token123')

        assert result is False

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.retrieve_models.save_models_versions')
    @patch('worker_scripts.retrieve_models.create_config_file')
    @patch('requests.get')
    def test_retrieve_models_high_accuracy(self, mock_get, mock_create_config,
                                            mock_save_versions, mock_exists, mock_os_system, mock_file):
        """Test retrieving high accuracy models"""
        from worker_scripts.retrieve_models import retrieve_models

        # Mock exists to return True for model.zip files, False otherwise
        mock_exists.side_effect = lambda path: 'model.zip' in path

        # Mock download response
        mock_response = MagicMock()
        mock_response.content = b'fake_zip_content'
        mock_get.return_value = mock_response

        data = {
            'models': {
                'model1': {
                    '_id': 'proj1',
                    'name': 'Test Model',
                    'models': ['v1']
                }
            },
            'exclude_models': {},
            'model_type': 'high_accuracy'
        }

        # Mock zipfile extraction
        with patch('zipfile.ZipFile') as mock_zipfile:
            mock_zip = MagicMock()
            mock_zipfile.return_value.__enter__.return_value = mock_zip

            result = retrieve_models(data, 'token123')

            # Should create config file for high_accuracy models
            mock_create_config.assert_called_once()
            mock_save_versions.assert_called_once()

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.retrieve_models.save_models_versions')
    def test_retrieve_models_high_speed(self, mock_save_versions, mock_exists, mock_os_system, mock_file):
        """Test retrieving high speed (lite) models"""
        from worker_scripts.retrieve_models import retrieve_models

        # Return True for model.zip files, False for model folders
        mock_exists.side_effect = lambda path: 'model.zip' in path

        data = {
            'models': {
                'model1': {
                    '_id': 'proj1',
                    'name': 'Fast Model',
                    'models': ['v1']
                }
            },
            'exclude_models': {},
            'model_type': 'high_speed'
        }

        with patch('requests.get') as mock_get, \
             patch('zipfile.ZipFile') as mock_zipfile:

            mock_response = MagicMock()
            mock_get.return_value = mock_response

            mock_zip = MagicMock()
            mock_zipfile.return_value.__enter__.return_value = mock_zip

            result = retrieve_models(data, 'token123')

            # Should use predictlite docker container
            docker_calls = [str(call) for call in mock_os_system.call_args_list
                           if 'docker' in str(call)]
            assert any('predictlite' in call for call in docker_calls)

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.retrieve_models.save_models_versions')
    @patch('worker_scripts.retrieve_models.download_by_link')
    def test_retrieve_models_ocr(self, mock_download, mock_save_versions,
                                  mock_exists, mock_os_system, mock_file):
        """Test retrieving OCR models"""
        from worker_scripts.retrieve_models import retrieve_models

        # Return True for model.zip files, False for model folders
        mock_exists.side_effect = lambda path: 'model.zip' in path

        data = {
            'models': {
                'model1': {
                    '_id': 'proj1',
                    'name': 'OCR Model',
                    'models': ['v1']
                }
            },
            'exclude_models': {},
            'model_type': 'ocr'
        }

        with patch('zipfile.ZipFile') as mock_zipfile:
            mock_zip = MagicMock()
            mock_zipfile.return_value.__enter__.return_value = mock_zip

            result = retrieve_models(data, 'token123')

            # Should use download_by_link for OCR
            mock_download.assert_called()
            # Should use ocr docker container
            docker_calls = [str(call) for call in mock_os_system.call_args_list
                           if 'docker' in str(call)]
            assert any('ocr' in call for call in docker_calls)

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('os.system')
    @patch('os.path.exists')
    def test_retrieve_models_excluded_model(self, mock_exists, mock_os_system, mock_file):
        """Test that excluded models are skipped"""
        from worker_scripts.retrieve_models import retrieve_models

        # Model version directory exists
        mock_exists.return_value = True

        data = {
            'models': {
                'model1': {
                    '_id': 'proj1',
                    'name': 'Test Model',
                    'models': ['v1']
                }
            },
            'exclude_models': {
                'Test_Model': ['v1']
            }
        }

        with patch('worker_scripts.retrieve_models.save_models_versions') as mock_save:
            result = retrieve_models(data, 'token123')

            # Should still call save_models_versions with the excluded model
            mock_save.assert_called_once()

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('os.system')
    @patch('os.path.exists')
    def test_retrieve_models_removes_existing(self, mock_exists, mock_os_system, mock_file):
        """Test that existing models are removed when exclude_models is empty"""
        from worker_scripts.retrieve_models import retrieve_models

        mock_exists.return_value = True

        data = {
            'models': {
                'model1': {
                    '_id': 'proj1',
                    'name': 'Test Model',
                    'models': ['v1']
                }
            },
            'exclude_models': {}
        }

        with patch('requests.get'), \
             patch('zipfile.ZipFile'), \
             patch('worker_scripts.retrieve_models.save_models_versions'):

            retrieve_models(data, 'token123')

            # Should remove existing models directory
            rm_calls = [str(call) for call in mock_os_system.call_args_list
                       if 'rm -rf' in str(call)]
            assert len(rm_calls) > 0


class TestSaveModelsVersions:
    """Tests for save_models_versions function"""

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_models.presets_collection')
    @patch('worker_scripts.retrieve_models.models_collection')
    def test_save_models_versions_basic(self, mock_models_collection,
                                         mock_presets_collection):
        """Test basic model versions saving"""
        from worker_scripts.retrieve_models import save_models_versions

        mock_models_collection.find.return_value = []
        mock_presets_collection.find.return_value = []

        models_versions = [
            {'type': 'model1', 'versions': ['v1', 'v2']},
            {'type': 'model2', 'versions': ['v1']}
        ]

        save_models_versions(models_versions, 'versions')

        # Should update both models
        assert mock_models_collection.update_one.call_count >= 2

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_models.presets_collection')
    @patch('worker_scripts.retrieve_models.models_collection')
    def test_save_models_versions_clears_existing(self, mock_models_collection,
                                                    mock_presets_collection):
        """Test that existing model type lists are cleared"""
        from worker_scripts.retrieve_models import save_models_versions

        mock_models_collection.find.return_value = [
            {'type': 'model1', 'versions': ['old_v1']},
        ]
        mock_presets_collection.find.return_value = []

        models_versions = [
            {'type': 'model1', 'versions': ['new_v1', 'new_v2']}
        ]

        save_models_versions(models_versions, 'versions')

        # Should clear and then set new versions
        update_calls = mock_models_collection.update_one.call_args_list
        assert len(update_calls) >= 2  # At least clear + update

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_models.presets_collection')
    @patch('worker_scripts.retrieve_models.models_collection')
    def test_save_models_versions_deletes_empty(self, mock_models_collection,
                                                  mock_presets_collection):
        """Test that models with no versions are deleted"""
        from worker_scripts.retrieve_models import save_models_versions

        mock_models_collection.find.return_value = [
            {'type': 'old_model', 'versions': [], 'high_speed': []}
        ]
        mock_presets_collection.find.return_value = []

        models_versions = [
            {'type': 'new_model', 'versions': ['v1']}
        ]

        save_models_versions(models_versions, 'versions')

        # Should delete the old_model since it has no versions
        mock_models_collection.delete_one.assert_called()


class TestAssignPresetToLatestVersion:
    """Tests for assign_preset_to_latest_version function"""

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_models.presets_collection')
    def test_assign_preset_to_latest_version(self, mock_presets_collection):
        """Test assigning preset to latest version"""
        from worker_scripts.retrieve_models import assign_preset_to_latest_version

        mock_presets_collection.find.return_value = [
            {'presetId': 'preset1', 'modelName': 'test_model'}
        ]

        versions = ['v1', 'v3', 'v2']  # Unsorted
        assign_preset_to_latest_version('test_model', versions, 'versions')

        # Should update preset to use v3 (latest after sorting)
        mock_presets_collection.update.assert_called()
        update_call = mock_presets_collection.update.call_args
        assert 'v3' in str(update_call)

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_models.presets_collection')
    def test_assign_preset_multiple_presets(self, mock_presets_collection):
        """Test assigning multiple presets to latest version"""
        from worker_scripts.retrieve_models import assign_preset_to_latest_version

        mock_presets_collection.find.return_value = [
            {'presetId': 'preset1', 'modelName': 'test_model'},
            {'presetId': 'preset2', 'modelName': 'test_model'}
        ]

        versions = ['v1', 'v2']
        assign_preset_to_latest_version('test_model', versions, 'versions')

        # Should update all presets
        assert mock_presets_collection.update.call_count == 2


class TestModelTypeMapping:
    """Tests for model type mapping logic"""

    @pytest.mark.unit
    def test_lite_model_types_constant(self):
        """Test LITE_MODEL_TYPES constant"""
        from worker_scripts.retrieve_models import LITE_MODEL_TYPES

        assert 'high_speed' in LITE_MODEL_TYPES
        assert isinstance(LITE_MODEL_TYPES, list)

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.retrieve_models.save_models_versions')
    def test_model_type_default_to_versions(self, mock_save_versions, mock_exists,
                                             mock_os_system, mock_file):
        """Test that model_type defaults to 'versions' when not specified"""
        from worker_scripts.retrieve_models import retrieve_models

        # Mock exists to return True for model.zip files, False otherwise
        mock_exists.side_effect = lambda path: 'model.zip' in path

        data = {
            'models': {
                'model1': {
                    '_id': 'proj1',
                    'name': 'Test Model',
                    'models': ['v1']
                }
            },
            'exclude_models': {}
            # No model_type specified
        }

        with patch('requests.get'), \
             patch('zipfile.ZipFile') as mock_zipfile:

            mock_zip = MagicMock()
            mock_zipfile.return_value.__enter__.return_value = mock_zip

            result = retrieve_models(data, 'token123')

            # Should use 'versions' as model_type
            call_args = mock_save_versions.call_args
            assert 'versions' in str(call_args)


class TestZipFileExtraction:
    """Tests for zip file extraction logic"""

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.retrieve_models.save_models_versions')
    @patch('requests.get')
    def test_zip_extraction_moves_files(self, mock_get, mock_save_versions,
                                         mock_exists, mock_os_system, mock_file):
        """Test that files are moved after zip extraction"""
        from worker_scripts.retrieve_models import retrieve_models

        mock_exists.side_effect = lambda path: 'model.zip' in path or '/models' in path

        data = {
            'models': {
                'model1': {
                    '_id': 'proj1',
                    'name': 'Test Model',
                    'models': ['v1']
                }
            },
            'exclude_models': {}
        }

        with patch('zipfile.ZipFile') as mock_zipfile:
            mock_zip = MagicMock()
            mock_zipfile.return_value.__enter__.return_value = mock_zip

            retrieve_models(data, 'token123')

            # Check for mv commands to move job.json and pbtxt
            mv_calls = [str(call) for call in mock_os_system.call_args_list
                       if 'mv' in str(call)]
            assert any('job.json' in call for call in mv_calls)
            assert any('object-detection.pbtxt' in call for call in mv_calls)

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.retrieve_models.save_models_versions')
    def test_bad_zipfile_handling(self, mock_save_versions, mock_exists, mock_os_system):
        """Test handling of bad zip files"""
        from worker_scripts.retrieve_models import retrieve_models

        mock_exists.side_effect = lambda path: 'model.zip' in path

        data = {
            'models': {
                'model1': {
                    '_id': 'proj1',
                    'name': 'Test Model',
                    'models': ['v1']
                }
            },
            'exclude_models': {}
        }

        with patch('requests.get'), \
             patch('zipfile.ZipFile') as mock_zipfile:

            # Simulate bad zipfile
            mock_zipfile.side_effect = zipfile.BadZipfile('Bad zip')

            result = retrieve_models(data, 'token123')

            # Should still complete but skip the bad file
            # The function continues despite bad zipfile


class TestDockerIntegration:
    """Tests for Docker integration operations"""

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.retrieve_models.save_models_versions')
    def test_docker_commands_for_high_accuracy(self, mock_save_versions, mock_exists,
                                                 mock_os_system, mock_file):
        """Test correct Docker commands for high accuracy models"""
        from worker_scripts.retrieve_models import retrieve_models

        # Return True for model.zip files, False for model folders
        mock_exists.side_effect = lambda path: 'model.zip' in path

        data = {
            'models': {
                'model1': {
                    '_id': 'proj1',
                    'name': 'Test Model',
                    'models': ['v1']
                }
            },
            'exclude_models': {}
        }

        with patch('requests.get'), \
             patch('zipfile.ZipFile') as mock_zipfile:

            mock_zip = MagicMock()
            mock_zipfile.return_value.__enter__.return_value = mock_zip

            retrieve_models(data, 'token123')

            docker_calls = [str(call) for call in mock_os_system.call_args_list
                           if 'docker' in str(call)]

            # Should exec rm, cp, and restart localprediction
            assert any('localprediction' in call for call in docker_calls)
            assert any('docker exec' in call for call in docker_calls)
            assert any('docker cp' in call for call in docker_calls)
            assert any('docker restart' in call for call in docker_calls)
