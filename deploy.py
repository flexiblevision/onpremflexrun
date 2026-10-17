import getpass
import glob
import shutil
import site
import sys
import subprocess
import os
import json
import time
import platform
from setup.management import generate_environment_config

def clear_text_color():
    print("\033[0m")

# Both of these read $FLEXRUN_NET_CONFIG, a two-line file: interface, then
# address. They used to open an undefined `path_ref` inside a bare except, so
# every call quietly returned the hardcoded default and the file was never
# read - and both read the *same* path, so the interface name would have been
# an IP address. Harmless only because set_static_ip() is not called.
NET_CONFIG = os.environ.get('FLEXRUN_NET_CONFIG', '/etc/flexrun/network')
DEFAULT_INTERFACE = 'enp0s31f6'
DEFAULT_STATIC_IP = '192.168.10.35'


def _net_config():
    """(interface, address) from the config file, or the defaults."""
    try:
        with open(NET_CONFIG) as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return DEFAULT_INTERFACE, DEFAULT_STATIC_IP
    interface = lines[0] if len(lines) > 0 else DEFAULT_INTERFACE
    address = lines[1] if len(lines) > 1 else DEFAULT_STATIC_IP
    return interface, address

def set_static_ip():
    interface_name, ip = _net_config()
    
    os.system('sudo ifconfig ' + interface_name + ' '  + ip + ' netmask 255.255.255.0')
    with open ('/etc/netplan/fv-net-init.yaml', 'w') as f:
        f.write('network:\n')
        f.write('  version: 2\n')
        f.write('  renderer: NetworkManager\n')
        f.write('  ethernets:\n')
        f.write('    '+interface_name+':\n')
        f.write('      dhcp4: false\n')
        f.write('      mtu: 9000\n')
        f.write('      addresses: ['+ip+'/24]')

    os.system("sudo netplan apply")

# Everything system_setup.sh verifies. The old list was capdev, localprediction
# and captureui only, so setup could report success with vision, nodecreator and
# predictlite all dead.
EXPECTED_CONTAINERS = ('mongo', 'capdev', 'captureui', 'localprediction',
                       'predictlite', 'vision', 'nodecreator')


