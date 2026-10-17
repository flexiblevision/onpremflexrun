"""/timezone sets the machine clock's zone for the device's timezone.

The zone reaches a root command, so the guard that matters is that anything
timedatectl does not list is refused before set-timezone ever runs.
"""
import pytest
from unittest.mock import patch, MagicMock, mock_open


@pytest.fixture
def client():
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    with patch('settings.config', {'latest_stable_ref': 'test_version', 'use_aws': False}):
        from routes import system_routes
        system_routes.register_routes(api)
    return app.test_client()


def _done(returncode=0, stdout='', stderr=''):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


LISTED = 'America/Chicago\nAmerica/Los_Angeles\nEurope/Berlin\n'


class TestSetTimezone:
    @pytest.mark.integration
    def test_an_unlisted_zone_is_refused_before_anything_runs(self, client):
        with patch('subprocess.run', return_value=_done(stdout=LISTED)) as run:
            res = client.post('/timezone', json={'timezone': 'Mars/Olympus; reboot'})
        assert res.status_code == 400
        assert [c.args[0] for c in run.call_args_list] == [['timedatectl', 'list-timezones']]

    @pytest.mark.integration
    def test_a_missing_zone_is_refused(self, client):
        with patch('subprocess.run') as run:
            res = client.post('/timezone', json={})
        assert res.status_code == 400
        run.assert_not_called()

    @pytest.mark.integration
    def test_a_listed_zone_is_set_and_etc_timezone_follows(self, client):
        calls = [_done(stdout=LISTED), _done()]
        m = mock_open(read_data='Europe/Berlin\n')
        with patch('subprocess.run', side_effect=calls) as run, patch('builtins.open', m):
            res = client.post('/timezone', json={'timezone': 'Europe/Berlin'})
        assert res.status_code == 200
        assert run.call_args_list[1].args[0] == ['timedatectl', 'set-timezone', 'Europe/Berlin']
        body = res.get_json()
        assert body['etcTimezone'] == 'Europe/Berlin' and body['containersFollowOnRecreate'] is True
        m().write.assert_called_with('Europe/Berlin\n')

    @pytest.mark.integration
    def test_a_failing_timedatectl_says_why(self, client):
        with patch('subprocess.run', side_effect=[_done(stdout=LISTED), _done(1, stderr='Access denied')]):
            res = client.post('/timezone', json={'timezone': 'Europe/Berlin'})
        assert res.status_code == 500 and res.get_json()['error'] == 'Access denied'
