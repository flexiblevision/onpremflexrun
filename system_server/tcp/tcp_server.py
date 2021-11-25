import socket
import sys
import os
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
pin_state_ref = client["fvonprem"]["pin_state"]


so_file = os.environ['HOME']+"/flex-run/system_server/gpio/gpio.so"
functions = CDLL(so_file)


def take_action(actions):
    params = ''
    for key in actions.keys():
        if key == 'did':
            params+='&did='+str(actions[key])

    return params 

def set_pass_fail_pins(data):
    if 'pass_fail' in data:
        new_pin_state = {}
        print(data['pass_fail'], ' <======================')
        if data['pass_fail'] == 'PASS':
            #set pass pin
            print(functions.set_gpio(1, 5, 0), 'PASS PIN ON')
            new_pin_state['GPO5'] = True
        if data['pass_fail'] == 'FAIL':
            #set fail pin
            print(functions.set_gpio(1, 6, 0), 'FAIL PIN ON')
            new_pin_state['GPO6'] = True

        pin_state_ref.update_one({'type': 'gpio_pin_state'}, {'$set': new_pin_state}, True)
        time.sleep(.5)

        print(functions.set_gpio(1, 5, 1), 'PASS PIN OFF')
        print(functions.set_gpio(1, 6, 1), 'FAIL PIN OFF')
        new_pin_state['GPO5'] = False
        new_pin_state['GPO6'] = False
        pin_state_ref.update_one({'type': 'gpio_pin_state'}, {'$set': new_pin_state}, True)
        return data['pass_fail']
    return


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
                data = connections.recv(100)
                print('received: ', data)
                if data:
                    incoming_command = data.decode('utf-8')
                    command          = None
                    actions          = None
                    params           = ''
                    try:
                        incoming_command = json.loads(incoming_command)
                        command          = list(incoming_command.keys())[0]
                        actions          = incoming_command[command]
                        params           = take_action(actions)
                    except:
                        print('INVALID COMMAND PARSE')

                    if command in valid_commands.keys():
                        preset  = valid_commands[command]
                        res     = util_ref.find_one({'type': 'id_token'}, {'_id': 0})
                        token   = res['token']
                        host    = 'http://172.17.0.1'
                        port    = '5000'
                        path    = '/api/capture/predict/snap/'+preset['modelName']+'/'+str(preset['modelVersion'])+'/'+str(preset['cameraId'])+'?workstation='+'TCP: '+client_address[0]+':'+preset['ioVal']+'&preset_id='+str(preset['presetId'])+params
                        url     = host+':'+port+path
                        headers = {'Authorization': 'Bearer '+ token}
                        resp    = requests.get(url, headers=headers)

                        if resp.status_code == 200:
                            data           = resp.json()
                            try:
                                set_pass_fail_pins(data)
                            except:
                                print('failed to set pass fail pins')
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
                            connections.send(b'request failed\n')
                            
                    else:
                        print('COMMAND INVALID')
                        connections.send(b'Invalid Command\n')
                else:
                    break
            except socket.error as msg:
                print('failed', msg)
    finally:
        connections.close()



    
