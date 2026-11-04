# Releasing

A release is a signed list of exact image digests. The tool builds nothing — it
reads the registry and signs what it finds. So the work happens in this order:
**get images published → choose which ones ship → sign → publish.**

Keys: [KEYS.md](KEYS.md)

```bash
KEY=gcpkms://projects/flexible-vision-staging/locations/us-central1/keyRings/onprem-release-signing/cryptoKeys/release-signing/versions/1
```

---

## A. Publish the images  *(per service repo that changed)*

**Nothing to bump.** Merging to the default branch mints the next version by
itself.

1. **Merge the PR.** `master` for every repo except `nodecreator`, which is
   `main`.
2. **Watch the Actions run.** It must go green through:
   - tests (or compile, depending on the repo)
   - `Work out the next version` — prints the version it chose
   - `Build`
   - `Confirm the commit was stamped in`
   - `Reserve the version tag`
   - `Push the commit tag` / `Push the version tag`

That's it. You don't need to note the version — phase B finds it.

Repos: `visionapi`, `onprembackend`, `onpremfrontend`, `predictlite`,
`visiontools`, `nodecreator`.

> `arm-nodecreator` has no CI build — ARM needs qemu. Run `arm/build.sh` by hand.

### How the version is chosen

Each repo has a `VERSION` file beside its `build.sh` holding a **series** — the
stable prefix. CI appends the next build number, taken from the highest
`v<SERIES>.<n>` git tag:

| repo | series | next version |
|---|---|---|
| visionapi | `1` | 1.3 |
| onprembackend | `1` | 1.98 |
| onpremfrontend | `1.9` | 1.9.5 |
| predictlite | `0` | 0.2 |
| visiontools | `0` | 0.46 |
| nodecreator | `0` | 0.6 |

The build number is always the **last** component, which is what makes versions
compare correctly — `0.44 → 0.45`, never `0.5`, because 5 sorts below 44. CI
owning that component is what removes the trap.

**Change the series only when it means something** — a new product line, a
breaking change. Edit the `VERSION` file, merge, and the count continues from
wherever the tags are.

A local `./build.sh` with no `BUILD_NUMBER` builds `<series>-dev` and refuses to
push, so a laptop build can never reach the registry.

---

## B. Choose what ships

You don't type version numbers. Ask the registry what CI has published:

```bash
python3 -m release.cut --update-components \
  --components release/components.json --use-docker-login --key "$KEY"
```

It rewrites `release/components.json` and prints what moved:

```
  [move] x86  visiontools  0.43 -> 0.45
  [ok  ] x86  vision       1.2 (already newest)
  [warn] arm  backend      1.93 kept - no CI-built tag in the newest 12
```

Then:

```bash
git diff release/components.json      # read what would change
git commit -am "promote visiontools 0.45"
git push
```

**That commit is the promote.** The lookup is automatic; the decision is not.

It will only ever offer images CI built — anything without a provenance label is
skipped — and it never moves a pin backwards.

Push before cutting: the manifest pins this repo's HEAD, and the counter is
reserved by pushing a tag.

To see what devices run right now instead:

```bash
python3 -m release.cut --from-stable --use-docker-login --key "$KEY" \
  --write-components /tmp/live.json
```

---

## C. Cut

**Prereqs:** `gcloud auth login`, `docker login`, clean tree, `$EDITOR` set.

1. **Preflight** — writes nothing, and each failure prints its own fix:

```bash
python3 -m release.cut --preflight-only \
  --components release/components.json --use-docker-login --key "$KEY"
```

2. **Run the guided cut:**

```bash
python3 -m release.cut --components release/components.json \
  --use-docker-login --key "$KEY" \
  --public-key ~/.flexrun-trust \
  --previous .release-work/manifest.json
```

It walks you through, in order:

- resolves every digest
- audits provenance (which images carry a CI-recorded commit)
- **reserves the counter** on the remote — from here, aborting spends the number
- opens your editor for release notes
- shows every digest and asks you to type the version to confirm
- signs with KMS
- verifies the signature the way a device will

Output lands in `.release-work/`.

---

## D. Publish

1. Paste the printed block into `RELEASES` in `release/cloudfunction/releases.py`
2. Set `CHANNELS['stable'] = <counter>`
3. Deploy:

```bash
gcloud functions deploy release_manifest \
  --gen2 --runtime python312 --trigger-http --allow-unauthenticated \
  --entry-point release_manifest --region us-central1 \
  --source release/cloudfunction --project flexible-vision-staging
```

4. Confirm:

```bash
curl -s -X POST https://functions-proxy.flexiblevision.com/release_manifest \
  -H 'Content-Type: application/json' -d '{"channel":"stable"}'
```

---

## Rollback

Point `CHANNELS['stable']` at the previous counter and redeploy. Devices that
already moved past it will not downgrade — that's anti-rollback working.

---

## Gotchas

- **`--update-components` reported "kept - no CI-built tag"?** That component
  hasn't merged to its default branch yet, so CI has never built and labelled it.
- **`--update-components` reported "which is older"?** The newest CI build sorts
  below the current pin. Usually a series that was shortened by hand (`0.5` is
  older than `0.44`). The pin is kept; fix the series.
- **`nodecreator` is on `main`, not `master`.** Its CI gates on `main`.
- **A failed build does not burn a version.** The tag is reserved after the
  build succeeds. Two merges racing means one is refused — re-run it.
- **Aborting during C.2 spends the counter.** Re-run and take the next number.
  Gaps are invisible to devices; a reused counter is not.
- **`--source` is relative to your shell**, not the repo. Run from the repo root.
- **Manifests and signatures are opaque.** Never reformat or re-indent them.
- **`RELEASES` is append-only** — old entries are the audit trail.
- **Notes are operator-facing.** The cut refuses to sign until `summary` is real.
- **Provenance warnings don't block yet.** Add `--strict-provenance` once every
  component has been rebuilt through CI.

## One-time setup

Done once per repo, then never again.

- **Push the seed tag.** CI counts from the highest `v<SERIES>.<n>` tag. Without
  one it starts at `.1` and produces a version older than what is already
  published. The tags exist locally; push them:

  ```bash
  cd visionapi      && git push origin v1.2
  cd onprembackend  && git push origin v1.97
  cd onpremfrontend && git push origin v1.9.4
  cd predictlite    && git push origin v0.1
  cd visiontools    && git push origin v0.45
  cd nodecreator    && git push origin v0.5
  ```

  They record the *number*, not which commit built that image — that is unknown
  for anything published before CI existed, and the tag does not pretend
  otherwise.

- **`DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`** in each repo's Actions secrets:
  `python3 -m release.set_ci_secrets --repos`
- **Restrict registry push to CI.** Until humans lose write access to Docker
  Hub, "only CI-built images ship" is a convention rather than a control.

## Not done yet

- Devices don't fetch or verify manifests — `latest_stable_version` is still
  what the fleet follows. **Cutting a release does not yet change what devices
  run.**
- `/rollback` returns 501.
- `vernemq: dev` is a channel, not a version.
