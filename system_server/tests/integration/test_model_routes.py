"""
Integration tests for model management routes
"""
import pytest
import json
from unittest.mock import patch, MagicMock, mock_open
from io import BytesIO


@pytest.fixture
def app_with_model_routes():
    """Create app with model routes registered"""
    from flask import Flask, jsonify
    from flask_restx import Api
    import auth

    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)

    # Register auth error handler
    @app.errorhandler(auth.AuthError)
    def handle_auth_error(ex):
        response = jsonify(ex.error)
        response.status_code = ex.status_code
        return response

    # Use mock config with necessary keys for job_manager
    mock_config = {
        'latest_stable_ref': 'test_version',
        'use_aws': False,
        'environ': 'test',
        'jwt_secret_key': 'test_secret',
        'auth0_domain': 'test.auth0.com'
    }

    with patch('settings.config', mock_config):
        from routes import model_routes
        model_routes.register_routes(api)

    return app


@pytest.fixture
def model_client(app_with_model_routes):
    """Create test client"""
    return app_with_model_routes.test_client()


class TestCategoryIndexEndpoint:
    """Tests for category index endpoint"""

    @pytest.mark.integration
    @patch('routes.model_routes.exists', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data='{"labelmap_dict": {"class1": 1, "class2": 2, "class3": 3}}')
    def test_get_category_index_success(self, mock_file, mock_exists, model_client):
        """Test successful category index retrieval"""
        response = model_client.get('/category_index/test_model/v1.0.0')

        assert response.status_code == 200
        data = json.loads(response.data)
        # JSON keys are always strings
        assert '1' in data
        assert data['1']['name'] == 'class1'
        assert data['1']['id'] == 1

    @pytest.mark.integration
    @patch('routes.model_routes.exists', return_value=False)
    def test_get_category_index_not_found(self, mock_exists, model_client):
        """Test category index when model not found"""
        response = model_client.get('/category_index/nonexistent/v1.0.0')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == {}


class TestDownloadModelsEndpoint:
    """Tests for model download endpoint"""

    @pytest.mark.integration
    @pytest.mark.network
    @patch('rq.Queue.enqueue')
    @patch('worker_scripts.job_manager.insert_job')
    @patch('auth.jwt.get_unverified_header')
    @patch('auth.jwt.decode')
    @patch('six.moves.urllib.request.urlopen')
    def test_download_models_success(self, mock_urlopen, mock_jwt_decode, mock_get_header,
                                     mock_insert_job, mock_enqueue, model_client):
        """Test successful model download initiation"""
        # Mock JWT verification
        mock_get_header.return_value = {'kid': 'test_kid'}
        mock_urlopen.return_value.read.return_value = json.dumps({
            'keys': [{'kid': 'test_kid', 'kty': 'RSA', 'use': 'sig', 'n': 'test', 'e': 'AQAB'}]
        }).encode()
        mock_jwt_decode.return_value = {'sub': 'test_user'}

        mock_job = MagicMock()
        mock_job.id = 'test_job_123'
        mock_enqueue.return_value = mock_job

        data = {
            'model_ids': ['model1', 'model2'],
            'destination': '/models/'
        }

        headers = {
            'Access-Token': 'test_token',
            'Authorization': 'Bearer test_token'
        }

        response = model_client.post('/download_models',
                                     data=json.dumps(data),
                                     content_type='application/json',
                                     headers=headers)

        assert response.status_code == 200
        assert response.data == b'true'
        assert mock_enqueue.call_count == 3  # models, masks, programs

    @pytest.mark.integration
    def test_download_models_no_token(self, model_client):
        """Test model download without access token"""
        data = {'model_ids': ['model1']}

        response = model_client.post('/download_models',
                                     data=json.dumps(data),
                                     content_type='application/json')

        # Should return 401 when auth is missing
        assert response.status_code == 401
        response_data = json.loads(response.data)
        assert 'authorization_header_missing' in response_data['code']


class TestDownloadProgramsEndpoint:
    """Tests for program download endpoint"""

    @pytest.mark.integration
    @pytest.mark.network
    @patch('rq.Queue.enqueue')
    @patch('worker_scripts.job_manager.insert_job')
    @patch('auth.requires_auth', lambda f: f)
    def test_download_programs_success(self, mock_insert_job, mock_enqueue, model_client):
        """Test successful program download"""
        mock_job = MagicMock()
        mock_job.id = 'test_job_456'
        mock_enqueue.return_value = mock_job

        data = {'program_ids': ['prog1', 'prog2']}
        headers = {'Access-Token': 'test_token'}

        response = model_client.post('/download_programs',
                                     data=json.dumps(data),
                                     content_type='application/json',
                                     headers=headers)

        assert response.status_code == 200
        assert response.data == b'true'
        mock_enqueue.assert_called_once()


class TestUploadModelEndpoint:
    """Tests for model upload endpoint"""

    @pytest.mark.integration
    @patch('os.path.exists', return_value=True)
    @patch('os.system')
    @patch('zipfile.ZipFile')
    @patch('tempfile.gettempdir', return_value='/tmp/')
    @patch('rq.Queue.enqueue')
    @patch('worker_scripts.job_manager.insert_job')
    def test_upload_model_success(self, mock_insert_job, mock_enqueue, mock_tempdir,
                                  mock_zipfile, mock_system, mock_exists, model_client):
        """Test successful model upload"""
        # Create mock ZIP file
        mock_zip_instance = MagicMock()
        mock_zipfile.return_value.__enter__.return_value = mock_zip_instance

        # Mock job.json content
        job_data = {'model_version': 'v1.0.0'}

        with patch('builtins.open', mock_open(read_data=json.dumps(job_data))):
            # Create file data
            file_data = BytesIO(b'fake zip content')
            data = {
                'file': (file_data, 'test_model#v1.zip')
            }

            mock_job = MagicMock()
            mock_job.id = 'upload_job_789'
            mock_enqueue.return_value = mock_job

            response = model_client.post('/upload_model',
                                        data=data,
                                        content_type='multipart/form-data')

            assert response.status_code == 200

    @pytest.mark.integration
    def test_upload_model_no_file(self, model_client):
        """Test model upload without file"""
        response = model_client.post('/upload_model',
                                    data={},
                                    content_type='multipart/form-data')

        assert response.status_code == 200
        assert response.data == b'false'

    @pytest.mark.integration
    @patch('zipfile.ZipFile', side_effect=Exception('Bad ZIP'))
    @patch('tempfile.gettempdir', return_value='/tmp/')
    def test_upload_model_bad_zip(self, mock_tempdir, mock_zipfile, model_client):
        """Test model upload with corrupted ZIP"""
        file_data = BytesIO(b'corrupted zip')
        data = {
            'file': (file_data, 'bad_model.zip')
        }

        response = model_client.post('/upload_model',
                                    data=data,
                                    content_type='multipart/form-data')

        # Should handle error gracefully
        assert response.status_code == 200
