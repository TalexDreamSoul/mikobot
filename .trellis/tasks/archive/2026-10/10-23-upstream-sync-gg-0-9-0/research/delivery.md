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
- GG runs the wheel built from `628bb8ac`. The later commit `764c4a11` keeps member, OIDC and trusted-proxy WebUI turns out of the host's shared `unified:default` session; it is not deployed. GG has `unifiedSession` off, so the gap is not reachable there. Superseded: 0.9.1 deploys it (below).

# Accepted Mikobot 0.9.1 delivery

## Source and artifact

- Patch on the 0.9.0 integration commit `628bb8ac0c3858514e3ac4bf8523e5f4ec579735`; no upstream commit was merged. Build commit `2cab8d58d487835eda3e300d4a3fb43de8a2361a` on `sync/upstream-20261023`, pushed to `origin`. The code changes are `764c4a11` (unified session), `4f9a3b04` (settings identity and skill toggles) and `c83e8047` (host-owner commands) plus the version bump: 9 files, +293 / −17 lines, none of them WebUI or TUI sources.
- GG wheel: `nanobot_ai-0.9.1-py3-none-manylinux_2_17_x86_64.whl` (62,700,794 bytes), SHA-256 `ea688d6bb6c4cf191ed1bdacc4acc95fa7e350408930f662a6d46b4051bf2668`, payload byte-identical to the build commit.
- The staged environment and the installed GG environment matched the exact wheel: 1,397 payload members compared byte for byte, Python 3.13.15, the dependency set frozen to what ran before (no drift), `pip check` clean. The install itself ran offline from the prestaged cache.

## Executed gates

- Ruff over the CI scope passes; strict BasedPyright reports 0 errors, 0 warnings and 0 notes.
- Full Python suite: **12,178 passed, 1 failed**, the same known benchmark failure as 0.9.0 (`tests/test_benchmark_session_storage.py::test_session_storage_benchmark_emits_auditable_windows_safe_result`). The two modules that carry the new regression tests, `tests/command/test_host_only_commands.py` and `tests/webui/test_collaboration_ws_api.py`, pass together: 119 tests.
- The WebUI and TUI sources are untouched since 0.9.0 (the diff touches Python files and `pyproject.toml` only) and the served bundle is the same `assets/index-BlylvZJr.js`, so their suites were not re-run.

## Rehearsal, canary and host-gate replay

- Rehearsal on a copy of the GG state with the staged interpreter: configuration loaded, 71 sessions (16,938 messages) opened from the existing SQLite store, 66 WebUI history turns (1,145 entries), a second start succeeded, usage store 16,555 rows, delivery outbox opened, collaboration schema 14 with no problems. Every database kept integrity, schema version, schema digest, row counts and markers; legacy JSONL stayed byte-identical; peak RSS 58.3 MiB.
- Canary: the staged gateway booted against a copy of production state with every channel except the WebUI listener and every provider neutralised, under a memory cap and an elevated OOM score so it could not take memory from the production gateway on the 3.6 GiB host. Ready in 6.0 s; index 200; anonymous bootstrap, settings and usage 401; authenticated bootstrap, sessions, settings, usage and skills 200; 12 of 12 WebUI threads 200; the WebSocket handshake delivered `ready`; an owner-only slash command and a `skill.update` ran as the owner; an anonymous `/api/settings` request carrying a `client_id` left no identity behind; RSS 196.7 MiB; no traceback, no error line, clean exit.
- Host-gate replay: each of the 10 identities in a copy of the production collaboration store (8 owner, 2 non-owner without admin) went through `AgentLoop.is_host_conversation` in both modes. No owner identity is locked out of host commands or `/restart`, and no other identity can run either. The store held no stray WebUI identity: its two WebUI identities are owner bindings from 2026-09-05.

## Production proof and rollback

