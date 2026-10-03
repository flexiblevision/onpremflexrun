# Device placement sync

How an on-prem system learns where it is, and how the cloud learns what it runs.

## Why

Every domain envelope requires `site_id` — it is the authorization axis, and a
row without one is refused. Today a prediction record carries
`metadata: {device_id}` and nothing else, so nothing downstream can place it and
every record is skipped.

Only the cloud knows the device → station → line → site mapping. Only the device
knows which domains it actually runs. So each side tells the other, on one call,
using the token the device already holds for its sync cycle.

## Contract

`cloud-assembly-backend`, `routes/stations.py` — the stations live there, and it
already owns `device_id` binding under normal user auth. On-prem already calls
`/api/assembly/*` with its token (`cloud_sync.py`), so no new credential.

```jsonc
PUT /api/assembly/stations/device/<device_id>
{ "domains": ["inspection", "anomaly", "time_machine"] }

// 200 — placed
{ "place": { "site_id": "SITE-FRE-02", "site_name": "Fremont Plant 2",
             "line_id": "L1", "line_name": "Final Assembly A",
             "station_id": "ST-03", "station_name": "Solder & Inspect",
             "org_id": "org_fv" },
  "domains": ["anomaly", "inspection", "time_machine"],
  "reported_domains": ["anomaly", "time_machine"],
  "unavailable_domains": ["assembly", "waveform"] }

// 200 — not placed on a line
{ "place": null, "reason": "device is not assigned to a station on a line" }
```

`GET` on the same path reads the place without reporting domains — for the
indicator, and for a device that has nothing new to say.

Resolution is one query: a station denormalizes `site_id` and `line_id`, so
`stations_db.find_one({'device_id': ...})` is the whole lookup. Names come from
`lines_db` / `sites_db` for display.

Domains are validated against `analytics.core.envelope.EVENT_TYPES`, so a device
cannot report a domain the spine has no pack for, and a typo is a 400 rather
than a silently stored value.

## Decisions

**Placement gates the spine, not the data.** An unplaced device still writes
`inspections.predictions` and `.contexts` in full; it is only absent from the
site dashboard. This is intended behaviour, not data loss — a device has to be
on a line to appear on the line.

**Inspection is baseline; everything else is an add-on.** Every system node can
inspect, so `inspection` needs no device report and is never absent. `assembly`,
`anomaly`, `waveform` and `time_machine` appear only once the device reports the
addon enabled — which is what stops the cloud offering setup for a capability the
machine does not have. Worker guidance instructions cannot be authored for a
station until `assembly` is in its domains.

**Add-ons are authoritative from on-prem.** The report replaces the stored
add-on set rather than merging, so a capability turned off on the device
disappears from the cloud next sync. Two fields, kept separate so they are never
confused: `reported_domains` is exactly what the device said, `domains` is the
effective set (baseline ∪ reported) that the UI gates on.

**One device, several domains.** A station node may run worker guidance,
inspection and time machine at once. `station.domains` is already an array and
the frontend already reads it (`deviceDomains.js`), so this adds no concept.

## On-prem domain mapping

Built from `addons.state.enabled()`:

| addon | domain |
|---|---|
| `assembly` | `assembly` (worker guidance) |
| `anomaly_visual` | `anomaly` |
| `anomaly_audio` | `waveform` |
| `timemachine` | `time_machine` |
| `ocr`, `client_mode`, `ftp` | none — not domains |

`ocr` is a `model_type` inside inspection, not a domain of its own.

## Setup flow

The failure to design for is a user looking for worker guidance and not finding
it, with nothing saying why. So the cloud does **not** hide unavailable
capabilities: the endpoint returns `unavailable_domains`, and the UI shows those
add-ons present but not enabled, naming the action — enable the addon on the
device — rather than omitting them.

Reading the two lists together is the whole setup story:

| field | means |
|---|---|
| `domains` | what this station can do now |
| `reported_domains` | what the device turned on |
| `unavailable_domains` | what it could do, once enabled on the device |

## Touch points

| repo | change | state |
|---|---|---|
| cloud-assembly-backend | `routes/stations.py` — `GET`/`PUT /stations/device/<id>` | **written** |
| onpremflexrun | `worker_scripts/device_identity.py`, called first in the sync cycle | **written** |
| onprembackend | `prediction_caller` stamps cached place into `analytics_obj['metadata']` | **written** |
| bq_ingest | topology fetch and `TOPOLOGY_*` removed; `_resolve` checks the claim only | **written** |
| visionbackend | none — this is assembly topology, not device CRUD | — |
| visionfrontend | `SiteConnection.js` — `PlacementChip` + `SiteConnection` | **written** |
| visionfrontend | wired into `DeviceInfoPanel` header | **written** |
| visionfrontend | unplaced surface in `Devices.js`; on-prem surface | todo |

A worker-guidance device can skip the call entirely: `cloud_sync.py` already
imports sites, lines and workstations using cloud ids, so it can resolve
locally. The sync is the path for every other device.

## Site connection indicator

The device's placement must be visible where someone can act on it. Absent one,
an unplaced device looks identical to a broken pipeline: nothing on the
dashboard and no stated reason.

Two surfaces, both fed by the cached `place`:

- **on-prem** — site / line / station, or "not placed on a line", for whoever is
  standing at the machine
- **cloud device list** — placed vs unplaced, for whoever can fix it

On-prem UI follows `fv-design-ops`; this is touch-first and both orientations.

**The cloud surface cannot be SiteViewer.** A station exists only once it is on a
line, so an unplaced device has no station and never appears there at all — the
one place it is invisible is the place someone would look. It belongs in the
device list (`Devices.js`), which enumerates devices rather than stations.

## Failure modes

| case | behaviour |
|---|---|
| device not placed | `place: null`, records skipped and counted under `missing_station` |
| sync has not run yet | no cached place, same as unplaced, recovers on next cycle |
| device moved to another station | next sync returns the new place; existing rows keep the old one, which is correct — they happened there |
| cloud unreachable | cached place is used; it is configuration, not telemetry |

## Open

- Whether an unplaced device should be listed in the cloud UI automatically or
  only surface once it has synced at least once
