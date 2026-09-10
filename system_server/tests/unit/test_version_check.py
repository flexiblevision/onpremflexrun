"""Version comparison that decides what a device installs.

Every upgrade on the fleet goes through is_container_uptodate: the second
element of its return value is passed straight to the upgrade scripts as the
tag to pull. A wrong answer here either pins a device to a stale build or
points it at a tag that does not exist, so the shape of the return value
matters as much as the boolean.
"""
import subprocess
import pytest
from unittest.mock import patch, MagicMock

import version_check


def _popen(stdout=b'', stderr=None):
    """A subprocess.Popen double whose communicate() returns (stdout, stderr)."""
    proc = MagicMock()
    proc.communicate.return_value = (stdout, stderr)
    return proc


class TestSystemArch:
    """arch(1) output is normalised to the two names the cloud registry uses."""

    @pytest.mark.unit
    @pytest.mark.parametrize('raw,expected', [
        (b'aarch64\n', 'arm'),
        (b'x86_64\n', 'x86'),
    ])
    def test_known_arches_are_normalised(self, raw, expected):
        with patch('subprocess.Popen', return_value=_popen(raw)):
            assert version_check.system_arch() == expected

    @pytest.mark.unit
    def test_unknown_arch_passes_through_unchanged(self):
        # armv7l is not translated, so it reaches the cloud as-is and no
        # images will match. Documented rather than endorsed.
        with patch('subprocess.Popen', return_value=_popen(b'armv7l\n')):
            assert version_check.system_arch() == 'armv7l'

    @pytest.mark.unit
    def test_surrounding_whitespace_is_stripped(self):
        with patch('subprocess.Popen', return_value=_popen(b'  x86_64  \n')):
            assert version_check.system_arch() == 'x86'

    @pytest.mark.unit
    def test_invokes_arch_binary(self):
        with patch('subprocess.Popen', return_value=_popen(b'x86_64')) as popen:
            version_check.system_arch()
        assert popen.call_args[0][0] == ['arch']


class TestGetCurrentContainerVersion:
    """Reads the running image tag out of `docker inspect`."""

    @pytest.mark.unit
    def test_extracts_tag_from_quoted_image_reference(self):
        out = b"'flexiblevision/capdev:1.4.2'\n"
        with patch('subprocess.Popen', return_value=_popen(out)):
            assert version_check.get_current_container_version('capdev') == '1.4.2'

    @pytest.mark.unit
    def test_inspects_the_container_it_was_given(self):
        with patch('subprocess.Popen', return_value=_popen(b"'x:1'")) as popen:
            version_check.get_current_container_version('captureui')
        assert popen.call_args[0][0] == [
            'docker', 'inspect', "--format='{{.Config.Image}}'", 'captureui']

    @pytest.mark.unit
    def test_missing_container_returns_false(self):
        # docker inspect writes nothing to stdout when the container is absent.
        with patch('subprocess.Popen', return_value=_popen(b'\n')):
            assert version_check.get_current_container_version('nope') is False

    @pytest.mark.unit
    def test_stderr_output_returns_false(self):
        with patch('subprocess.Popen', return_value=_popen(b"'a:1'", b'boom')):
            assert version_check.get_current_container_version('capdev') is False

    @pytest.mark.unit
    def test_untagged_image_raises(self):
        # An image reference with no ':' makes split(':')[1] fail. This is a
        # real crash on a device running an untagged image; the test pins the
        # behaviour so a fix is a deliberate change, not an accident.
        with patch('subprocess.Popen', return_value=_popen(b"'flexiblevision/capdev'\n")):
            with pytest.raises(IndexError):
                version_check.get_current_container_version('capdev')


