"""Power, backend-restart and flex-run upgrade endpoints.

These are the endpoints that take a factory-floor machine off the line:
/shutdown and /restart cut power to it, /refresh_backend drops the camera
locks, /upgrade_flex_run replaces the code this very server is running from.
Each one is one HTTP call away from a caller who guessed the URL.
"""
import itertools

import pytest
from testsupport import thread_aware_sleep_mock
from unittest.mock import patch, MagicMock, call


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


@pytest.fixture
def no_sleep():
    """RestartBackend polls on a 5s interval; tests must not actually wait."""
    with patch('time.sleep', new=thread_aware_sleep_mock()) as sleep:
        yield sleep


@pytest.fixture(autouse=True)
def offline_channel():
    """No test reaches the release endpoint.

    /releases now asks the channel what it offers. Left alone that is a real
    request per call - slow here, and answered by whatever the CI runner's DNS
    resolves. Tests that care about the offer patch this with their own.
    """
    with patch('routes.system_routes._channel_offer',
               return_value=({'reachable': False, 'detail': 'offline'}, 'stable')) as offer:
        yield offer


def _completed(returncode=0, stdout='', stderr=''):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestShutdown:
    @pytest.mark.integration
    def test_get_powers_the_machine_off(self, client):
        with patch('os.system') as system:
            response = client.get('/shutdown')

        assert response.status_code == 200
        system.assert_called_once_with('poweroff')

    @pytest.mark.integration
    def test_is_not_reachable_by_post(self, client):
        with patch('os.system') as system:
            response = client.post('/shutdown')

        assert response.status_code == 405
        system.assert_not_called()


class TestRestart:
    @pytest.mark.integration
    def test_get_reboots_the_machine(self, client):
        with patch('os.system') as system:
            response = client.get('/restart')

        assert response.status_code == 200
        system.assert_called_once_with('reboot')

    @pytest.mark.integration
    def test_is_not_reachable_by_post(self, client):
        with patch('os.system') as system:
            response = client.post('/restart')

        assert response.status_code == 405
        system.assert_not_called()


def _resp(status=200, payload=None):
    r = MagicMock(status_code=status)
    r.json.return_value = [] if payload is None else payload
    return r


def _routed_get(cameras, release=None, capdev=None):
    """A requests.get double that answers by URL.

    Each of cameras/release/capdev is a response or an exception to raise;
    cameras may also be a list handed out in turn, the last one repeating.
    capdev defaults to healthy so a test can assert on the vision budget
    without the capdev readiness poll contributing sleeps of its own.
    """
    release = _resp(200) if release is None else release
    capdev = _resp(200) if capdev is None else capdev
    queue = list(cameras) if isinstance(cameras, list) else None

    def answer(value):
        if isinstance(value, Exception):
            raise value
        return value

    def get(url, **kw):
        if '/auth/jwks' in url:
            return answer(capdev)
        if url.endswith('/releaseAll'):
            return answer(release)
        if queue:
            return answer(queue.pop(0) if len(queue) > 1 else queue[0])
        return answer(cameras)

    return get


