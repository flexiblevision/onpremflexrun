import os
import subprocess
from flask import render_template, make_response
from flask_restx import Resource
import auth
from version_check import is_container_uptodate, get_current_container_version
from setup.management import generate_environment_config

CONTAINERS = {
    'backend': 'capdev',
    'frontend': 'captureui',
    'prediction': 'localprediction',
    'predict lite': 'predictlite',
    'nodecreator': 'nodecreator',
    'vision': 'vision',
    'database': 'mongo',
    'visiontools': 'visiontools'
}

daemon_services_list = {
    "FlexRun Server": "server.py",
    "TCP Server": "tcp/tcp_server.py",
    "GPIO Server": "gpio/gpio_controller.py",
    "Sync Worker": "worker_scripts/sync_worker.py",
    "Worker Server": "worker.py",
    "Inference Server Watcher": "worker_scripts/ping_prediction_server.py",
    "Job Watcher": "job_watcher.py"
}

class Shutdown(Resource):
    @auth.requires_auth
    def get(self):
        print('shutting down system')
        os.system("poweroff")

class Restart(Resource):
    @auth.requires_auth
    def get(self):
        print('restarting system')
        os.system("reboot")

class RestartBackend(Resource):
    @auth.requires_auth
    def get(self):
        print('restarting capdev and vision...')
        os.system("docker restart capdev")
        try:
            import requests
            host = 'http://172.17.0.1'
            port = '5555'
            path = '/api/vision/releaseAll'
            url = host+':'+port+path
            resp = requests.get(url)
        except Exception as e:
            print(e)
        os.system("docker restart vision")

class ListServices(Resource):
    def get(self):
        f_services = []
        scripts_base_path = os.environ['HOME']+"/flex-run/system_server/"
        for key in daemon_services_list:
            service_path = scripts_base_path + daemon_services_list[key]
            is_running = subprocess.getoutput("forever list | grep {} | wc -l | sed -e 's/1/Running/' | sed -e 's/0/Not Running/'".format(service_path))
            color = 'green' if is_running == "Running" else 'red'
            txt = key + " - " + is_running
            f_services.append({'txt': txt, 'color': color})

        c_services = []
        for f_name in CONTAINERS:
            container_name = CONTAINERS[f_name]
            inspect = subprocess.Popen(['docker', 'inspect', '-f', "{{.State.Running}}", container_name], stdout=subprocess.PIPE)
            is_running = inspect.communicate()[0].decode('utf-8').strip()
            color = 'green' if is_running=='true' else 'red'
            r_txt = 'Running' if is_running=='true' else 'Not Running'
            txt = f_name + " - " + r_txt
            c_services.append({'txt': txt, 'color': color})

        resp = make_response(render_template('services_doc.html', daemon_services=f_services, container_services=c_services))
        resp.headers['Content-type'] = 'text/html; charset=utf-8'
        return resp

class Upgrade(Resource):
    @auth.requires_auth
    def get(self):
        cap_uptd = is_container_uptodate('backend')[1]
        capui_uptd = is_container_uptodate('frontend')[1]
        predict_uptd = is_container_uptodate('prediction')[1]
        predictlite_uptd = is_container_uptodate('predictlite')[1]
        vision_uptd = is_container_uptodate('vision')[1]
        creator_uptd = is_container_uptodate('nodecreator')[1]
        visiontools_uptd = is_container_uptodate('visiontools')[1]

        try:
            import requests
            host = 'http://172.17.0.1'
            port = '5555'
            path = '/api/vision/releaseAll'
            url = host+':'+port+path
            resp = requests.get(url)
        except Exception as e:
            print(e)

        generate_environment_config()
        os.system("chmod +x "+os.environ['HOME']+"/flex-run/upgrades/upgrade_flex_run.sh")
        os.system("sh "+os.environ['HOME']+"/flex-run/upgrades/upgrade_flex_run.sh")

        os.system("chmod +x "+os.environ['HOME']+"/flex-run/system_server/upgrade_system.sh")
        os.system("sh "+os.environ['HOME']+"/flex-run/system_server/upgrade_system.sh "+cap_uptd+" "+capui_uptd+" "+predict_uptd+" "+predictlite_uptd+" "+vision_uptd+" "+creator_uptd+" "+visiontools_uptd)

class UpgradeFlexRun(Resource):
    @auth.requires_auth
    def get(self):
        os.system("chmod +x "+os.environ['HOME']+"/flex-run/upgrades/upgrade_flex_run.sh")
        os.system("sh "+os.environ['HOME']+"/flex-run/upgrades/upgrade_flex_run.sh")

class SystemVersions(Resource):
    def get(self):
        backend_version = get_current_container_version('capdev')
        frontend_version = get_current_container_version('captureui')
        prediction_version = get_current_container_version('localprediction')
        predictlite_version = get_current_container_version('predictlite')
        vision_version = get_current_container_version('vision')
        creator_version = get_current_container_version('nodecreator')
        visiontools_version = get_current_container_version('visiontools')

        return {'backend_version': backend_version,
                'frontend_version': frontend_version,
                'prediction_version': prediction_version,
                'predictlite_version': predictlite_version,
                'vision_version': vision_version,
                'creator_version': creator_version,
                'visiontools_version': vision_version
                }

class SystemIsUptodate(Resource):
    def get(self):
        return all([
            is_container_uptodate('backend')[0],
            is_container_uptodate('frontend')[0],
            is_container_uptodate('prediction')[0],
            is_container_uptodate('predictlite')[0],
            is_container_uptodate('vision')[0],
            is_container_uptodate('nodecreator')[0],
            is_container_uptodate('visiontools')[0]
        ])

class RestartFO(Resource):
    def get(self):
        try:
            os.system("forever restart /root/flex-run/aws/fo_server.py")
            return "FO server restarted", 200
        except Exception as e:
            print("Error restarting FO server:", e)
            return "Error restarting FO server", 500

def register_routes(api):
    api.add_resource(Shutdown, '/shutdown')
    api.add_resource(Restart, '/restart')
    api.add_resource(RestartBackend, '/refresh_backend')
    api.add_resource(ListServices, '/list_services')
    api.add_resource(Upgrade, '/upgrade')
    api.add_resource(UpgradeFlexRun, '/upgrade_flex_run')
    api.add_resource(SystemVersions, '/system_versions')
    api.add_resource(SystemIsUptodate, '/system_uptodate')