class TestGetLatestImageVersions:
    """POSTs {arch, image} to the cloud and returns the decoded list."""

    @pytest.mark.unit
    def test_returns_decoded_body(self):
        resp = MagicMock()
        resp.json.return_value = ['1.0.0', '1.1.0']
        resp.__bool__ = lambda self: True
        with patch('version_check.system_arch', return_value='x86'), \
             patch('requests.post', return_value=resp):
            assert version_check.get_latest_image_versions('backend') == ['1.0.0', '1.1.0']

    @pytest.mark.unit
    def test_posts_arch_and_image_as_json(self):
        resp = MagicMock()
        resp.json.return_value = []
        with patch('version_check.system_arch', return_value='arm'), \
             patch('requests.post', return_value=resp) as post:
            version_check.get_latest_image_versions('vision')

        url = post.call_args[0][0]
        assert url.endswith('container_versions_list')
        assert post.call_args[1]['json'] == {'arch': 'arm', 'image': 'vision'}
        assert post.call_args[1]['headers'] == {'Content-Type': 'application/json'}

    @pytest.mark.unit
    def test_error_response_returns_none(self):
        # requests.Response is falsy for 4xx/5xx, so the body is never read.
        resp = MagicMock()
        resp.__bool__ = lambda self: False
        with patch('version_check.system_arch', return_value='x86'), \
             patch('requests.post', return_value=resp):
            assert version_check.get_latest_image_versions('backend') is None


class TestLatestStableImageVersion:
    """Reads the single stable tag the fleet is supposed to be on."""

    @pytest.mark.unit
    def test_returns_body_text_on_200(self):
        resp = MagicMock(status_code=200, text='1.4.2')
        with patch('version_check.system_arch', return_value='x86'), \
             patch('requests.post', return_value=resp):
            assert version_check.latest_stable_image_version('backend') == '1.4.2'

    @pytest.mark.unit
    def test_non_200_returns_none(self):
        resp = MagicMock(status_code=500, text='error')
        with patch('version_check.system_arch', return_value='x86'), \
             patch('requests.post', return_value=resp):
            assert version_check.latest_stable_image_version('backend') is None

    @pytest.mark.unit
    def test_uses_configured_stable_ref_endpoint(self):
        resp = MagicMock(status_code=200, text='1.0.0')
        with patch('version_check.system_arch', return_value='x86'), \
             patch('requests.post', return_value=resp) as post:
            version_check.latest_stable_image_version('frontend')

        assert post.call_args[0][0].endswith(version_check.LATEST_STABLE_REF)
        assert post.call_args[1]['json'] == {'arch': 'x86', 'image': 'frontend'}


@pytest.fixture
def uptodate_env():
    """Patch the three lookups is_container_uptodate composes."""
    with patch('version_check.get_current_container_version') as current, \
         patch('version_check.get_latest_image_versions') as available, \
         patch('version_check.latest_stable_image_version') as stable:
        yield {'current': current, 'available': available, 'stable': stable}


