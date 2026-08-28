# Addons — a plugin folder for a-la-carte enterprise services

## The problem

There are seven optional capabilities on a device — OCR, Assembly Guidance, Audio
Anomaly Detection, Time Machine, FTP, Client Mode, IO/TCP — and every one of them was
hand-wired. Adding an eighth meant touching five files in this repo and two in the
frontend, and forgetting any one of them failed silently rather than loudly.

Where a single addon used to live, taking Audio as the example:

| Concern | Location | Duplicated per addon? |
|---|---|---|
| Deploy | `helpers/install_audio.sh` | yes, and inconsistently |
| Job shim | `worker_scripts/job_manager.py:398` | yes |
| Toggle + status route | `routes/audio_routes.py` | yes, with its own Redis queue |
| Registration | `routes/__init__.py` | yes |
| Frontend card | `Settings/Features/EnableAudioDevices.js` | yes |
| Frontend registry | `src/config/features.js` | yes |

What that cost, concretely:

- **The install scripts had drifted.** `install_assembly.sh` and `install_audio.sh`
  were arch-aware and honoured `IMAGE_TAG`; `install_ocr.sh` was a single line,
  x86-only, tagged `prod`, with no pull-failure handling and no teardown of the
  previous container — so a second run collided on the container name instead of
  upgrading.
- **Enabled addons were never upgraded.** `upgrades/system_container_upgrades.sh`
  does not mention `ocr`, `assembly-client`, or `audio-anomaly`. They were installed
  once at whatever `:1` resolved to that day and then drifted forever, while every
  foundational container moved underneath them.
- **Nothing recorded which addons were enabled.** Status was a live HTTP probe of the
  container's port, so a crashed addon reported as *disabled* and the UI offered to
  enable it. And `release/manifest.py:applicable(m, arch, enabled_features)` — which
  raises rather than silently skipping an enabled feature the release does not pin —
  had **no source for its `enabled_features` argument**.
- **There was no entitlement enforcement.** The only check was `validate_account()` in
  `system_server/timemachine/installer.py:60`, which fails open three ways: it
  initialises `is_valid = True`, it returns the bound method `res.json` instead of
  calling `res.json()`, and it returns `True` on any exception.
- **Addons were invisible to the system.** Absent from `CONTAINERS` in both
  `routes/system_routes.py:14` and `version_check.py:6`, so `/list_services` and
  `/system_versions` did not know they exist.

## The shape

One folder. One descriptor per addon. Everything else — routes, deploy, status,
upgrade, release pinning, licensing, the frontend card — is generic and reads the
descriptor.

```
addons/
  DESIGN.md              this file
  schema.py              the descriptor contract; validation
  registry.py            discover + load + validate every catalog/*/addon.json
  runtime.py             deploy/teardown/probe, driven by the descriptor
  state.py               enabled-state persistence (recorded intent, not a probe)
  entitlements.py        licence check: token claims, cache, cloud
  jobs.py                the rq entry points
  catalog/
    ocr/addon.json
    assembly/addon.json
    anomaly_audio/addon.json
    timemachine/addon.json    + hooks.py  (composite install, custom dialog)
    ftp/addon.json            + hooks.py  (host service, not a container)
    client_mode/addon.json    (config only; served by capdev)

system_server/routes/addon_routes.py   the generic route surface
```

Flask stays out of `addons/` so the registry, runtime and licence check are testable
without the web stack.

Adding an addon is: create `catalog/<name>/addon.json`, add its tag to the release
components file. No Python, no route wiring, no frontend change.

## The descriptor

`schema.py` is the contract; `catalog/anomaly_audio/addon.json` is a real example:

```jsonc
{
  "schema": "flexrun.addon/v1",
  "name": "anomaly_audio",             // matches the release-manifest feature name
  "label": "Audio Anomaly Detection",
  "group": {"key": "anomaly", "label": "Anomaly Detection"},
  "tier": "enterprise",                // "included" | "enterprise"
  "entitlement": "audio_anomaly",      // licence key; null if included
  "kind": "container",                 // container | host_service | config | composite
  "arches": ["x86", "arm"],
  "component": "audio-anomaly",        // -> fvonprem/<arch>-audio-anomaly
  "container": { ... },                // everything install_audio.sh hardcoded
  "health": { "type": "http", "port": 5702, "path": "/api/audio/devices" },
  "ui": { "icon": "GraphicEq", "order": 30, "manage": "toggle" },
  "legacy_routes": { "manage": "/manage_audio_devices",
                     "status": "/audio_devices_status" }
}
```

Four decisions worth calling out.

**Declarative JSON, not a Python plugin class.** A container addon is a fixed set of
facts, and the point is that adding one requires no code. JSON can be schema-validated
at load, diffed in review, and read by the release tooling and the frontend alike. The
escape hatch is an optional `hooks.py` beside the descriptor exposing `enable(ctx)` /
`disable(ctx)` / `status(ctx)`, which Time Machine and FTP need and container addons
do not. JSON rather than YAML because there is no YAML dependency in
`requirements.txt` and the release manifest is already canonical JSON.

**`name` is the release-manifest feature name.** `release/manifest.py` deliberately
does not enumerate features (`manifest.py:63-70`), because a hardcoded list would
silently drop a new one from the pinning. `registry.components()` is the enumeration
it was waiting for, and is what `build_release.py` should populate `features=` from.

**`kind` exists because not every addon is a container.** FTP is `vsftpd` plus
`setup/ftp_server_setup.sh`; Client Mode is a document in mongo `fvonprem.utils` read
by `cloud_env.py:34`; Time Machine is several containers plus a storage-type dialog.
Modelling them all as containers would force three of the seven into a lie. They still
get descriptors, so they appear in one list with one licence check and one status
surface — they just dispatch to `hooks.py` instead of `runtime.py`.

