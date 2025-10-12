"""
Unit tests for model_upload_worker.py worker script
"""
import pytest
import os
import json
from unittest.mock import Mock, patch, MagicMock, mock_open, call


class TestCreateConfigFile:
    """Tests for create_config_file function"""

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_create_config_file_basic(self, mock_models_collection, mock_file):
        """Test basic config file creation"""
        from worker_scripts.model_upload_worker import create_config_file

        # Mock models data
        mock_models_collection.find.return_value = [
            {'type': 'model1', 'versions': ['v1', 'v2']},
            {'type': 'model2', 'versions': ['v1']}
        ]

        create_config_file()

        # Verify file was opened for writing
        mock_file.assert_called_once_with('/models/model.config', 'w')

        # Get all write calls
        write_calls = [call[0][0] for call in mock_file().write.call_args_list]
        combined_output = ''.join(write_calls)

        # Verify structure
        assert 'model_config_list {' in combined_output
        assert 'name: \'model1\'' in combined_output
        assert 'name: \'model2\'' in combined_output
        assert 'base_path: \'/models/model1/\'' in combined_output
        assert 'model_platform: \'tensorflow\'' in combined_output
        assert 'model_version_policy: {all {}}' in combined_output

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_create_config_file_no_versions(self, mock_models_collection, mock_file):
        """Test config file creation with model without versions"""
        from worker_scripts.model_upload_worker import create_config_file

        # Model without versions should not be included
        mock_models_collection.find.return_value = [
            {'type': 'model1', 'versions': []},
            {'type': 'model2', 'versions': ['v1']}
        ]

        create_config_file()

        write_calls = [call[0][0] for call in mock_file().write.call_args_list]
        combined_output = ''.join(write_calls)

        # model1 should not be in config
        assert 'name: \'model1\'' not in combined_output
        assert 'name: \'model2\'' in combined_output

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open)
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_create_config_file_empty_models(self, mock_models_collection, mock_file):
        """Test config file creation with no models"""
        from worker_scripts.model_upload_worker import create_config_file

        mock_models_collection.find.return_value = []

        create_config_file()

        # Should return early without writing
        mock_file.assert_not_called()


class TestReadJobFile:
    """Tests for read_job_file function"""

    @pytest.mark.unit
    @patch('builtins.open', new_callable=mock_open, read_data='{"model_version": "v1.0.0", "model_type": "high_accuracy"}')
    @patch('os.path.exists')
    @patch('os.listdir')
    @patch('os.path.isdir')
    def test_read_job_file_success(self, mock_isdir, mock_listdir, mock_exists, mock_file):
        """Test successful job file reading"""
        from worker_scripts.model_upload_worker import read_job_file

        mock_listdir.return_value = ['123', 'other_file.txt']
        mock_isdir.side_effect = lambda path: '123' in path and 'other_file' not in path
        mock_exists.return_value = True

        result = read_job_file('/tmp/model')

        assert result == {"model_version": "v1.0.0", "model_type": "high_accuracy"}
        mock_file.assert_called_once_with('/tmp/model/123/job.json')

    @pytest.mark.unit
    @patch('os.path.exists')
    @patch('os.listdir')
    @patch('os.path.isdir')
    def test_read_job_file_not_found(self, mock_isdir, mock_listdir, mock_exists):
        """Test job file reading when file doesn't exist"""
        from worker_scripts.model_upload_worker import read_job_file

        mock_listdir.return_value = ['456']
        mock_isdir.return_value = True
        mock_exists.return_value = False

        result = read_job_file('/tmp/model')

        assert result == {}

    @pytest.mark.unit
    @patch('os.listdir')
    @patch('os.path.isdir')
    def test_read_job_file_no_numeric_dir(self, mock_isdir, mock_listdir):
        """Test job file reading when no numeric directory exists"""
        from worker_scripts.model_upload_worker import read_job_file

        mock_listdir.return_value = ['not_numeric', 'also_not_numeric.txt']
        mock_isdir.return_value = False

        result = read_job_file('/tmp/model')

        assert result == {}


