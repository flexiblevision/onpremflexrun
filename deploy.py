import getpass
import sys
import subprocess
import os
import http.client
import json
import time
import platform
from system_server.version_check import is_container_uptodate
from setup.management import generate_environment_config

def clear_text_color():
    print("\033[0m")

def get_static_ip_ref():
    static_ip  = '192.168.10.35'
    try:
        with open(path_ref, 'r') as file:
            static_ip = file.read().replace('\n', '')
    except: return static_ip
    return static_ip

def get_interface_name_ref():
    interface_name  = 'enp0s31f6'
    try:
        with open(path_ref, 'r') as file:
            interface_name = file.read().replace('\n', '')
    except: return interface_name
    return interface_name

def set_static_ip():
    ip             = get_static_ip_ref()
    interface_name = get_interface_name_ref()
    
    os.system('sudo ifconfig ' + interface_name + ' '  + ip + ' netmask 255.255.255.0')
    with open ('/etc/netplan/fv-net-init.yaml', 'w') as f:
        f.write('network:\n')
        f.write('  version: 2\n')
        f.write('  ethernets:\n')
        f.write('    '+interface_name+':\n')
        f.write('      dhcp4: false\n')
        f.write('      mtu: 9000\n')
        f.write('      addresses: ['+ip+'/24]')

    os.system("sudo netplan apply")

def containers_running():
    containers = ['capdev', 'localprediction', 'captureui']
    running = []
    for container in containers:
        state = subprocess.Popen(
            ['docker', 'inspect', '--format', '{{.State.Running}}', container],
            stdout=subprocess.PIPE)
        data = state.stdout.read().decode()
        running.append(json.loads(data))

    return all(running)

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

def choose_environment():
    #wait for user choice and generate config based on choice
    choice = None
    while choice == None:
        var = input("Please select environment option 1 or 2 (1=cloud, 2=local) >  ")
        if var == '1':
            choice = 'cloud'
        elif var == '2':
            choice = 'local'
        else:
            print("Please respond with '1' or '2'")

        if choice:
            print('Setting up {} environment'.format(choice))

    generate_environment_config(choice, True)

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

    subprocess.call([
        "sh", 
        "./scripts/local_setup.sh", 
        backend_version, 
        frontend_version, 
        prediction_version, 
        predictlite_version, 
        vision_version,
        creator_version,
        visiontools_version
    ])

def step_3():
    if containers_running():
        print("\033[0;36mStep (3/3) Launch application & setup device.")
        clear_text_color()
        print("Launch - http://<host ip>")
    else:
        print("\033[0;31m Step 2 did not complete, please retry setup.")
        clear_text_color()


# WIFI LOGIC-----------------
def display_connection_results():
    print('\033[0;32mInternet connected.') if check_connection() else print('\033[0;31mInternet not connected.')
    clear_text_color()

def check_connection():
    try:
        subprocess.check_output(['ping', '-c', '4', 'google.com'])
        return True
    except subprocess.CalledProcessError as e:
        return False

def connect_wifi(wifi, password):
    print("\n")
    print('\033[0;33mConnecting to ' + wifi)
    try:
        os.system("nmcli dev wifi connect "+wifi+" password "+password)
        clear_text_color()
        time.sleep(3)
    except subprocess.CalledProcessError as e:
        print("\033[0;31m Could not connect to "+ wifi+"...")
        clear_text_color()

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
        print("Enter wifi SSID from list above:")
        wifi = input()
        print("Enter wifi password:")
        password = input()
        connect_wifi(wifi, password)
        cycles += 1
    display_connection_results()


# MAIN---------------------
def main():
    print("\n\n\n")
    print("        Welcome to the Flexible Vision On Prem Setup")
    print("=============================================================\n")
    time.sleep(2)
    if platform.system() == 'Linux':
        step_1()
        if check_connection():
            step_2()
            step_3()
        else:
            print("\033[0;31m Wi-Fi not connected. Please retry setup process.")
            clear_text_color()
    else:
        print("\033[0;31mYou must be running linux to setup this program.")
        clear_text_color()


if __name__ == '__main__':
    main()
