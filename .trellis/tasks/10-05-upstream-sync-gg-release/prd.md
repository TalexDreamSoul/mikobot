# Full upstream sync, WeChat instance controls, and GG release

## Goal

Deliver the complete current upstream functionality and all three requested WeChat instance controls in one verified, state-preserving Mikobot release on GG.

## Confirmed Background

- Local `main` starts at `041f9d15e0cab66ab09d8df7c3ce91e48b8452a6`; 23 tracked files contain the already-verified project-creation and King work. Unrelated untracked agent/task artifacts are outside this release.
- The canonical upstream planning snapshot is `HKUDS/nanobot` main at `f75470e72f0993dcf92accc81282adaa48b16f56`. It contains 482 upstream-only commits and 1,152 changed files relative to the common ancestor. An in-memory merge preview reports 240 conflicting paths: 155 JSON/localization, 43 Python, 33 TypeScript and nine other paths. This was a preview, not a working-tree merge.
- The GG planning snapshot is healthy `nanobot-ai` 0.7.4 with zero service restarts, 19 GiB free disk, collaboration schema 13, four projects, three users, four memberships and 425 JSONL files. Known-good is 0.7.4; release acceptance must compare the fresh pre-upgrade state rather than assume these counts cannot change.
- The live receipt pins `/root/miko-guard/wheels/nanobot_ai-0.7.4-py3-none-any.whl` through TOML `path`. `/root/miko-guard/rollback.sh:93-101` currently handles `url` but not `path`, so its reinstall source selection does not preserve this pin.
- The screenshot and `nanobot/channels/weixin/webui/WeixinPanel.tsx:403-453` show toggles and reconnect but no deletion control. `WeixinConnectFlow.tsx:138-157` forces a fresh login only for expiry; `connect.py:119-130` short-circuits a loaded account without requesting a QR code. The local loaded-account branch probe reproduced this behavior; no production account or network login was exercised.
- Account replacement and project Pair Code binding are different operations. Their existing owners are `weixin/runtime.py:950-953`, `weixin/connect.py:219`, `webui/ws_http.py:1678-1681,1741-1756` and `webui/src/components/projects/ProjectChannelsPanel.tsx`. The user explicitly confirmed that both re-matching operations are required.

## Requirements

- R1: Integrate all canonical upstream changes while retaining local Mikobot identity, collaboration projects, channel assignments, private session provenance and restricted shell/filesystem policy. Resolve behavioral conflicts explicitly rather than accepting one complete side.
- R2: Include the current tracked project-creation and King changes. King is globally unique and independent of active-project preference; only the authenticated host owner gets cross-project tool authority in King. Revocation applies to running and derived work.
- R3: Provide confirmed deletion of one exact WeChat instance, including its runtime, credentials and assignment/pairing authority. Preserve other instances, projects and all historical conversations; never recursively delete an arbitrary configured state directory.
- R4: Provide separate replacement-account QR login and project Pair Code re-pairing controls for that instance. QR cancellation/failure preserves the old account. Project re-pair keeps the account, confirms removal of an old project assignment, revokes superseded Pair Codes, and uses one-time verification/consumption for the selected project and permitted assignee. After unlinking, normal delivery remains suspended until pairing completes.
- R5: Deploy one uniquely versioned exact wheel to GG with enabled-channel dependencies retained. Preserve the stopped-service state backup and exact prior wheel for rollback; never reset the collaboration store, overwrite an existing-version wheel or let snapshot automation bless a partial installation.

## Acceptance Criteria

- AC1 (R1–R2): The integrated commit contains the accepted canonical upstream head in its ancestry, has no unresolved conflicts, and passes Python lint/strict types/full tests plus frontend type-check/tests/build and affected CLI/TUI checks.
- AC2 (R2): Real-browser desktop and narrow-screen project creation succeeds. Exactly one King exists. Owner-in-King cross-project access succeeds; owner-in-ordinary-project and ordinary-member access stay restricted. An affected running turn rejects demoted King authority.
- AC3 (R3–R4): A disposable local instance can be deleted without changing other instances or conversation history. The real UI requests a fresh replacement QR for the same instance, cancellation preserves account state, and project re-pair consumes a one-time code without replacing account credentials. Unauthorized/stale requests, superseded codes and late cancelled/deleted login results cannot mutate current state.
- AC4 (R5): An isolated copy of the latest GG collaboration state migrates from schema 13 to 14 with existing records/history preserved. Production reports healthy/ready, WebSocket running, active/running service and zero restarts. Installed Python/UI payloads match the accepted wheel; public HTML, authentication and actual Projects/channel controls are verified in Ego.
- AC5 (R5): Prior wheel and compatible stopped state remain recoverable. After acceptance, known-good identifies the new artifact/state, and the snapshot timer has a finite next activation.

## Scope Boundaries and Decisions

- Retain the fork's version line and plan 0.8.0, with a fresh collision check before release. Refresh the upstream head immediately before integration and record its exact identity.
- The user approved execution, scoped local preservation/merge/release commits and pushing the accepted result to the existing origin branch on 2026-10-05. Preserve the existing tested tracked work before merging.
- No branch creation, tag or hosted release; no deployment outside GG; no provider credential replacement, production reset, unrelated cleanup or blanket staging of untracked artifacts.
- Do not delete, reconnect, re-pair or message through existing production WeChat accounts for verification. Use disposable local data; public production controls are inspected without performing these mutations.

## Planning Artifacts

`design.md`, `implement.md`, `implement.jsonl` and `check.jsonl` describe the integration, lifecycle contracts, runtime gates and rollback procedure. Research evidence is under `research/`. The final summary and execution including local commits and push were approved on 2026-10-05. Start the existing task; no new task is required.
