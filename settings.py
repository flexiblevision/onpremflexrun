import json
import os
from setup.management import generate_environment_config

global config

generate_environment_config()
with open(os.environ['HOME']+'/fvconfig.json') as json_file:
    config = json.load(json_file)

global kinesis
kinesis = None
global FireOperator
FireOperator = None

if 'use_aws' in config and config['use_aws'] and kinesis == None:
    from aws.Kinesis import Kinesis
    kinesis = Kinesis()