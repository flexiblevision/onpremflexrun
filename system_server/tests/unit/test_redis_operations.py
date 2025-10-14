"""
Unit tests demonstrating fakeredis usage

These tests show how to use fakeredis for testing Redis operations
without requiring an actual Redis server.
"""
import pytest
from unittest.mock import patch


class TestFakeRedisBasicOperations:
    """Tests for basic Redis operations using fakeredis"""

    @pytest.mark.unit
    def test_redis_set_and_get(self, mock_redis):
        """Test basic set and get operations"""
        # Set a value
        mock_redis.set('test_key', 'test_value')

        # Get the value back
        result = mock_redis.get('test_key')

        assert result == b'test_value'

    @pytest.mark.unit
    def test_redis_delete(self, mock_redis):
        """Test delete operation"""
        # Set a value
        mock_redis.set('key_to_delete', 'value')

        # Delete it
        mock_redis.delete('key_to_delete')

        # Verify it's gone
        result = mock_redis.get('key_to_delete')
        assert result is None

    @pytest.mark.unit
    def test_redis_exists(self, mock_redis):
        """Test exists check"""
        mock_redis.set('existing_key', 'value')

        assert mock_redis.exists('existing_key') == 1
        assert mock_redis.exists('non_existing_key') == 0

    @pytest.mark.unit
    def test_redis_expire(self, mock_redis):
        """Test key expiration"""
        mock_redis.set('expiring_key', 'value')
        mock_redis.expire('expiring_key', 60)

        ttl = mock_redis.ttl('expiring_key')
        assert ttl > 0 and ttl <= 60


class TestFakeRedisListOperations:
    """Tests for Redis list operations"""

    @pytest.mark.unit
    def test_redis_list_push_pop(self, mock_redis):
        """Test list push and pop operations"""
        # Push items to list
        mock_redis.lpush('test_list', 'item1')
        mock_redis.lpush('test_list', 'item2')
        mock_redis.rpush('test_list', 'item3')

        # Check list length
        assert mock_redis.llen('test_list') == 3

        # Pop items
        item = mock_redis.lpop('test_list')
        assert item == b'item2'

    @pytest.mark.unit
    def test_redis_list_range(self, mock_redis):
        """Test list range operation"""
        mock_redis.rpush('range_list', 'a', 'b', 'c', 'd', 'e')

        # Get range
        items = mock_redis.lrange('range_list', 0, 2)
        assert items == [b'a', b'b', b'c']


class TestFakeRedisHashOperations:
    """Tests for Redis hash operations"""

    @pytest.mark.unit
    def test_redis_hash_set_get(self, mock_redis):
        """Test hash set and get operations"""
        # Set hash fields
        mock_redis.hset('user:1', 'name', 'John')
        mock_redis.hset('user:1', 'email', 'john@example.com')

        # Get hash field
        name = mock_redis.hget('user:1', 'name')
        assert name == b'John'

        # Get all hash fields
        all_data = mock_redis.hgetall('user:1')
        assert all_data == {b'name': b'John', b'email': b'john@example.com'}

    @pytest.mark.unit
    def test_redis_hash_multiple_set(self, mock_redis):
        """Test setting multiple hash fields at once"""
        data = {
            'field1': 'value1',
            'field2': 'value2',
            'field3': 'value3'
        }

        mock_redis.hmset('test_hash', data)

        # Verify all fields
        result = mock_redis.hgetall('test_hash')
        assert len(result) == 3


class TestFakeRedisSetOperations:
    """Tests for Redis set operations"""

    @pytest.mark.unit
    def test_redis_set_add_members(self, mock_redis):
        """Test adding members to a set"""
        mock_redis.sadd('test_set', 'member1', 'member2', 'member3')

        # Check set size
        assert mock_redis.scard('test_set') == 3

        # Check membership
        assert mock_redis.sismember('test_set', 'member1') == 1
        assert mock_redis.sismember('test_set', 'nonexistent') == 0

    @pytest.mark.unit
    def test_redis_set_operations(self, mock_redis):
        """Test set operations like union, intersection"""
        mock_redis.sadd('set1', 'a', 'b', 'c')
        mock_redis.sadd('set2', 'b', 'c', 'd')

        # Union
        union = mock_redis.sunion('set1', 'set2')
        assert len(union) == 4

        # Intersection
        intersection = mock_redis.sinter('set1', 'set2')
        assert intersection == {b'b', b'c'}


