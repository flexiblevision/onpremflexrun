"""
Integration tests for system management routes
"""
import pytest
import json
from unittest.mock import patch, MagicMock


@pytest.fixture
def app_with_routes():
    """Create app with system routes registered"""
    import os
    from flask import Flask
    from flask_restx import Api

    # Set template folder to find templates
    template_folder = os.path.join(os.path.dirname(__file__), '..', '..', 'templates')
    app = Flask(__name__, template_folder=template_folder)
    app.config['TESTING'] = True
    api = Api(app)

    # Use mock config with necessary keys for job_manager
    mock_config = {
        'latest_stable_ref': 'test_version',
        'use_aws': False
    }

    with patch('settings.config', mock_config):
        from routes import system_routes
        system_routes.register_routes(api)

    return app


@pytest.fixture
def client_with_routes(app_with_routes):
    """Create test client"""
    return app_with_routes.test_client()


class TestListServicesEndpoint:
    """Tests for list services endpoint"""

    @pytest.mark.integration
    @patch('subprocess.getoutput', return_value='Running')
    @patch('subprocess.Popen')
    @patch('routes.system_routes.render_template')
    def test_list_services_all_running(self, mock_render, mock_popen, mock_getoutput, client_with_routes):
        """Test listing services when all are running"""
        # Mock docker inspect
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'true', b'')
        mock_popen.return_value = mock_process

        mock_render.return_value = '<html>Services</html>'

        response = client_with_routes.get('/list_services')

        assert response.status_code == 200
        assert mock_render.called

    @pytest.mark.integration
    @patch('subprocess.getoutput', return_value='Not Running')
    @patch('subprocess.Popen')
    @patch('routes.system_routes.render_template')
    def test_list_services_some_stopped(self, mock_render, mock_popen, mock_getoutput, client_with_routes):
        """Test listing services when some are stopped"""
        # Mock docker inspect for stopped container
        mock_process = MagicMock()
        mock_process.communicate.return_value = (b'false', b'')
        mock_popen.return_value = mock_process

        mock_render.return_value = '<html>Services</html>'

        response = client_with_routes.get('/list_services')

        assert response.status_code == 200


class TestSystemVersionsEndpoint:
    """Tests for system versions endpoint"""

    @pytest.mark.integration
    @patch('routes.system_routes.get_current_container_version')
    def test_get_system_versions(self, mock_version, client_with_routes):
        """Test getting system versions"""
        mock_version.side_effect = [
            'v1.0.0',  # backend
            'v1.0.1',  # frontend
            'v2.0.0',  # prediction
            'v2.0.1',  # predictlite
            'v1.5.0',  # vision
            'v1.2.0',  # creator
            'v1.3.0',  # visiontools
        ]

        response = client_with_routes.get('/system_versions')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'backend_version' in data
        assert 'frontend_version' in data
        assert data['backend_version'] == 'v1.0.0'


class TestSystemIsUptodateEndpoint:
    """Tests for system uptodate check endpoint"""

    @pytest.mark.integration
    @patch('routes.system_routes.is_container_uptodate')
    def test_system_is_uptodate_all_current(self, mock_uptodate, client_with_routes):
        """Test when all components are up to date"""
        mock_uptodate.return_value = (True, 'current')

        response = client_with_routes.get('/system_uptodate')

        assert response.status_code == 200
        # Flask converts boolean to string with newline
        assert response.data.strip() == b'true'

    @pytest.mark.integration
    @patch('routes.system_routes.is_container_uptodate')
    def test_system_is_uptodate_needs_update(self, mock_uptodate, client_with_routes):
        """Test when some components need updates"""
        # Mock different return values for different containers
        mock_uptodate.side_effect = [
            (True, 'current'),
            (False, 'update_available'),
            (True, 'current'),
            (True, 'current'),
            (True, 'current'),
            (True, 'current'),
            (True, 'current'),
        ]

        response = client_with_routes.get('/system_uptodate')

        assert response.status_code == 200
        # Flask converts boolean to string with newline
        assert response.data.strip() == b'false'
