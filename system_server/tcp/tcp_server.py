import socket
import sys

import threading
import time
import requests
from ctypes import *
from pymongo import MongoClient
import datetime
import string
import json

client   = MongoClient("172.17.0.1")
io_ref   = client["fvonprem"]["io_presets"]
util_ref = client["fvonprem"]["utils"]
config_ref = client['fvonprem']['io_configs']

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_name    = '0.0.0.0'
server_address = (server_name, 5300)

print('starting server on port', server_address)
sock.bind(server_address)
sock.listen(1)

while True:
    print('Waiting for connection...')
    connections, client_address = sock.accept()
    try:
        print('neighbor connected: ', client_address)
        query = {'ioType': 'TCP'}
        presets = io_ref.find(query)
        valid_commands = {}
        config = config_ref.find_one({'type': 'tcp_config'})
        for preset in presets: valid_commands[preset['ioVal']] = preset
        while True:
            try:
                data = connections.recv(10)
                print('received: ', data)
                if data:
                    incoming_command = data.decode('utf-8')
                    if incoming_command in valid_commands.keys():
                        
                        preset  = valid_commands[incoming_command]
                        res     = util_ref.find_one({'type': 'id_token'}, {'_id': 0})
                        token   = res['token']
                        host    = 'http://172.17.0.1'
                        port    = '5000'
                        path    = '/api/capture/predict/snap/'+preset['modelName']+'/'+str(preset['modelVersion'])+'/'+str(preset['cameraId'])+'?workstation='+'TCP: '+client_address[0]+' '+preset['ioVal']
                        url     = host+':'+port+path
                        headers = {'Authorization': 'Bearer '+ token}
                        resp    = requests.get(url, headers=headers)

                        if resp:
                            data           = resp.json()
                            keys_to_remove = [k for k in config if not config[k] and k != 'packet_header']
                            for k in keys_to_remove: del data[k]

                            data_bytes = json.dumps(data).encode('utf-8')
                            packet_header = b''
                            if config['packet_header']:
                                packet_header = b'\x01'+str(len(data_bytes)).encode('utf-8')
                            data_bytes = packet_header+b'\x02'+data_bytes+b'\x03'+b'\x0d'
                            try:
                                connections.sendall(data_bytes)
                            except socket.error as msg:
                                print('failed', msg)
                                connections.sendall(b'-1')
                    else:
                        print('COMMAND INVALID')
                        connections.send(b'Invalid Command\n')
                else:
                    break
            except socket.error as msg:
                print('failed', msg)
    finally:
        connections.close()



    
