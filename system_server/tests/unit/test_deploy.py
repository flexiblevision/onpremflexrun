"""First-run device setup: the interactive installer run on a bare machine.

This is the one path a technician drives by hand on the floor, and it is the
one path with no retry: a step that reports success without having done
anything leaves a half-installed device behind.
"""
import os
import subprocess
import sys
import pytest
from testsupport import thread_aware_sleep_mock
from unittest.mock import patch, MagicMock, call, mock_open

import deploy


class TestClearTextColor:
    @pytest.mark.unit
    def test_emits_the_ansi_reset(self, capsys):
        deploy.clear_text_color()
        assert '\033[0m' in capsys.readouterr().out


class TestNetworkConfig:
    """Both readers used to open an undefined `path_ref` inside a bare except,
    so the default was always returned and the file was never read - and both
    read the same path, so the interface name would have been an IP."""

    @pytest.mark.unit
    def test_the_defaults_are_used_when_there_is_no_file(self, monkeypatch):
        monkeypatch.setattr(deploy, 'NET_CONFIG', '/nonexistent/flexrun/network')
        assert deploy._net_config() == ('enp0s31f6', '192.168.10.35')

    @pytest.mark.unit
    def test_the_file_is_read_now(self, tmp_path, monkeypatch):
        target = tmp_path / 'network'
        target.write_text('eth0\n10.0.0.9\n')
        monkeypatch.setattr(deploy, 'NET_CONFIG', str(target))
        assert deploy._net_config() == ('eth0', '10.0.0.9')

    @pytest.mark.unit
    def test_interface_and_address_are_separate_values(self, tmp_path, monkeypatch):
        """The old pair returned the same file contents for both."""
        target = tmp_path / 'network'
        target.write_text('eth0\n10.0.0.9\n')
        monkeypatch.setattr(deploy, 'NET_CONFIG', str(target))
        interface, address = deploy._net_config()
        assert interface != address

    @pytest.mark.unit
    def test_a_half_written_file_falls_back_for_the_missing_half(self, tmp_path,
                                                                 monkeypatch):
        target = tmp_path / 'network'
        target.write_text('eth0\n')
        monkeypatch.setattr(deploy, 'NET_CONFIG', str(target))
        assert deploy._net_config() == ('eth0', '192.168.10.35')


class TestSetStaticIp:
    @pytest.mark.unit
    def test_configures_the_interface_and_writes_netplan(self, tmp_path):
        opener = mock_open()
        with patch('os.system') as system, \
             patch('builtins.open', opener), \
             patch.object(deploy, '_net_config', return_value=('eth0', '192.168.10.35')):
            deploy.set_static_ip()

        assert system.call_args_list == [
            call('sudo ifconfig eth0 192.168.10.35 netmask 255.255.255.0'),
            call('sudo netplan apply'),
        ]
        opener.assert_called_once_with('/etc/netplan/fv-net-init.yaml', 'w')

    @pytest.mark.unit
    def test_netplan_yaml_names_the_interface_and_address(self):
        opener = mock_open()
        with patch('os.system'), patch('builtins.open', opener), \
             patch.object(deploy, '_net_config', return_value=('eth0', '192.168.10.35')):
            deploy.set_static_ip()

        written = ''.join(c.args[0] for c in opener().write.call_args_list)
        assert 'renderer: NetworkManager' in written
        assert '    eth0:' in written
        assert 'dhcp4: false' in written
        assert 'mtu: 9000' in written
        assert 'addresses: [192.168.10.35/24]' in written


class TestContainerState:
    """`docker inspect` on a container that does not exist writes nothing and
    exits non-zero. json.loads('') on that used to raise inside step_3, so the
    branch that reports a failed install was itself what crashed - which is
    exactly what happened on a real device."""

    @pytest.mark.unit
    def test_a_missing_container_is_none_not_an_exception(self):
        with patch('subprocess.run',
                   return_value=MagicMock(returncode=1, stdout='\n')):
            assert deploy.container_state('capdev') is None

    @pytest.mark.unit
    def test_a_running_container_is_true(self):
        with patch('subprocess.run',
                   return_value=MagicMock(returncode=0, stdout='true\n')):
            assert deploy.container_state('capdev') is True

    @pytest.mark.unit
    def test_a_stopped_container_is_false(self):
        with patch('subprocess.run',
                   return_value=MagicMock(returncode=0, stdout='false\n')):
            assert deploy.container_state('capdev') is False

    @pytest.mark.unit
    def test_docker_missing_entirely_is_none(self):
        with patch('subprocess.run', side_effect=OSError('no docker')):
            assert deploy.container_state('capdev') is None

    @pytest.mark.unit
    def test_unexpected_output_is_not_read_as_running(self):
        with patch('subprocess.run',
                   return_value=MagicMock(returncode=0, stdout='maybe')):
            assert deploy.container_state('capdev') is not True


