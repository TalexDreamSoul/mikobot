# Accepted Mikobot 0.8.0 delivery

## Source and artifact

- Preserved project/King work: `258405c5eb7f3a4b5678be0e56fb5104be591dc3`.
- Integrated release commit: `bb2ab8c4bf5d616dbe258a8ade03133d1f9e5b0c`, pushed to existing `origin/main` and verified by `ls-remote`.
- Canonical upstream ancestor: `9dc0abae4f7e2b015c56881d4eb4f3500b15e529`.
- GG wheel: `nanobot_ai-0.8.0-py3-none-manylinux_2_17_x86_64.whl`, SHA-256 `c3fc2b74314f70113eb6bd8c14d3421ad1cf35949ec5584b112b0adc940fba86`.
- 404 packaged Python files matched the accepted source. GG installed 1,125 payload members matched the exact wheel, including the Linux x64 baseline Bun 1.3.13/OpenTUI bundle, notices, licenses, corresponding source and relinking materials.
- No branch, tag, hosted GitHub release or PyPI upload was created.

## Executed gates

- Ruff full Python/channel/tests gate passed; BasedPyright reported 0 errors, 0 warnings.
- Full Python/channel suite: **10,063 passed, 17 skipped**, one existing Discord `aiohttp.BasicAuth` deprecation warning.
- WebUI production type-check/build, ESLint and all Vitest suites: **160 files, 2,620 tests passed**. Existing React act/KaTeX harness and Browserslist-age warnings remain; no failing contract was waived.
- TUI type-check plus full Bun suite: **252 passed**.
- Actual version/gateway CLI startup passed. Installed native executable ran on GG and correctly required bootstrap/WS environment; a full interactive terminal session was not exercised.
- Real FS/read/write/shell authority matrix on macOS and exact-wheel Linux container: owner-in-King allowed; owner ordinary, member King and OIDC-admin King restricted; demoted captured owner authority rejected.
- Member Linux explicit absolute-interpreter commands were rejected by the hard application guard before spawn. This proves fail-closed behavior, not successful kernel-level member command execution; no shell trick/bypass was attempted.

## Actual browser and disposable-state acceptance

One Ego TaskSpace **106**, closed successfully exactly once; no goal-owned agent space remains.

- Desktop and 375px mobile project creation completed through the compiled UI. King designation remained globally unique and separate from active-project preference.
- Same-instance replacement first request carried `force=true`, `mode=replace`, `instance_id=default` and exact extension revision; fetched a real new WeChat QR. Immediate cancel completed without waiting for the provider poll and preserved the original saved account.
- Re-pair selected the new project/permitted assignee, issued a real one-time code after confirmation, unlinked the old assignment, then verified through a disposable provider boundary and automatically consumed through the UI. Credentials retained their authoritative configured value. The initial fixture disk token deliberately differed from configured token; startup correctly restored configured token. The disposable fake credential's runtime auth failure is not claimed as real-account delivery proof.
- Confirmed default-instance deletion removed only that account/config/assignment; sibling credential bytes and conversation history remained unchanged. Destructive controls were exercised only on disposable local data.
- Public `https://nanobot.tagzxia.com/` rendered actual authenticated Projects/King and three instance controls. Existing production account controls were not clicked; viewing an unconfigured connect surface briefly opened a new temporary QR session, closed without scanning or persistent credentials.

## Production proof and rollback

- Stopped-service backup: `/root/miko-guard/pre-v0.8.0-20261006-035924` (complete state, receipt, guard and prior known-good).
- Schema **13 → 14**; users, identities, memberships, provisions, assignments, challenges, bindings and tasks compared unchanged, project changes limited to King fields.
- **426 pre-window JSONL files** retained byte-identical prefixes after startup.
- Gateway `/health`: status ok, process alive, ready true, WebSocket running; `nanobot.service` active/running, **NRestarts=0**.
- Old exact 0.7.4 wheel retained. Rollback parser handles receipt path and refuses unpinned same-name package fallback; old guard backup retained.
- Only after authenticated public acceptance, new known-good snapshot recorded **0.8.0**, exact platform-wheel receipt and **0 self-edits**.
- Snapshot timer rearmed with a finite next activation: **2026-10-06 05:10:48 CST**.
- Public HTML 200, unauthenticated bootstrap 401.