class TestRestartBackend:
    """capdev is stopped, cameras released, vision restarted, capdev started.

    capdev stays down for the whole vision restart on purpose. Its calls into
    vision carry no timeout, so a capdev left running while vision goes away
    hangs every gunicorn thread it has and never answers again.
    """

    @pytest.mark.integration
    def test_stops_capdev_for_the_whole_vision_restart(self, client, no_sleep):
        with patch('os.system', return_value=0) as system, \
             patch('requests.get', side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]))):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        assert system.call_args_list == [
            call('docker stop capdev'),
            call('docker restart vision'),
            call('docker start capdev'),
        ]
        assert response.get_json() == {'cameras_ready': True, 'capdev_ready': True}

    @pytest.mark.integration
    def test_releases_camera_locks_before_restarting_vision(self, client, no_sleep):
        with patch('os.system', return_value=0), \
             patch('requests.get',
                   side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]))) as get:
            client.get('/refresh_backend')

        assert get.call_args_list[0][0][0].endswith('/releaseAll')

    @pytest.mark.integration
    def test_starts_capdev_even_when_release_all_fails(self, client, no_sleep):
        # A vision container that is already down must not strand capdev in the
        # stopped state.
        with patch('os.system', return_value=0) as system, \
             patch('requests.get',
                   side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]),
                                           release=ConnectionError('vision is down'))):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        assert call('docker start capdev') in system.call_args_list

    @pytest.mark.integration
    def test_stops_polling_as_soon_as_cameras_appear(self, client, no_sleep):
        found = _resp(200, [{'id': 'cam0'}, {'id': 'cam1'}])

        with patch('os.system', return_value=0), \
             patch('requests.get',
                   side_effect=_routed_get([_resp(200), _resp(200), found])):
            client.get('/refresh_backend')

        # Three /cameras polls, then it stops rather than burning the full
        # 120s budget.
        assert no_sleep.call_count == 3

    @pytest.mark.integration
    def test_gives_up_after_the_timeout_and_starts_capdev_anyway(self, client, no_sleep):
        with patch('os.system', return_value=0) as system, \
             patch('requests.get', side_effect=_routed_get(_resp(200))):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        # 120s budget on a 5s interval.
        assert no_sleep.call_count == 24
        assert system.call_args_list[-1] == call('docker start capdev')
        assert response.get_json()['cameras_ready'] is False

    @pytest.mark.integration
    def test_a_slow_poll_is_charged_against_the_budget(self, client, no_sleep):
        # listCameras runs synchronously on the first /cameras call, so a poll
        # can block for its full 30s timeout. Charging only the 5s interval
        # made the 120s budget 24 * 35s of capdev downtime instead.
        clock = itertools.count(0, 30)

        with patch('os.system', return_value=0), \
             patch('time.monotonic', side_effect=lambda: next(clock)), \
             patch('requests.get', side_effect=_routed_get(_resp(200))):
            client.get('/refresh_backend')

        # 35s consumed per poll, so four of them exhaust the budget.
        assert no_sleep.call_count == 4

    @pytest.mark.integration
    def test_unreachable_vision_does_not_abort_the_restart(self, client, no_sleep):
        with patch('os.system', return_value=0) as system, \
             patch('requests.get',
                   side_effect=_routed_get(ConnectionError('refused'),
                                           release=ConnectionError('refused'))):
            response = client.get('/refresh_backend')

        assert response.status_code == 200
        assert call('docker start capdev') in system.call_args_list

    @pytest.mark.integration
    def test_non_200_from_cameras_keeps_polling(self, client, no_sleep):
        with patch('os.system', return_value=0), \
             patch('requests.get', side_effect=_routed_get(_resp(503))):
            client.get('/refresh_backend')

        assert no_sleep.call_count == 24

    @pytest.mark.integration
    def test_a_failed_start_is_retried(self, client, no_sleep):
        # capdev was stopped by hand, so unless-stopped will not bring it back
        # on its own - not in the background, and not across a reboot. An
        # unchecked start is what leaves a device dark.
        def system(cmd):
            return 1 if cmd == 'docker start capdev' else 0

        with patch('os.system', side_effect=system) as sysmock, \
             patch('requests.get', side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]))):
            response = client.get('/refresh_backend')

        assert sysmock.call_args_list.count(call('docker start capdev')) == 3
        assert response.get_json()['capdev_ready'] is False

    @pytest.mark.integration
    def test_reports_a_capdev_that_starts_but_never_answers(self, client, no_sleep):
        with patch('os.system', return_value=0), \
             patch('requests.get',
                   side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]),
                                           capdev=ConnectionError('refused'))):
            response = client.get('/refresh_backend')

        assert response.get_json() == {'cameras_ready': True, 'capdev_ready': False}

    @pytest.mark.integration
    def test_capdev_is_started_even_when_the_vision_phase_blows_up(self, client, no_sleep):
        def system(cmd):
            if cmd == 'docker restart vision':
                raise RuntimeError('docker daemon gone')
            return 0

        with patch('os.system', side_effect=system) as sysmock, \
             patch('requests.get', side_effect=_routed_get(_resp(200, [{'id': 'cam0'}]))):
            try:
                client.get('/refresh_backend')
            except RuntimeError:
                pass

        assert call('docker start capdev') in sysmock.call_args_list


