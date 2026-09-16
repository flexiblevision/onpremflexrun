"""Unit tests for the waveform classifier sync worker."""
import io
import json
import os
import zipfile

import pytest

from worker_scripts import retrieve_waveform_models as w


def make_package(tmp_path, method='waveform_classifier', name='pkg.zip'):
    path = str(tmp_path / name)
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('manifest.json', json.dumps({
            'schema': 1, 'method': method,
            'audio': {'sample_rate': 48000, 'mode': 'audio'},
            'label_map': {'noise': 0, 'tap': 1},
        }))
        zf.writestr('label_map.json', json.dumps({'noise': 0, 'tap': 1}))
        zf.writestr('classifier_v1.pth', b'\x00\x01\x02')
    return path


class TestSanitize:
    def test_strips_path_and_punctuation(self):
        # The name becomes a directory, so a slash or a dot-dot in a project
        # name must not reach the filesystem.
        assert w.sanitize_project_name('line 4/panel') == 'line_4_panel'
        assert '/' not in w.sanitize_project_name('../../etc')
        assert w.sanitize_project_name('Panel (v2)') == 'Panel__v2_'

    def test_trims_leading_junk_and_length(self):
        assert w.sanitize_project_name('__wave1') == 'wave1'
        assert len(w.sanitize_project_name('x' * 200)) == w.MAX_PROJECT_STEM


class TestManifest:
    def test_reads_the_manifest(self, tmp_path):
        assert w.read_manifest(make_package(tmp_path))['method'] == 'waveform_classifier'

    def test_raises_without_one(self, tmp_path):
        path = str(tmp_path / 'bare.zip')
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('something.txt', 'x')
        with pytest.raises(ValueError):
            w.read_manifest(path)


class TestInstall:
    def test_unpacks_under_the_version(self, tmp_path):
        project_dir = str(tmp_path / 'wave1')
        os.makedirs(project_dir)
        dest = w.install_package(make_package(tmp_path), project_dir, '17891')
        assert dest.endswith('wave1/17891')
        assert sorted(os.listdir(dest)) == ['classifier_v1.pth', 'label_map.json', 'manifest.json']

    def test_replaces_a_previous_copy(self, tmp_path):
        project_dir = str(tmp_path / 'wave1')
        os.makedirs(os.path.join(project_dir, '17891'))
        with open(os.path.join(project_dir, '17891', 'stale.txt'), 'w') as fh:
            fh.write('old')
        dest = w.install_package(make_package(tmp_path), project_dir, '17891')
        assert not os.path.exists(os.path.join(dest, 'stale.txt'))
        assert os.path.exists(os.path.join(dest, 'manifest.json'))

    # A half-written package directory is both visible and loadable to a service
    # that stats the directory, so a failed extract must leave nothing behind.
    def test_leaves_nothing_staged_on_failure(self, tmp_path):
        project_dir = str(tmp_path / 'wave1')
        os.makedirs(project_dir)
        bad = str(tmp_path / 'bad.zip')
        with open(bad, 'wb') as fh:
            fh.write(b'not a zip')
        with pytest.raises(Exception):
            w.install_package(bad, project_dir, '17891')
        assert os.listdir(project_dir) == []


class TestPrune:
    def test_removes_versions_not_in_the_plan(self, tmp_path):
        project_dir = str(tmp_path / 'wave1')
        for v in ('1', '2', '3'):
            os.makedirs(os.path.join(project_dir, v))
        assert sorted(w.prune_versions(project_dir, {'2'})) == ['1', '3']
        assert os.listdir(project_dir) == ['2']

    def test_ignores_staging_dirs(self, tmp_path):
        project_dir = str(tmp_path / 'wave1')
        os.makedirs(os.path.join(project_dir, '.staged'))
        w.prune_versions(project_dir, set())
        assert os.path.exists(os.path.join(project_dir, '.staged'))


class TestBinding:
    def test_binds_every_device_on_the_project(self, monkeypatch):
        seen = {}

        class FakeDevices:
            def update_many(self, query, update):
                seen['query'] = query
                seen['set'] = update['$set']
                return type('R', (), {'modified_count': 2})()

        monkeypatch.setattr(w, 'device_collection', FakeDevices())
        assert w.bind_devices('proj-1', '17891', '/app/data/models/wave1/17891') == 2
        assert seen['query'] == {'cloud_project.id': 'proj-1'}

        bound = seen['set']['cloud_classifier']
        assert bound['model_version'] == '17891'
        assert bound['project_id'] == 'proj-1'
        # The path the SERVICE reads, inside the container — not the host side.
        assert bound['package_path'].startswith('/app/data/')
        assert isinstance(bound['bound_at'], int)


class TestDataDirs:
    def test_reads_the_addon_mount(self):
        host, container = w.data_dirs()
        assert container == '/app/data'
        assert host == '/root/waveform'

    def test_falls_back_when_the_descriptor_is_missing(self, monkeypatch):
        monkeypatch.setattr(w, 'ADDON_JSON', '/nonexistent/addon.json')
        assert w.data_dirs() == (w.FALLBACK_HOST_DATA, w.FALLBACK_CONTAINER_DATA)


class TestPlan:
    def test_refuses_an_empty_plan(self, monkeypatch):
        monkeypatch.setattr(w, 'record_job_error', lambda m: None)
        assert w.retrieve_waveform_models({'models': {}}, 'tok') is False

    def test_rejects_a_package_of_the_wrong_method(self, tmp_path, monkeypatch):
        errors = []
        monkeypatch.setattr(w, 'record_job_error', errors.append)
        monkeypatch.setattr(w, 'update_job_progress', lambda p: None)
        monkeypatch.setattr(w, 'data_dirs', lambda: (str(tmp_path), '/app/data'))
        monkeypatch.setattr(w, 'download_package',
                            lambda token, pid, ver, dest: (
                                open(dest, 'wb').write(open(make_package(tmp_path, method='anomaly'), 'rb').read())))

        assert w.retrieve_waveform_models(
            {'models': {'p': {'_id': 'p1', 'name': 'wave1', 'models': [1]}},
             'exclude_models': []}, 'tok') is False
        assert any('method' in e for e in errors)
