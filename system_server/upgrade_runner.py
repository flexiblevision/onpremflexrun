"""Detached runner for a full system upgrade.

POST /upgrade starts this and returns immediately. An upgrade takes far longer
than any reasonable HTTP timeout, and its last step restarts the very server
that would otherwise be waiting on it - so running it inside the request means
the caller never learns the outcome either way.

The runner is started in its own session, so `forever stop server.py` at the
end of start_servers.sh cannot truncate it part-way through.

A single-holder lock means a retried or double-submitted request cannot start a
second concurrent upgrade: two runs would fight over the same containers and
the same staging paths.
"""
import datetime
import errno
import os
import subprocess
import sys

# Exit codes are defined in upgrades/upgrade_flex_run.sh; keep in sync with it.
FLEX_RUN_ERRORS = {
    10: 'Bad or missing fvconfig.json - could not determine which branch to deploy',
    11: 'Could not fetch the update from git - check the network and try again',
    12: 'Fetched update was incomplete - nothing was changed',
    13: 'Not enough disk space to apply the update',
    14: 'Copying the update into place failed - do not reboot, contact support',
    15: 'Update did not match the version this release was signed for - nothing was changed',
}

LOCK_PATH = os.environ.get('FLEXRUN_UPGRADE_LOCK', '/var/lock/flex-run-upgrade.lock')
LOG_DIR = os.environ.get('FLEXRUN_UPGRADE_LOG_DIR', '/var/log/flex-run')

VERSION_ARGS = ('backend', 'frontend', 'prediction', 'predictlite',
                'vision', 'nodecreator', 'visiontools')

# Where the resolved plan is written for the deploy scripts to read. Under
# /var/lib rather than /tmp: the upgrade restarts this server part-way through,
# and a tmpfs cleaned on reboot would leave the second half of the run pulling
# by tag with no sign anything was wrong.
PLAN_PATH = os.environ.get('FLEXRUN_PLAN', '/var/lib/flex-run/plan')


def flex_run_error(code):
    return FLEX_RUN_ERRORS.get(code, 'Update fetch failed (exit {})'.format(code))


def _now_ms():
    return int(datetime.datetime.now().timestamp() * 1000)


def _records():
    from pymongo import MongoClient
    client = MongoClient(os.environ.get('MONGO_SERVER', '172.17.0.1'),
                         int(os.environ.get('MONGO_PORT', 27017)),
                         serverSelectionTimeoutMS=5000)
    return client['fvonprem']['upgrade_records']


# --- locking ---------------------------------------------------------------

def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError as exc:
        # EPERM means the process exists but belongs to someone else.
        return exc.errno == errno.EPERM
    return True


def lock_info():
    """(pid, run_id) of a live upgrade, or (None, None). Clears a stale lock."""
    try:
        with open(LOCK_PATH) as handle:
            fields = handle.read().split()
        pid = int(fields[0])
        run_id = fields[1] if len(fields) > 1 else None
    except (IOError, OSError, ValueError, IndexError):
        return None, None

    # A pid of 0 or below is not a process: os.kill() would interpret it as a
    # process group (-1 means "everything we can signal") and wrongly report
    # the lock as held, blocking upgrades forever.
    if pid > 0 and _pid_alive(pid):
        return pid, run_id

    # The run that held this died without releasing it.
    try:
        os.unlink(LOCK_PATH)
    except OSError:
        pass
    return None, None


def lock_holder():
    """Return the pid of a live upgrade, or None."""
    return lock_info()[0]


def acquire_lock(run_id):
    """Atomically take the lock. False if another live run already holds it."""
    directory = os.path.dirname(LOCK_PATH)
    if directory and not os.path.isdir(directory):
        try:
            os.makedirs(directory)
        except OSError:
            pass

    for _ in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            if lock_holder() is not None:
                return False
            # Nobody live holds it, so whatever is there is stale or corrupt -
            # an unparseable lock file must not block upgrades forever.
            try:
                os.unlink(LOCK_PATH)
            except OSError:
                pass
            continue
        with os.fdopen(fd, 'w') as handle:
            handle.write('{} {}\n'.format(os.getpid(), run_id))
        return True

    return False


def release_lock():
    try:
        os.unlink(LOCK_PATH)
    except OSError:
        pass


# --- status ----------------------------------------------------------------

def _latest_record():
    try:
        records = _records()
        found = list(records.find().sort('start_time', -1).limit(1))
        if not found:
            return None
        record = found[0]
        record.pop('_id', None)
        return record
    except Exception:
        return None