class TestIsContainerUptodate:
    """The decision itself: (is_up_to_date, tag_to_install)."""

    @pytest.mark.unit
    def test_matching_versions_report_uptodate(self, uptodate_env):
        uptodate_env['current'].return_value = '1.4.2'
        uptodate_env['available'].return_value = ['1.4.0', '1.4.2']
        uptodate_env['stable'].return_value = '1.4.2'

        assert version_check.is_container_uptodate('backend') == (True, 'True')

    @pytest.mark.unit
    def test_outdated_container_returns_the_stable_tag_to_install(self, uptodate_env):
        uptodate_env['current'].return_value = '1.4.0'
        uptodate_env['available'].return_value = ['1.4.0', '1.4.2']
        uptodate_env['stable'].return_value = '1.4.2'

        is_uptodate, target = version_check.is_container_uptodate('backend')

        assert is_uptodate is False
        # This string is handed to the upgrade scripts as the tag to pull.
        assert target == '1.4.2'

    @pytest.mark.unit
    def test_unpublished_stable_version_suppresses_the_upgrade(self, uptodate_env):
        # The cloud advertises a stable tag that was never pushed for this
        # arch. Upgrading would pull a nonexistent image, so the device is
        # deliberately told it is current.
        uptodate_env['current'].return_value = '1.4.0'
        uptodate_env['available'].return_value = ['1.4.0']
        uptodate_env['stable'].return_value = '9.9.9'

        assert version_check.is_container_uptodate('backend') == (True, 'True')

    @pytest.mark.unit
    def test_missing_container_is_treated_as_out_of_date(self, uptodate_env):
        # get_current_container_version returns False when the container is not
        # present at all - a fresh device - and it must be offered the install.
        uptodate_env['current'].return_value = False
        uptodate_env['available'].return_value = ['1.4.2']
        uptodate_env['stable'].return_value = '1.4.2'

        assert version_check.is_container_uptodate('backend') == (False, '1.4.2')

    @pytest.mark.unit
    def test_looks_up_the_docker_name_not_the_logical_name(self, uptodate_env):
        uptodate_env['available'].return_value = ['1.0.0']
        uptodate_env['stable'].return_value = '1.0.0'
        uptodate_env['current'].return_value = '1.0.0'

        version_check.is_container_uptodate('prediction')

        # 'prediction' is the key callers use; 'localprediction' is what docker
        # knows the container as.
        uptodate_env['current'].assert_called_once_with('localprediction')
        uptodate_env['available'].assert_called_once_with('prediction')
        uptodate_env['stable'].assert_called_once_with('prediction')

    @pytest.mark.unit
    @pytest.mark.parametrize('name', sorted(version_check.CONTAINERS))
    def test_every_declared_container_resolves(self, uptodate_env, name):
        uptodate_env['current'].return_value = '1.0.0'
        uptodate_env['available'].return_value = ['1.0.0']
        uptodate_env['stable'].return_value = '1.0.0'

        assert version_check.is_container_uptodate(name) == (True, 'True')

    @pytest.mark.unit
    def test_unknown_container_name_raises(self, uptodate_env):
        with pytest.raises(KeyError):
            version_check.is_container_uptodate('not-a-container')

    @pytest.mark.unit
    def test_unreachable_cloud_raises_rather_than_reporting_uptodate(self, uptodate_env):
        # get_latest_image_versions returns None when the cloud is unreachable.
        # `in None` is a TypeError, which propagates - the device fails loudly
        # instead of silently deciding it is current.
        uptodate_env['current'].return_value = '1.4.0'
        uptodate_env['available'].return_value = None
        uptodate_env['stable'].return_value = '1.4.2'

        with pytest.raises(TypeError):
            version_check.is_container_uptodate('backend')

    @pytest.mark.unit
    def test_no_stable_version_published_suppresses_the_upgrade(self, uptodate_env):
        # latest_stable_image_version returns None on a non-200; 'None' is not
        # a published tag, so the device is left alone.
        uptodate_env['current'].return_value = '1.4.0'
        uptodate_env['available'].return_value = ['1.4.0', '1.4.2']
        uptodate_env['stable'].return_value = None

        assert version_check.is_container_uptodate('backend') == (True, 'True')


class TestContainerMap:
    """The name map is a release-path contract - upgrade_runner indexes it."""

    @pytest.mark.unit
    def test_maps_logical_names_to_docker_container_names(self):
        assert version_check.CONTAINERS == {
            'backend': 'capdev',
            'frontend': 'captureui',
            'prediction': 'localprediction',
            'predictlite': 'predictlite',
            'vision': 'vision',
            'nodecreator': 'nodecreator',
            'visiontools': 'visiontools',
        }

    @pytest.mark.unit
    def test_upgrade_runner_version_args_are_all_known_containers(self):
        import upgrade_runner
        unknown = set(upgrade_runner.VERSION_ARGS) - set(version_check.CONTAINERS)
        assert not unknown, f'upgrade_runner asks for containers version_check cannot resolve: {unknown}'
