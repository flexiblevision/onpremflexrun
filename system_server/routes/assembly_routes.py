import os
import io
import json
import zipfile
import uuid
from flask import request, send_from_directory
from flask_restx import Resource

# Base path for assembly storage
ASSEMBLY_BASE_PATH = os.path.join(os.environ['HOME'], 'visioncell', 'Documents', 'assembly')


class UploadAssembly(Resource):
    def post(self):
        """
        Receive a ZIP file, extract it to /Documents/assembly/<id>/,
        and return the config.json content with updated media paths.
        """
        if 'file' not in request.files:
            return {'error': 'No file provided'}, 400

        file = request.files['file']
        if file.filename == '':
            return {'error': 'No file selected'}, 400

        if not file.filename.endswith('.zip'):
            return {'error': 'File must be a ZIP archive'}, 400

        try:
            # Read the ZIP file into memory
            zip_bytes = io.BytesIO(file.read())

            # Open the ZIP and extract config.json to get the ID
            with zipfile.ZipFile(zip_bytes, 'r') as zip_ref:
                # Find config.json (may be nested in a subdirectory)
                config_path = None
                for name in zip_ref.namelist():
                    if name.endswith('config.json'):
                        config_path = name
                        break

                if not config_path:
                    return {'error': 'config.json not found in ZIP'}, 400

                # Determine the base directory within the ZIP (if nested)
                zip_base_dir = os.path.dirname(config_path)

                # Read and parse config.json
                config_data = zip_ref.read(config_path)
                config = json.loads(config_data.decode('utf-8'))

                # Get or generate assembly ID
                assembly_id = config.get('id', str(uuid.uuid4()))

                # Create output directory
                output_path = os.path.join(ASSEMBLY_BASE_PATH, assembly_id)
                os.makedirs(output_path, exist_ok=True)

                # Extract files, stripping the nested directory prefix if present
                for member in zip_ref.namelist():
                    # Skip directories
                    if member.endswith('/'):
                        continue

                    # Calculate relative path from the config.json location
                    if zip_base_dir and member.startswith(zip_base_dir + '/'):
                        relative_path = member[len(zip_base_dir) + 1:]
                    elif zip_base_dir:
                        # Skip files not in the same directory tree
                        continue
                    else:
                        relative_path = member

                    # Create target path
                    target_path = os.path.join(output_path, relative_path)
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)

                    # Extract file
                    with zip_ref.open(member) as src, open(target_path, 'wb') as dst:
                        dst.write(src.read())

            # Update media paths in config to point to backend API
            config = update_media_paths(config, assembly_id)

            return {
                'config': config,
                'assemblyId': assembly_id
            }, 200

        except zipfile.BadZipFile:
            return {'error': 'Invalid ZIP file'}, 400
        except json.JSONDecodeError:
            return {'error': 'Invalid config.json format'}, 400
        except Exception as e:
            return {'error': str(e)}, 500


class ServeMedia(Resource):
    def get(self, assembly_id, filename):
        """
        Serve media files from the assembly directory.
        """
        assembly_path = os.path.join(ASSEMBLY_BASE_PATH, assembly_id)

        if not os.path.exists(assembly_path):
            return {'error': 'Assembly not found'}, 404

        # Handle nested paths (e.g., "subdir/file.png")
        file_path = os.path.join(assembly_path, filename)

        if not os.path.exists(file_path):
            return {'error': 'File not found'}, 404

        # Ensure the resolved path is within the assembly directory (security check)
        real_assembly_path = os.path.realpath(assembly_path)
        real_file_path = os.path.realpath(file_path)
        if not real_file_path.startswith(real_assembly_path):
            return {'error': 'Invalid path'}, 403

        directory = os.path.dirname(file_path)
        file_name = os.path.basename(file_path)

        return send_from_directory(directory, file_name)


def update_media_paths(config, assembly_id):
    """
    Update media paths in config to point to the backend API endpoints.
    """
    if 'steps' in config:
        for step in config['steps']:
            # Update screen1 media
            if 'screen1' in step and step['screen1']:
                if 'media' in step['screen1'] and step['screen1']['media']:
                    filename = step['screen1']['media']
                    step['screen1']['mediaUrl'] = f'/assembly/media/{assembly_id}/{filename}'

            # Update screen2 media if present
            if 'screen2' in step and step['screen2']:
                if 'media' in step['screen2'] and step['screen2']['media']:
                    filename = step['screen2']['media']
                    step['screen2']['mediaUrl'] = f'/assembly/media/{assembly_id}/{filename}'

    return config


def register_routes(api):
    api.add_resource(UploadAssembly, '/assembly/upload')
    api.add_resource(ServeMedia, '/assembly/media/<string:assembly_id>/<path:filename>')
