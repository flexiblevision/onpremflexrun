"""Server bootstrap: route registration and the daemon entrypoint.

register_all_routes is the single place that decides which endpoints a device
exposes. Two of them are conditional, and a condition that silently inverts
either leaves the MQTT bridge unmanageable or exposes an AWS route on a fleet
that has no AWS.
"""
import os
import runpy
import sys
import pytest
from unittest.mock import patch, MagicMock, call

import routes


SERVER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'server.py'))

ROUTE_MODULES = [
    'system_routes', 'network_routes', 'model_routes', 'image_routes',
    'device_routes', 'auth_routes', 'ftp_routes', 'timemachine_routes',
    'assembly_routes', 'addon_routes',
]


@pytest.fixture
def registrars():
    """Patch register_routes on every route module and hand back the mocks."""
    patchers = {name: patch.object(getattr(routes, name), 'register_routes')
                for name in ROUTE_MODULES + ['mqtt_routes']}
    started = {name: p.start() for name, p in patchers.items()}
    yield started
    for p in patchers.values():
        p.stop()


def _settings(**config):
    return MagicMock(config=config)


class TestRegisterAllRoutes:
    @pytest.mark.unit
    def test_every_unconditional_module_is_registered(self, registrars):
        api = MagicMock()
        routes.register_all_routes(api, _settings())

        for name in ROUTE_MODULES:
            registrars[name].assert_called_once_with(api)

    @pytest.mark.unit
    def test_the_same_api_instance_is_handed_to_every_module(self, registrars):
        api = MagicMock()
        routes.register_all_routes(api, _settings())

        assert all(registrars[n].call_args[0][0] is api for n in ROUTE_MODULES)

    @pytest.mark.unit
    def test_mqtt_is_not_registered_by_default(self, registrars):
        # Registering it also starts a background health-monitor thread that
        # restarts VerneMQ, so it must stay off unless the device opts in.
        routes.register_all_routes(MagicMock(), _settings())
        registrars['mqtt_routes'].assert_not_called()

    @pytest.mark.unit
    def test_mqtt_is_registered_when_enabled(self, registrars):
        api = MagicMock()
        routes.register_all_routes(api, _settings(use_mqtt=True))
        registrars['mqtt_routes'].assert_called_once_with(api)

    @pytest.mark.unit
    def test_mqtt_key_present_but_false_does_not_register(self, registrars):
        routes.register_all_routes(MagicMock(), _settings(use_mqtt=False))
        registrars['mqtt_routes'].assert_not_called()

    @pytest.mark.unit
    def test_restart_fo_is_not_exposed_by_default(self, registrars):
        api = MagicMock()
        routes.register_all_routes(api, _settings())
        api.add_resource.assert_not_called()

    @pytest.mark.unit
    def test_restart_fo_is_exposed_when_aws_is_enabled(self, registrars):
        api = MagicMock()
        routes.register_all_routes(api, _settings(use_aws=True))

        api.add_resource.assert_called_once_with(
            routes.system_routes.RestartFO, '/restart_fo')

    @pytest.mark.unit
    def test_aws_key_present_but_false_does_not_expose_it(self, registrars):
        api = MagicMock()
        routes.register_all_routes(api, _settings(use_aws=False))
        api.add_resource.assert_not_called()

    @pytest.mark.unit
    def test_both_conditionals_can_be_on_at_once(self, registrars):
        api = MagicMock()
        routes.register_all_routes(api, _settings(use_aws=True, use_mqtt=True))

        registrars['mqtt_routes'].assert_called_once()
        api.add_resource.assert_called_once()

    @pytest.mark.unit
    def test_the_package_exports_every_module_it_registers(self):
        # A module added to register_all_routes but missing from the package
        # import list would be an AttributeError at startup.
        for name in ROUTE_MODULES + ['mqtt_routes']:
            assert hasattr(routes, name), f'routes package does not export {name}'


class TestServerModule:
    @pytest.mark.unit
    def test_importing_builds_an_app_with_all_routes_registered(self):
        with patch.object(routes, 'register_all_routes') as register:
            sys.modules.pop('server', None)
            import server

        register.assert_called_once()
        assert server.app is not None
        assert server.api is not None
        sys.modules.pop('server', None)

    @pytest.mark.unit
    def test_the_repo_root_is_on_the_path_so_settings_resolves(self):
        with patch.object(routes, 'register_all_routes'):
            sys.modules.pop('server', None)
            import server

        assert server.settings_path == os.environ['HOME'] + '/flex-run'
        sys.modules.pop('server', None)

    @pytest.mark.unit
    def test_importing_does_not_bind_a_port(self):
        with patch.object(routes, 'register_all_routes'), \
             patch('flask.Flask.run') as run:
            sys.modules.pop('server', None)
            import server  # noqa: F401

        run.assert_not_called()
        sys.modules.pop('server', None)


class TestServerMain:
    """The __main__ block, run the way forever runs it."""

    def _run_main(self, settings_config):
        with patch('settings.config', settings_config), \
             patch.object(routes, 'register_all_routes'), \
             patch('flask.Flask.run') as run, \
             patch('flask_cors.CORS'):
            runpy.run_path(SERVER_PATH, run_name='__main__')
        return run

    @pytest.mark.unit
    def test_listens_on_all_interfaces_on_5001(self):
        run = self._run_main({})
        run.assert_called_once_with(host='0.0.0.0', port='5001')

    @pytest.mark.unit
    def test_the_fire_operator_is_not_started_by_default(self):
        fire = MagicMock()
        with patch.dict(sys.modules, {'aws.FireOperator': fire}):
            self._run_main({})
        fire.run_operator.assert_not_called()

    @pytest.mark.unit
    def test_the_fire_operator_starts_when_aws_is_enabled(self):
        fire = MagicMock()
        with patch.dict(sys.modules, {'aws': MagicMock(), 'aws.FireOperator': fire}):
            self._run_main({'use_aws': True})
        fire.run_operator.assert_called_once()

    @pytest.mark.unit
    def test_aws_key_present_but_false_does_not_start_it(self):
        fire = MagicMock()
        with patch.dict(sys.modules, {'aws': MagicMock(), 'aws.FireOperator': fire}):
            self._run_main({'use_aws': False})
        fire.run_operator.assert_not_called()

    @pytest.mark.unit
    def test_the_operator_starts_before_the_server_blocks(self):
        # app.run never returns, so anything sequenced after it never runs.
        fire = MagicMock()
        order = []
        fire.run_operator.side_effect = lambda: order.append('operator')

        with patch.dict(sys.modules, {'aws': MagicMock(), 'aws.FireOperator': fire}), \
             patch('settings.config', {'use_aws': True}), \
             patch.object(routes, 'register_all_routes'), \
             patch('flask.Flask.run', side_effect=lambda **kw: order.append('serve')), \
             patch('flask_cors.CORS'):
            runpy.run_path(SERVER_PATH, run_name='__main__')

        assert order == ['operator', 'serve']
