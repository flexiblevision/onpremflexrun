"""First-run device setup: the interactive installer run on a bare machine.

This is the one path a technician drives by hand on the floor, and it is the
one path with no retry: a step that reports success without having done
anything leaves a half-installed device behind.
"""
import subprocess
import pytest
from testsupport import thread_aware_sleep_mock
from unittest.mock import patch, MagicMock, call, mock_open

import deploy


class TestClearTextColor:
    @pytest.mark.unit
    def test_emits_the_ansi_reset(self, capsys):
        deploy.clear_text_color()
        assert '\033[0m' in capsys.readouterr().out


class TestStaticIpReferences:
    """Both readers fall through to their defaults on this build."""

    @pytest.mark.unit
    def test_static_ip_falls_back_to_the_documented_default(self):
        # path_ref is never defined at module scope, so the `open(path_ref)`
        # raises NameError, the bare `except` swallows it and the default is
        # returned. Pinned so that defining path_ref later is a deliberate
        # change with a visible test diff, not a silent behaviour swap.
        assert deploy.get_static_ip_ref() == '192.168.10.35'

    @pytest.mark.unit
    def test_interface_name_falls_back_to_the_documented_default(self):
        assert deploy.get_interface_name_ref() == 'enp0s31f6'

    @pytest.mark.unit
    def test_a_defined_path_ref_would_be_read(self, tmp_path, monkeypatch):
        ref = tmp_path / 'ip'
        ref.write_text('10.0.0.9\n')
        monkeypatch.setattr(deploy, 'path_ref', str(ref), raising=False)

        assert deploy.get_static_ip_ref() == '10.0.0.9'
        assert deploy.get_interface_name_ref() == '10.0.0.9'


class TestSetStaticIp:
    @pytest.mark.unit
    def test_configures_the_interface_and_writes_netplan(self, tmp_path):
        opener = mock_open()
        with patch('os.system') as system, \
             patch('builtins.open', opener), \
             patch.object(deploy, 'get_static_ip_ref', return_value='192.168.10.35'), \
             patch.object(deploy, 'get_interface_name_ref', return_value='eth0'):
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
             patch.object(deploy, 'get_static_ip_ref', return_value='192.168.10.35'), \
             patch.object(deploy, 'get_interface_name_ref', return_value='eth0'):
            deploy.set_static_ip()

        written = ''.join(c.args[0] for c in opener().write.call_args_list)
        assert 'renderer: NetworkManager' in written
        assert '    eth0:' in written
        assert 'dhcp4: false' in written
        assert 'mtu: 9000' in written
        assert 'addresses: [192.168.10.35/24]' in written


class TestContainersRunning:
    @pytest.mark.unit
    def test_all_three_running_is_true(self):
        proc = MagicMock()
        proc.stdout.read.return_value = b'true\n'
        with patch('subprocess.Popen', return_value=proc):
            assert deploy.containers_running() is True

    @pytest.mark.unit
    def test_one_stopped_container_is_false(self):
        procs = []
        for state in (b'true\n', b'false\n', b'true\n'):
            p = MagicMock()
            p.stdout.read.return_value = state
            procs.append(p)

        with patch('subprocess.Popen', side_effect=procs):
            assert deploy.containers_running() is False

    @pytest.mark.unit
    def test_inspects_the_three_application_containers(self):
        proc = MagicMock()
        proc.stdout.read.return_value = b'true\n'
        with patch('subprocess.Popen', return_value=proc) as popen:
            deploy.containers_running()

        inspected = [c[0][0][-1] for c in popen.call_args_list]
        assert inspected == ['capdev', 'localprediction', 'captureui']

    @pytest.mark.unit
    def test_a_missing_container_raises(self):
        # docker inspect prints nothing for an unknown container, and
        # json.loads('') is a decode error rather than a False.
        import json
        proc = MagicMock()
        proc.stdout.read.return_value = b''
        with patch('subprocess.Popen', return_value=proc):
            with pytest.raises(json.JSONDecodeError):
                deploy.containers_running()


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


