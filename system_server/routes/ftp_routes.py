import os
import subprocess
from flask import request
from flask_restx import Resource
import auth

class AddFtpUser(Resource):
    @auth.requires_auth
    def post(self):
        data = request.json
        if 'username' and 'password' in data:
            home = os.environ['HOME']
            subprocess.call(["sh", home+"/flex-run/scripts/add_ftp_user.sh", data['username'], data['password']])
            return True
        return False

class DeleteFtpUser(Resource):
    @auth.requires_auth
    def delete(self):
        data = request.json

        if 'username' in data:
            os.system('sudo deluser -f '+data['username'])
            os.system('sudo rm -r /home/'+data['username'])
            return True
        return False

class UpdateFtpPort(Resource):
    @auth.requires_auth
    def put(self):
        data = request.json

        if 'port' in data:
            home = os.environ['HOME']
            port = int(data['port'])

            if port > 0:
                subprocess.call(["sh", home+"/flex-run/scripts/update_ftp.sh", "listen_port", str(port)])
            return True
        return False

class EnableFtp(Resource):
    @auth.requires_auth
    def post(self):
        data = request.json
        if 'port' in data:
            home = os.environ['HOME']
            subprocess.call(["sh", home+"/flex-run/setup/ftp_server_setup.sh"])
            return True
        return False

def register_routes(api):
    api.add_resource(AddFtpUser, '/add_ftp_user')
    api.add_resource(DeleteFtpUser, '/delete_ftp_user')
    api.add_resource(UpdateFtpPort, '/update_ftp_port')
    api.add_resource(EnableFtp, '/enable_ftp')
