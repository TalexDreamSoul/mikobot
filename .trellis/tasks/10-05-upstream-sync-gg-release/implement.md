# Execution plan

## Start gate

Do not execute product changes, commits, a merge or service mutations until the user explicitly approves the final planning summary. Start this task only after that approval; the parent owns direct integration work.

## 1. Preserve and integrate

1. Refresh local/remote identity and the canonical upstream head; record the exact accepted upstream commit. Re-check the working tree for intervening changes and the target version for collisions.
2. Preserve the complete tracked diff and release inputs in a task-owned temporary backup. Commit only the already-tested 23 tracked project/King paths once local-commit permission is granted. Exclude unrelated `.omp/` and old untracked Trellis tasks; never blanket stash/reset/clean the shared worktree.
3. Perform an ancestry-preserving, no-auto-commit merge of canonical upstream. Resolve shared core/security/DTO conflicts centrally. Fan out genuinely independent backend and frontend/localization slices with explicit file ownership and no mid-flight checks.
4. Retain all local project/privacy semantics, incorporate the full upstream behavior, and migrate obsolete caller paths. Tester owns permanent behavioral test repairs; do not retain incidental copy/wiring tests that only pin implementation text.

## 2. WeChat controls

1. Add the exact-instance delete operation at the current locked configuration/runtime owner and clean up collaboration authority and pending connector sessions. Expose the operation through existing authenticated settings mutations with actor/revision checks.
2. Add the confirmed per-instance UI action and refresh the actual feature/instance projection after completion. Cancellation leaves the instance unchanged; errors do not hide the instance or claim success.
3. Make explicit rescan request the same instance with replacement mode and force on the first attempt. Preserve existing credentials until success, including cancellation, failure and late-poll races.
4. Offer project re-pairing with project/assignee selection, unlink confirmation, superseded-code invalidation, Pair Code status and exact-instance activation. Distinguish this action from account rescan.
5. Integrate these controls with upstream mobile/dialog/auth-recovery behavior and all supported locale keys. Update the existing WebUI/channel documentation and release archive.

## 3. Integrated verification and artifact

1. Prepare the repository-declared dependencies and channel test dependencies, then run:
   - `uv run --no-sync ruff check nanobot tests conftest.py`
   - `uv run --no-sync basedpyright`
   - `uv run --no-sync python -m pytest -q`
   - the frontend's declared type-check/test/build commands
   - the affected CLI/TUI's declared checks/build and an actual CLI startup smoke
2. Exercise the integrated gateway against an isolated local config. In one Ego TaskSpace verify desktop and narrow-screen project creation, King designation, three WeChat actions, cancellation/error states and project pairing using disposable local fixtures only.
3. Run real filesystem/shell authority probes for owner-in-King, owner-in-ordinary-project and ordinary member; include changed-King revocation and private session boundaries. Use an isolated Linux environment for Linux-specific runtime paths where needed.
4. Rehearse schema migration on a copied latest GG state and compare record/history integrity. Do not mutate live production state for rehearsal.
5. Only after runtime proof complete the scoped merge/release commits, bump version to the collision-free new release, build the exact wheel into `/tmp`, and verify its Python/UI payloads and version metadata. The user approved pushing the accepted result to the existing origin branch; do not create tags or hosted releases.

## 4. GG rollout and acceptance

1. Read the current receipt, service health, guard, snapshot timer, channels, collaboration state and disk headroom. This is a fresh pre-window inventory; planning counts are not immutable production expectations.
2. Back up and correct rollback receipt parsing for `path` sources; prove it resolves the retained wheel without changing the healthy installation.
3. Transfer the exact wheel to a new guard-wheel filename and require equal SHA-256. Prepare enabled-channel dependencies before stopping service where possible.
4. Stop the snapshot timer and service; create the final stopped-state backup with receipt and guard scripts. Install the exact artifact with the retained/new required dependencies, run precheck and start the service.
5. Require ready health, running WebSocket, active/running service and zero restarts. Verify receipt, installed package/UI hashes, schema and history integrity. Preserve old wheel and state for paired rollback.
6. Verify public HTML 200, unauthenticated bootstrap 401, authenticated bootstrap and actual public Projects/channel surfaces. Do not operate existing production WeChat accounts. If an independently expired account remains down, compare pre-window evidence rather than treating login expiry as an installation failure.
7. After acceptance take the new known-good snapshot, verify version/receipt/state, re-arm the monotonic timer and require a finite next activation. Review warnings without exposing credentials.
8. Close the single successful Ego TaskSpace once, stop task-owned local services, and remove throwaway scripts. Persist exact release, upstream, wheel and backup evidence in this task and existing release documentation.

## Rollback triggers

Missing migration records, lost history/credentials, incorrect artifact identity, startup instability, privacy/King regression or failed authenticated WebUI acceptance block promotion. Restore the exact 0.7.4 wheel and its compatible stopped backup on failed rollout; retain the failed new state separately for diagnosis and do not claim the new release is deployed.
