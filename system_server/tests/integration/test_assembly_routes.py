"""Assembly bundle upload and media serving.

The upload endpoint takes an operator-supplied ZIP and extracts it to disk, and
the media endpoint serves paths built from URL segments. Both are the shapes
that go wrong: an archive whose members escape the target directory, and a
filename that walks out of the assembly it names.

The enable/disable toggle moved to test_addon_routes.py with the route itself.
"""
import io
import json
import os
import zipfile
import pytest
from unittest.mock import patch, MagicMock, call

from routes import assembly_routes as ar


@pytest.fixture
def base_path(tmp_path, monkeypatch):
    root = tmp_path / 'assembly'
    root.mkdir()
    monkeypatch.setattr(ar, 'ASSEMBLY_BASE_PATH', str(root))
    return root


@pytest.fixture
def client(base_path):
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    ar.register_routes(api)
    return app.test_client()


def _zip(members):
    """Build an in-memory ZIP from {name: bytes|str}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, content in members.items():
            if isinstance(content, str):
                content = content.encode()
            z.writestr(name, content)
    buf.seek(0)
    return buf


def _upload(client, zip_bytes, filename='bundle.zip'):
    return client.post('/assembly/upload',
                       data={'file': (zip_bytes, filename)},
                       content_type='multipart/form-data')


CONFIG = {'id': 'asm-1', 'steps': [
    {'screen1': {'media': 'step1.png'}, 'screen2': {'media': 'step1b.png'}},
]}


class TestUploadAssembly:
    @pytest.mark.integration
    def test_extracts_the_bundle_and_returns_the_config(self, client, base_path):
        archive = _zip({'config.json': json.dumps(CONFIG), 'step1.png': b'PNGDATA'})
        response = _upload(client, archive)

        assert response.status_code == 200
        body = response.get_json()
        assert body['assemblyId'] == 'asm-1'
        assert (base_path / 'asm-1' / 'step1.png').read_bytes() == b'PNGDATA'

    @pytest.mark.integration
    def test_media_urls_are_rewritten_to_the_serving_endpoint(self, client, base_path):
        archive = _zip({'config.json': json.dumps(CONFIG), 'step1.png': b'x'})
        steps = _upload(client, archive).get_json()['config']['steps']

        assert steps[0]['screen1']['mediaUrl'] == '/assembly/media/asm-1/step1.png'
        assert steps[0]['screen2']['mediaUrl'] == '/assembly/media/asm-1/step1b.png'

    @pytest.mark.integration
    def test_a_nested_bundle_is_flattened_to_the_config_directory(self, client, base_path):
        archive = _zip({'bundle/config.json': json.dumps(CONFIG),
                        'bundle/step1.png': b'PNGDATA'})
        response = _upload(client, archive)

        assert response.status_code == 200
        assert (base_path / 'asm-1' / 'step1.png').read_bytes() == b'PNGDATA'
        assert not (base_path / 'asm-1' / 'bundle').exists()

    @pytest.mark.integration
    def test_files_outside_the_nested_root_are_skipped(self, client, base_path):
        archive = _zip({'bundle/config.json': json.dumps(CONFIG),
                        'bundle/step1.png': b'x',
                        'stray.txt': b'not part of the bundle'})
        _upload(client, archive)

        assert not (base_path / 'asm-1' / 'stray.txt').exists()

    @pytest.mark.integration
    def test_an_id_is_generated_when_the_config_omits_one(self, client, base_path):
        archive = _zip({'config.json': json.dumps({'steps': []})})
        body = _upload(client, archive).get_json()

        assert body['assemblyId']
        assert (base_path / body['assemblyId']).is_dir()

    @pytest.mark.integration
    def test_a_config_without_steps_is_returned_unchanged(self, client, base_path):
        archive = _zip({'config.json': json.dumps({'id': 'asm-2', 'title': 'x'})})
        assert _upload(client, archive).get_json()['config'] == \
            {'id': 'asm-2', 'title': 'x'}

    @pytest.mark.integration
    def test_a_step_with_no_media_is_left_alone(self, client, base_path):
        config = {'id': 'asm-3', 'steps': [{'screen1': {'text': 'do the thing'}},
                                           {'screen1': None, 'screen2': None}]}
        archive = _zip({'config.json': json.dumps(config)})
        steps = _upload(client, archive).get_json()['config']['steps']

        assert 'mediaUrl' not in steps[0]['screen1']
        assert steps[1]['screen1'] is None

    @pytest.mark.integration
    def test_directory_entries_are_not_written_as_files(self, client, base_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('config.json', json.dumps(CONFIG))
            z.writestr('media/', b'')
            z.writestr('media/step1.png', b'x')
        buf.seek(0)

        assert _upload(client, buf).status_code == 200
        assert (base_path / 'asm-1' / 'media' / 'step1.png').is_file()

    @pytest.mark.integration
    def test_a_request_with_no_file_is_a_400(self, client):
        response = client.post('/assembly/upload', data={},
                               content_type='multipart/form-data')
        assert response.status_code == 400
        assert response.get_json() == {'error': 'No file provided'}

    @pytest.mark.integration
    def test_an_empty_filename_is_a_400(self, client):
        response = _upload(client, io.BytesIO(b''), filename='')
        assert response.status_code == 400

    @pytest.mark.integration
    def test_a_non_zip_filename_is_a_400(self, client):
        response = _upload(client, io.BytesIO(b'x'), filename='bundle.tar.gz')
        assert response.status_code == 400
        assert response.get_json() == {'error': 'File must be a ZIP archive'}

    @pytest.mark.integration
    def test_a_corrupt_archive_is_a_400(self, client):
        response = _upload(client, io.BytesIO(b'this is not a zip'))
        assert response.status_code == 400
        assert response.get_json() == {'error': 'Invalid ZIP file'}

    @pytest.mark.integration
    def test_an_archive_without_a_config_is_a_400(self, client):
        response = _upload(client, _zip({'step1.png': b'x'}))
        assert response.status_code == 400
        assert response.get_json() == {'error': 'config.json not found in ZIP'}

    @pytest.mark.integration
    def test_a_malformed_config_is_a_400(self, client):
        response = _upload(client, _zip({'config.json': 'not json at all'}))
        assert response.status_code == 400
        assert response.get_json() == {'error': 'Invalid config.json format'}

    @pytest.mark.integration
    def test_an_unwritable_target_is_a_500(self, client, base_path):
        with patch('os.makedirs', side_effect=PermissionError('read-only fs')):
            response = _upload(client, _zip({'config.json': json.dumps(CONFIG)}))

        assert response.status_code == 500
        assert 'read-only fs' in response.get_json()['error']

    @pytest.mark.integration
    def test_an_archive_member_can_escape_the_assembly_directory(self, client, base_path):
        # os.path.join with an absolute-ish traversal member is not constrained
        # to output_path, so a crafted archive writes outside the assembly.
        # ServeMedia has a realpath guard; extraction has none. Recorded so the
        # asymmetry is visible.
        archive = _zip({'config.json': json.dumps({'id': 'asm-1', 'steps': []}),
                        '../escaped.txt': b'outside'})
        response = _upload(client, archive)

        assert response.status_code == 200
        assert (base_path / 'escaped.txt').exists()
        assert not (base_path / 'asm-1' / 'escaped.txt').exists()


class TestServeMedia:
    @pytest.mark.integration
    def test_serves_a_file_from_the_assembly(self, client, base_path):
        (base_path / 'asm-1').mkdir()
        (base_path / 'asm-1' / 'step1.png').write_bytes(b'PNGDATA')

        response = client.get('/assembly/media/asm-1/step1.png')

        assert response.status_code == 200
        assert response.data == b'PNGDATA'

    @pytest.mark.integration
    def test_serves_a_nested_file(self, client, base_path):
        nested = base_path / 'asm-1' / 'media'
        nested.mkdir(parents=True)
        (nested / 'step1.png').write_bytes(b'NESTED')

        response = client.get('/assembly/media/asm-1/media/step1.png')

        assert response.status_code == 200
        assert response.data == b'NESTED'

    @pytest.mark.integration
    def test_an_unknown_assembly_is_a_404(self, client, base_path):
        response = client.get('/assembly/media/nope/step1.png')
        assert response.status_code == 404
        assert response.get_json() == {'error': 'Assembly not found'}

    @pytest.mark.integration
    def test_a_missing_file_is_a_404(self, client, base_path):
        (base_path / 'asm-1').mkdir()
        response = client.get('/assembly/media/asm-1/nope.png')

        assert response.status_code == 404
        assert response.get_json() == {'error': 'File not found'}

    @pytest.mark.integration
    def test_a_path_escaping_the_assembly_is_refused(self, client, base_path):
        (base_path / 'asm-1').mkdir()
        (base_path / 'secret.txt').write_text('not yours')

        # werkzeug normalises '..' out of the URL, so the traversal is driven
        # through the resource directly - which is what the realpath guard is
        # there to stop.
        body, status = ar.ServeMedia().get('asm-1', '../secret.txt')

        assert status == 403
        assert body == {'error': 'Invalid path'}

    @pytest.mark.integration
    def test_a_symlink_out_of_the_assembly_is_refused(self, client, base_path):
        (base_path / 'asm-1').mkdir()
        (base_path / 'secret.txt').write_text('not yours')
        os.symlink(str(base_path / 'secret.txt'), str(base_path / 'asm-1' / 'link.txt'))

        body, status = ar.ServeMedia().get('asm-1', 'link.txt')

        assert status == 403
        assert body == {'error': 'Invalid path'}


class TestUpdateMediaPaths:
    @pytest.mark.unit
    def test_is_a_no_op_without_steps(self):
        assert ar.update_media_paths({'id': 'a'}, 'a') == {'id': 'a'}

    @pytest.mark.unit
    def test_only_screens_with_media_get_a_url(self):
        config = {'steps': [{'screen1': {'media': 'a.png'}, 'screen2': {'media': ''}}]}
        step = ar.update_media_paths(config, 'asm')['steps'][0]

        assert step['screen1']['mediaUrl'] == '/assembly/media/asm/a.png'
        assert 'mediaUrl' not in step['screen2']

    @pytest.mark.unit
    def test_the_config_is_updated_in_place(self):
        config = {'steps': [{'screen1': {'media': 'a.png'}}]}
        assert ar.update_media_paths(config, 'asm') is config


class TestRouteRegistration:
    @pytest.mark.integration
    def test_every_path_is_registered(self, client):
        rules = {r.rule for r in client.application.url_map.iter_rules()}
        assert {'/assembly/upload',
                '/assembly/media/<string:assembly_id>/<path:filename>',
                } <= rules
