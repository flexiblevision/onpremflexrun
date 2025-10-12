"""
Unit tests for retrieve_masks.py worker script
"""
import pytest
import uuid
from unittest.mock import Mock, patch, MagicMock


class TestRetrieveMasks:
    """Tests for retrieve_masks function"""

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_basic(self, mock_get, mock_masks_collection):
        """Test basic mask retrieval"""
        from worker_scripts.retrieve_masks import retrieve_masks

        # Mock response
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'maskName': 'mask1', 'maskId': 'id1', 'data': 'test'},
            {'maskName': 'mask2', 'maskId': 'id2', 'data': 'test2'}
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {},
                'project2': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Should make request for each project
        assert mock_get.call_count == 2
        # Should update masks collection for each mask
        assert mock_masks_collection.update_one.call_count == 4  # 2 projects * 2 masks

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_correct_url(self, mock_get, mock_masks_collection):
        """Test that correct URL is constructed"""
        from worker_scripts.retrieve_masks import retrieve_masks, CLOUD_DOMAIN

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project123': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Verify URL construction
        expected_url = f'{CLOUD_DOMAIN}/api/capture/mask/get_masks/project123'
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert expected_url in str(call_args)

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_with_auth_headers(self, mock_get, mock_masks_collection):
        """Test that authorization headers are included"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }
        token = 'test_bearer_token'

        retrieve_masks(resp_data, token)

        # Check headers
        call_kwargs = mock_get.call_args[1]
        assert 'headers' in call_kwargs
        assert call_kwargs['headers']['Authorization'] == f'Bearer {token}'
        assert call_kwargs['headers']['Content-Type'] == 'application/json'

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_with_timeout(self, mock_get, mock_masks_collection):
        """Test that request includes timeout"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        call_kwargs = mock_get.call_args[1]
        assert 'timeout' in call_kwargs
        assert call_kwargs['timeout'] == 30

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_generates_mask_id(self, mock_get, mock_masks_collection):
        """Test that maskId is generated when not present"""
        from worker_scripts.retrieve_masks import retrieve_masks

        # Mask without maskId
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'maskName': 'mask_without_id', 'data': 'test'}
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Check that maskId was added
        update_call = mock_masks_collection.update_one.call_args[0]
        mask_data = update_call[1]['$set']
        assert 'maskId' in mask_data
        # Should be a valid UUID-like string
        assert len(mask_data['maskId']) > 0

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_preserves_existing_mask_id(self, mock_get, mock_masks_collection):
        """Test that existing maskId is preserved"""
        from worker_scripts.retrieve_masks import retrieve_masks

        existing_id = 'existing-mask-id-123'
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'maskName': 'mask_with_id', 'maskId': existing_id, 'data': 'test'}
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Check that existing maskId was preserved
        update_call = mock_masks_collection.update_one.call_args[0]
        mask_data = update_call[1]['$set']
        assert mask_data['maskId'] == existing_id

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_updates_by_mask_name(self, mock_get, mock_masks_collection):
        """Test that masks are updated/upserted by maskName"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'maskName': 'test_mask', 'maskId': 'id1', 'data': 'test'}
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Check query and update
        update_call = mock_masks_collection.update_one.call_args[0]
        query = update_call[0]
        update_data = update_call[1]
        upsert = mock_masks_collection.update_one.call_args[0][2]

        assert query == {'maskName': 'test_mask'}
        assert '$set' in update_data
        assert upsert is True  # Should be upsert

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_empty_response(self, mock_get, mock_masks_collection):
        """Test handling of empty mask response"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Should not update collection if no masks
        mock_masks_collection.update_one.assert_not_called()

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_none_response(self, mock_get, mock_masks_collection):
        """Test handling of None response"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = None
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Should not update collection if response is None
        mock_masks_collection.update_one.assert_not_called()

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_multiple_projects(self, mock_get, mock_masks_collection):
        """Test retrieving masks for multiple projects"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'maskName': 'mask1', 'maskId': 'id1'}
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {},
                'project2': {},
                'project3': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Should make 3 requests, one per project
        assert mock_get.call_count == 3
        # Should update 3 times (1 mask per project)
        assert mock_masks_collection.update_one.call_count == 3

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_non_dict_items(self, mock_get, mock_masks_collection):
        """Test that non-dict items in response are skipped"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'maskName': 'valid_mask', 'maskId': 'id1'},
            'invalid_string',
            123,
            ['invalid_list'],
            {'maskName': 'another_valid_mask', 'maskId': 'id2'}
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Should only update for the 2 valid dict items
        assert mock_masks_collection.update_one.call_count == 2

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_preserves_all_fields(self, mock_get, mock_masks_collection):
        """Test that all mask fields are preserved"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                'maskName': 'complex_mask',
                'maskId': 'id1',
                'data': 'base64_encoded_data',
                'width': 1920,
                'height': 1080,
                'format': 'png',
                'metadata': {'key': 'value'}
            }
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Check that all fields are in the update
        update_call = mock_masks_collection.update_one.call_args[0]
        mask_data = update_call[1]['$set']

        assert mask_data['maskName'] == 'complex_mask'
        assert mask_data['maskId'] == 'id1'
        assert mask_data['data'] == 'base64_encoded_data'
        assert mask_data['width'] == 1920
        assert mask_data['height'] == 1080
        assert mask_data['format'] == 'png'
        assert mask_data['metadata'] == {'key': 'value'}

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_retrieve_masks_request_exception(self, mock_get, mock_masks_collection):
        """Test handling of request exceptions"""
        from worker_scripts.retrieve_masks import retrieve_masks

        # Simulate a request exception
        mock_get.side_effect = Exception('Network error')

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        # Should raise the exception (function doesn't handle it)
        with pytest.raises(Exception):
            retrieve_masks(resp_data, 'test_token')

    @pytest.mark.unit
    def test_cloud_domain_constant(self):
        """Test CLOUD_DOMAIN constant"""
        from worker_scripts.retrieve_masks import CLOUD_DOMAIN

        # Should have a valid cloud domain
        assert CLOUD_DOMAIN is not None
        assert 'http' in CLOUD_DOMAIN or 'flexiblevision' in CLOUD_DOMAIN