class TestUpgradeFlexRun:
    """Replaces the flex-run checkout this server runs from."""

    @pytest.mark.integration
    def test_success_reports_updated(self, client):
        with patch('subprocess.run', return_value=_completed(0, 'pulled', '')), \
             patch('os.environ', {'HOME': '/home/visioncell'}):
            response = client.get('/upgrade_flex_run')

        assert response.status_code == 200
        assert response.get_json() == {'status': 'flex-run updated'}

    @pytest.mark.integration
    def test_makes_the_script_executable_before_running_it(self, client):
        with patch('subprocess.run', return_value=_completed(0)) as run, \
             patch('os.environ', {'HOME': '/home/visioncell'}):
            client.get('/upgrade_flex_run')

        chmod, script = run.call_args_list
        assert chmod[0][0] == ['chmod', '+x',
                               '/home/visioncell/flex-run/upgrades/upgrade_flex_run.sh']
        assert script[0][0] == ['sh',
                                '/home/visioncell/flex-run/upgrades/upgrade_flex_run.sh']

    @pytest.mark.integration
    def test_failure_returns_500_with_the_mapped_reason(self, client):
        import upgrade_runner

        with patch('subprocess.run', return_value=_completed(1, '', 'git failed')), \
             patch('os.environ', {'HOME': '/home/visioncell'}):
            response = client.get('/upgrade_flex_run')

        assert response.status_code == 500
        body = response.get_json()
        assert body['exit_code'] == 1
        assert body['error'] == upgrade_runner.flex_run_error(1)
        assert body['detail'] == 'git failed'

    @pytest.mark.integration
    def test_long_stderr_is_truncated_to_the_tail(self, client):
        with patch('subprocess.run', return_value=_completed(3, '', 'x' * 900)), \
             patch('os.environ', {'HOME': '/home/visioncell'}):
            response = client.get('/upgrade_flex_run')

        assert len(response.get_json()['detail']) == 500

    @pytest.mark.integration
    def test_missing_stderr_does_not_break_the_error_body(self, client):
        with patch('subprocess.run', return_value=_completed(2, '', None)), \
             patch('os.environ', {'HOME': '/home/visioncell'}):
            response = client.get('/upgrade_flex_run')

        assert response.status_code == 500
        assert response.get_json()['detail'] == ''


class TestRestartFO:
    @pytest.mark.integration
    def test_restarts_the_fo_server(self, client):
        with patch('os.system') as system:
            response = client.get('/restart_fo')

        # Not registered by register_routes - documented so that adding it
        # later is a deliberate act.
        assert response.status_code == 404
        system.assert_not_called()

    @pytest.mark.integration
    def test_returns_200_when_invoked_directly(self):
        from routes.system_routes import RestartFO

        with patch('os.system') as system:
            body, status = RestartFO().get()

        assert (body, status) == ('FO server restarted', 200)
        system.assert_called_once_with('forever restart /root/flex-run/aws/fo_server.py')

    @pytest.mark.integration
    def test_returns_500_when_forever_raises(self):
        from routes.system_routes import RestartFO

        with patch('os.system', side_effect=OSError('no forever')):
            body, status = RestartFO().get()

        assert status == 500
        assert body == 'Error restarting FO server'


class TestRouteRegistration:
    @pytest.mark.integration
    def test_every_documented_path_is_registered(self, client):
        expected = {'/shutdown', '/restart', '/refresh_backend', '/list_services',
                    '/upgrade', '/upgrade_status', '/upgrade_flex_run',
                    '/system_versions', '/system_uptodate', '/start_teamviewer'}

        from flask import current_app
        rules = {r.rule for r in client.application.url_map.iter_rules()}

        assert expected <= rules


@pytest.fixture
def installed_release():
    """A device sitting on release 1.3 (counter 7).

    The collection is faked rather than reached: state.read only calls
    find_one, and these assertions must not depend on whether the machine
    running them happens to have a mongo with release state in it.
    """
    collection = MagicMock()
    collection.find_one.return_value = {
        'installed': {'counter': 7, 'release': '1.3'},
        'high_water': 7,
        'history': [{'counter': 7, 'release': '1.3'}],
    }
    with patch('routes.system_routes._release_collection', return_value=collection):
        yield collection