def container_state(container):
    """True, False, or None when the container does not exist.

    A missing container makes `docker inspect` write nothing to stdout, and the
    previous json.loads('') raised inside step_3 - so the branch that exists to
    report a failed install was itself the thing that crashed.
    """
    try:
        result = subprocess.run(
            ['docker', 'inspect', '--format', '{{.State.Running}}', container],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    answer = (result.stdout or '').strip()
    if answer == 'true':
        return True
    if answer == 'false':
        return False
    return None


def not_running(containers=EXPECTED_CONTAINERS):
    """Which containers are missing or stopped, so the caller can name them."""
    broken = []
    for container in containers:
        if container_state(container) is not True:
            broken.append(container)
    return broken


def containers_running(containers=EXPECTED_CONTAINERS):
    return not not_running(containers)

def query_yes_no(question, default="yes"):
    """Ask a yes/no question via raw_input() and return their answer.

    "question" is a string that is presented to the user.
    "default" is the presumed answer if the user just hits <Enter>.
        It must be "yes" (the default), "no" or None (meaning
        an answer is required of the user).

    The "answer" return value is True for "yes" or False for "no".
    """
    valid = {"yes": True, "y": True, "ye": True,
             "no": False, "n": False}
    if default is None:
        prompt = " [y/n] "
    elif default == "yes":
        prompt = " [Y/n] "
    elif default == "no":
        prompt = " [y/N] "
    else:
        raise ValueError("invalid default answer: '%s'" % default)

    while True:
        sys.stdout.write(question + prompt)
        choice = input().lower()
        if default is not None and choice == '':
            return valid[default]
        elif choice in valid:
            return valid[choice]
        else:
            sys.stdout.write("Please respond with 'yes' or 'no' "
                             "(or 'y' or 'n').\n")

def choose_release_track():
    """prod or dev - which cloud, and which release channel the device follows.

    prod is the default and the answer to a bare Enter: a device that ends up
    on dev by accident takes releases before the fleet does, and on a factory
    floor that is discovered by the line going down.
    """
    while True:
        print("\n1 [prod]: Production - follows the stable release channel")
        print("2 [dev]:  Development - clouddeploy, follows the beta channel")
        var = input("Please select 1 or 2 (1=prod, 2=dev) >  ").strip()
        if var in ('', '1'):
            return 'prod'
        if var == '2':
            print("\033[0;33mThis device will take beta releases before the "
                  "fleet does.")
            clear_text_color()
            return 'dev'
        print("Please respond with '1' or '2'")


def choose_environment():
    #wait for user choice and generate config based on choice
    choice = None
    while choice == None:
        print("1 [default]: Connect onprem system to Flexible Vision Cloud")
        print("2 [cluster]: Connect onprem system to local running Flexible Vision cluster")
        var = input("Please select environment option 1 or 2 (1=default, 2=cluster) >  ")
        if var == '1':
            choice = 'cloud'
        elif var == '2':
            choice = 'local'
        else:
            print("Please respond with '1' or '2'")

        if choice:
            print('Setting up {} environment'.format(choice))

    track = choose_release_track()
    print('Setting up {} environment on the {} release track'.format(
        choice, track))
    generate_environment_config(choice, True, release_track=track)
    return choice, track

# LAUNCH STEPS---------------------
def step_1():
    choose_environment()

    print("\033[0;36mStep (1/3) Setting up internet connection.")
    #set_static_ip()  #conflicts with arm
    print("\033[0;33mChecking internet connection...\n")
    time.sleep(2)
    if check_connection():
        print('\033[0;32mOnline.')
    else:
        setup_wifi()
        
    clear_text_color()

# system_setup.sh exit codes, so a failure can be named instead of guessed at.
SETUP_ERRORS = {
    20: 'bad arguments - a container version was missing or the arch is unsupported',
    21: 'bad configuration - check ~/fvconfig.json',
    22: 'could not pull one or more images - check the network and Docker login',
    23: 'a container failed to start',
    24: 'containers started but did not come up healthy',
}


COMPONENTS = ('backend', 'frontend', 'prediction', 'predictlite', 'vision',
              'nodecreator', 'visiontools')

PLAN_PATH = os.environ.get('FLEXRUN_PLAN', '/var/lib/flex-run/plan')

ARCHES = {'x86_64': 'x86', 'aarch64': 'arm'}


def device_arch():
    return ARCHES.get(platform.machine(), platform.machine())


def device_channel():
    """The release channel this install follows, from the config step_1 wrote."""
    try:
        with open(os.path.join(os.environ['HOME'], 'fvconfig.json')) as handle:
            channel = json.load(handle).get('release_channel')
    except (OSError, ValueError, KeyError):
        return 'stable'
    return channel if channel in ('stable', 'beta') else 'stable'


def release_plan(arch=None, channel=None, plan_path=None, now=None):
    """Fetch and verify the release this device's channel names.

    Returns a plan dict, or None when there is nothing to install from - an
    unreachable endpoint or an empty channel, both of which are ordinary states
    that fall back to the version endpoint.

    A signature that does not verify is NOT one of those: it raises. Falling
    back there would install the same software unverified, which makes the
    signature advisory - the one thing it must never be.
    """
    import datetime

    from release import apply as apply_mod
    from release import fetch as fetch_mod
    from release import trust as trust_mod
    from release import verify as verify_mod

    arch = arch or device_arch()
    channel = channel or device_channel()
    plan_path = plan_path or PLAN_PATH
    now = now or datetime.datetime.utcnow()
    trust_dir = os.environ.get('FLEXRUN_TRUST_DIR', trust_mod.DEFAULT_TRUST_DIR)

    try:
        raw, signature, _envelope = fetch_mod.fetch_release(arch, channel=channel)
    except fetch_mod.FetchError as exc:
        print("\033[0;33mNo signed release on '{}' ({}).".format(channel, exc))
        print("Falling back to the version endpoint.")
        clear_text_color()
        return None

    directory = os.path.dirname(os.path.abspath(plan_path)) or '.'
    manifest_file = os.path.join(directory, 'manifest.json')
    signature_file = os.path.join(directory, 'manifest.sig')
    try:
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(manifest_file, 'wb') as handle:
            handle.write(raw)
        with open(signature_file, 'w') as handle:
            handle.write(signature)
    except OSError as exc:
        print("\033[0;33mCannot write {} ({}).".format(directory, exc))
        print("Falling back to the version endpoint - this install will pull by")
        print("tag rather than by digest.")
        clear_text_color()
        return None

    # high_water 0: this device has accepted nothing, so no counter is a
    # rollback. What it installs is recorded afterwards, and every later
    # upgrade is then an ordinary monotonic comparison.
    parsed = verify_mod.verify(
        raw, arch, 0, now,
        manifest_path=manifest_file, signature_path=signature_file,
        public_key_path=trust_dir)

    plan = apply_mod.plan(parsed, arch, current={}, plan_path=plan_path)
    plan['manifest'] = parsed
    return plan


def record_installed_release(parsed_counter_source):
    """Write the installed release into the device state, after the containers
    are up - so an install that failed part way does not claim a release."""
    try:
        from release import state as state_mod
        from pymongo import MongoClient
        client = MongoClient(os.environ.get('MONGO_SERVER', '172.17.0.1'),
                             int(os.environ.get('MONGO_PORT', 27017)),
                             serverSelectionTimeoutMS=5000)
        state_mod.record_applied(client['fvonprem']['utils'],
                                 parsed_counter_source)
        return True
    except Exception as exc:
        print("\033[0;33mInstalled, but could not record the release: {}".format(exc))
        print("The next upgrade will treat this device as having no release yet.")
        clear_text_color()
        return False

# What is_container_uptodate returns when it decides nothing needs pulling.
UPTODATE_SENTINEL = 'True'


def legacy_versions():
    """Versions from latest_stable_ref - the fallback when no release applies.

    Returns (versions, error_code). A non-zero code means nothing is safe to
    install and the caller must stop.
    """
    from system_server.version_check import CONTAINERS, is_container_uptodate

    versions = [is_container_uptodate(name)[1] for name in COMPONENTS]

    missing = [name for name, value in zip(COMPONENTS, versions) if not value]
    if missing:
        print("\033[0;31mCould not work out a version for: {}".format(
            ', '.join(missing)))
        print("The version service may be unreachable. Nothing was installed.")
        clear_text_color()
        return versions, 22

    # is_container_uptodate returns the string 'True' both for "already current"
    # and for "the endpoint did not answer" - and on a container that does not
    # exist yet, only the second can be true. Left alone it becomes
    # `docker pull fvonprem/x86-backend:True`.
    unresolved = [name for name, value in zip(COMPONENTS, versions)
                  if value == UPTODATE_SENTINEL
                  and container_state(CONTAINERS[name]) is None]
    if unresolved:
        print("\033[0;31mThe version service says these need nothing, but they "
              "are not installed:")
        print("\033[0;31m  {}".format(', '.join(unresolved)))
        print("That means it could not be reached, or it serves no version for")
        print("this device's release track. Check latest_stable_ref in "
              "~/fvconfig.json.")
        clear_text_color()
        return versions, 22

    return versions, 0


def step_2():
    print("\033[0;36mStep (2/3) Pulling latest software & creating enviornment.")
    clear_text_color()
    time.sleep(2)

    plan = release_plan()
    if plan is not None:
        print("\033[0;32mInstalling release {} (counter {}) from the {} "
              "channel.".format(plan['release'], plan['counter'],
                                device_channel()))
        clear_text_color()
        os.environ['FLEXRUN_PLAN'] = plan['plan_path']
        versions = plan['versions']
    else:
        plan = None
        versions, code = legacy_versions()
        if code != 0:
            return code

    code = subprocess.call(["sh", "./scripts/local_setup.sh"] + versions)
    if code != 0:
        print("\033[0;31mSetup failed: {}".format(
            SETUP_ERRORS.get(code, 'local_setup.sh exited {}'.format(code))))
        clear_text_color()
        return code

    if plan is not None and not not_running():
        record_installed_release(plan['manifest'])
    return code


def step_3():
    broken = not_running()
    if not broken:
        print("\033[0;36mStep (3/3) Launch application & setup device.")
        clear_text_color()
        print("Launch - http://<host ip>")
        return 0

    print("\033[0;31mStep 2 did not complete - these containers are not "
          "running:")
    for name in broken:
        state = container_state(name)
        print("\033[0;31m  {:<16} {}".format(
            name, 'not created' if state is None else 'stopped'))
    print("\033[0;31mCheck 'docker logs <name>', then retry setup.")
    clear_text_color()
    return 24


# WIFI LOGIC-----------------
def display_connection_results():
    print('\033[0;32mInternet connected.') if check_connection() else print('\033[0;31mInternet not connected.')
    clear_text_color()

# What setup actually needs to reach: the registry the images come from and
# the functions proxy. Pinging google.com tested neither - a device on an
# isolated factory network with a working route to both was told "Wi-Fi not
# connected" and setup stopped. ICMP is also commonly blocked where HTTPS is not.
REACHABILITY_TARGETS = (
    'https://registry-1.docker.io/v2/',
    'https://functions-proxy.flexiblevision.com/',
)


def check_connection(targets=REACHABILITY_TARGETS, timeout=10):
    """True if any target answers at all - including 401, which means reached."""
    for url in targets:
        try:
            result = subprocess.run(
                ['curl', '-sS', '-o', '/dev/null', '-m', str(timeout), url],
                capture_output=True, text=True, timeout=timeout + 5)
        except (OSError, subprocess.SubprocessError):
            continue
        # Any HTTP answer means the network path works; 401 from the registry
        # is a reachable registry, not a broken network.
        if result.returncode in (0, 22):
            return True
    return False

def connect_wifi(wifi, password):
    """No shell. An SSID or password containing a space used to break the
    command, and one containing ; or $(...) used to execute."""
    print("\n")
    print('\033[0;33mConnecting to ' + wifi)
    result = subprocess.run(
        ['nmcli', 'dev', 'wifi', 'connect', wifi, 'password', password],
        capture_output=True, text=True)
    if result.returncode != 0:
        # nmcli puts the useful part on stdout, not stderr.
        detail = (result.stdout or result.stderr or '').strip().splitlines()
        print("\033[0;31mCould not connect to {}{}".format(
            wifi, ': ' + detail[-1] if detail else ''))
    clear_text_color()
    time.sleep(3)

def retry_prompt(cycles):
    if cycles > 0:
        return query_yes_no('Retry setup?', default="yes")
    return True

def setup_wifi():
    print("Turning on Wi-Fi & scanning...\n")
    os.system("nmcli radio wifi on")
    time.sleep(4)
    os.system("nmcli d wifi list")
    print("\n")
    cycles = 0
    while not check_connection() and retry_prompt(cycles) :
        wifi = input("Enter wifi SSID from list above: ").strip()
        # getpass, not input: the password was echoed to the screen and left in
        # the scrollback of whatever terminal set the device up.
        password = getpass.getpass("Enter wifi password (hidden): ")
        connect_wifi(wifi, password)
        cycles += 1
    display_connection_results()


REQUIREMENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'requirements.txt')