class TestUploadModel:
    """Tests for upload_model function"""

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    @patch('worker_scripts.model_upload_worker.models_collection')
    @patch('worker_scripts.model_upload_worker.create_config_file')
    def test_upload_model_new_model(self, mock_create_config, mock_models_collection,
                                     mock_read_job, mock_exists, mock_os_system):
        """Test uploading a completely new model"""
        from worker_scripts.model_upload_worker import upload_model

        mock_read_job.return_value = {
            'model_version': 'v1',
            'model_type': 'high_accuracy'
        }
        # Model path doesn't exist
        mock_exists.side_effect = lambda path: '/models/testmodel' not in path

        result = upload_model('/tmp/testmodel', 'testmodel#v1.zip')

        assert result is True
        # Should create directory and move version
        assert any('mkdir' in str(call) for call in mock_os_system.call_args_list)
        assert any('mv' in str(call) for call in mock_os_system.call_args_list)
        # Should create config file for high_accuracy models
        mock_create_config.assert_called_once()
        # Should update MongoDB
        mock_models_collection.update_one.assert_called()

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_upload_model_add_version(self, mock_models_collection, mock_read_job,
                                       mock_exists, mock_os_system):
        """Test adding a new version to existing model"""
        from worker_scripts.model_upload_worker import upload_model

        mock_read_job.return_value = {
            'model_version': 'v2',
            'model_type': 'high_accuracy'
        }
        # Model path exists but version doesn't
        mock_exists.side_effect = lambda path: (
            '/models/testmodel' in path and
            '/models/testmodel/v2' not in path
        )

        result = upload_model('/tmp/testmodel', 'testmodel#v2.zip')

        assert result is True
        # Should move version but not create directory
        mv_calls = [call for call in mock_os_system.call_args_list if 'mv' in str(call)]
        assert len(mv_calls) > 0
        # Should add version to existing model
        mock_models_collection.update_one.assert_called()

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    def test_upload_model_already_exists(self, mock_read_job, mock_exists, mock_os_system):
        """Test uploading a model version that already exists"""
        from worker_scripts.model_upload_worker import upload_model

        mock_read_job.return_value = {
            'model_version': 'v1',
            'model_type': 'high_accuracy'
        }
        # Both model and version exist
        mock_exists.return_value = True

        result = upload_model('/tmp/testmodel', 'testmodel#v1.zip')

        assert result is False
        # Should remove temp files
        rm_calls = [call for call in mock_os_system.call_args_list if 'rm -rf' in str(call)]
        assert len(rm_calls) > 0

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_upload_model_lite_model(self, mock_models_collection, mock_read_job,
                                      mock_exists, mock_os_system):
        """Test uploading a lite/high_speed model"""
        from worker_scripts.model_upload_worker import upload_model

        mock_read_job.return_value = {
            'model_version': 'v1',
            'model_type': 'high_speed'
        }
        # Lite model path doesn't exist
        mock_exists.side_effect = lambda path: '/lite_models' not in path

        result = upload_model('/tmp/testmodel', 'testmodel#v1.zip')

        assert result is True
        # Should use lite_models path
        docker_calls = [str(call) for call in mock_os_system.call_args_list if 'docker' in str(call)]
        assert any('predictlite' in call for call in docker_calls)
        # Should update high_speed field in MongoDB
        update_call = mock_models_collection.update_one.call_args
        assert '$set' in str(update_call) and 'high_speed' in str(update_call)

    @pytest.mark.unit
    @patch('os.path.exists')
    def test_upload_model_no_path(self, mock_exists):
        """Test upload with invalid path"""
        from worker_scripts.model_upload_worker import upload_model

        mock_exists.return_value = False

        result = upload_model('/nonexistent/path', 'model.zip')

        assert result is False

    @pytest.mark.unit
    @patch('os.path.exists')
    def test_upload_model_no_filename(self, mock_exists):
        """Test upload with no filename"""
        from worker_scripts.model_upload_worker import upload_model

        mock_exists.return_value = True

        result = upload_model('/tmp/model', None)

        assert result is False

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_upload_model_version_from_filename(self, mock_models_collection, mock_read_job,
                                                  mock_exists, mock_os_system):
        """Test extracting version from filename when not in job.json"""
        from worker_scripts.model_upload_worker import upload_model

        # No model_version in job data
        mock_read_job.return_value = {'model_type': 'high_accuracy'}
        mock_exists.side_effect = lambda path: '/models/testmodel' not in path

        result = upload_model('/tmp/testmodel', 'testmodel#v123.zip')

        assert result is True
        # Version should be extracted from filename (v123)
        mv_call = str(mock_os_system.call_args_list)
        assert 'v123' in mv_call


class TestModelTypeHandling:
    """Tests for model type handling logic"""

    @pytest.mark.unit
    def test_lite_model_types_list(self):
        """Test that lite_model_types is correctly defined"""
        from worker_scripts.model_upload_worker import lite_model_types

        assert 'high_speed' in lite_model_types
        assert isinstance(lite_model_types, list)

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_model_default_type(self, mock_models_collection, mock_read_job,
                                 mock_exists, mock_os_system):
        """Test that model defaults to high_accuracy when type not specified"""
        from worker_scripts.model_upload_worker import upload_model

        # No model_type in job data
        mock_read_job.return_value = {'model_version': 'v1'}
        mock_exists.side_effect = lambda path: '/models/testmodel' not in path

        result = upload_model('/tmp/testmodel', 'testmodel#v1.zip')

        # Should use regular models path, not lite_models
        docker_calls = [str(call) for call in mock_os_system.call_args_list if 'docker' in str(call)]
        assert any('localprediction' in call for call in docker_calls)