class TestChooseEnvironment:
    @pytest.mark.unit
    def test_option_one_is_the_cloud(self):
        with patch('builtins.input', return_value='1'), \
             patch.object(deploy, 'generate_environment_config') as generate:
            deploy.choose_environment()

        generate.assert_called_once_with('cloud', True)

    @pytest.mark.unit
    def test_option_two_is_a_local_cluster(self):
        with patch('builtins.input', return_value='2'), \
             patch.object(deploy, 'generate_environment_config') as generate:
            deploy.choose_environment()

        generate.assert_called_once_with('local', True)

    @pytest.mark.unit
    def test_reprompts_on_anything_else(self, capsys):
        with patch('builtins.input', side_effect=['3', 'x', '1']), \
             patch.object(deploy, 'generate_environment_config'):
            deploy.choose_environment()

        assert capsys.readouterr().out.count("Please respond with '1' or '2'") == 2


class TestCheckConnection:
    @pytest.mark.unit
    def test_successful_ping_is_online(self):
        with patch('subprocess.check_output', return_value=b'4 received') as ping:
            assert deploy.check_connection() is True
        assert ping.call_args[0][0] == ['ping', '-c', '4', 'google.com']

    @pytest.mark.unit
    def test_failed_ping_is_offline(self):
        with patch('subprocess.check_output',
                   side_effect=subprocess.CalledProcessError(1, 'ping')):
            assert deploy.check_connection() is False


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
        with patch('os.system') as system, patch('time.sleep', new=thread_aware_sleep_mock()):
            deploy.connect_wifi('shopfloor', 'hunter2')

        system.assert_called_once_with('nmcli dev wifi connect shopfloor password hunter2')

    @pytest.mark.unit
    def test_settles_before_returning(self):
        with patch('os.system'), patch('time.sleep', new=thread_aware_sleep_mock()) as sleep:
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
             patch('builtins.input', side_effect=['shopfloor', 'hunter2']), \
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
             patch.object(deploy, 'is_container_uptodate', return_value=(False, '1.2.3')), \
             patch('subprocess.call') as call_script:
            deploy.step_2()

        args = call_script.call_args[0][0]
        assert args[:3] == ['sh', './scripts/local_setup.sh', '1.2.3']
        # Seven containers are installed by the first-run script.
        assert len(args) == 9

    @pytest.mark.unit
    def test_resolves_each_container_by_name(self):
        with patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'is_container_uptodate', return_value=(False, '1.0')) as uptodate, \
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
             patch.object(deploy, 'is_container_uptodate', return_value=(True, 'True')), \
             patch('subprocess.call') as call_script:
            deploy.step_2()

        assert call_script.call_args[0][0][2:] == ['True'] * 7


class TestStep3:
    @pytest.mark.unit
    def test_running_containers_report_the_launch_url(self, capsys):
        with patch.object(deploy, 'containers_running', return_value=True):
            deploy.step_3()
        assert 'Launch - http://<host ip>' in capsys.readouterr().out

    @pytest.mark.unit
    def test_stopped_containers_tell_the_operator_to_retry(self, capsys):
        with patch.object(deploy, 'containers_running', return_value=False):
            deploy.step_3()

        out = capsys.readouterr().out
        assert 'Step 2 did not complete' in out
        assert 'Launch' not in out


class TestMain:
    @pytest.mark.unit
    def test_runs_all_three_steps_on_linux(self):
        with patch('platform.system', return_value='Linux'), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'step_1') as s1, \
             patch.object(deploy, 'step_2') as s2, \
             patch.object(deploy, 'step_3') as s3, \
             patch.object(deploy, 'check_connection', return_value=True):
            deploy.main()

        s1.assert_called_once()
        s2.assert_called_once()
        s3.assert_called_once()

    @pytest.mark.unit
    def test_a_machine_still_offline_after_step_1_does_not_install(self, capsys):
        # Pulling images without a network would leave a broken half-install.
        with patch('platform.system', return_value='Linux'), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'step_1'), \
             patch.object(deploy, 'step_2') as s2, \
             patch.object(deploy, 'step_3') as s3, \
             patch.object(deploy, 'check_connection', return_value=False):
            deploy.main()

        s2.assert_not_called()
        s3.assert_not_called()
        assert 'Wi-Fi not connected' in capsys.readouterr().out

    @pytest.mark.unit
    @pytest.mark.parametrize('os_name', ['Darwin', 'Windows'])
    def test_non_linux_refuses_to_run(self, os_name, capsys):
        with patch('platform.system', return_value=os_name), \
             patch('time.sleep', new=thread_aware_sleep_mock()), \
             patch.object(deploy, 'step_1') as s1:
            deploy.main()

        s1.assert_not_called()
        assert 'must be running linux' in capsys.readouterr().out
