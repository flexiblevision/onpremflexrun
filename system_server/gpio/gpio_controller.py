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

class GPIO:
    def __init__(self):
        self.state_query      = {'type': 'gpio_pin_state'}
        self.cur_pin_state    = pin_state_ref.find_one(self.state_query)
        self.last_input_state = {}
        self.debounce_delay   = .05

    def get_pass_fail_entry(self, model, version):
        query = {'modelName': model, 'modelVersion': version}
        res   = pass_fail_ref.find(query)
        data  = json.loads(json_util.dumps(res))
        if not data: return False
        return data[0]

    def run_inference(self, cameraId, modelName, modelVersion, ioVal, pin, presetId):
        res     = util_ref.find_one({'type': 'id_token'}, {'_id': 0})
        token   = res['token']
        host    = 'http://172.17.0.1'
        port    = '5000'
        path    = '/api/capture/predict/snap/'+str(modelName)+'/'+str(modelVersion)+'/'+str(cameraId)+'?workstation='+str(ioVal)+'&preset_id='+str(presetId)
        url     = host+':'+port+path
        headers = {'Authorization': 'Bearer '+ token}
        res  = requests.get(url, headers=headers)
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
                functions.set_gpio(1, 5, 0)
                self.cur_pin_state['GPO5'] = True
            if data['pass_fail'] == 'FAIL':
                #set fail pin
                functions.set_gpio(1, 6, 0)
                self.cur_pin_state['GPO6'] = True

            pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
            time.sleep(.3)

            functions.set_gpio(1, 5, 1)
            functions.set_gpio(1, 6, 1)
            self.cur_pin_state['GPO5'] = False
            self.cur_pin_state['GPO6'] = False
            pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
            return data['pass_fail']
        return 
            
    def pin_switch_inference_start(self, pin):
        functions.set_gpio(1, 2, 1)        # ready OFF
        functions.set_gpio(1, 3, 0)        # system busy
        self.cur_pin_state['GPO2'] = False # ready pin OFF - RED
        self.cur_pin_state['GPO3'] = True  # busy pin ON - RED
        self.cur_pin_state['GPI'+str(pin)] = True
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)

    def pin_switch_inference_end(self, pin):
        functions.set_gpio(1, 1, 1) # Process complete
        self.cur_pin_state['GPO1'] = True
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)
        time.sleep(.3)
        functions.set_gpio(1, 1, 0) # Process complete
        functions.set_gpio(1, 2, 0) # System Ready
        functions.set_gpio(1, 3, 1) # Not Busy
        self.cur_pin_state['GPO1'] = False # GPO Process Complete Pin OFF - GREEN
        self.cur_pin_state['GPO2'] = True  # Ready Pin ON - GREEN
        self.cur_pin_state['GPO3'] = False # Busy Pin OFF - ORANGE
        self.cur_pin_state['GPI'+str(pin)] = False
        pin_state_ref.update_one(self.state_query, {'$set': self.cur_pin_state}, True)

    def allow_inference(self, cur_input_state_high, pin_num):
        run_inference = False
        if pin_num not in self.last_input_state: self.last_input_state[pin_num] = True
        last_input_state_high = self.last_input_state[pin_num]

        # HIGH / LOW
        if last_input_state_high and not cur_input_state_high:
            run_inference = True
            self.last_input_state[pin_num] = False
        # LOW / HIGH
        if not last_input_state_high and cur_input_state_high:
            run_inference = False
            self.last_input_state[pin_num] = True

        return run_inference

    def run(self):
        while True:
            for pin in range(1,9):
                pin_debounce = functions.read_gpi(pin)
                time.sleep(self.debounce_delay)
                pin_high = functions.read_gpi(pin)
                #check to make sure pin reading is still the same
                #if it is, move into allow_inference logic
                #if not, pass - false reading
                bounced = pin_debounce != pin_high
                if not bounced and self.allow_inference(pin_high, pin):
                    self.pin_switch_inference_start(pin)
                    query = {'ioVal': 'GPI'+str(pin)}
                    presets = io_ref.find(query)
                    for preset in presets:
                        inference_args = (preset['cameraId'], preset['modelName'], preset['modelVersion'], preset['ioVal'], pin, preset['presetId'])
                        
                        thread = threading.Thread(target=self.run_inference, args=inference_args, daemon=True)
                        thread.start()

init_gpio = GPIO()
init_gpio.run()

            
# print("input on pin 1 is low - TURNING ON LIGHT")
# print(functions.set_gpio(1, 1, 0))

# # input high - TURN OFF
# print("input on pin 1 is high - TURNING OFF LIGHT")
# print(functions.set_gpio(1, 1, 1))
