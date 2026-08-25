import os
import re
import subprocess
import time
from flask import render_template, make_response
from flask_restx import Resource
import auth
from version_check import is_container_uptodate, get_current_container_version
from setup.management import generate_environment_config

CONTAINERS = {
    'backend': 'capdev',
    'frontend': 'captureui',
    'prediction': 'localprediction',
    'predict lite': 'predictlite',
    'nodecreator': 'nodecreator',
    'vision': 'vision',
    'database': 'mongo',
    'visiontools': 'visiontools'
}

daemon_services_list = {
    "FlexRun Server": "server.py",
    "TCP Server": "tcp/tcp_server.py",
    "GPIO Server": "gpio/gpio_controller.py",
    "Sync Worker": "worker_scripts/sync_worker.py",
    "Worker Server": "worker.py",
    "Inference Server Watcher": "worker_scripts/ping_prediction_server.py",
    "Job Watcher": "job_watcher.py"
}

class Shutdown(Resource):
    @auth.requires_auth
    def get(self):
        print('shutting down system')
        os.system("poweroff")

class Restart(Resource):
    @auth.requires_auth
    def get(self):
        print('restarting system')
        os.system("reboot")

class RestartBackend(Resource):
    @auth.requires_auth
    def get(self):
        import time
        import requests

        vision_base = 'http://172.17.0.1:5555'
        vision_api = vision_base + '/api/vision/vision'

        print('stopping capdev to release camera locks...')
        os.system("docker restart capdev")

        # Release cameras and restart vision
        try:
            requests.get(vision_api + '/releaseAll', timeout=5)
        except Exception as e:
            print('releaseAll:', e)

        print('restarting vision...')
        os.system("docker restart vision")

        # Wait for vision to finish camera discovery (listCameras runs synchronously on first /cameras call)
        max_wait = 120
        poll_interval = 5
        elapsed = 0
        cameras_ready = False

        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval
            try:
                resp = requests.get(vision_api + '/cameras', timeout=30)
                if resp.status_code == 200:
                    cameras = resp.json()
                    if len(cameras) > 0:
                        print(f'vision: {len(cameras)} cameras discovered and connected ({elapsed}s)')
                        cameras_ready = True
                        break
            except Exception as e:
                print(f'waiting for vision... ({elapsed}s)')

        if not cameras_ready:
            print('warning: vision not ready, starting capdev anyway')

        print('starting capdev...')
        os.system("docker start capdev")

class ListServices(Resource):
    def get(self):
        f_services = []
        scripts_base_path = os.environ['HOME']+"/flex-run/system_server/"
        for key in daemon_services_list:
            service_path = scripts_base_path + daemon_services_list[key]
            is_running = subprocess.getoutput("forever list | grep {} | wc -l | sed -e 's/1/Running/' | sed -e 's/0/Not Running/'".format(service_path))
            color = 'green' if is_running == "Running" else 'red'
            txt = key + " - " + is_running
            f_services.append({'txt': txt, 'color': color})

        c_services = []
        for f_name in CONTAINERS:
            container_name = CONTAINERS[f_name]
            inspect = subprocess.Popen(['docker', 'inspect', '-f', "{{.State.Running}}", container_name], stdout=subprocess.PIPE)
            is_running = inspect.communicate()[0].decode('utf-8').strip()
            color = 'green' if is_running=='true' else 'red'
            r_txt = 'Running' if is_running=='true' else 'Not Running'
            txt = f_name + " - " + r_txt
            c_services.append({'txt': txt, 'color': color})

        resp = make_response(render_template('services_doc.html', daemon_services=f_services, container_services=c_services))
        resp.headers['Content-type'] = 'text/html; charset=utf-8'
        return resp