DEPS_PROBE = 'import requests'

DEPS_FAILED = 25


def _as_root(argv):
    return argv if os.geteuid() == 0 else ['sudo'] + argv


def _probe_stderr(probe=DEPS_PROBE):
    """(ok, stderr) for the imports step_2 will make. Subprocess, so a failed
    import cannot poison sys.modules for the retry."""
    try:
        result = subprocess.run([sys.executable, '-c', probe],
                                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, '{}: {}'.format(type(exc).__name__, exc)
    if result.returncode == 0:
        return True, ''
    return False, (result.stderr or '').strip() or 'exit {}'.format(
        result.returncode)


def probe_python_deps(probe=DEPS_PROBE):
    """(ok, one-line detail) for the imports step_2 will make."""
    ok, stderr = _probe_stderr(probe)
    if ok:
        return True, ''
    lines = stderr.splitlines()
    return False, lines[-1] if lines else stderr


def _pip_site_dirs():
    """The directories pip installs into on this interpreter."""
    dirs = []
    try:
        dirs.extend(site.getsitepackages())
    except AttributeError:
        pass
    try:
        dirs.append(site.getusersitepackages())
    except (AttributeError, TypeError):
        pass
    return [d for d in dirs if d and os.path.isdir(d)]


def broken_packages(stderr_text):
    """(site_dir, name) for every installed package a traceback blames.

    The traceback names the file each frame is in, so a failing import points
    straight at the packages whose files are inconsistent.
    """
    found = []
    for site_dir in _pip_site_dirs():
        marker = site_dir.rstrip(os.sep) + os.sep
        for line in stderr_text.splitlines():
            start = line.find(marker)
            if start == -1:
                continue
            name = line[start + len(marker):].split(os.sep)[0]
            entry = (site_dir, name)
            if entry in found or not name:
                continue
            if os.path.isdir(os.path.join(site_dir, name)):
                found.append(entry)
    return found


def purge_packages(packages):
    """Delete each package's directory and every version's metadata.

    --ignore-installed writes a new version's files over an old one without
    uninstalling it, so one directory ends up holding two versions: a compiled
    module left behind by the version that was replaced still shadows the .py
    that replaced it, and the import dies on a symbol that moved between them.
    pip cannot repair this - it reads the metadata, sees the pin as already
    satisfied and does nothing - so the files have to go before the reinstall.
    """
    removed = []
    for site_dir, name in packages:
        targets = [os.path.join(site_dir, name)]
        for pattern in ('-*.dist-info', '-*.egg-info'):
            targets.extend(glob.glob(os.path.join(site_dir, name + pattern)))
        for target in targets:
            if os.path.exists(target) and subprocess.call(
                    _as_root(['rm', '-rf', target])) == 0:
                removed.append(target)
    return removed


def pip_argv():
    """pip as this interpreter, with --break-system-packages where supported."""
    argv = [sys.executable, '-m', 'pip', 'install']
    try:
        helped = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--help'],
            capture_output=True, text=True, timeout=60)
        if '--break-system-packages' in (helped.stdout or ''):
            argv.append('--break-system-packages')
    except (OSError, subprocess.SubprocessError):
        pass
    return argv