class TestContainersRunning:

    @pytest.mark.unit
    def test_all_running_is_true(self):
        with patch.object(deploy, 'container_state', return_value=True):
            assert deploy.containers_running() is True

    @pytest.mark.unit
    def test_one_stopped_container_is_false(self):
        with patch.object(deploy, 'container_state',
                          side_effect=lambda n: n != 'vision'):
            assert deploy.containers_running() is False

    @pytest.mark.unit
    def test_it_names_what_is_broken(self):
        with patch.object(deploy, 'container_state',
                          side_effect=lambda n: n != 'vision'):
            assert deploy.not_running() == ['vision']

    @pytest.mark.unit
    def test_it_covers_everything_system_setup_verifies(self):
        """The old list was capdev, localprediction and captureui only, so
        setup could report success with vision, nodecreator and predictlite
        all dead."""
        expected = set(deploy.EXPECTED_CONTAINERS)
        assert {'vision', 'nodecreator', 'predictlite', 'mongo'} <= expected


class TestQueryYesNo:
    @pytest.mark.unit
    @pytest.mark.parametrize('answer', ['yes', 'y', 'ye', 'YES', 'Y'])
    def test_affirmative_answers(self, answer):
        with patch('builtins.input', return_value=answer):
            assert deploy.query_yes_no('go?') is True

    @pytest.mark.unit
    @pytest.mark.parametrize('answer', ['no', 'n', 'NO', 'N'])
    def test_negative_answers(self, answer):
        with patch('builtins.input', return_value=answer):
            assert deploy.query_yes_no('go?') is False

    @pytest.mark.unit
    def test_empty_input_takes_the_default(self):
        with patch('builtins.input', return_value=''):
            assert deploy.query_yes_no('go?', default='no') is False
            assert deploy.query_yes_no('go?', default='yes') is True

    @pytest.mark.unit
    def test_without_a_default_an_answer_is_required(self):
        with patch('builtins.input', side_effect=['', 'y']):
            assert deploy.query_yes_no('go?', default=None) is True

    @pytest.mark.unit
    def test_reprompts_until_the_answer_is_understood(self, capsys):
        with patch('builtins.input', side_effect=['maybe', 'later', 'y']):
            assert deploy.query_yes_no('go?') is True
        assert capsys.readouterr().out.count("Please respond with 'yes' or 'no'") == 2

    @pytest.mark.unit
    @pytest.mark.parametrize('default,prompt', [
        (None, ' [y/n] '), ('yes', ' [Y/n] '), ('no', ' [y/N] '),
    ])
    def test_prompt_shows_the_default(self, default, prompt, capsys):
        with patch('builtins.input', return_value='y'):
            deploy.query_yes_no('go?', default=default)
        assert 'go?' + prompt in capsys.readouterr().out

    @pytest.mark.unit
    def test_an_invalid_default_is_a_programming_error(self):
        with pytest.raises(ValueError):
            deploy.query_yes_no('go?', default='perhaps')


