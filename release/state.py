"""What release this device is running, and what it can go back to.

Three values, and the distinction between the first two is the whole point:

  installed    the release running now
  high_water   the highest counter ever accepted here. The automatic path
               compares against THIS, so a device that rolled back cannot be
               pushed straight back down by whatever the channel is serving.
  history      releases this device has actually applied. Rollback is limited
               to these, so nobody can walk a device backwards into a release
               it never ran.

Rolling back moves `installed` and leaves `high_water` alone. That is what
keeps "a newer release is available" true while the device sits on an older one.

The collection is injected rather than imported so this is testable without a
database, and so the device and the tests cannot drift apart.
"""
import datetime

STATE_TYPE = 'release_state'

# Enough to get back to something that worked without keeping images forever.
# The cleanup job's retention has to outlast this or the history lists releases
# whose images are gone.
MAX_HISTORY = 10


class StateError(Exception):
    pass


def _now_iso(now=None):
    moment = now or datetime.datetime.utcnow()
    return moment.replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')


def _blank():
    return {'installed': None, 'high_water': 0, 'history': []}


def read(collection):
    """Current state, or a blank record for a device that has never applied one."""
    try:
        found = collection.find_one({'type': STATE_TYPE}, {'_id': 0})
    except Exception as exc:
        raise StateError('could not read release state: {}'.format(exc))

    if not found:
        return _blank()

    state = _blank()
    state.update({k: v for k, v in found.items() if k in state})

    # A high_water below the installed counter would silently re-open the
    # downgrade the counter exists to prevent, so repair it on read rather
    # than trusting whatever is in the database.
    installed = state.get('installed') or {}
    counter = installed.get('counter') or 0
    if not isinstance(state.get('high_water'), int) or state['high_water'] < counter:
        state['high_water'] = counter

    if not isinstance(state.get('history'), list):
        state['history'] = []
    return state


def record_applied(collection, manifest, now=None, rolled_back=False):
    """Record that a release is now running.

    `rolled_back` marks a deliberate move to an older release: `installed`
    changes but `high_water` is left where it was.
    """
    counter = manifest.get('counter')
    if not isinstance(counter, int) or isinstance(counter, bool):
        raise StateError('manifest has no usable counter: {!r}'.format(counter))

    state = read(collection)
    stamp = _now_iso(now)

    entry = {
        'counter': counter,
        'release': manifest.get('release'),
        'applied_at': stamp,
    }

    history = [h for h in state['history'] if h.get('counter') != counter]
    history.append(entry)
    history.sort(key=lambda h: h.get('counter') or 0)
    history = history[-MAX_HISTORY:]

    high_water = state['high_water'] if rolled_back else max(state['high_water'], counter)

    record = {
        'type': STATE_TYPE,
        'installed': entry,
        'high_water': high_water,
        'history': history,
        'last_change': stamp,
        'last_change_was_rollback': bool(rolled_back),
    }

    try:
        collection.update_one({'type': STATE_TYPE}, {'$set': record}, upsert=True)
    except Exception as exc:
        raise StateError('could not write release state: {}'.format(exc))

    return record


def rollback_targets(collection):
    """Releases this device could go back to: its history, minus what is running.

    Newest first, because that is the order an operator wants them in.
    """
    state = read(collection)
    installed = (state.get('installed') or {}).get('counter')
    targets = [h for h in state['history'] if h.get('counter') != installed]
    targets.sort(key=lambda h: h.get('counter') or 0, reverse=True)
    return targets


def known_counters(collection):
    """The set verify_rollback() is allowed to accept."""
    return {h['counter'] for h in read(collection)['history'] if 'counter' in h}


def summary(collection, available=None):
    """What the settings screen needs, in one call.

    `available` is the release the channel is currently offering, if known.
    A release equal to high_water is reported as `rolled_back_from` rather than
    as an upgrade: the device left it deliberately and should not be nagged to
    reapply it as though it were new.
    """
    state = read(collection)
    installed = state.get('installed') or {}
    high_water = state['high_water']

    result = {
        'installed': installed or None,
        'high_water': high_water,
        'history': list(reversed(state['history'])),
        'rollback_targets': rollback_targets(collection),
        'available': None,
        'update_available': False,
        'rolled_back_from': None,
    }

    if available:
        counter = available.get('counter')
        result['available'] = available
        if isinstance(counter, int) and counter > high_water:
            result['update_available'] = True
        elif isinstance(counter, int) and counter == high_water \
                and counter != installed.get('counter'):
            result['rolled_back_from'] = available

    return result
