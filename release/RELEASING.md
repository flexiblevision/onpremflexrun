# Cutting a release

Step by step. Read [KEYS.md](KEYS.md) for the signing keys themselves.

A release is a **signed list of exact image digests**. It does not build
anything and never touches the service repositories - it reads the registry and
signs what it finds. Two separate decisions feed it:

| decision | where it lives | who makes it |
|---|---|---|
| **bump** - "this build is worth naming" | `VERSION=` in each repo's `build.sh` | whoever merges to master |
| **promote** - "the fleet should run it" | `release/components.json` | you, at release time |

They are deliberately separate. Publishing an image and deciding devices should
run it are different calls, often days apart.

---

## Before you start

You need all four. Preflight checks them and refuses if any is missing.

1. **gcloud, logged in** - `gcloud auth login`. Signing is a Cloud KMS call.
2. **Docker Hub credentials** - `docker login`, then pass `--use-docker-login`.
   The `fvonprem` repos are private; without them digest resolution returns 401.
3. **A clean working tree on `onpremflexrun`.** The manifest pins this repo's
   HEAD, so a dirty tree means the pinned commit is not what you tested.
4. **`$EDITOR` set** - the notes step opens it. Defaults to `vi`.

---

## 1. Preflight

Run this first, always. It writes nothing.

    python3 -m release.cut --preflight-only --components release/components.json \
      --use-docker-login \
      --key gcpkms://projects/flexible-vision-staging/locations/us-central1/keyRings/onprem-release-signing/cryptoKeys/release-signing/versions/1

Expect:

    [ok  ] cosign installed        not needed
    [ok  ] KMS key reference       onprem-release-signing/release-signing v1
    [ok  ] gcloud installed        ...
    [ok  ] KMS key reachable       ENABLED EC_SIGN_P256_SHA256
    [ok  ] Docker Hub credentials  fvonprem (from docker login)
    [ok  ] release/VERSION         1.9
    [ok  ] git HEAD                74993d530e73
    [ok  ] release tags on remote  none yet -> next is 1
    [ok  ] working tree clean      clean

Every failure prints what to do about it. Common ones:

- **KMS key reachable** fails → `gcloud auth login`. Tokens expire.
- **Docker Hub credentials** fails → `docker login`, and pass `--use-docker-login`.
- **release tags on remote** fails → the counter lives in remote git tags. No
  network, no release.
- **working tree clean** fails → commit or stash. `--allow-dirty` exists but
  makes the pinned commit a lie about what was tested; use it for rehearsals
  only.

---

## 2. Decide what ships

Edit `release/components.json` by hand. It is a literal per-arch map of image
tags - there is no "pick the latest" logic anywhere, on purpose:

```json
{
  "components": {
    "x86": { "backend": "1.97", "vision": "1.2", "visiontools": "0.5" },
    "arm": { "backend": "1.93", "vision": "1.1" }
  }
}
```

Auto-selecting the newest tag would mean a merge reaches the factory floor with
nobody deciding it should. Editing this file makes the promote a commit - with
an author, a diff, a reviewer, and a revert.

**Commit it before cutting.** Preflight will stop you otherwise.

To see what devices run right now, without changing anything:

    python3 -m release.cut --from-stable --use-docker-login \
      --key <the gcpkms:// ref> --write-components /tmp/live.json

That reads the `latest_stable_version` endpoint and stops. Use it to seed
release 1 or to diff against what you are about to pin.

---

## 3. Cut it

    python3 -m release.cut --components release/components.json \
      --use-docker-login \
      --key gcpkms://projects/flexible-vision-staging/locations/us-central1/keyRings/onprem-release-signing/cryptoKeys/release-signing/versions/1 \
      --public-key ~/.flexrun-trust \
      --previous .release-work/manifest.json

`--public-key` verifies the signature the way a device will, before you publish.
`--previous` is the currently promoted manifest, and produces the CHANGED
markers. Both optional, both worth passing.

What happens, in order:

**a. Digests resolve.** One registry call per image. A tag that does not exist
is a hard error - a release cannot pin an image that was never pushed.

**b. Provenance is audited.** Each image is checked for the
`org.opencontainers.image.revision` label CI stamps in:

    [ok  ] backend      fvonprem/x86-backend:1.97  a1b2c3d4e5f6
    [warn] vernemq      fvonprem/x86-vernemq:dev   no revision label

