"""USB image export.

Both endpoints mount a removable drive and write to it. The check that matters
is the boot-partition guard: writing to /boot/efi on a device whose only USB
"drive" is the boot medium would corrupt the machine it is running on.
"""
import base64
import json
import pytest
from unittest.mock import patch, MagicMock, call

from routes import image_routes


PNG = base64.b64encode(b'\x89PNG\r\n\x1a\n fake image bytes').decode()


@pytest.fixture
def client():
    from flask import Flask
    from flask_restx import Api
    app = Flask(__name__)
    app.config['TESTING'] = True
    api = Api(app)
    image_routes.register_routes(api)
    return app.test_client()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return tmp_path


def _mount_commands(system):
    """mount/umount calls only - tmp_path names can contain 'mount' themselves."""
    return [c[0][0] for c in system.call_args_list
            if c[0][0].startswith(('sudo mount', 'sudo umount'))]


def _lsblk(mountpoint):
    proc = MagicMock()
    proc.communicate.return_value = (mountpoint.encode(), b'')
    return proc


class TestSaveImage:
    @pytest.mark.integration
    def test_writes_the_decoded_image_under_home(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system'):
            response = client.post('/save_img', json={'img': PNG})

        assert response.status_code == 200
        written = list(home.rglob('*.jpg'))
        assert len(written) == 1
        assert written[0].read_bytes() == base64.b64decode(PNG)

    @pytest.mark.integration
    def test_snapshots_are_filed_under_todays_date(self, client, home):
        import time
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system'):
            client.post('/save_img', json={'img': PNG})

        written = list(home.rglob('*.jpg'))[0]
        assert written.parent.name == time.strftime('%d-%m-%y')
        assert 'flexible_vision/snapshots' in str(written)

    @pytest.mark.integration
    def test_no_usb_connected_is_a_400(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=[]), \
             patch('subprocess.Popen') as popen:
            response = client.post('/save_img', json={'img': PNG})

        assert response.status_code == 400
        assert response.get_json() == {'error': 'No USB drive connected'}
        popen.assert_not_called()

    @pytest.mark.integration
    def test_the_boot_partition_is_refused(self, client, home):
        # Writing here would mount and overwrite the device's own boot medium.
        with patch.object(image_routes, 'list_usb_paths', return_value=['sda1']), \
             patch('subprocess.Popen', return_value=_lsblk('/boot/efi')), \
             patch('os.system') as system:
            response = client.post('/save_img', json={'img': PNG})

        system.assert_not_called()
        assert list(home.rglob('*.jpg')) == []
        assert response.get_json() is False

    @pytest.mark.integration
    def test_an_unmounted_device_is_refused(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('')), \
             patch('os.system') as system:
            client.post('/save_img', json={'img': PNG})

        system.assert_not_called()

    @pytest.mark.integration
    def test_a_scsi_device_is_mounted_and_unmounted_around_the_write(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system') as system:
            client.post('/save_img', json={'img': PNG})

        commands = [c[0][0] for c in system.call_args_list]
        assert commands[0].startswith('sudo mount /dev/sdb1')
        assert commands[-1].startswith('sudo umount /dev/sdb1')

    @pytest.mark.integration
    def test_a_non_scsi_device_is_not_mounted(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['nvme0n1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system') as system:
            response = client.post('/save_img', json={'img': PNG})

        assert response.status_code == 200
        system.assert_not_called()

    @pytest.mark.integration
    def test_a_payload_without_an_image_writes_nothing(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system') as system:
            response = client.post('/save_img', json={'note': 'no image here'})

        assert response.status_code == 200
        assert list(home.rglob('*.jpg')) == []
        system.assert_not_called()

    @pytest.mark.integration
    def test_the_last_connected_drive_is_used(self, client, home):
        with patch.object(image_routes, 'list_usb_paths',
                          return_value=['sdb1', 'sdc1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')) as popen, \
             patch('os.system'):
            client.post('/save_img', json={'img': PNG})

        assert popen.call_args[0][0][-1] == '/dev/sdc1'


class TestExportImage:
    def _payload(self, **extra):
        data = {'img': PNG, 'model': 'widgets', 'version': 'v3'}
        data.update(extra)
        return data

    @pytest.mark.integration
    def test_writes_the_image_under_model_and_version(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system'):
            client.post('/export_img', json=self._payload())

        written = list(home.rglob('*.jpg'))
        assert len(written) == 1
        assert 'flexible_vision/widgets/v3/images' in str(written[0])
        assert written[0].read_bytes() == base64.b64decode(PNG)

    @pytest.mark.integration
    def test_no_usb_connected_is_a_400(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=[]), \
             patch('os.system'):
            response = client.post('/export_img', json=self._payload())

        assert response.status_code == 400
        assert response.get_json() == {'error': 'No USB drive connected'}

    @pytest.mark.integration
    def test_the_boot_partition_is_refused(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['sda1']), \
             patch('subprocess.Popen', return_value=_lsblk('/boot/efi')), \
             patch('os.system') as system:
            response = client.post('/export_img', json=self._payload())

        assert response.get_json() is False
        # The staging mkdir may run, but nothing is ever mounted.
        assert not _mount_commands(system)

    @pytest.mark.integration
    def test_an_unmounted_device_is_refused(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('')), \
             patch('os.system') as system:
            assert client.post('/export_img', json=self._payload()).get_json() is False
        assert not _mount_commands(system)

    @pytest.mark.integration
    def test_inference_json_is_written_alongside_the_image(self, client, home):
        inference = {'did': 'abc123', 'labels': ['ok']}
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system'):
            client.post('/export_img', json=self._payload(inference=inference))

        written = list(home.rglob('*.json'))
        assert len(written) == 1
        assert 'flexible_vision/widgets/v3/inferences' in str(written[0])
        assert json.loads(written[0].read_text()) == inference

    @pytest.mark.integration
    def test_the_detection_id_is_appended_to_both_filenames(self, client, home):
        # The image and its inference have to be pairable after the fact.
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system'):
            client.post('/export_img',
                        json=self._payload(inference={'did': 'abc123'}))

        image = list(home.rglob('*.jpg'))[0]
        meta = list(home.rglob('*.json'))[0]
        assert image.stem.endswith('_abc123')
        assert meta.stem == image.stem

    @pytest.mark.integration
    def test_an_inference_without_a_detection_id_still_writes(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system'):
            client.post('/export_img', json=self._payload(inference={'labels': []}))

        assert len(list(home.rglob('*.json'))) == 1

    @pytest.mark.integration
    def test_no_inference_writes_only_the_image(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system'):
            client.post('/export_img', json=self._payload())

        assert list(home.rglob('*.json')) == []

    @pytest.mark.integration
    def test_the_drive_is_unmounted_after_writing(self, client, home):
        # A drive left mounted is a drive a technician pulls and corrupts.
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system') as system:
            client.post('/export_img', json=self._payload())

        assert system.call_args_list[-1][0][0].startswith('sudo umount /dev/sdb1')

    @pytest.mark.integration
    def test_a_non_scsi_device_is_skipped(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['nvme0n1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system'):
            client.post('/export_img', json=self._payload())

        assert list(home.rglob('*.jpg')) == []

    @pytest.mark.integration
    def test_the_staging_directory_is_created_when_missing(self, client, home):
        with patch.object(image_routes, 'list_usb_paths', return_value=['sdb1']), \
             patch('subprocess.Popen', return_value=_lsblk('/media/usb')), \
             patch('os.system') as system:
            client.post('/export_img', json=self._payload())

        assert system.call_args_list[0][0][0] == 'mkdir ' + str(home) + '/usb_images'


class TestRouteRegistration:
    @pytest.mark.integration
    def test_both_paths_are_registered(self, client):
        rules = {r.rule for r in client.application.url_map.iter_rules()}
        assert {'/save_img', '/export_img'} <= rules