class TestChooseReleaseTrack:
    """Which cloud, and which release channel the device then follows.

    prod is the answer to a bare Enter and to option 1. A device that lands on
    dev by accident takes beta releases before the fleet does, and on a factory
    floor that is discovered by the line going down - so the default has to be
    the safe one, not the one being tested this week.
    """

    @pytest.mark.unit
    def test_option_one_is_prod(self):
        with patch('builtins.input', return_value='1'):
            assert deploy.choose_release_track() == 'prod'

    @pytest.mark.unit
    def test_a_bare_enter_is_prod(self):
        with patch('builtins.input', return_value=''):
            assert deploy.choose_release_track() == 'prod'

    @pytest.mark.unit
    def test_whitespace_is_a_bare_enter(self):
        with patch('builtins.input', return_value='  '):
            assert deploy.choose_release_track() == 'prod'

    @pytest.mark.unit
    def test_option_two_is_dev(self):
        with patch('builtins.input', return_value='2'):
            assert deploy.choose_release_track() == 'dev'

    @pytest.mark.unit
    def test_dev_says_what_it_means(self, capsys):
        with patch('builtins.input', return_value='2'):
            deploy.choose_release_track()

        assert 'beta' in capsys.readouterr().out

    @pytest.mark.unit
    def test_reprompts_on_anything_else(self, capsys):
        with patch('builtins.input', side_effect=['9', 'dev', '2']):
            assert deploy.choose_release_track() == 'dev'

        assert capsys.readouterr().out.count("Please respond with '1' or '2'") == 2


class TestChooseEnvironment:
    @pytest.mark.unit
    def test_option_one_is_the_cloud(self):
        with patch('builtins.input', side_effect=['1', '1']), \
             patch.object(deploy, 'generate_environment_config') as generate:
            deploy.choose_environment()

        generate.assert_called_once_with('cloud', True, release_track='prod')

    @pytest.mark.unit
    def test_option_two_is_a_local_cluster(self):
        with patch('builtins.input', side_effect=['2', '1']), \
             patch.object(deploy, 'generate_environment_config') as generate:
            deploy.choose_environment()

        generate.assert_called_once_with('local', True, release_track='prod')

    @pytest.mark.unit
    def test_the_track_reaches_the_config(self):
        with patch('builtins.input', side_effect=['1', '2']), \
             patch.object(deploy, 'generate_environment_config') as generate:
            assert deploy.choose_environment() == ('cloud', 'dev')

        generate.assert_called_once_with('cloud', True, release_track='dev')

    @pytest.mark.unit
    def test_the_environment_and_the_track_are_independent(self):
        """A local cluster on the dev track is a real combination - the two
        questions must not collapse into one."""
        with patch('builtins.input', side_effect=['2', '2']), \
             patch.object(deploy, 'generate_environment_config') as generate:
            assert deploy.choose_environment() == ('local', 'dev')

        generate.assert_called_once_with('local', True, release_track='dev')

    @pytest.mark.unit
    def test_the_config_is_written_once(self):
        with patch('builtins.input', side_effect=['1', '9', '2']), \
             patch.object(deploy, 'generate_environment_config') as generate:
            deploy.choose_environment()

        assert generate.call_count == 1

    @pytest.mark.unit
    def test_reprompts_on_anything_else(self, capsys):
        with patch('builtins.input', side_effect=['3', 'x', '1', '1']), \
             patch.object(deploy, 'generate_environment_config'):
            deploy.choose_environment()

        assert capsys.readouterr().out.count("Please respond with '1' or '2'") == 2


class TestCheckConnection:
    """Isolated factory networks are the normal case. Pinging google.com told a
    device with a working route to the registry that it had no network, and
    stopped setup."""

    @pytest.mark.unit
    def test_a_reachable_target_is_online(self):
        with patch('subprocess.run', return_value=MagicMock(returncode=0)):
            assert deploy.check_connection() is True

    @pytest.mark.unit
    def test_an_http_error_still_counts_as_reachable(self):
        """The registry answers 401 without credentials - curl exit 22. That
        is a reachable registry, not a broken network."""
        with patch('subprocess.run', return_value=MagicMock(returncode=22)):
            assert deploy.check_connection() is True

    @pytest.mark.unit
    def test_nothing_reachable_is_offline(self):
        with patch('subprocess.run', return_value=MagicMock(returncode=6)):
            assert deploy.check_connection() is False

    @pytest.mark.unit
    def test_it_checks_what_setup_actually_needs(self):
        targets = ' '.join(deploy.REACHABILITY_TARGETS)
        assert 'registry-1.docker.io' in targets
        assert 'functions-proxy.flexiblevision.com' in targets
        assert 'google.com' not in targets


