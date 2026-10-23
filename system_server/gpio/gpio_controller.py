import functools
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
from gpio_helper import *

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

# gpio.so has no locking of its own: concurrent callers corrupt register reads
# and drop writes. This process is its only caller, and every call holds this.
gpio_lock = threading.RLock()

PASS_PIN = 5
FAIL_PIN = 6
PASS_FAIL_HOLD = .5


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
        # 10ms is ~25 reads per part at 4 parts/s; 1ms only widened the collision window.
        self.debounce_delay   = .01
        self.last_pin_state   = None
        self.set_gpio_func = functions.set_gpio
        self.read_gpi_func = functions.read_gpi
        self.read_gpo_func = functions.read_gpo
        self.current_output_state = [self._read_gpo(pin) for pin in range(1, 9)]


    def _set_gpio(self, direction, pin_index, value):
        """Wrapper for set_gpio with error handling."""
        if self.set_gpio_func:
            with gpio_lock:
                return self.set_gpio_func(direction, pin_index, value)
        return -1 # Indicate failure if function not loaded

    def _read_gpi(self, pin_index):
        """Wrapper for read_gpi with error handling."""
        if self.read_gpi_func:
            with gpio_lock:
                return self.read_gpi_func(pin_index)
        return -1 # Indicate failure if function not loaded

    def _read_gpo(self, pin_index):
        if self.read_gpo_func:
            with gpio_lock:
                return self.read_gpo_func(pin_index)
        return -1

    def _read_all_gpio_locked(self):
        with gpio_lock:
            return read_all_gpio_states_as_json()

    def _record_outputs(self, changes):
        self.cur_pin_state.update(changes)
        pin_state_ref.update_one(self.state_query, {'$set': changes}, True)
        self._emit_pin_state_update()

    def set_output_pin(self, pin, on):
        """Drive an output pin (active low); returns the driver's return code."""
        rc = self._set_gpio(1, pin, 0 if on else 1)
        self._record_outputs({f'GPO{pin}': on})
        return rc

    def toggle_output_pin(self, pin):
        with gpio_lock:
            on = self._read_gpo(pin) != 0
            self._set_gpio(1, pin, 0 if on else 1)
        self._record_outputs({f'GPO{pin}': on})
        return on

    def input_asserted(self, pin):
        return self._read_gpi(pin) == 0

    def pulse_pass_fail(self, verdict):
        pin = {'PASS': PASS_PIN, 'FAIL': FAIL_PIN}.get(verdict)
        if pin:
            self.set_output_pin(pin, True)
            time.sleep(PASS_FAIL_HOLD)
        # Both are always released: a pin left asserted latches the reject gate.
        with gpio_lock:
            self._set_gpio(1, PASS_PIN, 1)
            self._set_gpio(1, FAIL_PIN, 1)
        self._record_outputs({f'GPO{PASS_PIN}': False, f'GPO{FAIL_PIN}': False})
        return verdict

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

            gpio_state = self._read_all_gpio_locked()
            all_output_state = gpio_state['outputs']
            all_pin_state = gpio_state['inputs']

            if self.current_output_state != all_output_state:
                self.current_output_state = all_output_state[:]
                self.update_pin_state('O', all_output_state)
                print("Output state changed:", all_output_state)
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


# --- Pin API: tcp_server and the system server drive pins through here ---
LOOPBACK = {'127.0.0.1', '::1'}


def local_only(view):
    # Port 1817 is reachable from the plant network; pin writes are not.
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if request.remote_addr not in LOOPBACK:
            return jsonify({'error': 'forbidden'}), 403
        return view(*args, **kwargs)
    return wrapper


def _pin(value):
    pin = int(value)
    if not 1 <= pin <= 8:
        raise ValueError(f'pin {pin} out of range')
    return pin


def _bad_request(error):
    return jsonify({'error': str(error)}), 400


@app.route('/api/gpio/state', methods=['GET'])
@local_only
def api_gpio_state():
    return jsonify(init_gpio._read_all_gpio_locked())


@app.route('/api/gpio/set', methods=['POST'])
@local_only
def api_gpio_set():
    body = request.get_json(silent=True) or {}
    try:
        pin = _pin(body['pin'])
    except (KeyError, TypeError, ValueError) as error:
        return _bad_request(error)
    on = body.get('state') == True
    rc = init_gpio.set_output_pin(pin, on)
    return jsonify({'state': 'on' if on else 'off', 'rc': rc})


@app.route('/api/gpio/toggle', methods=['POST'])
@local_only
def api_gpio_toggle():
    body = request.get_json(silent=True) or {}
    try:
        pin = _pin(body['pin'])
    except (KeyError, TypeError, ValueError) as error:
        return _bad_request(error)
    return jsonify({'state': init_gpio.toggle_output_pin(pin)})


@app.route('/api/gpio/input/<pin>', methods=['GET'])
@local_only
def api_gpio_input(pin):
    try:
        pin = _pin(pin)
    except ValueError as error:
        return _bad_request(error)
    return jsonify({'asserted': init_gpio.input_asserted(pin)})


@app.route('/api/gpio/pass_fail', methods=['POST'])
@local_only
def api_gpio_pass_fail():
    body = request.get_json(silent=True) or {}
    return jsonify({'pass_fail': init_gpio.pulse_pass_fail(body.get('pass_fail'))})


# --- Main Execution ---
if __name__ == '__main__':
    gpio_thread = threading.Thread(target=init_gpio.run, daemon=True)
    gpio_thread.start()

    print("Starting Flask-SocketIO server on http://0.0.0.0:1817")
    socketio.run(app, host='0.0.0.0', port=1817, allow_unsafe_werkzeug=True)