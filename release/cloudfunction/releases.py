"""Published releases and channel pointers.

THIS REPOSITORY IS PUBLIC, and this is the file edited under time pressure
during a release or a rollback. Nothing but base64 manifests, signatures and
integers belongs here - no tokens, no signed URLs, no customer names, and never
the signing key.

This is the whole storage layer. There is no database, matching how
latest_stable_version already works: promotion is a source edit plus a redeploy,
which is reviewable, revertable and has an author.

Two rules:

  RELEASES is append-only. Keep old entries - they are the audit trail for
  "what exactly did we ship as 44?" and a device that lost its local cache can
  be pointed at one. They are a few KB each.

  manifest_b64 and signature are OPAQUE. The signature covers the exact
  manifest bytes, so base64 in means base64 out, character for character. Do
  not reformat, re-indent, decode-and-re-encode, or "tidy" them. Nothing in
  this file should ever parse a manifest.

To promote: add the release to RELEASES, change one integer in CHANNELS,
redeploy. To roll the fleet back: change the integer back. Devices still refuse
anything not newer than their own high-water mark, so a mistaken promote cannot
downgrade a device that already moved past it.
"""
import json
import os

# The data lives in releases.json beside this file, not in this source.
#
# It used to be dicts here, edited by hand during a release. That put a Python
# syntax error one keystroke away from the storage layer at exactly the moment
# nobody has time for it, and made promoting something a script could not do
# safely - so it was done by hand, and the deploy that has to follow it was
# forgettable. A data file is equally reviewable in a diff and can be written
# by release_cut.sh promote.
#
# Same two rules as before: RELEASES is append-only, and manifest_b64 and
# signature are opaque - base64 in, base64 out, character for character.
_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'releases.json')

with open(_DATA) as _handle:
    _loaded = json.load(_handle)

# JSON object keys are strings; the counters devices send are integers.
RELEASES = {arch: {int(counter): entry for counter, entry in entries.items()}
            for arch, entries in _loaded['releases'].items()}
CHANNELS = _loaded['channels']