class TestDisplayConnectionResults:
    @pytest.mark.unit
    def test_reports_connected(self, capsys):
        with patch.object(deploy, 'check_connection', return_value=True):
            deploy.display_connection_results()
        assert 'Internet connected.' in capsys.readouterr().out

    @pytest.mark.unit
    def test_reports_not_connected(self, capsys):
        with patch.object(deploy, 'check_connection', return_value=False):
            deploy.display_connection_results()
        assert 'Internet not connected.' in capsys.readouterr().out


class TestConnectWifi:
    @pytest.mark.unit
    def test_invokes_nmcli_with_the_credentials(self):
        with patch('subprocess.run',
                   return_value=MagicMock(returncode=0, stdout='', stderr='')) as run, \
             patch('time.sleep', new=thread_aware_sleep_mock()):
            deploy.connect_wifi('shopfloor', 'hunter2')

        run.assert_called_once()
        assert run.call_args[0][0] == ['nmcli', 'dev', 'wifi', 'connect',
                                       'shopfloor', 'password', 'hunter2']

    @pytest.mark.unit
    def test_the_password_never_reaches_a_shell(self):
        """os.system with a concatenated password: a space broke the command,
        and ; or $(...) executed."""
        with patch('subprocess.run',
                   return_value=MagicMock(returncode=0, stdout='', stderr='')) as run, \
             patch('time.sleep', new=thread_aware_sleep_mock()):
            deploy.connect_wifi('my network', 'p; rm -rf /')

        argv = run.call_args[0][0]
        assert isinstance(argv, list)
        assert 'my network' in argv and 'p; rm -rf /' in argv

    @pytest.mark.unit
    def test_a_failure_is_reported_not_silent(self, capsys):
        with patch('subprocess.run',
                   return_value=MagicMock(returncode=10,
                                          stdout='Secrets were required',
                                          stderr='')), \
             patch('time.sleep', new=thread_aware_sleep_mock()):
            deploy.connect_wifi('shopfloor', 'hunter2')
        assert 'Could not connect' in capsys.readouterr().out

    @pytest.mark.unit
    def test_settles_before_returning(self):
        with patch('subprocess.run',
                   return_value=MagicMock(returncode=0, stdout='', stderr='')), \
             patch('time.sleep', new=thread_aware_sleep_mock()) as sleep:
            deploy.connect_wifi('shopfloor', 'hunter2')
        sleep.assert_called_once_with(3)


class TestRetryPrompt:
    @pytest.mark.unit
    def test_the_first_attempt_is_never_prompted_for(self):
        with patch.object(deploy, 'query_yes_no') as ask:
            assert deploy.retry_prompt(0) is True
        ask.assert_not_called()

    @pytest.mark.unit
    def test_later_attempts_ask_the_operator(self):
        with patch.object(deploy, 'query_yes_no', return_value=False) as ask:
            assert deploy.retry_prompt(1) is False
        ask.assert_called_once_with('Retry setup?', default='yes')


class TestSetupWifi:
    @pytest.mark.unit
    def test_an_already_online_machine_is_left_alone(self):
        with patch('os.system') as system, patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'check_connection', return_value=True), \
             patch('builtins.input') as prompt:
            deploy.setup_wifi()

        assert system.call_args_list == [call('nmcli radio wifi on'),
                                          call('nmcli d wifi list')]
        prompt.assert_not_called()

    @pytest.mark.unit
    def test_prompts_for_credentials_and_connects(self):
        with patch('os.system'), patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'check_connection', side_effect=[False, True]), \
             patch('builtins.input', return_value='shopfloor'), \
             patch('getpass.getpass', return_value='hunter2'), \
             patch.object(deploy, 'connect_wifi') as connect, \
             patch.object(deploy, 'display_connection_results'):
            deploy.setup_wifi()

        connect.assert_called_once_with('shopfloor', 'hunter2')

    @pytest.mark.unit
    def test_retries_until_the_operator_declines(self):
        with patch('os.system'), patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'check_connection', return_value=False), \
             patch.object(deploy, 'retry_prompt', side_effect=[True, True, False]), \
             patch('builtins.input', side_effect=['a', 'b', 'c', 'd']), \
             patch('getpass.getpass', side_effect=['w', 'x', 'y', 'z']), \
             patch.object(deploy, 'connect_wifi') as connect, \
             patch.object(deploy, 'display_connection_results'):
            deploy.setup_wifi()

        assert connect.call_count == 2

    @pytest.mark.unit
    def test_always_reports_the_final_state(self):
        with patch('os.system'), patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'check_connection', return_value=True), \
             patch.object(deploy, 'display_connection_results') as report:
            deploy.setup_wifi()
        report.assert_called_once()