class TestReleasesReportsTheOffer:
    """Which release this device would take, and from which channel.

    Without this the settings screen showed "Update available" from the legacy
    per-container check, which knows nothing about channels - so a stable
    device displayed the same badge whether or not anything was promoted to it,
    and nobody could tell what pressing the button would install.
    """

    @pytest.mark.integration
    def test_it_reports_the_offer_and_the_channel(
            self, client, offline_channel, installed_release):
        offline_channel.return_value = (
            {'reachable': True, 'counter': 8, 'release': '1.4',
             'newer_than_installed': True}, 'beta')

        body = client.get('/releases').get_json()

        assert body['channel'] == 'beta'
        assert body['available']['release'] == '1.4'
        assert body['update_available'] is True

    @pytest.mark.integration
    def test_a_channel_with_nothing_newer_is_not_an_update(
            self, client, offline_channel, installed_release):
        # The device is already on what beta offers. The old badge said
        # "Update available" here anyway.
        offline_channel.return_value = (
            {'reachable': True, 'counter': 7, 'release': '1.3',
             'newer_than_installed': False}, 'beta')

        body = client.get('/releases').get_json()

        assert body['update_available'] is False

    @pytest.mark.integration
    def test_an_unreachable_endpoint_renders_rather_than_failing(
            self, client, installed_release):
        # The autouse fixture is already the offline case: a device on a
        # factory network is offline more often than not, and the settings
        # screen still has to draw.
        body = client.get('/releases').get_json()

        assert body['available']['reachable'] is False
        assert body['update_available'] is False
        assert 'high_water' in body

    @pytest.mark.integration
    def test_the_offer_is_asked_for_against_the_device_high_water(
            self, client, offline_channel, installed_release):
        # Not the installed counter: a device that rolled back must not be
        # offered the release it deliberately left.
        client.get('/releases')

        assert offline_channel.call_args[0][0] == 7

    @pytest.mark.integration
    def test_a_failure_resolving_the_channel_does_not_break_the_payload(self, client):
        with patch('routes.system_routes._channel_offer',
                   side_effect=RuntimeError('no upgrade_runner')):
            body = client.get('/releases').get_json()

        assert body['update_available'] is False
        assert body['channel'] is None
        assert 'unavailable' in body