def status():
    """What the operator needs to know: is one running, and how far along."""
    holder, run_id = lock_info()
    record = _latest_record()

    # A run in progress whose shell steps have not been recorded yet. Without
    # this the previous run's completed record would be reported as the current
    # state, and a poller would conclude the new upgrade had already finished.
    if holder and (record is None or record.get('id') != run_id):
        return {'state': 'running', 'id': run_id, 'pid': holder,
                'cur_step': 0, 'upgrade_steps': None,
                'cur_step_txt': 'fetching update', 'running': True}

    if record is None:
        return {'state': 'idle'}

    record['running'] = holder is not None
    if holder:
        record['pid'] = holder
    elif record.get('state') == 'running':
        # No process holds the lock, so a record still marked running is stale.
        record['state'] = 'interrupted'
        record['cur_step_txt'] = 'upgrade stopped before finishing'
    return record


# --- the run ---------------------------------------------------------------

def _mark_failed(run_id, message):
    """Fail the record the shell created for this run, or create one."""
    sys.stderr.write('[upgrade_runner] FAILED: {}\n'.format(message))
    fields = {'state': 'failed', 'cur_step_txt': message,
              'last_updated': _now_ms(), 'end_time': _now_ms()}
    try:
        records = _records()
        running = list(records.find({'state': 'running'}).sort('start_time', -1).limit(1))
        if running:
            records.update_one({'_id': running[0]['_id']}, {'$set': fields})
        else:
            fields.update({'id': run_id, 'start_time': _now_ms(),
                           'cur_step': 0, 'upgrade_steps': 0, 'log': message})
            records.update_one({'id': run_id}, {'$set': fields}, upsert=True)
    except Exception as exc:
        sys.stderr.write('[upgrade_runner] could not record failure: {}\n'.format(exc))


def run(run_id, versions, plan_path=None):
    home = os.environ['HOME']
    flex_run = os.path.join(home, 'flex-run', 'upgrades', 'upgrade_flex_run.sh')
    upgrade_system = os.path.join(home, 'flex-run', 'system_server', 'upgrade_system.sh')

    print('[upgrade_runner] run {} starting'.format(run_id))

    refresh = subprocess.run(['sh', flex_run], capture_output=True, text=True)
    sys.stdout.write(refresh.stdout or '')
    sys.stderr.write(refresh.stderr or '')

    # upgrade_system.sh is one of the files the refresh replaces. Continuing
    # after a failed refresh runs the old scripts against the new versions.
    if refresh.returncode != 0:
        _mark_failed(run_id, flex_run_error(refresh.returncode))
        return refresh.returncode

    # The shell scripts record their own step progress against this id.
    env = dict(os.environ, FLEXRUN_RUN_ID=run_id)

    # Set only when a verified manifest drove this run. Without it the deploy
    # scripts fall back to the positional versions, which is how an upgrade
    # started by an older runner still works.
    if plan_path:
        env['FLEXRUN_PLAN'] = plan_path
    system = subprocess.run(['sh', upgrade_system] + list(versions), env=env)

    if system.returncode != 0:
        _mark_failed(run_id, 'Container upgrade did not complete '
                             '(exit {})'.format(system.returncode))
        return system.returncode

    print('[upgrade_runner] run {} finished'.format(run_id))
    return 0


def _release_collection():
    from pymongo import MongoClient
    client = MongoClient(os.environ.get('MONGO_SERVER', '172.17.0.1'),
                         int(os.environ.get('MONGO_PORT', 27017)),
                         serverSelectionTimeoutMS=5000)
    return client['fvonprem']['utils']


def _running_versions():
    """What each container is on right now, by inspecting them.

    Without this every component in a release looks like a change, so applying
    a release that pins what is already running would tear down and recreate
    every container for nothing - minutes of downtime on a line to install
    software it was already running.

    A container that cannot be inspected is left out rather than guessed at:
    absent from this map means "not known to be current", so it is upgraded.
    """
    from version_check import CONTAINERS, get_current_container_version

    running = {}
    for component, container in CONTAINERS.items():
        try:
            version = get_current_container_version(container)
        except Exception:
            continue
        if version:
            running[component] = version
    return running


