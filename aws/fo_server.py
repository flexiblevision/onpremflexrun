import os
import sys
settings_path = os.environ['HOME']+'/flex-run'
sys.path.append(settings_path)
import settings
from FireOperator import FireOperator
from flask import Flask, jsonify, request

app = Flask(__name__)

@app.route('/inspection_status', methods=['GET'])
def get_status():
        data = request.json
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
    
@app.route('/aws_warehouse_zone', methods=['PUT'])
def update_zone():
    data = request.json
    if 'warehouse' in data and 'zone' in data:
        doc_key = f"{data['warehouse']}_{data['zone']}"
        settings.config['fire_operator']['document'] = doc_key
        update_config(settings.config)
        os.system(f"forever restart {os.environ['HOME']}/flex-run/system_server/server.py")


if __name__ == '__main__':
    settings.FireOperator = FireOperator()
    app.run(host='0.0.0.0', port=5012)

