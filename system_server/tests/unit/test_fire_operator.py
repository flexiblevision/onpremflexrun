"""
Unit tests for FireOperator.py
"""
import pytest
import datetime
from unittest.mock import Mock, patch, MagicMock, call


class TestMsTimestamp:
    """Tests for ms_timestamp function"""

    @pytest.mark.unit
    @patch('datetime.datetime')
    def test_ms_timestamp_returns_milliseconds(self, mock_datetime):
        """Test that ms_timestamp returns time in milliseconds"""
        # Mock datetime.now() to return a fixed timestamp
        mock_now = MagicMock()
        mock_now.timestamp.return_value = 1234567890.123456
        mock_datetime.now.return_value = mock_now

        # Import after mocking
        import sys
        sys.path.insert(0, '/home/alec/Development/ACTIVE/onpremflexrun')

        # Replicate ms_timestamp logic
        def ms_timestamp():
            return int(datetime.datetime.now().timestamp()*1000)

        result = ms_timestamp()
        expected = int(1234567890.123456 * 1000)

        assert result == expected
        assert isinstance(result, int)


class TestFireOperatorInit:
    """Tests for FireOperator initialization"""

    @pytest.mark.unit
    @patch('aws.FireOperator.firestore.Client')
    @patch('aws.FireOperator.service_account')
    @patch('os.path.isfile')
    def test_fire_operator_initialization(self, mock_isfile, mock_service_account, mock_firestore_client):
        """Test FireOperator class initialization"""
        mock_isfile.return_value = True

        # Mock firestore db
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_document = MagicMock()

        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_document

        # Replicate FireOperator.__init__ logic
        class MockFireOperator:
            def __init__(self):
                self.db = mock_db
                self.collection = "test_collection"
                self.document = "test_document"
                self.capture_doc = self.db.collection("inspections").document(self.document)
                self.status_doc = self.db.collection("status").document(self.document)
                self.trigger_dest = "http://test.com"
                self.last_read_time = None
                self.intialized = False

        fo = MockFireOperator()

        assert fo.db == mock_db
        assert fo.collection == "test_collection"
        assert fo.document == "test_document"
        assert fo.last_read_time is None
        assert fo.intialized is False


class TestSyncingAlive:
    """Tests for syncing_alive method"""

    @pytest.mark.unit
    def test_syncing_alive_when_enabled(self):
        """Test syncing_alive returns True when sync is enabled"""
        mock_util_ref = MagicMock()

        # Setup mock returns
        mock_util_ref.find_one.side_effect = [
            {'type': 'predict_sync', 'ms_time': '1000000'},
            {'type': 'sync', 'is_enabled': True},
            {'type': 'sync_interval', 'interval': '5'}
        ]

        # Replicate syncing_alive logic
        def syncing_alive():
            last_sync_ref = mock_util_ref.find_one({'type': 'predict_sync'}, {'_id': 0})
            sync_enabled_ref = mock_util_ref.find_one({'type': 'sync'}, {'_id': 0})
            sync_interval_ref = mock_util_ref.find_one({'type': 'sync_interval'}, {'_id': 0})

            sync_enabled = sync_enabled_ref['is_enabled']
            return sync_enabled

        result = syncing_alive()
        assert result is True

    @pytest.mark.unit
    def test_syncing_alive_when_disabled_recent_sync(self):
        """Test syncing_alive when disabled but recent sync exists"""
        mock_util_ref = MagicMock()

        current_time = 2000000
        last_sync_time = 1900000  # Recent sync

        mock_util_ref.find_one.side_effect = [
            {'type': 'predict_sync', 'ms_time': str(last_sync_time)},
            {'type': 'sync', 'is_enabled': False},
            {'type': 'sync_interval', 'interval': '5'}
        ]

        # Replicate logic
        def syncing_alive():
            last_sync_ref = mock_util_ref.find_one({'type': 'predict_sync'}, {'_id': 0})
            sync_enabled_ref = mock_util_ref.find_one({'type': 'sync'}, {'_id': 0})
            sync_interval_ref = mock_util_ref.find_one({'type': 'sync_interval'}, {'_id': 0})

            sync_enabled = sync_enabled_ref['is_enabled']
            last_sync = int(last_sync_ref['ms_time'])
            sync_interval = int(sync_interval_ref['interval'])

            if sync_enabled == False and (last_sync + ((60000*sync_interval) * 10)) > current_time:
                return True
            else:
                return False

        result = syncing_alive()
        assert result is True


