"""
Routes package for organizing API endpoints by functional category.
This module provides a centralized registration function for all route modules.
"""

from . import (
    system_routes,
    network_routes,
    model_routes,
    image_routes,
    device_routes,
    auth_routes,
    ftp_routes,
    timemachine_routes
)

def register_all_routes(api, settings):
    """
    Register all routes from different modules to the Flask API.

    Args:
        api: Flask-RESTX API instance
        settings: Settings module for conditional route registration
    """
    # System management routes
    system_routes.register_routes(api)

    # Network configuration routes
    network_routes.register_routes(api)

    # Model management routes
    model_routes.register_routes(api)

    # Image handling routes
    image_routes.register_routes(api)

    # Device/hardware routes
    device_routes.register_routes(api)

    # Authentication and cloud sync routes
    auth_routes.register_routes(api)

    # FTP management routes
    ftp_routes.register_routes(api)

    # Timemachine and OCR routes
    timemachine_routes.register_routes(api)

    # Conditional AWS routes
    if 'use_aws' in settings.config and settings.config['use_aws']:
        api.add_resource(system_routes.RestartFO, '/restart_fo')