- Stopped-service backup: `/root/miko-guard/pre-v0.9.1-20261024-083616` (complete state, uv receipt, guard scripts, previous known-good and release input), verified byte for byte (24,262 files, 1,484,973,004 bytes) before the install.
- Switch finished at 2026-10-24 08:38:28 CST after 132 s without service, then a 20 s soak. Post-switch verification passed: version 0.9.1, `NRestarts=0`, collaboration schema 14 with every record count equal, SQLite holds the same 71 sessions, 16,938 messages, 66 turns and 1,145 history entries at schema 2 with integrity ok, guard scripts unchanged, no fatal log marker and no new error signature (the single error line, an expired WeChat login, already existed before the switch).
- Gateway `/health`: status ok, process alive, ready true, WebSocket running, runtime version 0.9.1.
- Public site: HTML 200 serving `assets/index-BlylvZJr.js`; unauthenticated `/webui/bootstrap`, `/api/sessions`, `/api/settings` and `/api/settings/extensions` return 401.
- Real browser on the public site (Ego TaskSpace 20, finished exactly once, read-only): the Chinese UI connected with the owner's session and listed its threads; Settings → Overview and Extensions (102 packages, Email enabled, the expired WeChat package shown as failed) rendered; Settings → About shows `v0.9.1 (2cab8d5)`, "up to date" and only a re-check button, with no install or update action. Nothing was sent, changed or deleted.
- Only after that acceptance the known-good snapshot was recorded as 0.9.1 with the exact wheel receipt and 0 self-edits. The snapshot timer is armed with a finite next activation (2026-10-24 09:43:59 CST).
- Rollback path: the hardened `/root/miko-guard/rollback.sh` on GG, the retained previous exact wheel `/root/miko-guard/wheels/nanobot_ai-0.9.0-py3-none-manylinux_2_17_x86_64.whl` (SHA-256 `5cf8c57224a96612535a84325c235b82eddf981f46fbb2e5e50b7babf08c2209`) and the backup above. Conversations created after the switch live only in `sessions/<namespace>/sessions.sqlite3`; back that file up before any rollback. The apply and rollback logic was exercised against a fake system in tests; no real rollback was run on GG.

## Configuration changes made on GG with the release

Both were approved by the owner and are configuration, not code.

- E-mail channel: `channels.email.trustedAuthservIds` was added as `["mx.google.com"]`, the Authentication-Results host on each of the five newest inbox messages (header-only IMAP peeks), and `markSeen` was set to `false`. Before, the channel failed to start with "missing: trusted_authserv_ids". `allowFrom` is still empty, so nothing is delivered; with `markSeen` at its default the channel would also have marked every authenticated unread message as read. After the 0.9.1 restart the channel polls; its first pass logged one SPF or DKIM rejection warning per unread message (about 410) and then went quiet. Backup `/root/miko-guard/config.json.pre-emailfix-20261024-083503`.
- WeChat: the default instance, whose login had expired (no inbound message for 14 days, no project assignment), was removed with the Settings extension action over the authenticated WebSocket: hot, no restart, history retained, the other four instances untouched. The action rewrote `config.json` in the 0.9.x canonical form without changing any effective value (legacy keys such as `apiType` are gone, defaults such as `useProxy` are written out). Scheduled jobs that delivered to that instance were left as they are. Backups `/root/miko-guard/pre-wxdelete-20261024-084437/`.

## Known gaps

- Lower-severity authorization findings that were outside this patch are reported to the owner separately.
- A host-memory command run by the owner inside a project that is bound to or shared with others shows the host's memory to that conversation: the gate decides who may run the command, not where its output goes.
- The e-mail channel receives nothing until the owner chooses `allowFrom`; then remove `markSeen: false` and decide whether `verifySpf` stays on (SPF aligned on 1 of 5 sampled messages, DKIM on 5 of 5).
- Carried over from 0.9.0: the two session-access maintenance scripts still read the JSONL layout, and the JSONL importer is removed in upstream 1.0.0.