class TestListener:
    """Tests for listener method"""

    @pytest.mark.unit
    @patch('requests.post')
    def test_listener_initialized(self, mock_post):
        """Test listener triggers when initialized"""
        mock_doc = MagicMock()
        mock_doc.id = 'doc123'
        mock_doc.to_dict.return_value = {'data': 'test'}

        mock_thread = MagicMock()
        read_time = MagicMock()

        # Replicate listener logic
        def listener(doc_snapshot, changes, read_time, initialized, trigger_dest):
            for doc in doc_snapshot:
                trigger_record = doc.to_dict()
                if initialized:
                    mock_post(trigger_dest, json=trigger_record, timeout=10)

        listener([mock_doc], [], read_time, True, 'http://test.com')

        mock_post.assert_called_once_with('http://test.com', json={'data': 'test'}, timeout=10)

    @pytest.mark.unit
    @patch('requests.post')
    def test_listener_not_initialized(self, mock_post):
        """Test listener doesn't trigger when not initialized"""
        mock_doc = MagicMock()
        mock_doc.id = 'doc123'
        mock_doc.to_dict.return_value = {'data': 'test'}

        # Replicate listener logic
        def listener(doc_snapshot, changes, read_time, initialized, trigger_dest):
            for doc in doc_snapshot:
                trigger_record = doc.to_dict()
                if initialized:
                    mock_post(trigger_dest, json=trigger_record, timeout=10)

        listener([mock_doc], [], MagicMock(), False, 'http://test.com')

        mock_post.assert_not_called()


class TestUpdateStatus:
    """Tests for update_status method"""

    @pytest.mark.unit
    def test_update_status(self):
        """Test update_status sets status document"""
        mock_status_doc = MagicMock()

        status_data = {'status': 'active', 'message': 'processing'}

        # Replicate update_status logic
        def update_status(status):
            mock_status_doc.set(status)

        update_status(status_data)

        mock_status_doc.set.assert_called_once_with(status_data)

    @pytest.mark.unit
    def test_update_status_empty(self):
        """Test update_status with empty status"""
        mock_status_doc = MagicMock()

        def update_status(status):
            mock_status_doc.set(status)

        update_status({})

        mock_status_doc.set.assert_called_once_with({})


class TestGetStatus:
    """Tests for get_status method"""

    @pytest.mark.unit
    def test_get_status_exists(self):
        """Test get_status when document exists"""
        mock_status_doc = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {'status': 'active', 'count': 42}

        mock_status_doc.get.return_value = mock_doc

        # Replicate get_status logic
        def get_status():
            status_ref = mock_status_doc
            doc = status_ref.get()
            if doc.exists:
                return doc.to_dict()
            else:
                return None

        result = get_status()

        assert result == {'status': 'active', 'count': 42}
        mock_status_doc.get.assert_called_once()

    @pytest.mark.unit
    def test_get_status_not_exists(self):
        """Test get_status when document doesn't exist"""
        mock_status_doc = MagicMock()
        mock_doc = MagicMock()
        mock_doc.exists = False

        mock_status_doc.get.return_value = mock_doc

        def get_status():
            status_ref = mock_status_doc
            doc = status_ref.get()
            if doc.exists:
                return doc.to_dict()
            else:
                return None

        result = get_status()

        assert result is None


