"""
Unit tests for upgrade_recorder module
Tests the upgrade recording functionality that tracks system upgrade progress in MongoDB
"""

import pytest
import sys
from unittest.mock import Mock, MagicMock, patch, call
from datetime import datetime


# Mock the pymongo module before importing upgrade_recorder
sys.modules['pymongo'] = MagicMock()

# Add the upgrades directory to the path so we can import upgrade_recorder
sys.path.insert(0, '/home/alec/Development/ACTIVE/onpremflexrun/upgrades')

import upgrade_recorder


@pytest.fixture
def mock_mongo_client():
    """Fixture to mock MongoDB client and collection"""
    with patch('upgrade_recorder.MongoClient') as mock_client:
        mock_collection = MagicMock()
        mock_client.return_value.__getitem__.return_value.__getitem__.return_value = mock_collection

        # Set up the global upgrade_records in the module
        with patch('upgrade_recorder.upgrade_records', mock_collection):
            yield mock_collection


@pytest.fixture
def sample_record():
    """Fixture providing a sample upgrade record"""
    return {
        "id": "test_upgrade_001",
        "cur_step_txt": "Installing packages",
        "upgrade_steps": 5,
        "cur_step": 2,
        "last_updated": 1234567890000,
        "start_time": 1234567890000,
        "end_time": None,
        "state": "running",
        "log": " # Step 1 # Step 2"
    }


class TestMsTimestamp:
    """Tests for ms_timestamp function"""

    def test_ms_timestamp_returns_integer(self):
        """Test that ms_timestamp returns an integer"""
        result = upgrade_recorder.ms_timestamp()
        assert isinstance(result, int)

    def test_ms_timestamp_returns_milliseconds(self):
        """Test that ms_timestamp returns a value in milliseconds (13 digits)"""
        result = upgrade_recorder.ms_timestamp()
        # Check that the timestamp is in milliseconds (should be 13 digits)
        assert len(str(result)) == 13

    @patch('upgrade_recorder.datetime')
    def test_ms_timestamp_uses_current_time(self, mock_datetime):
        """Test that ms_timestamp uses current datetime"""
        mock_now = Mock()
        mock_now.timestamp.return_value = 1234567890.123
        mock_datetime.datetime.now.return_value = mock_now

        result = upgrade_recorder.ms_timestamp()

        mock_datetime.datetime.now.assert_called_once()
        assert result == 1234567890123


class TestInitialize:
    """Tests for initialize function"""

    def test_initialize_with_no_id_returns_none(self, mock_mongo_client, capsys):
        """Test that initialize with no ID prints error and returns None"""
        result = upgrade_recorder.initialize(None, 5)

        captured = capsys.readouterr()
        assert "Must pass an id" in captured.out
        assert result is None

    def test_initialize_with_empty_id_returns_none(self, mock_mongo_client, capsys):
        """Test that initialize with empty ID prints error and returns None"""
        result = upgrade_recorder.initialize("", 5)

        captured = capsys.readouterr()
        assert "Must pass an id" in captured.out
        assert result is None

    @patch('upgrade_recorder.ms_timestamp')
    def test_initialize_creates_correct_record(self, mock_timestamp, mock_mongo_client):
        """Test that initialize creates a record with correct structure"""
        mock_timestamp.return_value = 1234567890000

        result = upgrade_recorder.initialize("test_upgrade_001", 5)

        assert result is not None
        assert result['id'] == "test_upgrade_001"
        assert result['cur_step_txt'] == "Initializing"
        assert result['upgrade_steps'] == 5
        assert result['cur_step'] == 0
        assert result['last_updated'] == 1234567890000
        assert result['start_time'] == 1234567890000
        assert result['end_time'] is None
        assert result['state'] == "running"
        assert result['log'] == ""

    @patch('upgrade_recorder.ms_timestamp')
    def test_initialize_updates_running_upgrades_to_failed(self, mock_timestamp, mock_mongo_client):
        """Test that initialize marks any existing running upgrades as failed"""
        mock_timestamp.return_value = 1234567890000

        upgrade_recorder.initialize("test_upgrade_001", 5)

        # Check that update_one was called to set running upgrades to failed
        calls = mock_mongo_client.update_one.call_args_list
        assert len(calls) == 2
        # First call should update running to failed
        assert calls[0][0][0] == {'state': 'running'}
        assert calls[0][0][1] == {'$set': {'state': 'failed'}}

    @patch('upgrade_recorder.ms_timestamp')
    def test_initialize_inserts_new_record(self, mock_timestamp, mock_mongo_client):
        """Test that initialize inserts the new record into MongoDB"""
        mock_timestamp.return_value = 1234567890000

        result = upgrade_recorder.initialize("test_upgrade_001", 5)

        # Check that update_one was called with upsert=True
        calls = mock_mongo_client.update_one.call_args_list
        # Second call should insert the new record
        assert calls[1][0][0] == {'id': "test_upgrade_001"}
        assert calls[1][0][2] is True  # upsert=True