class TestDockerOperations:
    """Tests for Docker container operations"""

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    @patch('worker_scripts.model_upload_worker.models_collection')
    @patch('worker_scripts.model_upload_worker.create_config_file')
    def test_docker_restart_prediction_server(self, mock_create_config, mock_models_collection,
                                               mock_read_job, mock_exists, mock_os_system):
        """Test that prediction server is restarted after model upload"""
        from worker_scripts.model_upload_worker import upload_model

        mock_read_job.return_value = {'model_version': 'v1', 'model_type': 'high_accuracy'}
        mock_exists.side_effect = lambda path: '/models/testmodel' not in path

        upload_model('/tmp/testmodel', 'testmodel#v1.zip')

        # Check for docker restart command
        restart_calls = [str(call) for call in mock_os_system.call_args_list
                        if 'docker restart' in str(call)]
        assert len(restart_calls) > 0
        assert any('localprediction' in call for call in restart_calls)

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_docker_copy_to_lite_server(self, mock_models_collection, mock_read_job,
                                         mock_exists, mock_os_system):
        """Test that lite models are copied to predictlite container"""
        from worker_scripts.model_upload_worker import upload_model

        mock_read_job.return_value = {'model_version': 'v1', 'model_type': 'high_speed'}
        mock_exists.side_effect = lambda path: '/lite_models' not in path

        upload_model('/tmp/testmodel', 'testmodel#v1.zip')

        # Check for docker cp command to predictlite
        docker_calls = [str(call) for call in mock_os_system.call_args_list
                       if 'docker cp' in str(call)]
        assert len(docker_calls) > 0
        assert any('predictlite' in call for call in docker_calls)


class TestFilePathConstruction:
    """Tests for file path construction logic"""

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_model_paths(self, mock_models_collection, mock_read_job, mock_exists, mock_os_system):
        """Test correct model path construction"""
        from worker_scripts.model_upload_worker import upload_model

        mock_read_job.return_value = {'model_version': 'v1', 'model_type': 'high_accuracy'}
        mock_exists.side_effect = lambda path: False

        upload_model('/tmp/mymodel', 'mymodel#v1.zip')

        # Verify paths include model name
        system_calls = [str(call) for call in mock_os_system.call_args_list]
        combined = ' '.join(system_calls)

        assert '/models/mymodel' in combined or 'mymodel' in combined

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_version_paths(self, mock_models_collection, mock_read_job, mock_exists, mock_os_system):
        """Test correct version path construction"""
        from worker_scripts.model_upload_worker import upload_model

        mock_read_job.return_value = {'model_version': 'v2.5', 'model_type': 'high_accuracy'}
        mock_exists.side_effect = lambda path: '/models/mymodel' in path and 'v2.5' not in path

        upload_model('/tmp/mymodel', 'mymodel#v2.5.zip')

        # Verify version is in the paths
        system_calls = [str(call) for call in mock_os_system.call_args_list]
        combined = ' '.join(system_calls)

        assert 'v2.5' in combined


class TestCleanupOperations:
    """Tests for cleanup operations"""

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    @patch('worker_scripts.model_upload_worker.models_collection')
    def test_temp_path_cleanup_success(self, mock_models_collection, mock_read_job,
                                        mock_exists, mock_os_system):
        """Test that temp path is cleaned up after successful upload"""
        from worker_scripts.model_upload_worker import upload_model

        mock_read_job.return_value = {'model_version': 'v1', 'model_type': 'high_accuracy'}
        mock_exists.side_effect = lambda path: '/models/testmodel' not in path

        upload_model('/tmp/testmodel', 'testmodel#v1.zip')

        # Check for rm -rf of temp path
        rm_calls = [str(call) for call in mock_os_system.call_args_list
                   if 'rm -rf' in str(call)]
        assert any('/tmp/testmodel' in call for call in rm_calls)

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.exists')
    @patch('worker_scripts.model_upload_worker.read_job_file')
    def test_temp_path_cleanup_already_exists(self, mock_read_job, mock_exists, mock_os_system):
        """Test that temp path is cleaned up when model already exists"""
        from worker_scripts.model_upload_worker import upload_model

        mock_read_job.return_value = {'model_version': 'v1', 'model_type': 'high_accuracy'}
        mock_exists.return_value = True  # Model and version both exist

        upload_model('/tmp/testmodel', 'testmodel#v1.zip')

        # Should still clean up temp path
        rm_calls = [str(call) for call in mock_os_system.call_args_list
                   if 'rm -rf' in str(call)]
        assert any('/tmp/testmodel' in call for call in rm_calls)
