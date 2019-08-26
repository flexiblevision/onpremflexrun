import getpass
import sys
import subprocess
import os
import http.client
import json
import time
import platform

# def deploy_local_cam():
#     check_gcp_login()
#     exists = os.path.isfile('cloud_config/input.tfvars')
#     if not exists:
#         sys.stdout.write("File cloud_config/output.tfvars does not exist.\n")
#         sys.stdout.write("Deploy Cloud Components before continuing.\n")
#     p = query_yes_no("Deploy a Flexible Vision local camera server?")
#     if p:
#         sys.stdout.write("######################      Warning     ########################\n")
#         sys.stdout.write("Local Camera Servers Must :\n")
#         sys.stdout.write("\t1. Have A local NVIDIA Graphics Card\n")
#         sys.stdout.write("\t2. Be Running Ubuntu 18.04\n")
#         sys.stdout.write("\t3. Be Available through SSH\n")
#         sys.stdout.write("\t4. Have a USB Camera Attached\n")
#         p = query_yes_no("Does your local camera server meet these requirements ? ")
#         if p:
#             sys.stdout.write("What is the IP Address or Host Name of the local camera server ? ")
#             ip = input().lower()
#             sys.stdout.write("What is the SSH username to attach to the local camera server ? ")
#             user = input().lower()
#             password = getpass.getpass("What is the SSH Password to access the local camera server ? ")
#             with open ("cloud_config/output.tfvars", "r") as varfile:
#                 config = varfile.read()
#             config += "camera_server = \"{}\"\n".format(ip)
#             config += "ssh_username = \"{}\"\n".format(user)
#             config += "ssh_password = \"{}\"\n".format(password)
#             with open ("/tmp/input.tfvars", "w") as varfile:
#                 varfile.write(config)
#             subprocess.call([terraform,"init","local_setup/"])
#             subprocess.call([terraform,"apply","-var-file=/tmp/input.tfvars","-auto-approve","-state=local_config_{}/terraform.tfstate".format(ip),"local_setup/"])
#             os.remove("/tmp/input.tfvars")

# UTIL FUNCTIONS------------------
def clear_text_color():
    print("\033[0m")

def containers_running():
    containers = ['capdev', 'localprediction', 'captureui', 'gcs-s3']
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

# LAUNCH STEPS---------------------
def step_1():
    print("\033[0;36mStep (1/3) Setting up internet connection.")
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
    subprocess.call(["sh", "./scripts/local_setup.sh"])

def step_3():
    if containers_running():
        print("\033[0;36mStep (3/3) Launch application & setup device.")
        clear_text_color()
        print("Launch - http://localhost:3000")
    else:
        print("\033[0;31m Step 2 did not complete, please retry setup.")
        clear_text_color()


# WIFI LOGIC-----------------
def display_connection_results():
    print('\033[0;32mWi-Fi connected.') if check_connection() else print('\033[0;31mWi-Fi not connected.')
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
        os.system("nmcli dev Wi-Fi connect "+wifi+" password "+password)
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
