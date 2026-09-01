import os
import re
import subprocess
import sys
import time
import uuid
from flask import render_template, make_response
from flask_restx import Resource
import auth
import upgrade_runner
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
    def post(self):
        return self._start()

    @auth.requires_auth
    def get(self):
        # Deprecated in favour of POST. Kept because captureui is versioned and
        # upgraded independently of flex-run: if GET stopped working, a device
        # running an older UI could no longer start the upgrade that would fix
        # it. Retire once the fleet's UI is known to POST.
        return self._start()

    def _start(self):
        # Verify user is logged into Docker
        result = subprocess.run(['docker', 'info'], capture_output=True, text=True)
        if result.returncode != 0 or 'Username' not in result.stdout:
            return {'error': 'Not logged into Docker. Please run docker login first.'}, 403

        holder = upgrade_runner.lock_holder()
        if holder is not None:
            return {'error': 'An upgrade is already running on this device',
                    'pid': holder}, 409

        try:
            import requests
            requests.get('http://172.17.0.1:5555/api/vision/releaseAll', timeout=10)
        except Exception as e:
            print(e)

        generate_environment_config()
        home = os.environ['HOME']
        runner = os.path.join(home, 'flex-run', 'system_server', 'upgrade_runner.py')
        run_id = str(uuid.uuid4())

        # Detached: the upgrade outlives this request, and its final step stops
        # and restarts this very server. start_new_session keeps it out of the
        # process group that gets killed.
        log = upgrade_runner.log_path(run_id)
        try:
            handle = open(log, 'ab', 0) if log else subprocess.DEVNULL
        except IOError:
            handle, log = subprocess.DEVNULL, None

        # --release prefers a signed manifest and falls back to the version
        # endpoint when none can be obtained, so a device is never stranded by
        # an unreachable release service. No versions are passed: the runner
        # computes them for the fallback, so it upgrades to what is current
        # when the upgrade runs rather than when the request arrived.
        try:
            subprocess.Popen([sys.executable, runner, '--release', run_id],
                             stdout=handle, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,
                             start_new_session=True,
                             close_fds=True)
        except Exception as e:
            return {'error': 'Could not start the upgrade',
                    'detail': str(e)}, 500
        finally:
            if handle is not subprocess.DEVNULL:
                handle.close()

        return {'status': 'upgrade started', 'id': run_id, 'log': log,
                'poll': '/upgrade_status'}, 202


class UpgradeStatus(Resource):
    def get(self):
        return upgrade_runner.status()

class UpgradeFlexRun(Resource):
    @auth.requires_auth
    def get(self):
        home = os.environ['HOME']
        subprocess.run(["chmod", "+x", home+"/flex-run/upgrades/upgrade_flex_run.sh"])
        flex_run = subprocess.run(["sh", home+"/flex-run/upgrades/upgrade_flex_run.sh"],
                                  capture_output=True, text=True)
        print(flex_run.stdout, flex_run.stderr)

        if flex_run.returncode != 0:
            return {'error': upgrade_runner.flex_run_error(flex_run.returncode),
                    'exit_code': flex_run.returncode,
                    'detail': (flex_run.stderr or '').strip()[-500:]}, 500

        return {'status': 'flex-run updated'}

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
                'visiontools_version': visiontools_version
                }


def _release_collection():
    """The utils collection release state lives in."""
    from pymongo import MongoClient
    client = MongoClient(os.environ.get('MONGO_SERVER', '172.17.0.1'),
                         int(os.environ.get('MONGO_PORT', 27017)),
                         serverSelectionTimeoutMS=5000)
    return client['fvonprem']['utils']


class Releases(Resource):
    """What release is running, what is offered, and what it can go back to.

    One call, because the settings screen needs all of it at once and three
    round trips over a factory network is three chances to render half a state.
    """
    def get(self):
        try:
            from release import state as release_state
            summary = release_state.summary(_release_collection(), available=None)
        except Exception as e:
            # A device that predates release tracking has no state; say so
            # rather than 500ing the whole settings screen.
            summary = {'installed': None, 'high_water': 0, 'history': [],
                       'rollback_targets': [], 'available': None,
                       'update_available': False, 'rolled_back_from': None,
                       'unavailable': str(e)}

        # Which keys this device trusts. Reported so a rotation can be tracked
        # across the fleet: you cannot safely retire a key until every device
        # shows the replacement.
        try:
            from release import trust as release_trust
            summary['trust'] = release_trust.summary(
                os.environ.get('FLEXRUN_TRUST_DIR', release_trust.DEFAULT_TRUST_DIR))
        except Exception as e:
            summary['trust'] = {'count': 0, 'keys': [], 'unavailable': str(e)}

        return summary


class Rollback(Resource):
    """Return this device to a release it has previously run.

    POST, and deliberately not exposed as GET: it swaps containers.
    """
    @auth.requires_auth
    def post(self):
        from flask import request
        from release import state as release_state

        body = request.get_json(silent=True) or {}
        target = body.get('counter')
        if not isinstance(target, int) or isinstance(target, bool):
            return {'error': 'a numeric release counter is required'}, 400

        holder = upgrade_runner.lock_holder()
        if holder is not None:
            return {'error': 'an upgrade is already running on this device',
                    'pid': holder}, 409

        try:
            known = release_state.known_counters(_release_collection())
        except Exception as e:
            return {'error': 'could not read release history',
                    'detail': str(e)}, 500

        if target not in known:
            return {'error': 'release {} has never run on this device'.format(target),
                    'available': sorted(known)}, 400

        # Detached for the same reason /upgrade is: a rollback swaps every
        # container and restarts this server, so nothing is left to answer the
        # request it came in on.
        home = os.environ['HOME']
        runner = os.path.join(home, 'flex-run', 'system_server',
                              'upgrade_runner.py')
        run_id = str(uuid.uuid4())
        log = upgrade_runner.log_path(run_id)
        try:
            handle = open(log, 'ab', 0) if log else subprocess.DEVNULL
        except IOError:
            handle, log = subprocess.DEVNULL, None

        try:
            subprocess.Popen([sys.executable, runner, '--rollback', run_id,
                              str(target)],
                             stdout=handle, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True)
        except Exception as e:
            return {'error': 'Could not start the rollback',
                    'detail': str(e)}, 500
        finally:
            if handle is not subprocess.DEVNULL:
                handle.close()

        return {'started': True, 'run_id': run_id, 'target': target,
                'log': log}, 202


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
    api.add_resource(UpgradeStatus, '/upgrade_status')
    api.add_resource(UpgradeFlexRun, '/upgrade_flex_run')
    api.add_resource(SystemVersions, '/system_versions')
    api.add_resource(Releases, '/releases')
    api.add_resource(Rollback, '/rollback')
    api.add_resource(SystemIsUptodate, '/system_uptodate')
    api.add_resource(StartTeamviewer, '/start_teamviewer')
