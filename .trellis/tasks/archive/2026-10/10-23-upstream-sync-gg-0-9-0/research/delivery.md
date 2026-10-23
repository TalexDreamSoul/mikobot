# Accepted Mikobot 0.9.0 delivery

## Source and artifact

- Canonical upstream: `HKUDS/nanobot` main `a8873bb5657d20ef4278a0cfcb51fd18153cd0f3`, 325 upstream-only commits since merge base `9dc0abae4f7e2b015c56881d4eb4f3500b15e529`. Mikobot `main` before the merge: `d19b0cf0d2263b5bf8faf7222b18c35778b4429e`.
- Integration commit: `628bb8ac0c3858514e3ac4bf8523e5f4ec579735` on `sync/upstream-20261023` (1,589 files changed, 151 conflicted paths resolved by hand). Not pushed. No tag, hosted GitHub release or PyPI upload was created.
- GG wheel: `nanobot_ai-0.9.0-py3-none-manylinux_2_17_x86_64.whl` (62,699,541 bytes), SHA-256 `5cf8c57224a96612535a84325c235b82eddf981f46fbb2e5e50b7babf08c2209`, built from the integration commit with the version-matched Linux x64 TUI bundle.
- The installed GG environment matched the exact wheel: 1,397 payload members compared byte for byte. Two dependencies were pinned to the tested versions (`python-dateutil==2.9.0.post0`, `python-socks==2.8.2`) through a constraints file kept next to the wheel.

## Executed gates

- Ruff over the CI scope passes; strict BasedPyright analyzed 486 files with 0 errors and 0 warnings.
- Full Python suite: **12,104 passed, 63 skipped, 1 failed**. The failure is `tests/test_benchmark_session_storage.py::test_session_storage_benchmark_emits_auditable_windows_safe_result`, which also fails on unmodified upstream in this environment. No other test was waived.
- `scripts/check_import_boundaries.py` reports one violation, `nanobot/security/member_access.py:9`, that already exists on Mikobot `main`.
- WebUI type-check, production build and Vitest: **197 files, 3,397 tests passed**. TUI: **281 pass, 13 skip, 0 fail**.
- Authorization audit of every upstream-added surface that reads or writes sessions, attachments, media, automations, settings, shell or scheduled delivery. The audit fixed member scheduled-delivery media (rechecked against the pinned project's restricted scope, with a parametrized regression test) and marked recovery continuations as runtime-sourced so members re-authorize from persisted provenance. Two pre-existing gaps were found and are not part of this change; they are reported to the owner separately.
- `nanobot/distribution.py` keeps upstream's updater from replacing this fork: CLI `update` exits 1, WebUI update actions return 409 (403 for members), the version check is inert and the client keeps `allowUpdateFlow={false}`.

## Rehearsal, canary and local browser

- Rehearsal on a copy of the GG state with the staged Python 3.13.15 interpreter: configuration loaded, 71 JSONL sessions (16,790 messages) imported into SQLite schema 2 with no missing session and no message-count deficit, 66 WebUI history turns (1,145 entries) imported, a second start succeeded, legacy JSONL stayed untouched, peak RSS 280.6 MiB.
- Canary: the staged gateway booted against a copy of production state with every channel except the WebUI listener and every provider neutralised. Ready in 5.0 s; index 200; anonymous bootstrap 401; authenticated bootstrap 200; sessions, settings and usage 200; 12 of 12 WebUI threads 200; WebSocket handshake delivered `ready`; RSS 319 MiB; no traceback, no error line, clean exit.
- Local real-browser pass on the built WebUI against a deterministic fake model showed the Chinese UI and a completed chat round trip.

## Production proof and rollback

- Stopped-service backup: `/root/miko-guard/pre-v0.9.0-20261023-201841` (complete state, uv receipt, guard scripts, previous known-good and release input).
- Switch finished at 2026-10-23 20:20:34 CST after about two minutes without service. Post-switch verification passed: version 0.9.0, `NRestarts=0`, collaboration schema 14 with every record equal (ignoring `updatedAtMs`), 71 session JSONL files kept byte-identical prefixes and no other JSONL changed, SQLite holds 71 sessions at schema 2 with none missing or shorter, journal fatal-marker scan clean (2 error lines in the first 60, both present before the upgrade: e-mail `trusted_authserv_ids` and an expired default WeChat login).
- Gateway `/health`: status ok, process alive, ready true, WebSocket running, runtime version 0.9.0; `nanobot.service` active and running with zero restarts.
- Public site: HTML 200 serving `assets/index-BlylvZJr.js`; unauthenticated `/webui/bootstrap`, `/api/sessions` and `/api/settings` return 401.
- Real browser on the public site (Ego TaskSpace 12, finished exactly once, read-only): Chinese UI connected, migrated threads listed with history rendered, Projects shows the main project (King) and its task board, the chat-platform list renders, Settings → About shows `v0.9.0 (628bb8a)` with no install or update action. Nothing was sent, changed or deleted.
- Only after that acceptance the known-good snapshot was recorded as 0.9.0 with the exact wheel receipt and 0 self-edits. The snapshot timer is armed with a finite next activation (2026-10-23 21:27:13 CST).
- Rollback path: the hardened `/root/miko-guard/rollback.sh` on GG (the older copy under `deploy/systemd-guard/` must not overwrite it), the retained previous exact wheel `/root/miko-guard/wheels/nanobot_ai-0.8.0-py3-none-manylinux_2_17_x86_64.whl` (SHA-256 `c3fc2b74314f70113eb6bd8c14d3421ad1cf35949ec5584b112b0adc940fba86`) and the backup above. Conversations created after the switch live only in `sessions/<namespace>/sessions.sqlite3`; back that file up before any rollback.

## Known gaps

- Existing operational issues observed on GG and not changed here: the e-mail channel fails to start without `trusted_authserv_ids`, and the default WeChat login and one project instance need QR re-pairing.
- The two session-access maintenance scripts still read the JSONL layout; their test module is skipped (see the PRD).
- The JSONL importer is available through 0.9.x and is removed in upstream 1.0.0, so every deployment must have imported its JSONL sessions before this fork syncs 1.0.0 or later.