**`group` is for a product family sold as several services.** Anomaly Detection is
audio today and images later. They are siblings rather than one addon with two
containers, because each is entitled, pinned and health-checked on its own —
collapsing them would mean a device licensed for one gets both or neither. The UI
groups them under the shared label. Adding the image service is
`catalog/anomaly_image/addon.json` with the same `group`.

## Route surface

```
GET  /addons              every addon: intent, health, licence, image, last error
GET  /addons/<name>       one
PUT  /addons/<name>       {"state": true|false}
```

Plus the `legacy_routes` from each descriptor, registered as aliases onto the same
handlers, body and status code preserved exactly — `'enabling...'` with a 200,
`'state key not found'` with a 404, a bare boolean from the status probe. captureui is
versioned and upgraded independently of flex-run, so a device on an older UI must keep
working; the same reasoning keeps `GET /upgrade` alive alongside `POST` in
`system_routes.py:127`. Only addons whose `ui.manage` is `toggle` get generated
aliases — Client Mode lists its path for reference but capdev serves it.

`GET /addons` should then **replace** the hardcoded `FEATURE_REGISTRY` in the
frontend. One generic `<AddonCard>` renders whatever the device reports, and
`EnableOcr.js`, `AssemblyGuidance.js` and `EnableAudioDevices.js` collapse into it.
That is the half of the ergonomic the frontend registry's header comment asks for and
cannot provide on its own.

## Enabled state

A new mongo collection, `fvonprem.addons`:

```
{ name, enabled, enabled_at, enabled_by, image, release, last_error }
```

This separates *intent* from *reality*, which were one HTTP probe. Intent is what the
release path needs (`applicable()`), what the upgrade path needs (which addons to
redeploy), and what the licence re-check needs. The health probe stays as a separate
field, so the UI can say "enabled but not running" instead of offering to enable
something already enabled and broken.

Mongo rather than `fvconfig.json` because that file is rewritten wholesale by
`generate_environment_config()` and holds device identity, not runtime state.

## Licensing

`entitlements.check(addon, access_token, claims)` is the single enforcement point.
Three sources, in order:

1. **Token claims.** `auth.requires_auth` already verifies the token against the JWKS
   and leaves the payload in `flask.g.current_user`, so a namespaced custom claim on
   it is signed and needs no network to check. That suits on-prem devices better than
   any endpoint can: an offline device can still prove its licence.

   ```
   "https://flexiblevision.com/entitlements": ["audio_anomaly", "ocr"]
   "https://flexiblevision.com/org_id": "..."
   ```

   An Auth0 Action populates it from the org's subscription at token issuance. An
   **absent** claim is not a denial — during rollout no token carries one, and
   treating absent as denied would lock out the whole fleet.

2. **Cached grant**, honoured through a 30-day grace window. A hard online check would
   take a paid feature down on a network blip.

3. **`validate_service`**, which revokes faster than a token lifetime can.

`ENFORCED = False`. `check()` returns the honest answer and callers record it, but
nothing is blocked. Flipping that constant is the whole of turning enforcement on.

**Prerequisite before claims can be trusted.** `auth.py` decodes with
`options={'verify_exp': False}` on both the local and cloud branches, so an expired
token still authenticates. Today that is a session-length problem; once the token
carries entitlements it becomes a licensing hole — a revoked org keeps its features
forever by replaying an old token, and the grace window becomes meaningless. Turning
expiry verification on is the load-bearing change, and it should land before, not
with, enforcement.

## Upgrades — not yet done

After the foundational containers in `upgrades/system_container_upgrades.sh`, one more
phase: for each enabled addon, redeploy at the digest pinned in the active release
manifest, using the existing `deploy_common.sh` retire/rollback/smoke helpers.
`runtime.resolve_reference()` already takes a pinned reference for exactly this —
`release/manifest.py:pinned_reference()` produces the string it expects.

Enabled addons should also join `CONTAINERS` for `/list_services` and
`/system_versions`, generated from the registry rather than hardcoded twice.

## Status

Done:

1. `addons/` with schema, registry, runtime, state, entitlements, jobs, and
   descriptors for all six.
2. Generic routes plus legacy aliases. `audio_routes.py` deleted; the toggle/status
   pairs stripped out of `assembly_routes.py` and `timemachine_routes.py`.
3. `runtime.py` replaced `helpers/install_{ocr,assembly,audio}.sh`, which are gone.
   The three `enable_*` functions in `job_manager.py` remain only as shims — rq
   serialises a job by import path, so a job queued before the migration still names
   one of them.
4. `state.py` records intent.
5. Licence checks run and are reported; nothing is blocked.

Still to do:

6. Feed `state.enabled()` into `applicable()`, and `registry.components()` into
   `build_release.py`.
7. The addon phase in the upgrade script, and addon containers in `/list_services`.
8. Frontend switches to `GET /addons`; the three per-addon components collapse into
   one card.
9. Auth0 Action to issue the claim; `verify_exp` fixed; then `ENFORCED = True`.

## Open questions

1. **Do addons need per-addon config beyond on/off?** Time Machine has a storage-type
   dialog and Client Mode has a master IP. Both currently use `ui.manage: "custom"`
   and keep their existing component. The alternative is a `config_schema` in the
   descriptor and a generated form, which is more machinery than two addons justify.
2. **Should the entitlement be per-org or per-device?** A per-org claim is simpler and
   matches how these are sold, but any user of that org can then enable a paid addon
   on any device they can reach. A device-scoped grant is stricter and needs the
   device identity in the claim.
3. **Does FTP or Client Mode need to move fully into the folder?** They carry
   descriptors so they show up in one list with one licence check, but their install
   logic still lives where it did, and Client Mode's toggle is capdev's.
