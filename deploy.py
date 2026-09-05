import getpass
import shutil
import sys
import subprocess
import os
import json
import time
import platform
from system_server.version_check import is_container_uptodate
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


def step_2():
    print("\033[0;36mStep (2/3) Pulling latest software & creating enviornment.")
    clear_text_color()
    time.sleep(2)
    backend_version = is_container_uptodate('backend')[1]
    frontend_version = is_container_uptodate('frontend')[1]
    prediction_version = is_container_uptodate('prediction')[1]
    predictlite_version = is_container_uptodate('predictlite')[1]
    vision_version = is_container_uptodate('vision')[1]
    creator_version = is_container_uptodate('nodecreator')[1]
    visiontools_version = is_container_uptodate('visiontools')[1]

    versions = [backend_version, frontend_version, prediction_version,
                predictlite_version, vision_version, creator_version,
                visiontools_version]

    # An empty version becomes "fvonprem/x86-backend:", which pulls a tag that
    # does not exist. The shell scripts quote their arguments so an empty one no
    # longer shifts the arch out of position, but it still cannot be pulled -
    # so say which component could not be resolved instead.
    missing = [name for name, value in zip(
        ('backend', 'frontend', 'prediction', 'predictlite', 'vision',
         'nodecreator', 'visiontools'), versions) if not value]
    if missing:
        print("\033[0;31mCould not work out a version for: {}".format(
            ', '.join(missing)))
        print("The version service may be unreachable. Nothing was installed.")
        clear_text_color()
        return 22

    # The exit code was discarded here, so a failed install fell through to
    # step 3 and was reported - at best - as "containers are not running".
    code = subprocess.call(["sh", "./scripts/local_setup.sh"] + versions)
    if code != 0:
        print("\033[0;31mSetup failed: {}".format(
            SETUP_ERRORS.get(code, 'local_setup.sh exited {}'.format(code))))
        clear_text_color()
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

    code = step_2()
    if code != 0:
        return code
    return step_3()


if __name__ == '__main__':
    # A non-zero exit so anything driving this can tell success from failure.
    sys.exit(main())