class TestStep1:
    @pytest.mark.unit
    def test_online_machine_skips_wifi_setup(self, capsys):
        with patch.object(deploy, 'choose_environment'), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'check_connection', return_value=True), \
             patch.object(deploy, 'setup_wifi') as wifi:
            deploy.step_1()

        wifi.assert_not_called()
        assert 'Online.' in capsys.readouterr().out

    @pytest.mark.unit
    def test_offline_machine_runs_wifi_setup(self):
        with patch.object(deploy, 'choose_environment'), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'check_connection', return_value=False), \
             patch.object(deploy, 'setup_wifi') as wifi:
            deploy.step_1()

        wifi.assert_called_once()

    @pytest.mark.unit
    def test_the_environment_is_chosen_first(self):
        order = []
        with patch.object(deploy, 'choose_environment', side_effect=lambda: order.append('env')), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'check_connection',
                          side_effect=lambda: order.append('check') or True), \
             patch.object(deploy, 'setup_wifi'):
            deploy.step_1()

        assert order[0] == 'env'


class TestStep2:
    @pytest.mark.unit
    def test_passes_every_resolved_version_to_the_setup_script(self):
        with patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch('system_server.version_check.is_container_uptodate', return_value=(False, '1.2.3')), \
             patch('subprocess.call') as call_script:
            deploy.step_2()

        args = call_script.call_args[0][0]
        assert args[:3] == ['sh', './scripts/local_setup.sh', '1.2.3']
        # Seven containers are installed by the first-run script.
        assert len(args) == 9

    @pytest.mark.unit
    def test_resolves_each_container_by_name(self):
        with patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch('system_server.version_check.is_container_uptodate', return_value=(False, '1.0')) as uptodate, \
             patch('subprocess.call'):
            deploy.step_2()

        assert [c[0][0] for c in uptodate.call_args_list] == [
            'backend', 'frontend', 'prediction', 'predictlite',
            'vision', 'nodecreator', 'visiontools']

    @pytest.mark.unit
    def test_an_uptodate_container_passes_the_true_sentinel(self):
        # is_container_uptodate returns 'True' rather than a tag when nothing
        # needs pulling; the shell script reads that as "leave it alone".
        with patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch('system_server.version_check.is_container_uptodate', return_value=(True, 'True')), \
             patch('subprocess.call') as call_script:
            deploy.step_2()

        assert call_script.call_args[0][0][2:] == ['True'] * 7


class TestStep3:
    @pytest.mark.unit
    def test_running_containers_report_the_launch_url(self, capsys):
        with patch.object(deploy, 'container_state', return_value=True):
            assert deploy.step_3() == 0
        assert 'Launch - http://<host ip>' in capsys.readouterr().out

    @pytest.mark.unit
    def test_stopped_containers_tell_the_operator_to_retry(self, capsys):
        with patch.object(deploy, 'container_state', return_value=False):
            assert deploy.step_3() == 24

        out = capsys.readouterr().out
        assert 'Step 2 did not complete' in out
        assert 'Launch' not in out

    @pytest.mark.unit
    def test_it_names_the_containers_that_are_down(self, capsys):
        """"Please retry setup" alone gives a technician on the floor nothing
        to act on."""
        with patch.object(deploy, 'container_state',
                          side_effect=lambda n: n != 'vision'):
            deploy.step_3()
        out = capsys.readouterr().out
        assert 'vision' in out

    @pytest.mark.unit
    def test_a_missing_container_does_not_crash(self, capsys):
        """The regression hit on a real device: 'No such object: capdev'
        followed by a JSONDecodeError traceback out of this very branch."""
        with patch.object(deploy, 'container_state', return_value=None):
            assert deploy.step_3() == 24
        assert 'not created' in capsys.readouterr().out