class Upgrade(Resource):
    @auth.requires_auth
    def get(self):
        # Verify user is logged into Docker
        result = subprocess.run(['docker', 'info'], capture_output=True, text=True)
        if result.returncode != 0 or 'Username' not in result.stdout:
            return {'error': 'Not logged into Docker. Please run docker login first.'}, 403

        cap_uptd = is_container_uptodate('backend')[1]
        capui_uptd = is_container_uptodate('frontend')[1]
        predict_uptd = is_container_uptodate('prediction')[1]
        predictlite_uptd = is_container_uptodate('predictlite')[1]
        vision_uptd = is_container_uptodate('vision')[1]
        creator_uptd = is_container_uptodate('nodecreator')[1]
        visiontools_uptd = is_container_uptodate('visiontools')[1]

        try:
            import requests
            host = 'http://172.17.0.1'
            port = '5555'
            path = '/api/vision/releaseAll'
            url = host+':'+port+path
            resp = requests.get(url)
        except Exception as e:
            print(e)

        generate_environment_config()
        home = os.environ['HOME']
        subprocess.run(["chmod", "+x", home+"/flex-run/upgrades/upgrade_flex_run.sh"])
        subprocess.run(["sh", home+"/flex-run/upgrades/upgrade_flex_run.sh"])

        subprocess.run(["chmod", "+x", home+"/flex-run/system_server/upgrade_system.sh"])
        subprocess.run(["sh", home+"/flex-run/system_server/upgrade_system.sh",
                        cap_uptd, capui_uptd, predict_uptd, predictlite_uptd,
                        vision_uptd, creator_uptd, visiontools_uptd])

class UpgradeFlexRun(Resource):
    @auth.requires_auth
    def get(self):
        home = os.environ['HOME']
        subprocess.run(["chmod", "+x", home+"/flex-run/upgrades/upgrade_flex_run.sh"])
        subprocess.run(["sh", home+"/flex-run/upgrades/upgrade_flex_run.sh"])

class SystemVersions(Resource):
    def get(self):
        backend_version = get_current_container_version('capdev')
        frontend_version = get_current_container_version('captureui')
        prediction_version = get_current_container_version('localprediction')
        predictlite_version = get_current_container_version('predictlite')
        vision_version = get_current_container_version('vision')
        creator_version = get_current_container_version('nodecreator')
        visiontools_version = get_current_container_version('visiontools')

        return {'backend_version': backend_version,
                'frontend_version': frontend_version,
                'prediction_version': prediction_version,
                'predictlite_version': predictlite_version,
                'vision_version': vision_version,
                'creator_version': creator_version,
                'visiontools_version': vision_version
                }

class SystemIsUptodate(Resource):
    def get(self):
        return all([
            is_container_uptodate('backend')[0],
            is_container_uptodate('frontend')[0],
            is_container_uptodate('prediction')[0],
            is_container_uptodate('predictlite')[0],
            is_container_uptodate('vision')[0],
            is_container_uptodate('nodecreator')[0],
            is_container_uptodate('visiontools')[0]
        ])

class RestartFO(Resource):
    def get(self):
        try:
            os.system("forever restart /root/flex-run/aws/fo_server.py")
            return "FO server restarted", 200
        except Exception as e:
            print("Error restarting FO server:", e)
            return "Error restarting FO server", 500

TEAMVIEWER_SERVICE = 'teamviewerd'
TEAMVIEWER_START_TIMEOUT = 15
TEAMVIEWER_CMD_TIMEOUT = 15
TEAMVIEWER_GUI_TIMEOUT = 25
TEAMVIEWER_DBUS_NAME = 'com.teamviewer.TeamViewer'


def _run_cmd(cmd, timeout=30):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, (result.stdout or '').strip(), (result.stderr or '').strip()
    except Exception as e:
        return -1, '', str(e)


def _priv(cmd):
    return cmd if os.geteuid() == 0 else ['sudo', '-n'] + cmd


def _teamviewer_running():
    _, out, _ = _run_cmd(['systemctl', 'is-active', TEAMVIEWER_SERVICE], timeout=10)
    if out == 'active':
        return True
    code, out, _ = _run_cmd(_priv(['teamviewer', '--daemon', 'status']), timeout=10)
    return code == 0 and 'not running' not in out.lower()


def _wait_for_teamviewer(timeout=TEAMVIEWER_START_TIMEOUT):
    deadline = time.time() + timeout
    while True:
        if _teamviewer_running():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1)


def _graphical_session():
    code, out, _ = _run_cmd(['loginctl', 'list-sessions', '--no-legend'], timeout=10)
    if code != 0:
        return None

    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        _, props, _ = _run_cmd(
            ['loginctl', 'show-session', parts[0],
             '-p', 'Name', '-p', 'User', '-p', 'Type', '-p', 'Active'],
            timeout=10)
        p = dict(l.split('=', 1) for l in props.splitlines() if '=' in l)
        if p.get('Active') == 'yes' and p.get('Type') in ('x11', 'wayland'):
            return {'user': p.get('Name'), 'uid': p.get('User'), 'type': p.get('Type')}
    return None


