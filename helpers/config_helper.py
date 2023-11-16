import requests
import settings

PATH = os.environ['HOME']+'/fvconfig.json'

def write_settings_to_config():
    config = settings.config
    with open(PATH, 'w') as outfile:  
        json.dump(config, outfile, indent=4, sort_keys=True)
    print('SETTINGS WRITTEN TO CONFIG')