class TestMain:
    @pytest.mark.unit
    def test_runs_all_three_steps_on_linux(self):
        with patch('platform.system', return_value='Linux'), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'preflight', return_value=[]), \
             patch.object(deploy, 'step_1') as s1, \
             patch.object(deploy, 'step_2', return_value=0) as s2, \
             patch.object(deploy, 'step_3', return_value=0) as s3, \
             patch.object(deploy, 'check_connection', return_value=True):
            assert deploy.main() == 0

        s1.assert_called_once()
        s2.assert_called_once()
        s3.assert_called_once()

    @pytest.mark.unit
    def test_a_failed_install_does_not_reach_step_3(self):
        """The exit code used to be discarded, so a failed install fell
        through and was reported - at best - as containers not running."""
        with patch('platform.system', return_value='Linux'), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'preflight', return_value=[]), \
             patch.object(deploy, 'step_1'), \
             patch.object(deploy, 'step_2', return_value=22), \
             patch.object(deploy, 'step_3') as s3, \
             patch.object(deploy, 'check_connection', return_value=True):
            assert deploy.main() == 22
        s3.assert_not_called()

    @pytest.mark.unit
    def test_unmet_preconditions_stop_before_anything_is_installed(self, capsys):
        """No docker, no sudo or no disk each used to surface part-way
        through as a confusing failure with a half-built device behind it."""
        with patch('platform.system', return_value='Linux'), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'preflight',
                          return_value=['docker is not installed']), \
             patch.object(deploy, 'step_1') as s1:
            assert deploy.main() == 21
        s1.assert_not_called()
        assert 'docker is not installed' in capsys.readouterr().out

    @pytest.mark.unit
    def test_a_machine_still_offline_after_step_1_does_not_install(self, capsys):
        # Pulling images without a network would leave a broken half-install.
        with patch('platform.system', return_value='Linux'), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'preflight', return_value=[]), \
             patch.object(deploy, 'step_1'), \
             patch.object(deploy, 'step_2') as s2, \
             patch.object(deploy, 'step_3') as s3, \
             patch.object(deploy, 'check_connection', return_value=False):
            assert deploy.main() != 0

        s2.assert_not_called()
        s3.assert_not_called()
        assert 'No route to the image registry' in capsys.readouterr().out

    @pytest.mark.unit
    @pytest.mark.parametrize('os_name', ['Darwin', 'Windows'])
    def test_non_linux_refuses_to_run(self, os_name, capsys):
        with patch('platform.system', return_value=os_name), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'step_1') as s1:
            assert deploy.main() != 0

        s1.assert_not_called()
        assert 'must be running linux' in capsys.readouterr().out


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

BROKEN_REQUESTS = (
    'raise AttributeError('
    '"module \'charset_normalizer.md\' has no attribute \'CharInfo\'")\n')


class TestBootstrapsOnABareMachine:
    """deploy.py runs before anything is installed. If it cannot be imported on
    stock python3, it cannot even say what is missing."""

    def _import_deploy(self, extra_path):
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join([extra_path, REPO])
        return subprocess.run(
            [sys.executable, '-c', 'import deploy; print("IMPORTED")'],
            cwd=REPO, env=env, capture_output=True, text=True, timeout=120)

    @pytest.mark.unit
    def test_it_imports_when_requests_is_broken(self, tmp_path):
        """The failure seen on a real device: charset_normalizer half-upgraded,
        so `import requests` raises AttributeError rather than ImportError."""
        (tmp_path / 'requests.py').write_text(BROKEN_REQUESTS)

        result = self._import_deploy(str(tmp_path))

        assert 'IMPORTED' in result.stdout, result.stderr

    @pytest.mark.unit
    def test_no_third_party_import_at_module_scope(self):
        """requests is installed by system_server.sh, which runs inside step 2.
        Importing it at module scope made deploy.py depend on the thing it
        exists to install."""
        source = open(os.path.join(REPO, 'deploy.py')).read()
        head = source.split('def clear_text_color')[0]

        assert 'version_check' not in head
        assert 'import requests' not in head


