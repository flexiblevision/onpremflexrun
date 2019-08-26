from datetime import timedelta
from flask import make_response, request, current_app
from functools import update_wrapper

from flask import Flask
from flask_restful import Resource, Api
import json
from json import dumps

import subprocess
import os

from flask_cors import CORS
from flask import jsonify


app = Flask(__name__)
api = Api(app)

CORS(app)

class Shutdown(Resource):
    def get(self):
        print('shutting down system')
        os.system("power off")
        return True

class Restart(Resource):
    def get(self):
        print('restarting system')
        os.system("reboot")
        return True

class Upgrade(Resource):
    def get(self):
        print('upgrading system')
        os.system("sh ./upgrade_system.sh")
        return True


class AuthToken(Resource):
    def get(self):
        cmd = subprocess.Popen(['cat', 'creds.txt'], stdout=subprocess.PIPE)
        cmd_out, cmd_err = cmd.communicate()
        cleanStr = cmd_out.strip().decode("utf-8")
        if cleanStr: return cleanStr

    def post(self):
        j = request.json
        if j:
            os.system('echo '+j['refresh_token']+' > creds.txt')
            return True
        return False

class Networks(Resource):
    def get(self):
        networks = subprocess.check_output(['nmcli', '-f', 'SSID', 'dev', 'wifi'])
        nets = {}
        for i,line in enumerate(networks.splitlines()): nets[i] = line.decode('utf-8')
        return nets

    def post(self):
        j = request.json
        return os.system("nmcli dev wifi connect "+j['netName']+" password "+j['netPassword'])


api.add_resource(Networks, '/networks')
api.add_resource(AuthToken, '/auth_token')
api.add_resource(Shutdown, '/shutdown')
api.add_resource(Restart, '/restart')
api.add_resource(Restart, '/upgrade')


if __name__ == '__main__':
     app.run(host='0.0.0.0',port='5001')
