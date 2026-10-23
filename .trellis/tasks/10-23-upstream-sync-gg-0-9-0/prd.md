# Upstream sync to 0.9.0 and GG release

## Goal

Integrate the canonical `HKUDS/nanobot` main into Mikobot without losing any Mikobot behavior, then deploy the verified build to GG with a recoverable state.

## Confirmed Background

- Merge base `9dc0abae`; Mikobot `main` `d19b0cf0`; upstream `main` `a8873bb5` (325 upstream-only commits, 1,589 files changed, 151 conflicted paths resolved by hand). GG runs 0.8.0 from a local exact wheel.
- Upstream moved sessions from JSONL files to a per-namespace SQLite store with strict importers, made `SessionManager` state asynchronous, added a body-capable HTTP bridge for the WebSocket listener, runtime-context providers, and an in-app updater that installs the PyPI `nanobot-ai` package.
- Mikobot shares the `nanobot-ai` PyPI name with upstream. Letting the upstream updater run would replace this fork with a different codebase.

## Requirements

- R1: Merge with ancestry preserved and keep the collaboration control plane (schema 14), ConversationScope DIRECT/BOUND/ISOLATED, King/main project, private memory roots, per-project allowlists, fail-closed member execution, Weixin delete/replace/re-pair controls, OIDC settings, `deploy/systemd-guard/`, the Chinese-first WebUI copy and the Mikobot brand. The package and CLI identifiers stay `nanobot`.
- R2: Every new upstream surface that reads or writes sessions, attachments, media, automations, settings, shell execution or scheduled delivery must pass the same member/owner gates as the code it replaces. Member principals never inherit host-owner authority through runtime-sourced messages.
- R3: `nanobot/distribution.py` (`UPSTREAM_UPDATES_ENABLED = False`) disables every path that would install upstream's package: CLI `update` exits 1, WebUI `update-*` actions return 409, version-check is inert, and the client keeps `allowUpdateFlow={false}`.
- R4: The deployed artifact is one uniquely versioned exact platform wheel. The pre-upgrade state is backed up before the service stops, and the previous wheel plus that backup remain the rollback path.

## Acceptance Criteria

- AC1: No unresolved paths. `ruff check`, strict `basedpyright`, the full Python suite, WebUI typecheck/lint/vitest/build and TUI check/test pass. The only accepted exceptions are failures that already exist on `main`.
- AC2: A real-browser pass on the built WebUI shows the Chinese UI, a chat round trip, the King/main project page, the channel list and no update-install controls.
- AC3: A copy of the GG state loads with the staged interpreter: the legacy configuration, every session and WebUI history import without loss.
- AC4: GG reports healthy/ready with zero restarts after the switch and the snapshot timer keeps a finite next activation.

## Scope Boundaries and Decisions

- Adopt upstream's SQLite session store. The JSONL importer is available from 0.9.0 through 0.9.x and is removed in 1.0.0 (`docs/cli-reference.md`).
- `scripts/backfill_session_access_scope.py` and `scripts/verify_session_access_matrix.py` still read the JSONL layout and cannot run against SQLite sessions. Their test module is skipped. Port or retire them in a follow-up.
- No push, tag or hosted release without separate approval.

## Known Follow-ups (not part of this change)

- `/dream-log`, `/dream-restore`, `/dream-prompt init` and `/evaluator-prompt init` use the host memory store for member and ISOLATED conversations; the same code exists on `main`.
- Per-owner quota for pending attachment uploads, a size cap for King's local-path attachments, and full masking of proxy credentials in the administrator settings payload.
- The update-setup skill and `SECURITY.md` still name upstream's install channels, as on `main`.