class TestGetRecord:
    """Tests for get_record function"""

    def test_get_record_calls_find_one(self, mock_mongo_client):
        """Test that get_record calls find_one with correct ID"""
        mock_mongo_client.find_one.return_value = {"id": "test_001"}

        upgrade_recorder.get_record("test_001")

        mock_mongo_client.find_one.assert_called_once_with({'id': "test_001"})

    def test_get_record_returns_record(self, mock_mongo_client, sample_record):
        """Test that get_record returns the found record"""
        mock_mongo_client.find_one.return_value = sample_record

        result = upgrade_recorder.get_record("test_upgrade_001")

        assert result == sample_record

    def test_get_record_returns_none_when_not_found(self, mock_mongo_client):
        """Test that get_record returns None when record not found"""
        mock_mongo_client.find_one.return_value = None

        result = upgrade_recorder.get_record("nonexistent_id")

        assert result is None


class TestUpdate:
    """Tests for update function"""

    @patch('upgrade_recorder.ms_timestamp')
    def test_update_appends_to_log(self, mock_timestamp, mock_mongo_client, sample_record):
        """Test that update appends new text to the log"""
        mock_timestamp.return_value = 1234567890001

        upgrade_recorder.update(sample_record, 3, "Step 3 completed")

        assert " # Step 3 completed" in sample_record['log']

    @patch('upgrade_recorder.ms_timestamp')
    def test_update_handles_missing_log(self, mock_timestamp, mock_mongo_client):
        """Test that update handles records without a log field"""
        mock_timestamp.return_value = 1234567890001
        record = {
            "id": "test_001",
            "upgrade_steps": 5,
            "cur_step": 1,
            "state": "running"
        }

        upgrade_recorder.update(record, 2, "New step")

        assert 'log' in record
        assert " # New step" in record['log']

    @patch('upgrade_recorder.ms_timestamp')
    def test_update_updates_current_step(self, mock_timestamp, mock_mongo_client, sample_record):
        """Test that update updates the current step"""
        mock_timestamp.return_value = 1234567890001

        upgrade_recorder.update(sample_record, 3, "Step 3")

        assert sample_record['cur_step'] == 3
        assert sample_record['cur_step_txt'] == "Step 3"

    @patch('upgrade_recorder.ms_timestamp')
    def test_update_updates_last_updated_timestamp(self, mock_timestamp, mock_mongo_client, sample_record):
        """Test that update updates the last_updated timestamp"""
        mock_timestamp.return_value = 1234567890999

        upgrade_recorder.update(sample_record, 3, "Step 3")

        assert sample_record['last_updated'] == 1234567890999

    @patch('upgrade_recorder.ms_timestamp')
    def test_update_marks_completed_when_final_step(self, mock_timestamp, mock_mongo_client, sample_record):
        """Test that update marks upgrade as completed when reaching final step"""
        mock_timestamp.return_value = 1234567890999
        sample_record['upgrade_steps'] = 5

        upgrade_recorder.update(sample_record, 5, "Final step")

        assert sample_record['state'] == "completed"
        assert sample_record['end_time'] == 1234567890999

    @patch('upgrade_recorder.ms_timestamp')
    def test_update_does_not_mark_completed_when_not_final_step(self, mock_timestamp, mock_mongo_client, sample_record):
        """Test that update does not mark as completed when not on final step"""
        mock_timestamp.return_value = 1234567890999
        sample_record['upgrade_steps'] = 5
        sample_record['state'] = "running"

        upgrade_recorder.update(sample_record, 3, "Step 3")

        assert sample_record['state'] == "running"
        assert sample_record['end_time'] is None

    @patch('upgrade_recorder.ms_timestamp')
    def test_update_removes_id_field(self, mock_timestamp, mock_mongo_client, sample_record):
        """Test that update removes _id field before updating MongoDB"""
        mock_timestamp.return_value = 1234567890999
        sample_record['_id'] = "mongodb_object_id"

        upgrade_recorder.update(sample_record, 3, "Step 3")

        assert '_id' not in sample_record

    @patch('upgrade_recorder.ms_timestamp')
    def test_update_handles_string_cur_step(self, mock_timestamp, mock_mongo_client, sample_record):
        """Test that update handles cur_step as string"""
        mock_timestamp.return_value = 1234567890999
        sample_record['upgrade_steps'] = 5

        upgrade_recorder.update(sample_record, "5", "Final step")

        assert sample_record['state'] == "completed"

    @patch('upgrade_recorder.ms_timestamp')
    def test_update_calls_mongo_update_one(self, mock_timestamp, mock_mongo_client, sample_record, capsys):
        """Test that update calls MongoDB update_one with correct parameters"""
        mock_timestamp.return_value = 1234567890999

        upgrade_recorder.update(sample_record, 3, "Step 3")

        mock_mongo_client.update_one.assert_called_once()
        call_args = mock_mongo_client.update_one.call_args
        assert call_args[0][0] == {'id': sample_record['id']}
        assert call_args[0][2] is True  # upsert=True


