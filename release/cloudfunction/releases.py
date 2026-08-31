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

# arch -> counter -> the signed release. Counters are per architecture: x86 and
# arm ship on their own cadence, so counter 7 on x86 has nothing to do with
# counter 7 on arm, and promoting one must never move the other.
RELEASES = {
    'x86': {
        # 48: {
        #     'manifest_b64': 'eyJzY2hlbWEiOiJmbGV4cnVuLnJlbGVhc2UvdjIi...',
        #     'signature':    'MEUCIQDf3n2K8pXm...==',
        # },
    },
    'arm': {},
}

# arch -> channel -> counter. This is the only thing promotion changes.
CHANNELS = {
    'x86': {'stable': None, 'beta': None},
    'arm': {'stable': None, 'beta': None},
}