class TestProbePythonDeps:
    @pytest.mark.unit
    def test_a_working_import_is_ok(self):
        assert deploy.probe_python_deps('pass') == (True, '')

    @pytest.mark.unit
    def test_a_broken_import_reports_the_reason(self):
        ok, detail = deploy.probe_python_deps('raise AttributeError("CharInfo")')

        assert ok is False
        assert 'CharInfo' in detail

    @pytest.mark.unit
    def test_a_missing_package_reports_the_reason(self):
        ok, detail = deploy.probe_python_deps('import nosuchpackage_xyz')

        assert ok is False
        assert 'nosuchpackage_xyz' in detail

    @pytest.mark.unit
    def test_it_probes_in_a_subprocess(self):
        """An in-process retry sees the half-initialised sys.modules entry the
        first failure left behind, and reports broken forever after."""
        with patch.object(deploy.subprocess, 'run') as run:
            run.return_value = MagicMock(returncode=0, stdout='', stderr='')
            deploy.probe_python_deps()

        assert run.call_args[0][0][0] == sys.executable


class TestPipArgv:
    @pytest.mark.unit
    def test_it_uses_this_interpreter_not_pip3(self):
        """Two pythons on one device means `pip3` can install into the other
        one - a successful install followed by an import that still fails."""
        with patch.object(deploy.subprocess, 'run') as run:
            run.return_value = MagicMock(stdout='')
            argv = deploy.pip_argv()

        assert argv[:4] == [sys.executable, '-m', 'pip', 'install']

    @pytest.mark.unit
    def test_break_system_packages_when_pip_supports_it(self):
        with patch.object(deploy.subprocess, 'run') as run:
            run.return_value = MagicMock(stdout='  --break-system-packages\n')
            assert '--break-system-packages' in deploy.pip_argv()

    @pytest.mark.unit
    def test_not_passed_when_pip_is_too_old_to_know_it(self):
        with patch.object(deploy.subprocess, 'run') as run:
            run.return_value = MagicMock(stdout='  --user\n')
            assert '--break-system-packages' not in deploy.pip_argv()

    @pytest.mark.unit
    def test_an_unrunnable_pip_still_yields_an_argv(self):
        with patch.object(deploy.subprocess, 'run', side_effect=OSError):
            assert deploy.pip_argv()[:4] == [sys.executable, '-m', 'pip', 'install']


class TestAsRoot:
    @pytest.mark.unit
    def test_no_sudo_when_already_root(self):
        with patch.object(deploy.os, 'geteuid', return_value=0):
            assert deploy._as_root(['apt-get']) == ['apt-get']

    @pytest.mark.unit
    def test_sudo_otherwise(self):
        with patch.object(deploy.os, 'geteuid', return_value=1000):
            assert deploy._as_root(['apt-get']) == ['sudo', 'apt-get']


class TestEnsurePythonDeps:
    @pytest.mark.unit
    def test_a_working_environment_installs_nothing(self):
        with patch.object(deploy, 'probe_python_deps', return_value=(True, '')), \
             patch.object(deploy.subprocess, 'call') as call_:
            assert deploy.ensure_python_deps() == 0

        call_.assert_not_called()

    @pytest.mark.unit
    def test_it_installs_and_returns_zero(self):
        with patch.object(deploy, 'probe_python_deps',
                          side_effect=[(False, 'no module'), (True, '')]), \
             patch.object(deploy, 'ensure_pip', return_value=True), \
             patch.object(deploy, 'pip_argv', return_value=['pip', 'install']), \
             patch.object(deploy.subprocess, 'call', return_value=0) as call_:
            assert deploy.ensure_python_deps() == 0

        assert deploy.REQUIREMENTS in call_.call_args[0][0]

    @pytest.mark.unit
    def test_a_failed_pip_is_reported_not_ignored(self):
        with patch.object(deploy, 'probe_python_deps', return_value=(False, 'x')), \
             patch.object(deploy, 'ensure_pip', return_value=True), \
             patch.object(deploy, 'pip_argv', return_value=['pip', 'install']), \
             patch.object(deploy.subprocess, 'call', return_value=1):
            assert deploy.ensure_python_deps() == deploy.DEPS_FAILED

    @pytest.mark.unit
    def test_no_pip_and_none_installable_is_reported(self):
        with patch.object(deploy, 'probe_python_deps', return_value=(False, 'x')), \
             patch.object(deploy, 'ensure_pip', return_value=False), \
             patch.object(deploy.subprocess, 'call') as call_:
            assert deploy.ensure_python_deps() == deploy.DEPS_FAILED

        call_.assert_not_called()

    @pytest.mark.unit
    def test_a_missing_requirements_file_is_reported(self, monkeypatch):
        monkeypatch.setattr(deploy, 'REQUIREMENTS', '/nonexistent/requirements.txt')
        with patch.object(deploy, 'probe_python_deps', return_value=(False, 'x')), \
             patch.object(deploy.subprocess, 'call') as call_:
            assert deploy.ensure_python_deps() == deploy.DEPS_FAILED

        call_.assert_not_called()

    @pytest.mark.unit
    def test_an_install_that_does_not_fix_it_says_so(self, capsys):
        """pip exits 0 but the import still fails: two versions of a package in
        one directory, which is what a half-finished upgrade leaves behind."""
        with patch.object(deploy, 'probe_python_deps',
                          side_effect=[(False, 'CharInfo'), (False, 'CharInfo')]), \
             patch.object(deploy, 'ensure_pip', return_value=True), \
             patch.object(deploy, 'pip_argv', return_value=['pip', 'install']), \
             patch.object(deploy.subprocess, 'call', return_value=0):
            assert deploy.ensure_python_deps() == deploy.DEPS_FAILED

        out = capsys.readouterr().out
        assert 'CharInfo' in out
        assert 'dist-info' in out


