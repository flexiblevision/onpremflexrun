import os
os.environ['GRPC_DNS_RESOLVER'] = 'native'

import sys
import threading
settings_path = os.environ['HOME']+'/flex-run'
sys.path.append(settings_path)
import settings
from FireOperator import FireOperator
from flask import Flask, jsonify, request
from flask_cors import CORS
import json
app = Flask(__name__)
CORS(app)

def update_config(config):
    PATH = os.environ['HOME']+'/fvconfig.json'
    if os.path.exists(PATH):
        with open(PATH, 'w') as outfile:
            json.dump(config, outfile, indent=4, sort_keys=True)


def set_launchpad():
    """Update Chrome autostart to splash page."""
    desktop_path = os.path.expanduser('/home/visioncell/.config/autostart/launchpad.html.desktop')
    if os.path.exists(desktop_path):
        with open(desktop_path, 'r') as f:
            lines = f.readlines()

        exec_line = f'Exec=google-chrome -kiosk --no-first-run --incognito --disable-web-security --user-data-dir=/tmp/chrome-kiosk "file:///home/visioncell/FV_APP/VISIONCELL_SETUP_ASSETS/FILES/fv_splash.html" &\n'

        with open(desktop_path, 'w') as f:
            for line in lines:
                if line.startswith('Exec='):
                    f.write(exec_line)
                else:
                    f.write(line)

        print('Updated Chrome autostart to splash page')

@app.route('/inspection_status', methods=['GET'])
def get_status():
        #data = request.json
        if settings.FireOperator:
            data = settings.FireOperator.get_status()
            return data, 200
        else:
            return 'Operator not running', 404

@app.route('/inspection_status', methods=['POST'])
def update_status():
    data = request.json
    if settings.FireOperator:
        settings.FireOperator.update_status(data)
        return 'Updated', 200
    else:
        return 'Operator not running', 404

@app.route('/aws_warehouse_zone', methods=['GET'])
def get_zone():
    results = {'warehouse': "", 'zone': ""}
    station = settings.config['fire_operator']['document']
    wz      = station.split('_')
    if len(wz) == 2:
        results['warehouse'] = wz[0]
        results['zone']      = wz[1]
    return results
    
def restart_server():
    os.system(f"forever restart {os.environ['HOME']}/flex-run/aws/fo_server.py")

def enable_setup():
    """Update Chrome autostart to setup page."""
    desktop_path = os.path.expanduser('/home/visioncell/.config/autostart/launchpad.html.desktop')
    if os.path.exists(desktop_path):
        with open(desktop_path, 'r') as f:
            lines = f.readlines()
        with open(desktop_path, 'w') as f:
            for line in lines:
                if line.startswith('Exec='):
                    f.write('Exec=google-chrome -kiosk --incognito http://localhost:3013/setup &\n')
                else:
                    f.write(line)

@app.route('/decommission', methods=['GET'])
def decommission():
    settings.config['fire_operator']['document'] = '_'
    update_config(settings.config)
    enable_setup()
    return 'Decommissioned', 200

@app.route('/aws_warehouse_zone', methods=['PUT'])
def update_zone():
    data = request.json
    if 'warehouse' in data and 'zone' in data:
        doc_key = f"{data['warehouse']}_{data['zone']}"
        settings.config['fire_operator']['document'] = doc_key
        update_config(settings.config)
        set_launchpad()
        threading.Timer(2.0, restart_server).start()
        return 'Updated', 200


if __name__ == '__main__':
    settings.FireOperator = FireOperator()
    app.run(host='0.0.0.0', port=5012)