class TestFakeRedisWithPreloadedData:
    """Tests using the pre-loaded fake redis fixture"""

    @pytest.mark.unit
    def test_preloaded_data(self, fake_redis_with_data):
        """Test with pre-populated Redis data"""
        # Data was pre-populated in the fixture
        assert fake_redis_with_data.get('test_key') == b'test_value'
        assert fake_redis_with_data.hget('test_hash', 'field1') == b'value1'
        assert fake_redis_with_data.llen('test_list') == 2


class TestFakeRedisServer:
    """Tests using the fake Redis server fixture"""

    @pytest.mark.unit
    def test_shared_server_instance(self, fake_redis_server):
        """Test shared server instance across multiple connections"""
        server = fake_redis_server['server']
        redis1 = fake_redis_server['redis']

        # Create another connection to the same server
        import fakeredis
        redis2 = fakeredis.FakeStrictRedis(server=server)

        # Set data in first connection
        redis1.set('shared_key', 'shared_value')

        # Read from second connection
        assert redis2.get('shared_key') == b'shared_value'


class TestJobQueueWithFakeRedis:
    """Tests for job queue operations with fake Redis"""

    @pytest.mark.unit
    def test_job_queue_enqueue(self, mock_job_queue):
        """Test enqueueing a job"""
        job = mock_job_queue.enqueue(lambda: 'test')

        assert job.id == 'test_job_id'

    @pytest.mark.unit
    def test_job_queue_with_redis_backend(self, mock_job_queue, mock_redis):
        """Test job queue using fake Redis backend"""
        # The job queue has access to fake Redis
        assert mock_job_queue.connection == mock_redis

        # Enqueue a job
        job = mock_job_queue.enqueue(lambda: 'test')

        # Verify job was created
        assert job is not None
        assert hasattr(job, 'id')


class TestRedisIntegrationScenarios:
    """Integration test scenarios using fakeredis"""

    @pytest.mark.integration
    def test_cache_pattern(self, mock_redis):
        """Test common caching pattern"""
        cache_key = 'user:123:profile'

        # Check if cached
        cached_data = mock_redis.get(cache_key)
        if cached_data is None:
            # Simulate fetching from database
            data = {'name': 'John', 'age': 30}

            # Cache the data
            import json
            mock_redis.setex(cache_key, 3600, json.dumps(data))

        # Retrieve from cache
        cached_data = mock_redis.get(cache_key)
        assert cached_data is not None

        import json
        retrieved_data = json.loads(cached_data)
        assert retrieved_data['name'] == 'John'

    @pytest.mark.integration
    def test_counter_pattern(self, mock_redis):
        """Test counter pattern"""
        counter_key = 'page:views:count'

        # Increment counter
        mock_redis.incr(counter_key)
        mock_redis.incr(counter_key)
        mock_redis.incr(counter_key)

        # Get count
        count = mock_redis.get(counter_key)
        assert int(count) == 3

    @pytest.mark.integration
    def test_queue_pattern(self, mock_redis):
        """Test queue pattern using lists"""
        queue_key = 'task:queue'

        # Add tasks to queue
        mock_redis.rpush(queue_key, 'task1')
        mock_redis.rpush(queue_key, 'task2')
        mock_redis.rpush(queue_key, 'task3')

        # Process tasks from queue
        task = mock_redis.lpop(queue_key)
        assert task == b'task1'

        # Check remaining tasks
        remaining = mock_redis.llen(queue_key)
        assert remaining == 2
