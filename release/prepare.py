"""Turn a release candidate into something signable.

CI records what moved; a human writes why it matters and what the operator will
see. The gate sits at signing rather than merge because signing already requires
someone present with a hardware token, so it can actually be enforced.

Not generated from commit messages: notes that say nothing teach people to stop
reading them. Commit subjects are offered as reference and discarded.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile

from . import manifest as manifest_mod

PLACEHOLDER_PREFIX = '<'


class PrepareError(Exception):
    pass


# --- deriving what moved ----------------------------------------------------

def derive_notes(previous, current, arch='x86'):
    """The facts, from the two manifests. No prose."""
    after = manifest_mod.components_for(current, arch)

    if not previous:
        changed = [{'component': name,
                    'from': None,
                    'to': after[name]['tag']}
                   for name in sorted(after)]
        return {'changed': changed, 'unchanged': 0,
                'features_added': sorted(manifest_mod.features_for(current, arch)),
                'features_removed': []}

    before = manifest_mod.components_for(previous, arch)

    changed, unchanged = [], 0
    for name in sorted(set(before) & set(after)):
        if before[name]['digest'] != after[name]['digest']:
            changed.append({'component': name,
                            'from': before[name]['tag'],
                            'to': after[name]['tag']})
        else:
            unchanged += 1

    return {
        'changed': changed,
        'unchanged': unchanged,
        'features_added': sorted(set(after) - set(before)),
        'features_removed': sorted(set(before) - set(after)),
    }


# --- the editor round trip --------------------------------------------------

# What an operator loses, in their words rather than container names. No
# duration: a restart figure would have to be measured on real hardware, and a
# confidently wrong number is worse here than none - this is read by someone
# deciding whether to interrupt a running line.
VISIBLE_TO_OPERATOR = {
    'backend': 'image capture is unavailable',
    'frontend': 'the operator UI reloads',
    'vision': 'vision processing pauses',
    'predictlite': 'inference pauses',
    'prediction': 'inference pauses',
    'nodecreator': 'flows restart',
    'visiontools': 'vision tools are briefly unavailable',
    'vernemq': 'MQTT clients reconnect',
}


def derive_impact(derived):
    """What an operator will see, worked out from the diff.

    Derivable because the upgrade path treats every container the same - retire,
    run, smoke check - so which containers move is exactly which ones restart.
    Written for someone deciding whether to accept this now or wait for shift
    change, which is the only question the field has to answer.
    """
    changed = [entry['component'] for entry in (derived.get('changed') or [])
               if isinstance(entry, dict) and entry.get('component')]
    if not changed:
        return 'No container changes, so nothing restarts.'

    effects = []
    for name in sorted(changed):
        effect = VISIBLE_TO_OPERATOR.get(name)
        if effect and effect not in effects:
            effects.append(effect)

    one = len(changed) == 1
    restarts = '{} container{} restart{} in turn: {}.'.format(
        len(changed), '' if one else 's', 's' if one else '',
        ', '.join(sorted(changed)))

    if not effects:
        return restarts

    return '{} While that happens, {}.'.format(restarts, '; '.join(effects))


def render_template(manifest, derived, reference=None):
    lines = [
        '# Release {} (counter {}) - notes are required before signing.'.format(
            manifest.get('release'), manifest.get('counter')),
        '# Lines beginning with # are ignored. Replace the <...> placeholders.',
        '#',
        '# ALREADY RECORDED (derived from the manifest, shown for reference):',
    ]
    if derived['changed']:
        for entry in derived['changed']:
            lines.append('#   changed:   {}  {} -> {}'.format(
                entry['component'], entry['from'] or '(new)', entry['to']))
    else:
        lines.append('#   changed:   nothing')
    lines.append('#   unchanged: {} components'.format(derived['unchanged']))
    if derived['features_added']:
        lines.append('#   features added:   {}'.format(', '.join(derived['features_added'])))
    if derived['features_removed']:
        lines.append('#   features removed: {}'.format(', '.join(derived['features_removed'])))

    if reference:
        lines += ['#', '# FOR ENGINEERS ONLY - commit subjects, for writing the notes.',
                  '# Discarded unless you copy lines under "reference:" below.']
        for line in str(reference).splitlines()[:40]:
            lines.append('#   {}'.format(line))

    lines += [
        '#',
        '# These notes are signed with the release and are visible to anyone',
        '# who can read it. Do not put credentials, hostnames or customer',
        '# names here.',
        '',
        'security: no',
        '',
        'summary:',
        '<one sentence: why this release matters>',
        '',
        '# Worked out from the diff. Edit if it understates anything - it knows',
        '# which containers restart, not what the change does. Only the summary',
        '# is left blank: a machine can restate what moved, not why it matters.',
        'impact:',
        derive_impact(derived),
        '',
        '# Optional, engineer-facing. Left out of the manifest if empty.',
        'reference:',
        '',
    ]
    return '\n'.join(lines)


def _is_placeholder(text):
    stripped = (text or '').strip()
    return stripped.startswith(PLACEHOLDER_PREFIX) and stripped.endswith('>')


def parse_notes(text):
    """Parse the edited template. Placeholders count as empty, so an unedited
    template fails the gate rather than shipping the prompt as the notes."""
    blocks = {'summary': [], 'impact': [], 'reference': []}
    security = False
    current = None

    for raw in (text or '').splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith('#'):
            continue

        lowered = line.strip().lower()
        if lowered.startswith('security:'):
            value = lowered.split(':', 1)[1].strip()
            if value not in ('yes', 'no', 'true', 'false', ''):
                raise PrepareError(
                    "security must be yes or no, got {!r}".format(value))
            security = value in ('yes', 'true')
            current = None
            continue

        if lowered in ('summary:', 'impact:', 'reference:'):
            current = lowered[:-1]
            continue

        if current:
            blocks[current].append(line)

    result = {'security': security}
    for key, collected in blocks.items():
        body = '\n'.join(collected).strip()
        body = '' if _is_placeholder(body) else body
        # reference is optional: an empty one is left out entirely rather than
        # added as a blank field to every manifest.
        if key == 'reference' and not body:
            continue
        result[key] = body
    return result


def default_editor(text):
    """Open $EDITOR on the text and return what was saved."""
    editor = os.environ.get('EDITOR') or os.environ.get('VISUAL') or 'vi'
    handle = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False)
    try:
        handle.write(text)
        handle.close()
        result = subprocess.run([editor, handle.name])
        if result.returncode != 0:
            raise PrepareError('{} exited {}'.format(editor, result.returncode))
        with open(handle.name) as reopened:
            return reopened.read()
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


# --- the gate ---------------------------------------------------------------

def prepare(candidate_raw, previous_raw=None, editor=default_editor,
            arch='x86', reference=None):
    """Returns the manifest bytes to sign, or raises.

    Refuses rather than producing a signable manifest with empty notes: this is
    the only place the requirement can be enforced, because it is the only step
    a human is guaranteed to be present for.
    """
    current = manifest_mod.loads(candidate_raw)
    previous = manifest_mod.loads(previous_raw) if previous_raw else None

    derived = derive_notes(previous, current, arch=arch)
    edited = parse_notes(editor(render_template(current, derived, reference)))

    notes = manifest_mod.blank_notes()
    notes.update(derived)
    notes.update(edited)

    candidate = dict(current)
    candidate['notes'] = manifest_mod.normalise_notes(notes)

    missing = manifest_mod.notes_shortfall(candidate)
    if missing:
        raise PrepareError(
            'refusing to prepare a signable release - notes are incomplete:\n'
            + '\n'.join('  - ' + m for m in missing))

    manifest_mod.validate(candidate)
    return manifest_mod.canonical_bytes(candidate)


def commit_reference(since_tag, run=None):
    """Commit subjects since a tag, as reference material only."""
    runner = run or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, check=False))
    span = '{}..HEAD'.format(since_tag) if since_tag else 'HEAD'
    result = runner(['git', 'log', '--no-merges', '--format=%s', span])
    if result.returncode != 0:
        return ''
    return (result.stdout or '').strip()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Add required release notes to a candidate, ready for signing.')
    parser.add_argument('candidate')
    parser.add_argument('--previous', help='the release currently promoted')
    parser.add_argument('--since-tag', help='show commit subjects since this tag')
    parser.add_argument('--arch', default='x86')
    parser.add_argument('--out', default='-')
    args = parser.parse_args(argv)

    try:
        with open(args.candidate) as handle:
            candidate_raw = handle.read()
        previous_raw = None
        if args.previous and os.path.exists(args.previous):
            with open(args.previous) as handle:
                previous_raw = handle.read()

        reference = commit_reference(args.since_tag) if args.since_tag else None
        final = prepare(candidate_raw, previous_raw, arch=args.arch,
                        reference=reference)
    except (PrepareError, manifest_mod.ManifestError) as exc:
        sys.stderr.write('{}\n'.format(exc))
        return 1

    if args.out == '-':
        sys.stdout.write(final.decode('utf-8'))
    else:
        with open(args.out, 'wb') as handle:
            handle.write(final)

    document = json.loads(final.decode('utf-8'))
    notes = document['notes']
    sys.stderr.write('release {} ready to sign\n'.format(document['release']))
    sys.stderr.write('  summary:  {}\n'.format(notes['summary']))
    sys.stderr.write('  impact:   {}\n'.format(notes['impact']))
    sys.stderr.write('  security: {}\n'.format('YES' if notes['security'] else 'no'))
    sys.stderr.write('  changed:  {}\n'.format(
        ', '.join(c['component'] for c in notes['changed']) or 'nothing'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
