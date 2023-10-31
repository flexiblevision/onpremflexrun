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

client   = MongoClient("172.17.0.1")
io_ref   = client["fvonprem"]["io_presets"]
util_ref = client["fvonprem"]["utils"]
pin_state_ref = client["fvonprem"]["pin_state"]
pass_fail_ref = client["fvonprem"]["pass_fail"]

so_file = os.environ['HOME']+"/flex-run/system_server/gpio/gpio.so"
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
        self.last_input_state = "wait"
        self.debounce_delay   = .001
        self.last_pin_state   = None

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
            #self.set_pass_fail_pins(modelName, modelVersion, data['tags'])
            self.set_pass_fail_pins(data)
            self.pin_switch_inference_end(pin)

    def set_pass_fail_pins(self, data):
        if 'pass_fail' in data:
            print(data['pass_fail'], ' <======================')
            if data['pass_fail'] == 'PASS':
                #set pass pin
                print(functions.set_gpio(1, 5, 0), 'PASS PIN ON')
                self.cur_pin_state['GPO5'] = True
            if data['pass_fail'] == 'FAIL':
                #set fail pin
                print(functions.set_gpio(1, 6, 0), 'FAIL PIN ON')
                self.cur_pin_state['GPO6'] = True

            pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
            time.sleep(.5)

            print(functions.set_gpio(1, 5, 1), 'PASS PIN OFF')
            print(functions.set_gpio(1, 6, 1), 'FAIL PIN OFF')
            self.cur_pin_state['GPO5'] = False
            self.cur_pin_state['GPO6'] = False
            pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
            self.cur_pin_state    = pin_state_ref.find_one(self.state_query)
            return data['pass_fail']
        return

    def pin_switch_inference_start(self, pin):
        functions.set_gpio(1, 2, 1)        # ready OFF
        functions.set_gpio(1, 3, 0)        # system busy
        self.cur_pin_state['GPO2'] = False # ready pin OFF - RED
        self.cur_pin_state['GPO3'] = True  # busy pin ON - RED
        self.cur_pin_state['GPI'+str(pin)] = True
        self.cur_pin_state = pin_state_ref.find_one(self.state_query)
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)

    def pin_switch_inference_end(self, pin):
        functions.set_gpio(1, 1, 0) # Process complete
        self.cur_pin_state['GPO1'] = True
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)

        time.sleep(.3)

        functions.set_gpio(1, 1, 1) # Process complete
        functions.set_gpio(1, 2, 0) # System Ready
        functions.set_gpio(1, 3, 1) # Not Busy
        self.cur_pin_state['GPO1'] = False # GPO Process Complete Pin OFF - GREEN
        self.cur_pin_state['GPO2'] = True  # Ready Pin ON - GREEN
        self.cur_pin_state['GPO3'] = False # Busy Pin OFF - ORANGE
        self.cur_pin_state['GPI'+str(pin)] = False
        self.cur_pin_state = pin_state_ref.find_one(self.state_query)
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)

    def allow_inference(self, cur_input_state_high, pin_num):
        run_inference = False

        if self.last_input_state == "wait" and not cur_input_state_high:
            self.last_input_state = "run"
            run_inference = True

        return run_inference

    def default_pin_state(self):
        functions.set_gpio(1, 1, 1) # Process complete
        functions.set_gpio(1, 2, 0) # System Ready
        functions.set_gpio(1, 3, 1) # Not Busy
        functions.set_gpio(1, 5, 1) # Pass pin off
        functions.set_gpio(1, 6, 1) # Fail pin off
        self.cur_pin_state['GPO1'] = False # GPO Process Complete Pin OFF - GREEN
        self.cur_pin_state['GPO2'] = True  # Ready Pin ON - GREEN
        self.cur_pin_state['GPO3'] = False # Busy Pin OFF - ORANGE
        self.cur_pin_state['GPO5'] = False
        self.cur_pin_state['GPO6'] = False
        for gpi in range(1,9):
            self.cur_pin_state['GPI'+str(gpi)] = False
        self.cur_pin_state = pin_state_ref.find_one(self.state_query)
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)

    def send_pin_status(self, pin_state):
        data = {
            'state': pin_state,
            'timestamp': ms_timestamp()
        }
        resp = requests.post('http://172.17.0.1:1880/io', json=pin_state, timeout=2)
        return resp

    def run(self):
        self.default_pin_state()
        while True:
            cur_pin = None
            all_pin_state = [
                functions.read_gpi(1),
                functions.read_gpi(2),
                functions.read_gpi(3),
                functions.read_gpi(4),
                functions.read_gpi(5),
                functions.read_gpi(6),
                functions.read_gpi(7),
                functions.read_gpi(8)
            ]

            if 0 in all_pin_state:
                cur_pin = all_pin_state.index(0)+1
                if self.last_pin_state != None and self.last_pin_state != all_pin_state:
                    thr = threading.Thread(target=self.send_pin_status, args=({'state': all_pin_state}), daemon=True)
                    thr.start()

            else:
                #clear input state
                self.last_input_state = "wait"

            if cur_pin and self.allow_inference(0, cur_pin):
                self.pin_switch_inference_start(cur_pin)
                query = {'ioVal': 'GPI'+str(cur_pin)}
                presets = io_ref.find(query)
                for preset in presets:
                    self.run_inference(preset, cur_pin)
                    #time.sleep(1.5)



init_gpio = GPIO()
init_gpio.run()


# print("input on pin 1 is low - TURNING ON LIGHT")
# print(functions.set_gpio(1, 1, 0))

# # input high - TURN OFF
# print("input on pin 1 is high - TURNING OFF LIGHT")
# print(functions.set_gpio(1, 1, 1))
                                      