def run_release(run_id, arch, channel='stable', counter=None,
                collection=None, now=None, plan_path=None,
                trust_dir=None, current=None):
    """Fetch a signed release, verify it, and apply exactly what it pins.

    The order is the point. Nothing is fetched until we know what this device
    has already accepted, nothing is applied until the signature and the
    counter check out, and nothing is recorded until the containers actually
    moved - so an interrupted run leaves the device on the release it was on,
    not on a half-recorded one.
    """
    import datetime as _dt
    from release import apply as apply_mod
    from release import fetch as fetch_mod
    from release import state as state_mod
    from release import trust as trust_mod
    from release import verify as verify_mod

    collection = collection if collection is not None else _release_collection()
    now = now or _dt.datetime.utcnow()
    plan_path = plan_path or PLAN_PATH
    trust_dir = trust_dir or os.environ.get('FLEXRUN_TRUST_DIR',
                                            trust_mod.DEFAULT_TRUST_DIR)

    existing = state_mod.read(collection)
    high_water = existing.get('high_water') or 0

    raw, signature, _envelope = fetch_mod.fetch_release(
        arch, channel=channel, counter=counter)

    signature_file = os.path.join(os.path.dirname(plan_path) or '.',
                                  'manifest.sig')
    if not os.path.isdir(os.path.dirname(signature_file) or '.'):
        os.makedirs(os.path.dirname(signature_file))
    with open(signature_file, 'w') as handle:
        handle.write(signature)
    manifest_file = os.path.join(os.path.dirname(plan_path) or '.',
                                 'manifest.json')
    with open(manifest_file, 'wb') as handle:
        handle.write(raw)

    if counter is None:
        parsed = verify_mod.verify(
            raw, arch, high_water, now,
            signature_path=signature_file, manifest_path=manifest_file,
            public_key_path=trust_dir)
    else:
        # An explicit counter is a rollback: it is deliberately older, so the
        # high-water check must not apply - but it still has to be a release
        # this device has actually run.
        parsed = verify_mod.verify_rollback(
            raw, arch, state_mod.known_counters(collection), now,
            signature_path=signature_file, manifest_path=manifest_file,
            public_key_path=trust_dir)

    if current is None:
        current = _running_versions()
    plan = apply_mod.plan(parsed, arch, current=current, plan_path=plan_path)
    print('[upgrade_runner] release {} (counter {}) moves: {}'.format(
        plan['release'], plan['counter'], ', '.join(plan['changing']) or 'nothing'))

    code = run(run_id, plan['versions'], plan_path=plan['plan_path'])
    if code != 0:
        return code

    state_mod.record_applied(collection, parsed, now=now,
                             rolled_back=counter is not None)
    print('[upgrade_runner] recorded release {} as installed'.format(
        plan['release']))
    return 0


USAGE = (
    'usage: upgrade_runner.py <run-id> <7 version args>\n'
    '       upgrade_runner.py --release  <run-id> [channel]\n'
    '       upgrade_runner.py --rollback <run-id> <counter>\n')


def _device_arch():
    from utils.device_utils import system_arch
    return system_arch()


def _legacy_versions():
    """What the pre-manifest path upgrades to: whatever latest_stable_version
    currently serves."""
    from version_check import is_container_uptodate
    return [is_container_uptodate(name)[1] for name in VERSION_ARGS]


def _release_or_legacy(run_id, channel='stable'):
    """Prefer the signed manifest; fall back to the old version endpoint.

    The fallback is only for a release that cannot be OBTAINED - the endpoint
    is unreachable, or nothing is promoted yet. A manifest that arrives and
    fails verification is not a reason to go and do the same upgrade
    unverified: that would make the signature advisory, which is the one thing
    it must never be.
    """
    from release import fetch as fetch_mod

    try:
        return run_release(run_id, _device_arch(), channel=channel)
    except fetch_mod.FetchError as exc:
        print('[upgrade_runner] no signed release available ({}) - falling '
              'back to the version endpoint'.format(exc))

    return run(run_id, _legacy_versions())


def main(argv):
    if not argv:
        sys.stderr.write(USAGE)
        return 2

    mode = argv[0] if argv[0].startswith('--') else None
    rest = argv[1:] if mode else argv

    if mode in ('--release', '--rollback') and len(rest) < 1:
        sys.stderr.write(USAGE)
        return 2
    if mode is None and len(argv) < 2:
        sys.stderr.write(USAGE)
        return 2

    run_id = rest[0]

    # One lock for every mode: an upgrade and a rollback fight over the same
    # containers and the same staging paths just as two upgrades would.
    if not acquire_lock(run_id):
        sys.stderr.write('[upgrade_runner] another upgrade is already running\n')
        return 3

    try:
        if mode == '--rollback':
            if len(rest) < 2:
                sys.stderr.write(USAGE)
                return 2
            return run_release(run_id, _device_arch(), counter=int(rest[1]))
        if mode == '--release':
            channel = rest[1] if len(rest) > 1 else 'stable'
            return _release_or_legacy(run_id, channel)
        return run(run_id, argv[1:])
    except Exception as exc:
        _mark_failed(run_id, 'Upgrade runner crashed: {}'.format(exc))
        raise
    finally:
        release_lock()


def log_path(run_id):
    if not os.path.isdir(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except OSError:
            return None
    return os.path.join(LOG_DIR, 'upgrade-{}.log'.format(run_id))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
