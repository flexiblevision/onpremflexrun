import threading
import os
import time
import requests
from ctypes import *
from pymongo import MongoClient
import datetime
import string
import json
from bson import json_util, ObjectId

# Import Flask and Flask-SocketIO
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit

# --- Flask and SocketIO Setup ---
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- Database Connections ---
client   = MongoClient("172.17.0.1")
io_ref   = client["fvonprem"]["io_presets"]
util_ref = client["fvonprem"]["utils"]
pin_state_ref = client["fvonprem"]["pin_state"]
pass_fail_ref = client["fvonprem"]["pass_fail"]

# --- GPIO Library Loading ---
so_file = os.environ.get('HOME', '') + "/flex-run/system_server/gpio/gpio.so"
if not os.path.exists(so_file):
    print(f"GPIO shared library not found at: {so_file}. Please ensure it's correctly built and located.")
    functions = None
else:
    functions = CDLL(so_file)


#(<direction>,<pin_index>,<value>)
# direction - IN  = 0
# direction - OUT = 1

# value - HIGH = 1
# value - LOW  = 0

def ms_timestamp():
    return int(datetime.datetime.now().timestamp()*1000)

class GPIO:
    def __init__(self):
        self.state_query      = {'type': 'gpio_pin_state'}
        self.cur_pin_state    = pin_state_ref.find_one(self.state_query)
        if not self.cur_pin_state:
            self.cur_pin_state = {
                'GPO1': False, 'GPO2': False, 'GPO3': False, 'GPO4': False,
                'GPO5': False, 'GPO6': False, 'GPO7': False, 'GPO8': False,
                'GPI1': False, 'GPI2': False, 'GPI3': False, 'GPI4': False,
                'GPI5': False, 'GPI6': False, 'GPI7': False, 'GPI8': False,
            }
            pin_state_ref.insert_one(self.cur_pin_state)
        else:
            del self.cur_pin_state['_id']
            del self.cur_pin_state['type']

        self.last_input_state = "wait"
        self.debounce_delay   = .001
        self.last_pin_state   = None 
        self.set_gpio_func = functions.set_gpio
        self.read_gpi_func = functions.read_gpi
        self.read_gpo_func = functions.read_gpo
        self.current_output_state = [
            self.read_gpo_func(1),
            self.read_gpo_func(2),
            self.read_gpo_func(3),
            self.read_gpo_func(4),
            self.read_gpo_func(5),
            self.read_gpo_func(6),
            self.read_gpo_func(7),
            self.read_gpo_func(8)
        ]


    def _set_gpio(self, direction, pin_index, value):
        """Wrapper for set_gpio with error handling."""
        if self.set_gpio_func:
            return self.set_gpio_func(direction, pin_index, value)
        return -1 # Indicate failure if function not loaded

    def _read_gpi(self, pin_index):
        """Wrapper for read_gpi with error handling."""
        if self.read_gpi_func:
            return self.read_gpi_func(pin_index)
        return -1 # Indicate failure if function not loaded

    def get_pass_fail_entry(self, model, version):
        query = {'modelName': model, 'modelVersion': version}
        res   = pass_fail_ref.find(query)
        data  = json.loads(json_util.dumps(res))
        if not data: return False
        return data[0]

    def run_inference(self, preset, pin):
        cameraId, modelName, modelVersion = preset['cameraId'], preset['modelName'], preset['modelVersion']
        ioVal, presetId                   = preset['ioVal'], preset['presetId']
        server = preset['server'] if 'server' in preset else 'vision'

        res     = util_ref.find_one({'type': 'id_token'}, {'_id': 0})
        token   = res['token']
        host    = 'http://172.17.0.1'
        port    = '5000'
        path    = '/api/capture/predict/snap/'+str(modelName)+'/'+str(modelVersion)+'/'+str(cameraId)+'?workstation='+str(ioVal)+'&preset_id='+str(presetId)

        if server == 'thermal':
            tport = '5400'
            tpath = '/api/ir/vision/b64Frame/'+str(cameraId)

            t_url     = host+':'+tport+tpath
            headers = {'Authorization': 'Bearer '+ token}
            try:
                t_res  = requests.get(t_url, headers=headers, timeout=2)
                data = t_res.json()

                path = '/api/capture/predict/single_inference/1/1?preset_id='+str(presetId)
                url  = host+':'+port+path
                res  = requests.put(url, json=data, headers=headers, timeout=2)

            except Exception as error:
                print(error)
                return

        else:
            url     = host+':'+port+path
            headers = {'Authorization': 'Bearer '+ token}
            try:
                res  = requests.get(url, headers=headers, timeout=2)
            except Exception as error:
                print(error)
                return

        if res.status_code == 200:
            data = res.json()
            print(data, '-----------------------')
            self.pin_switch_inference_end(pin)



    def pin_switch_inference_start(self, pin):
        self.cur_pin_state['GPI'+str(pin)] = True 
        self.cur_pin_state = pin_state_ref.find_one(self.state_query) or self.cur_pin_state
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
        self._emit_pin_state_update() 

    def pin_switch_inference_end(self, pin):
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
        self._emit_pin_state_update()

        time.sleep(.3)
        self.cur_pin_state['GPI'+str(pin)] = False 
        self.cur_pin_state = pin_state_ref.find_one(self.state_query) or self.cur_pin_state
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
        self._emit_pin_state_update() 

    def allow_inference(self, cur_input_state_high, pin_num):
        run_inference = False
        if self.last_input_state == "wait" and not cur_input_state_high:
            self.last_input_state = "run"
            run_inference = True

        return run_inference

    def default_pin_state(self):
        for gpo in range(1,9):
            self._set_gpio(1, gpo, 1)
            self.cur_pin_state['GPO'+str(gpo)] = False 
        for gpi in range(1,9):
            self.cur_pin_state['GPI'+str(gpi)] = False

        self.cur_pin_state = pin_state_ref.find_one(self.state_query) or self.cur_pin_state
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
        self._emit_pin_state_update() # Emit update to frontend

    def _emit_pin_state_update(self):
        """Emits the current pin state to connected SocketIO clients."""
        try:
            if '_id' in self.cur_pin_state:
                del self.cur_pin_state['_id']
                del self.cur_pin_state['type']

            socketio.emit('pin_state_update', self.cur_pin_state, namespace='/gpio')
        except Exception as e:
            print(f"Error emitting SocketIO event: {e}")

    def update_pin_state(self, i_o, pins):
        for pin, state in enumerate(pins):
            self.cur_pin_state[f'GP{i_o}{pin+1}'] = state == 0


    def run(self):
        self.default_pin_state()
        print("GPIO monitoring thread started.")
        while True:
            cur_pin = None
            all_pin_state = [
                self._read_gpi(1),
                self._read_gpi(2),
                self._read_gpi(3),
                self._read_gpi(4),
                self._read_gpi(5),
                self._read_gpi(6),
                self._read_gpi(7),
                self._read_gpi(8)
            ]

            all_output_state = [
                self.read_gpo_func(1),
                self.read_gpo_func(2),
                self.read_gpo_func(3),
                self.read_gpo_func(4),
                self.read_gpo_func(5),
                self.read_gpo_func(6),
                self.read_gpo_func(7),
                self.read_gpo_func(8)
            ]

            if self.current_output_state != all_output_state:
                self.current_output_state = all_output_state[:]
                self.update_pin_state('O', all_output_state)
                self._emit_pin_state_update()

            if 0 in all_pin_state:
                cur_pin = all_pin_state.index(0) + 1 
                if self.last_pin_state != all_pin_state:
                    self.last_pin_state = all_pin_state[:] 
                    self.update_pin_state('I', all_pin_state)
                    self._emit_pin_state_update()

                if self.allow_inference(0, cur_pin):
                    self.pin_switch_inference_start(cur_pin)
                    query = {'ioVal': 'GPI'+str(cur_pin)}
                    presets = io_ref.find(query)
                    for preset in presets:
                        inference_thread = threading.Thread(target=self.run_inference, args=(preset, cur_pin), daemon=True)
                        inference_thread.start()
            else:
                if self.last_input_state == "run":
                    self.last_input_state = "wait"
                    for i in range(1, 9):
                        self.cur_pin_state[f'GPI{i}'] = (all_pin_state[i-1] == 0) # Update based on actual read
                    pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
                    self._emit_pin_state_update()

                if self.last_pin_state != all_pin_state:
                    self.last_pin_state = all_pin_state[:]
                    for i in range(1, 9):
                        self.cur_pin_state[f'GPI{i}'] = (all_pin_state[i-1] == 0)
                    pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
                    self._emit_pin_state_update()

            time.sleep(self.debounce_delay) 



init_gpio = GPIO() # Instantiate GPIO class
@socketio.on('connect', namespace='/gpio')
def handle_connect():
    print('Client connected to /gpio namespace')
    emit('pin_state_update', init_gpio.cur_pin_state)

@socketio.on('disconnect', namespace='/gpio')
def handle_disconnect():
    print('Client disconnected from /gpio namespace')


# --- Main Execution ---
if __name__ == '__main__':
    gpio_thread = threading.Thread(target=init_gpio.run, daemon=True)
    gpio_thread.start()

    print("Starting Flask-SocketIO server on http://0.0.0.0:1817")
    socketio.run(app, host='0.0.0.0', port=1817, allow_unsafe_werkzeug=True)