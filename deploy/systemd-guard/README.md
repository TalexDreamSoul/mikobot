# systemd crash-loop guard

A hardening layer for the systemd deployment described in
[`docs/deployment.md`](../../docs/deployment.md). It exists because nanobot can edit its
own installed code: a bad self-edit, or a config the agent wrote itself into a corner
with, must not leave the gateway dead until someone notices.

These files are copied verbatim from a running deployment rather than generalized. Paths
are hardcoded for that host (`/root/.nanobot`, a `uv tool` install under
`/root/.local/share/uv/tools/nanobot-ai`, health on `127.0.0.1:18790`). Adapt them before
reuse; keeping them verbatim means the repo copy and the live copy stay diffable, and
drift between the two is the failure mode this whole layer exists to catch.

## How it fits together

| File | Role |
|---|---|
| `nanobot.service` | The gateway. `StartLimitBurst=5` in 5 minutes means a crash loop, which fires `OnFailure`. |
| `precheck.sh` | Runs before every start. Repairs in place so *this* start can succeed. |
| `snapshot.sh` | Marks the current state known-good. Refuses unless the gateway is live and healthy. |
| `rollback.sh` | Fires only on a systemd-detected crash loop. |
| `miko-snapshot.timer` | Hourly. |

The ordering matters: `precheck` fixes the cheap, obvious breakage (invalid JSON, a
self-edit that does not compile) so most bad states never reach a crash loop at all.
`rollback` is the heavier path for everything else.

## What `snapshot.sh` keeps, and why each item is there

- **`config.json`** — the agent can rewrite its own config.
- **`selfedits/`** — only files whose hash differs from the wheel's `RECORD`, i.e. the ones
  the agent actually changed. The official files are recoverable by reinstalling, so
  copying them would be dead weight.
- **`collaboration/`** — the collaboration store carries its own `schemaVersion`, and
  migrations only go forward. A newer nanobot that migrates the store leaves an older
  nanobot unable to load it, so the store has to roll back together with the code.
- **`uv-receipt.toml`** — the real install source, including extras and any git URL.
- **`version`** — what was actually installed.

The last two exist because a reinstall that is not pinned to the recorded source will
resolve to whatever the index currently offers. If the deployed build came from git and
carries the same version string as the published release, that reinstall silently swaps
the code out and reports success. `rollback.sh` reinstalls from the receipt and warns
when the resulting version does not match the recorded one.

## What `rollback.sh` will not do

It never deletes the collaboration store. A store created after the last known-good
snapshot is moved to `collaboration.rollback-<epoch>` instead, because a rollback that
destroys tenant data is worse than the crash it was fixing.

## Install

```bash
install -m 755 -D precheck.sh snapshot.sh rollback.sh -t /root/miko-guard/
mkdir -p /root/miko-guard/good
cp nanobot.service miko-rollback.service miko-snapshot.service miko-snapshot.timer \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nanobot.service miko-snapshot.timer
```

Take the first snapshot by hand once the gateway is healthy — `rollback.sh` has nothing
to restore until then:

```bash
/root/miko-guard/snapshot.sh
```

## Verifying it, rather than assuming it

An untested rollback path is the thing this layer is supposed to prevent, so check it:

```bash
# The receipt parser rebuilds the install command. Compare with `uv tool list`.
python3 - /root/miko-guard/good/uv-receipt.toml <<'EOF'
import sys, tomllib
print(tomllib.load(open(sys.argv[1], "rb"))["tool"]["requirements"])
EOF

# The store logic is worth exercising in a scratch directory before trusting it.
# Three cases: known-good has no store, both have one, neither has one.
```

`snapshot.sh` is safe to run at any time; it only writes under `good/`. `rollback.sh`
restarts the gateway, so do not run it casually to "see what happens".