class TestEnsurePip:
    @pytest.mark.unit
    def test_a_working_pip_installs_nothing(self):
        with patch.object(deploy.subprocess, 'run') as run, \
             patch.object(deploy.subprocess, 'call') as call_:
            run.return_value = MagicMock(returncode=0)
            assert deploy.ensure_pip() is True

        call_.assert_not_called()

    @pytest.mark.unit
    def test_a_missing_pip_is_installed_from_apt(self):
        with patch.object(deploy.subprocess, 'run') as run, \
             patch.object(deploy.subprocess, 'call', return_value=0) as call_, \
             patch.object(deploy.os, 'geteuid', return_value=0):
            run.side_effect = [MagicMock(returncode=1), MagicMock(returncode=0)]
            assert deploy.ensure_pip() is True

        assert 'python3-pip' in call_.call_args[0][0]


class TestDepsRunBeforeStepTwo:
    """Ordering is the whole point: step_2 is the first thing that imports any
    of this, and it must not be reached with the environment unfixed."""

    @pytest.mark.unit
    def test_deps_are_ensured_before_step_2(self):
        order = []
        with patch.object(deploy, 'preflight', return_value=[]), \
             patch.object(deploy.platform, 'system', return_value='Linux'), \
             patch.object(deploy, 'step_1'), \
             patch.object(deploy, 'check_connection', return_value=True), \
             patch.object(deploy, 'ensure_python_deps',
                          side_effect=lambda: order.append('deps') or 0), \
             patch.object(deploy, 'step_2',
                          side_effect=lambda: order.append('step_2') or 0), \
             patch.object(deploy, 'step_3',
                          side_effect=lambda: order.append('step_3') or 0):
            deploy.main()

        assert order == ['deps', 'step_2', 'step_3']

    @pytest.mark.unit
    def test_step_2_is_not_reached_when_deps_cannot_be_fixed(self):
        with patch.object(deploy, 'preflight', return_value=[]), \
             patch.object(deploy.platform, 'system', return_value='Linux'), \
             patch.object(deploy, 'step_1'), \
             patch.object(deploy, 'check_connection', return_value=True), \
             patch.object(deploy, 'ensure_python_deps',
                          return_value=deploy.DEPS_FAILED), \
             patch.object(deploy, 'step_2') as step_2:
            assert deploy.main() == deploy.DEPS_FAILED

        step_2.assert_not_called()

    @pytest.mark.unit
    def test_deps_are_not_attempted_before_the_network_is_up(self):
        """pip needs the network, so this runs after step_1, not in preflight."""
        with patch.object(deploy, 'preflight', return_value=[]), \
             patch.object(deploy.platform, 'system', return_value='Linux'), \
             patch.object(deploy, 'step_1'), \
             patch.object(deploy, 'check_connection', return_value=False), \
             patch.object(deploy, 'ensure_python_deps') as deps:
            deploy.main()

        deps.assert_not_called()
