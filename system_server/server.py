import os
import sys
settings_path = os.environ['HOME']+'/flex-run'
sys.path.append(settings_path)

from flask import Flask
from flask_restx import Api
from flask_cors import CORS
import settings

# Initialize Flask app and API
app = Flask(__name__)
api = Api(app)
CORS(app)

# Import and register all routes from the routes package
from routes import register_all_routes
register_all_routes(api, settings)

if __name__ == '__main__':
    if 'use_aws' in settings.config and settings.config['use_aws']:
        from aws.FireOperator import run_operator
        run_operator()

    app.run(host='0.0.0.0', port='5001')