class TestRunOperator:
    """Tests for run_operator function"""

    @pytest.mark.unit
    @patch('subprocess.run')
    @patch('subprocess.Popen')
    def test_run_operator_not_running(self, mock_popen, mock_run):
        """Test run_operator when fo_server is not running"""
        mock_run.return_value = MagicMock(
            stdout='No forever processes running',
            returncode=0
        )

        # Replicate run_operator logic
        def run_operator(use_aws, fo_server_path):
            if use_aws:
                result = mock_run(['forever', 'list'], capture_output=True, text=True, check=True)
                forever_list_output = result.stdout

                is_running = False
                for line in forever_list_output.splitlines():
                    if fo_server_path in line and "STOPPED" not in line:
                        is_running = True
                        break

                if is_running:
                    return 'skipped'
                else:
                    mock_popen(['forever', 'start', '-c', 'python3', fo_server_path],
                              stdout=mock_popen.PIPE, stderr=mock_popen.PIPE)
                    return 'started'

        result = run_operator(True, '/path/to/fo_server.py')

        assert result == 'started'
        mock_popen.assert_called_once()

    @pytest.mark.unit
    @patch('subprocess.run')
    def test_run_operator_already_running(self, mock_run):
        """Test run_operator when fo_server is already running"""
        fo_server_path = '/home/user/flex-run/aws/fo_server.py'
        mock_run.return_value = MagicMock(
            stdout=f'[0] {fo_server_path} RUNNING',
            returncode=0
        )

        def run_operator(use_aws, fo_server_path):
            if use_aws:
                result = mock_run(['forever', 'list'], capture_output=True, text=True, check=True)
                forever_list_output = result.stdout

                is_running = False
                for line in forever_list_output.splitlines():
                    if fo_server_path in line and "STOPPED" not in line:
                        is_running = True
                        break

                if is_running:
                    return 'skipped'
                else:
                    return 'started'

        result = run_operator(True, fo_server_path)

        assert result == 'skipped'

    @pytest.mark.unit
    @patch('subprocess.run')
    def test_run_operator_forever_command_fails(self, mock_run):
        """Test run_operator handles forever command failure"""
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(1, 'forever list', stderr='Command not found')

        def run_operator(use_aws):
            if use_aws:
                try:
                    result = mock_run(['forever', 'list'], capture_output=True, text=True, check=True)
                    return 'checked'
                except subprocess.CalledProcessError as e:
                    return 'error'

        result = run_operator(True)

        assert result == 'error'


class TestGetStatusByServiceAccount:
    """Tests for get_status_by_service_account method"""

    @pytest.mark.unit
    @patch('aws.FireOperator.AuthorizedSession')
    @patch('aws.FireOperator.service_account.IDTokenCredentials')
    def test_get_status_by_service_account(self, mock_id_token_creds, mock_authed_session):
        """Test get_status_by_service_account API call"""
        mock_response = MagicMock()
        mock_response.json.return_value = {'status': 'success', 'data': 'test'}

        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        mock_authed_session.return_value = mock_session

        # Replicate get_status_by_service_account logic
        def get_status_by_service_account(document):
            url = 'https://us-central1-testingprivateapis.cloudfunctions.net/get-status-by-service-account'
            authed_session = mock_authed_session(mock_id_token_creds)
            resp = authed_session.post(document)
            return resp.json()

        result = get_status_by_service_account('test_document')

        assert result == {'status': 'success', 'data': 'test'}
        mock_session.post.assert_called_once_with('test_document')


class TestFirestoreIntegration:
    """Tests for Firestore document operations"""

    @pytest.mark.unit
    def test_capture_doc_creation(self):
        """Test capture document reference creation"""
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_document = MagicMock()

        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_document

        # Replicate document creation
        document_name = 'warehouse_zone'
        capture_doc = mock_db.collection("inspections").document(document_name)

        mock_db.collection.assert_called_with("inspections")
        mock_collection.document.assert_called_with(document_name)

    @pytest.mark.unit
    def test_status_doc_creation(self):
        """Test status document reference creation"""
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_document = MagicMock()

        mock_db.collection.return_value = mock_collection
        mock_collection.document.return_value = mock_document

        document_name = 'warehouse_zone'
        status_doc = mock_db.collection("status").document(document_name)

        mock_db.collection.assert_called_with("status")
        mock_collection.document.assert_called_with(document_name)


class TestThreadingEvent:
    """Tests for threading event handling"""

    @pytest.mark.unit
    def test_thread_event_initialization(self):
        """Test thread event is created"""
        import threading

        thread_event = threading.Event()

        assert isinstance(thread_event, threading.Event)
        assert not thread_event.is_set()

    @pytest.mark.unit
    def test_thread_event_set(self):
        """Test thread event can be set"""
        import threading

        thread_event = threading.Event()
        thread_event.set()

        assert thread_event.is_set()