def _teamviewer_gui_running():
    code, _, _ = _run_cmd(['pgrep', '-x', 'TeamViewer'], timeout=10)
    return code == 0


def _wait_for_teamviewer_gui(timeout=TEAMVIEWER_GUI_TIMEOUT):
    deadline = time.time() + timeout
    while True:
        if _teamviewer_gui_running():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1)


def _launch_teamviewer_gui():
    if _teamviewer_gui_running():
        return True, 'already running'

    session = _graphical_session()
    if not session:
        return False, 'no active graphical session'

    uid = session['uid']
    env = ['DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{}/bus'.format(uid),
           'XDG_RUNTIME_DIR=/run/user/{}'.format(uid)]
    activate = ['dbus-send', '--session', '--dest=org.freedesktop.DBus',
                '--type=method_call', '--print-reply', '/org/freedesktop/DBus',
                'org.freedesktop.DBus.StartServiceByName',
                'string:' + TEAMVIEWER_DBUS_NAME, 'uint32:0']

    cmd = ['runuser', '-u', session['user'], '--', 'env'] + env + activate
    if os.geteuid() != 0:
        cmd = ['env'] + env + activate

    code, out, err = _run_cmd(cmd, timeout=TEAMVIEWER_CMD_TIMEOUT)
    if code != 0:
        return False, 'dbus activation failed: {}'.format(err or out or 'exit {}'.format(code))

    if not _wait_for_teamviewer_gui():
        return False, 'dbus activation returned but the GUI did not start'

    return True, 'activated in {} session for user {}'.format(session['type'], session['user'])


def _teamviewer_id():
    code, out, _ = _run_cmd(_priv(['teamviewer', 'info']), timeout=10)
    if code != 0:
        return None
    for line in out.splitlines():
        if 'TeamViewer ID' in line:
            raw = line.split(':')[-1].strip()
            return re.sub(r'\x1b\[[0-9;]*m', '', raw).strip()
    return None


def _start_teamviewer_daemon():
    if _teamviewer_running():
        return True, 'already_running', ''

    _run_cmd(_priv(['systemctl', 'unmask', TEAMVIEWER_SERVICE]), timeout=10)

    errors = []
    for cmd in (_priv(['systemctl', 'enable', '--now', TEAMVIEWER_SERVICE]),
                _priv(['teamviewer', '--daemon', 'start'])):
        code, out, err = _run_cmd(cmd, timeout=TEAMVIEWER_CMD_TIMEOUT)
        if code == 0 and _wait_for_teamviewer():
            return True, 'started', ''
        errors.append('{}: {}'.format(' '.join(cmd), err or out or 'exit {}'.format(code)))

    return False, 'failed', ' | '.join(errors)


class StartTeamviewer(Resource):
    def get(self):
        try:
            ok, status, detail = _start_teamviewer_daemon()
            if not ok:
                print('Failed to start TeamViewer daemon: ' + detail)
                return {'success': False, 'status': status, 'error': detail}, 500

            gui_ok, gui_detail = _launch_teamviewer_gui()
            body = {'success': gui_ok, 'status': status, 'daemon': 'running',
                    'gui': gui_detail, 'teamviewer_id': _teamviewer_id()}

            if not gui_ok:
                body['error'] = ('daemon is running but the GUI could not be started, '
                                 'so the device will stay offline: ' + gui_detail)
                print('TeamViewer GUI not started: ' + gui_detail)
                return body, 503

            return body, 200
        except Exception as e:
            print('Error starting TeamViewer:', e)
            return {'success': False, 'status': 'error', 'error': str(e)}, 500


def register_routes(api):
    api.add_resource(Shutdown, '/shutdown')
    api.add_resource(Restart, '/restart')
    api.add_resource(RestartBackend, '/refresh_backend')
    api.add_resource(ListServices, '/list_services')
    api.add_resource(Upgrade, '/upgrade')
    api.add_resource(UpgradeFlexRun, '/upgrade_flex_run')
    api.add_resource(SystemVersions, '/system_versions')
    api.add_resource(SystemIsUptodate, '/system_uptodate')
    api.add_resource(StartTeamviewer, '/start_teamviewer')