class TestMaskIdGeneration:
    """Tests for mask ID generation logic"""

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    @patch('uuid.uuid4')
    def test_mask_id_generation_uses_uuid(self, mock_uuid, mock_get, mock_masks_collection):
        """Test that mask ID generation uses uuid4"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_uuid.return_value = 'test-uuid-value'
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'maskName': 'mask1'}  # No maskId
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # uuid4 should have been called
        mock_uuid.assert_called_once()

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_mask_id_unique_per_mask(self, mock_get, mock_masks_collection):
        """Test that each mask without ID gets a unique ID"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'maskName': 'mask1'},  # No maskId
            {'maskName': 'mask2'}   # No maskId
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Get the generated IDs from both update calls
        calls = mock_masks_collection.update_one.call_args_list
        id1 = calls[0][0][1]['$set']['maskId']
        id2 = calls[1][0][1]['$set']['maskId']

        # IDs should be different
        assert id1 != id2
        assert len(id1) > 0
        assert len(id2) > 0


class TestDataIntegrity:
    """Tests for data integrity during mask retrieval"""

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_mask_name_used_as_key(self, mock_get, mock_masks_collection):
        """Test that maskName is consistently used as the query key"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'maskName': 'unique_mask_name', 'maskId': 'id1', 'data': 'test'}
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Verify query uses maskName
        query = mock_masks_collection.update_one.call_args[0][0]
        assert 'maskName' in query
        assert query['maskName'] == 'unique_mask_name'

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_upsert_flag_is_true(self, mock_get, mock_masks_collection):
        """Test that upsert flag is set to True"""
        from worker_scripts.retrieve_masks import retrieve_masks

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {'maskName': 'test_mask', 'maskId': 'id1'}
        ]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Check upsert parameter
        upsert = mock_masks_collection.update_one.call_args[0][2]
        assert upsert is True

    @pytest.mark.unit
    @patch('worker_scripts.retrieve_masks.masks_collection')
    @patch('requests.get')
    def test_mask_data_not_modified(self, mock_get, mock_masks_collection):
        """Test that mask data is not modified during processing"""
        from worker_scripts.retrieve_masks import retrieve_masks

        original_mask = {
            'maskName': 'test_mask',
            'maskId': 'original_id',
            'important_data': 'must_preserve'
        }
        mock_response = MagicMock()
        mock_response.json.return_value = [original_mask.copy()]
        mock_get.return_value = mock_response

        resp_data = {
            'models': {
                'project1': {}
            }
        }

        retrieve_masks(resp_data, 'test_token')

        # Check that data in update matches original
        update_data = mock_masks_collection.update_one.call_args[0][1]['$set']
        assert update_data['maskName'] == original_mask['maskName']
        assert update_data['maskId'] == original_mask['maskId']
        assert update_data['important_data'] == original_mask['important_data']
