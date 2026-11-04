"""Discover the addon catalog.

Discovery is eager and strict: one bad descriptor raises rather than being
skipped, because a skipped addon vanishes from the UI and from the release
pinning without anyone being told.

This is also the enumeration release/manifest.py deliberately does not carry -
components() is what build_release.py should populate features= from.
"""
import json
import os

from . import schema

CATALOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'catalog')
DESCRIPTOR = 'addon.json'


class RegistryError(Exception):
    pass


def load(catalog_dir=CATALOG_DIR):
    """Every descriptor in the catalog, keyed by name."""
    if not os.path.isdir(catalog_dir):
        raise RegistryError('addon catalog not found at {}'.format(catalog_dir))

    addons = {}
    for entry in sorted(os.listdir(catalog_dir)):
        path = os.path.join(catalog_dir, entry, DESCRIPTOR)
        if not os.path.isfile(path):
            continue

        try:
            with open(path) as handle:
                descriptor = json.load(handle)
        except ValueError as exc:
            raise RegistryError('{} is not valid JSON: {}'.format(path, exc))

        schema.validate(descriptor, source=path)

        name = descriptor['name']
        if name != entry:
            raise RegistryError(
                '{} declares name {!r} but lives in {}/ - the folder is how a '
                'reader finds it'.format(path, name, entry))
        if name in addons:
            raise RegistryError('two addons named {!r}'.format(name))

        descriptor['_path'] = os.path.dirname(path)
        addons[name] = descriptor

    return addons


def get(name, catalog_dir=CATALOG_DIR):
    addons = load(catalog_dir)
    if name not in addons:
        raise RegistryError(
            'no addon named {!r} (have: {})'
            .format(name, ', '.join(sorted(addons)) or 'none'))
    return addons[name]


def for_arch(arch, catalog_dir=CATALOG_DIR):
    """Addons installable on this device. ocr is x86-only, for instance."""
    return {name: addon for name, addon in load(catalog_dir).items()
            if arch in addon['arches']}


def containers(catalog_dir=CATALOG_DIR):
    """Addons the release pins and the upgrade path redeploys."""
    return {name: addon for name, addon in load(catalog_dir).items()
            if addon['kind'] == schema.KIND_CONTAINER}


def components(catalog_dir=CATALOG_DIR):
    """{addon name: release component}, the input to a manifest's features."""
    return {name: addon['component']
            for name, addon in containers(catalog_dir).items()}


def hooks_module(addon):
    """Import an addon's hooks.py, for the kinds that cannot be declarative."""
    if not addon.get('hooks'):
        return None

    import importlib.util

    path = os.path.join(addon['_path'], addon['hooks'])
    if not os.path.isfile(path):
        raise RegistryError(
            'addon {!r} declares hooks {!r} but {} does not exist'
            .format(addon['name'], addon['hooks'], path))

    spec = importlib.util.spec_from_file_location(
        'addons.catalog.{}.hooks'.format(addon['name']), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