class TestMain:
    """Tests for main function"""

    @patch('upgrade_recorder.get_record')
    @patch('upgrade_recorder.initialize')
    def test_main_initializes_new_record(self, mock_initialize, mock_get_record, mock_mongo_client, capsys):
        """Test that main initializes a new record when none exists"""
        mock_get_record.return_value = None
        mock_initialize.return_value = {"id": "test_001"}

        upgrade_recorder.main(['-i', 'test_001', '-s', '5'])

        mock_get_record.assert_called_once_with('test_001')
        mock_initialize.assert_called_once_with('test_001', 5)

    @patch('upgrade_recorder.get_record')
    @patch('upgrade_recorder.update')
    def test_main_updates_existing_record(self, mock_update, mock_get_record, mock_mongo_client, capsys, sample_record):
        """Test that main updates an existing record"""
        mock_get_record.return_value = sample_record

        upgrade_recorder.main(['-i', 'test_upgrade_001', '-c', '3', '-t', 'Step 3'])

        mock_get_record.assert_called_once_with('test_upgrade_001')
        mock_update.assert_called_once_with(sample_record, 3, 'Step 3')

    def test_main_parses_id_argument(self, mock_mongo_client, capsys):
        """Test that main correctly parses -i argument"""
        with patch('upgrade_recorder.get_record', return_value=None):
            with patch('upgrade_recorder.initialize') as mock_init:
                upgrade_recorder.main(['-i', 'my_upgrade_id', '-s', '10'])

                mock_init.assert_called_once_with('my_upgrade_id', 10)

    def test_main_parses_num_steps_argument(self, mock_mongo_client, capsys):
        """Test that main correctly parses -s argument"""
        with patch('upgrade_recorder.get_record', return_value=None):
            with patch('upgrade_recorder.initialize') as mock_init:
                upgrade_recorder.main(['-i', 'test_id', '-s', '7'])

                mock_init.assert_called_once_with('test_id', 7)

    def test_main_parses_current_step_argument(self, mock_mongo_client, capsys, sample_record):
        """Test that main correctly parses -c argument"""
        with patch('upgrade_recorder.get_record', return_value=sample_record):
            with patch('upgrade_recorder.update') as mock_update:
                upgrade_recorder.main(['-i', 'test_id', '-c', '4', '-t', 'text'])

                mock_update.assert_called_once_with(sample_record, 4, 'text')

    def test_main_parses_text_argument(self, mock_mongo_client, capsys, sample_record):
        """Test that main correctly parses -t argument"""
        with patch('upgrade_recorder.get_record', return_value=sample_record):
            with patch('upgrade_recorder.update') as mock_update:
                upgrade_recorder.main(['-i', 'test_id', '-c', '2', '-t', 'My status text'])

                mock_update.assert_called_once_with(sample_record, 2, 'My status text')

    def test_main_handles_invalid_arguments(self, mock_mongo_client):
        """Test that main handles invalid arguments gracefully"""
        with pytest.raises(SystemExit):
            upgrade_recorder.main(['-x', 'invalid'])

    def test_main_does_nothing_without_id(self, mock_mongo_client, capsys):
        """Test that main does nothing when no ID is provided"""
        with patch('upgrade_recorder.get_record') as mock_get:
            with patch('upgrade_recorder.initialize') as mock_init:
                upgrade_recorder.main(['-s', '5'])

                mock_get.assert_not_called()
                mock_init.assert_not_called()

    def test_main_handles_all_arguments_together(self, mock_mongo_client, capsys):
        """Test that main handles all arguments provided together"""
        with patch('upgrade_recorder.get_record', return_value=None):
            with patch('upgrade_recorder.initialize') as mock_init:
                upgrade_recorder.main(['-i', 'test_001', '-s', '10', '-c', '5', '-t', 'text'])

                mock_init.assert_called_once_with('test_001', 10)

    def test_main_prints_argv(self, mock_mongo_client, capsys):
        """Test that main prints the argv for debugging"""
        with patch('upgrade_recorder.get_record', return_value=None):
            with patch('upgrade_recorder.initialize'):
                argv_input = ['-i', 'test_id', '-s', '5']
                upgrade_recorder.main(argv_input)

                captured = capsys.readouterr()
                assert str(argv_input) in captured.out


