import json
import os
from aws.Kinesis import Kinesis

global config
with open(os.environ['HOME']+'/flex-run/setup_constants/config.json') as json_file:
    config = json.load(json_file)

global kinesis
kinesis = None

if 'use_aws' in config and config['use_aws']:
    kinesis = Kinesis()