Warnings do not block yet. Every image predating the CI build scripts has no
label, so enforcing on day one would block release 1. Add `--strict-provenance`
to refuse them once every component has been rebuilt through CI.

**c. The counter is reserved.** An annotated `release/<n>` tag is pushed to
`origin` **before** anything is signed:

    reserved refs/tags/release/1 on the remote - counter 1 is now spent,
    whether or not this cut finishes

That ordering is deliberate. Abandoning from here leaves a gap in the sequence,
which devices cannot see - they compare counters as integers. A *reused* counter
would silently break anti-rollback on every device that had already taken it. If
the push is refused, someone else took that number: re-run and you get the next.

**d. Your editor opens** with a notes template. `summary`, `impact` and
`security` are yours to write; `changed` / `unchanged` are filled in already.
The cut refuses to sign until `summary` says something real - these notes are
what an operator sees, so "update" is not an answer.

**e. You confirm, then it signs.** You type the release version to confirm. The
screen shows every digest and flags anything not test-gated. Read it.

**f. Verification** runs against your trust store, exactly as a device would.

Artifacts land in `.release-work/`: `candidate.json`, `manifest.json`,
`manifest.json.sig`.

---

## 4. Publish

The cut prints a block to paste into `release/cloudfunction/releases.py`:

    1: {
        'manifest_b64': '...',
        'signature':    '...',
    },

Then point the channel at it:

    CHANNELS['stable'] = 1

Deploy. **The first time you run this the function does not exist yet** - it has
never been deployed, so expect a create, not an update, and expect it to take a
few minutes:

    gcloud functions deploy release_manifest \
      --gen2 --runtime python312 --trigger-http --allow-unauthenticated \
      --entry-point release_manifest --region us-central1 \
      --source release/cloudfunction \
      --project flexible-vision-staging

`--source` is relative to your shell's working directory, not the repo - run it
from the repo root, or give the absolute path. "Provided directory does not
exist" means you were somewhere else.

`--allow-unauthenticated` is required, not optional - functions-proxy is a
transparent pass-through, and without it every device gets a 403. Devices must
reach this through `functions-proxy.flexiblevision.com`, the single IP customer
firewalls allow - never the `*.run.app` address Gen 2 also publishes.

Confirm it serves:

    curl -s -X POST https://functions-proxy.flexiblevision.com/release_manifest \
      -H 'Content-Type: application/json' -d '{"channel":"stable"}' | head -c 300

A 404 page from Google means the function is not deployed, or the proxy has no
route to it yet. To tell those apart, check the old endpoint still answers -
if this returns a version, the proxy is fine and the problem is the deploy:

    curl -s -X POST https://functions-proxy.flexiblevision.com/latest_stable_version \
      -H 'Content-Type: application/json' -d '{"arch":"x86","image":"backend"}'

---

## If something goes wrong

**Aborted mid-cut.** The counter is spent. Nothing was published. Re-run; you
get the next number. Gaps are invisible to devices.

**Signed the wrong thing, not yet published.** Nothing to undo - publishing is
the separate paste-and-deploy step above. Fix `components.json` and cut again.

**Published the wrong thing.** Point `CHANNELS['stable']` back at the previous
counter and redeploy. Devices refuse anything not newer than their own
high-water mark, so a device that already moved past it will not downgrade -
that is the anti-rollback working, not a fault.

**A tag was reserved but the cut failed.** Leave the tag. It costs a number and
nothing else.

---

## Known gaps

Honest list. None block a cut; all affect what a release can claim.

- **The device side is not wired.** Nothing on a device fetches or verifies a
  manifest yet, no public key is provisioned at install, and `/rollback` returns
  501. `latest_stable_version` is still what the fleet actually follows. Cutting
  a release establishes the signed record; it does not change device behaviour.
- **The cloud function is not deployed.** `release_manifest` does not exist in
  `flexible-vision-staging` yet - the proxy returns 404 for it, while
  `latest_stable_version` answers normally. Step 4 creates it. Nothing depends
  on it until the device side is wired, so this does not block cutting.
- **Publishing is manual** - paste and redeploy.
- **Provenance is advisory** until `--strict-provenance` is the default.
- **`vernemq: "dev"` is a channel, not a version.** Digest pinning still makes
  the release exact, but two cuts a week apart can pin different bytes under an
  unchanged name.
- **`arm-nodecreator` has no CI build.** ARM needs qemu; `arm/build.sh` is still
  run by hand.