class TestIntegrationScenarios:
    """Integration tests for common upgrade scenarios"""

    @patch('upgrade_recorder.ms_timestamp')
    def test_complete_upgrade_workflow(self, mock_timestamp, mock_mongo_client):
        """Test a complete upgrade workflow from initialization to completion"""
        mock_timestamp.return_value = 1234567890000

        # Initialize upgrade
        record = upgrade_recorder.initialize("upgrade_001", 3)
        assert record['state'] == "running"
        assert record['cur_step'] == 0

        # Update step 1
        mock_timestamp.return_value = 1234567890100
        upgrade_recorder.update(record, 1, "Step 1 completed")
        assert record['cur_step'] == 1
        assert record['state'] == "running"

        # Update step 2
        mock_timestamp.return_value = 1234567890200
        upgrade_recorder.update(record, 2, "Step 2 completed")
        assert record['cur_step'] == 2
        assert record['state'] == "running"

        # Update final step
        mock_timestamp.return_value = 1234567890300
        upgrade_recorder.update(record, 3, "Step 3 completed")
        assert record['cur_step'] == 3
        assert record['state'] == "completed"
        assert record['end_time'] == 1234567890300

    @patch('upgrade_recorder.ms_timestamp')
    def test_upgrade_log_accumulation(self, mock_timestamp, mock_mongo_client):
        """Test that logs accumulate correctly throughout upgrade"""
        mock_timestamp.return_value = 1234567890000

        record = upgrade_recorder.initialize("upgrade_002", 3)

        upgrade_recorder.update(record, 1, "Installing dependencies")
        upgrade_recorder.update(record, 2, "Configuring system")
        upgrade_recorder.update(record, 3, "Restarting services")

        log = record['log']
        assert "Installing dependencies" in log
        assert "Configuring system" in log
        assert "Restarting services" in log
        # Check order
        assert log.index("Installing dependencies") < log.index("Configuring system")
        assert log.index("Configuring system") < log.index("Restarting services")