def ensure_pip():
    """True if this interpreter can run pip, installing python3-pip if not."""
    try:
        if subprocess.run([sys.executable, '-m', 'pip', '--version'],
                          capture_output=True, timeout=60).returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass

    print("\033[0;33mpip is not installed - installing python3-pip...")
    clear_text_color()
    try:
        subprocess.call(_as_root(['apt-get', 'install', '-y', 'python3-pip']))
        return subprocess.run([sys.executable, '-m', 'pip', '--version'],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_python_deps():
    """Install what step_2 imports, before step_2 imports it."""
    ok, detail = probe_python_deps()
    if ok:
        return 0

    print("\033[0;33mPython dependencies are missing or broken:")
    print("\033[0;33m  {}".format(detail))
    print("Installing from {}...".format(REQUIREMENTS))
    clear_text_color()

    if not os.path.exists(REQUIREMENTS):
        print("\033[0;31m{} is missing - this deploy tree is incomplete.".format(
            REQUIREMENTS))
        clear_text_color()
        return DEPS_FAILED

    if not ensure_pip():
        print("\033[0;31mpip is not available and python3-pip could not be "
              "installed.")
        clear_text_color()
        return DEPS_FAILED

    code = subprocess.call(_as_root(pip_argv() + ['-r', REQUIREMENTS]))
    if code != 0:
        print("\033[0;31mInstalling dependencies failed (pip exited {}).".format(code))
        clear_text_color()
        return DEPS_FAILED

    ok, detail = probe_python_deps()
    if ok:
        print("\033[0;32mDependencies installed.")
        clear_text_color()
        return 0

    # Installing did not fix it, so the metadata pip trusts disagrees with the
    # files on disk. Clear out the packages the traceback blames and install
    # them again from scratch.
    ok, stderr = _probe_stderr()
    packages = broken_packages(stderr)
    if packages:
        print("\033[0;33mTwo versions of a package are installed in one "
              "directory. Removing and reinstalling:")
        for _, name in packages:
            print("\033[0;33m  - {}".format(name))
        clear_text_color()
        purge_packages(packages)

        code = subprocess.call(_as_root(pip_argv() + ['-r', REQUIREMENTS]))
        if code == 0:
            ok, detail = probe_python_deps()
            if ok:
                print("\033[0;32mDependencies repaired.")
                clear_text_color()
                return 0

    print("\033[0;31mDependencies still do not import after installing:")
    print("\033[0;31m  {}".format(detail))
    print("This is usually a half-finished upgrade leaving two versions of a")
    print("package in one directory. Remove that package's folder and every")
    print("matching *.dist-info from the path named above, then run setup again.")
    clear_text_color()
    return DEPS_FAILED


def preflight():
    """What has to be true before anything is installed.

    Each of these used to surface part-way through as a confusing failure: no
    docker means every pull fails, no sudo means the netplan write and the
    container starts fail, and running out of disk part-way leaves a half-built
    device - the vision base image alone is over 5GB.
    """
    problems = []

    if not shutil.which('docker'):
        problems.append('docker is not installed')
    else:
        info = subprocess.run(['docker', 'info'], capture_output=True, text=True)
        if info.returncode != 0:
            problems.append('the docker daemon is not running or not reachable')

    if os.geteuid() != 0 and not shutil.which('sudo'):
        problems.append('not running as root and sudo is not available')

    try:
        free_gb = shutil.disk_usage('/').free / (1024 ** 3)
        if free_gb < 20:
            problems.append(
                'only {:.1f}GB free on / - the images need roughly 20GB'
                .format(free_gb))
    except OSError:
        pass

    return problems


# MAIN---------------------
def main():
    print("\n\n\n")
    print("        Welcome to the Flexible Vision On Prem Setup")
    print("=============================================================\n")
    time.sleep(2)

    if platform.system() != 'Linux':
        print("\033[0;31mYou must be running linux to setup this program.")
        clear_text_color()
        return 1

    problems = preflight()
    if problems:
        print("\033[0;31mCannot set up this device yet:")
        for problem in problems:
            print("\033[0;31m  - {}".format(problem))
        clear_text_color()
        return 21

    step_1()
    if not check_connection():
        print("\033[0;31mNo route to the image registry or the update service.")
        print("\033[0;31mConnect this device to a network that can reach them, "
              "then retry setup.")
        clear_text_color()
        return 1

    code = ensure_python_deps()
    if code != 0:
        return code

    code = step_2()
    if code != 0:
        return code
    return step_3()


if __name__ == '__main__':
    # A non-zero exit so anything driving this can tell success from failure.
    if '--repair-deps' in sys.argv[1:]:
        # For the upgrade path, which installs over a running device and has
        # no technician in front of it.
        sys.exit(ensure_python_deps())
    sys.exit(main())