class TestReleaseChannel:
    """Reading and moving the channel this device follows.

    The write is gated in cloud_env, not here: mongo on 172.17.0.1 takes no
    credentials, so a customer device must refuse to be walked onto beta.
    These pin that the route reports the refusal instead of swallowing it,
    because the screen disables the control on the strength of it.
    """

    @pytest.mark.integration
    def test_it_reports_the_channel_and_whether_it_can_move(self, client):
        with patch('upgrade_runner._device_channel', return_value='beta'), \
             patch('cloud_env.release_override_allowed', return_value=True):
            body = client.get('/release_channel').get_json()

        assert body['channel'] == 'beta'
        assert body['changeable'] is True
        assert 'stable' in body['choices'] and 'beta' in body['choices']

    @pytest.mark.integration
    def test_a_customer_device_reports_that_it_cannot_move(self, client):
        with patch('upgrade_runner._device_channel', return_value='stable'), \
             patch('cloud_env.release_override_allowed', return_value=False):
            body = client.get('/release_channel').get_json()

        assert body['channel'] == 'stable'
        assert body['changeable'] is False

    @pytest.mark.integration
    def test_a_put_moves_the_channel(self, client):
        import cloud_env
        with patch('cloud_env.set_override') as write, \
             patch('cloud_env.get_cloud_domain',
                   return_value=cloud_env.CLOUD_DOMAINS['prod']), \
             patch('upgrade_runner._device_channel', return_value='beta'):
            response = client.put('/release_channel', json={'channel': 'beta'})

        assert response.status_code == 200
        assert write.call_args[0][0]['release_channel'] == 'beta'
        assert response.get_json()['channel'] == 'beta'

    @pytest.mark.integration
    def test_a_refused_device_gets_403_and_the_reason(self, client):
        import cloud_env
        with patch('cloud_env.set_override',
                   side_effect=cloud_env.CloudEnvError('on the prod release track')):
            response = client.put('/release_channel', json={'channel': 'beta'})

        assert response.status_code == 403
        assert 'prod release track' in response.get_json()['error']

    @pytest.mark.integration
    @pytest.mark.parametrize('channel', ['nightly', '', None, 'STABLE'])
    def test_an_unknown_channel_is_refused_without_writing(self, client, channel):
        with patch('cloud_env.set_override') as write:
            response = client.put('/release_channel', json={'channel': channel})

        assert response.status_code == 400
        write.assert_not_called()

    @pytest.mark.integration
    def test_beta_may_point_at_either_cloud(self, client):
        import cloud_env
        with patch('cloud_env.set_override') as write, \
             patch('upgrade_runner._device_channel', return_value='beta'):
            client.put('/release_channel', json={'channel': 'beta', 'cloud': 'dev'})

        assert write.call_args[0][0] == {
            'release_channel': 'beta',
            'cloud_domain': cloud_env.CLOUD_DOMAINS['dev'],
        }

    @pytest.mark.integration
    def test_stable_is_forced_back_to_prod(self, client):
        """Even when the caller asks for dev.

        A device taking fleet releases must not read its projects and models
        from the cloud those releases are tested against, so the rule is
        enforced here rather than only hidden in the UI.
        """
        import cloud_env
        with patch('cloud_env.set_override') as write, \
             patch('upgrade_runner._device_channel', return_value='stable'):
            client.put('/release_channel', json={'channel': 'stable', 'cloud': 'dev'})

        assert write.call_args[0][0] == {
            'release_channel': 'stable',
            'cloud_domain': cloud_env.CLOUD_DOMAINS['prod'],
        }

    @pytest.mark.integration
    def test_an_unknown_cloud_is_refused_without_writing(self, client):
        with patch('cloud_env.set_override') as write:
            response = client.put(
                '/release_channel', json={'channel': 'beta', 'cloud': 'staging'})

        assert response.status_code == 400
        write.assert_not_called()

    @pytest.mark.integration
    def test_beta_with_no_cloud_named_keeps_the_one_it_is_on(self, client):
        import cloud_env
        with patch('cloud_env.set_override') as write, \
             patch('cloud_env.get_cloud_domain',
                   return_value=cloud_env.CLOUD_DOMAINS['dev']), \
             patch('upgrade_runner._device_channel', return_value='beta'):
            client.put('/release_channel', json={'channel': 'beta'})

        assert write.call_args[0][0]['cloud_domain'] == cloud_env.CLOUD_DOMAINS['dev']

    @pytest.mark.integration
    def test_a_bespoke_cloud_is_left_alone_rather_than_relabelled(self, client):
        # A site on its own cloud is not dev or prod. Switching channel must
        # not silently move its data plane onto one of ours.
        with patch('cloud_env.set_override') as write, \
             patch('cloud_env.get_cloud_domain', return_value='https://cloud.acme.internal'), \
             patch('upgrade_runner._device_channel', return_value='beta'):
            client.put('/release_channel', json={'channel': 'beta'})

        assert 'cloud_domain' not in write.call_args[0][0]

    @pytest.mark.integration
    def test_the_reported_cloud_is_a_name_not_a_url(self, client):
        import cloud_env
        with patch('cloud_env.get_cloud_domain',
                   return_value=cloud_env.CLOUD_DOMAINS['dev']), \
             patch('upgrade_runner._device_channel', return_value='beta'):
            body = client.get('/release_channel').get_json()

        assert body['cloud'] == 'dev'
        assert sorted(body['cloud_choices']) == ['dev', 'prod']


class TestReleasesReportsTrust:
    """A rotation cannot be finished safely unless you can see which devices
    have picked up the new key, so /releases has to report it."""

    def _keypair(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        return key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)

    def test_it_lists_the_trusted_key_fingerprints(self, client, tmp_path, monkeypatch):
        from release import trust
        store = tmp_path / 'keys'
        pems = [self._keypair() for _ in range(2)]
        for index, pem in enumerate(pems):
            trust.provision(str(store), 'release-{}.pem'.format(index), pem)
        monkeypatch.setenv('FLEXRUN_TRUST_DIR', str(store))

        body = client.get('/releases').get_json()
        assert body['trust']['count'] == 2
        reported = {k['fingerprint'] for k in body['trust']['keys']}
        assert reported == {trust.fingerprint(pem) for pem in pems}

    def test_a_device_with_no_trust_store_reports_zero_not_an_error(
            self, client, tmp_path, monkeypatch):
        """Every device is in this state until provisioning happens, and the
        settings screen still has to render."""
        monkeypatch.setenv('FLEXRUN_TRUST_DIR', str(tmp_path / 'absent'))
        body = client.get('/releases').get_json()
        assert body['trust']['count'] == 0
        assert body['trust']['keys'] == []

    def test_trust_failure_does_not_break_the_rest_of_the_payload(
            self, client, tmp_path, monkeypatch):
        monkeypatch.setenv('FLEXRUN_TRUST_DIR', str(tmp_path / 'absent'))
        body = client.get('/releases').get_json()
        assert 'high_water' in body
        assert 'rollback_targets' in body
