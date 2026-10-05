# Design: full upstream integration and GG release

## Integration ownership and boundaries

The parent task has direct integration work: preserve the current tested tracked changes, integrate the pinned canonical upstream, resolve shared runtime/security contracts, and own the final artifact and GG release. The feature slices are not independent release candidates; all must pass the same integrated verification before deployment.

Use an ordinary ancestry-preserving merge, not selective cherry-picks or blanket conflict-side selection. Keep Mikobot branding, project collaboration, host-owner/King authority, private session provenance and the narrower member capability model. Incorporate upstream provider/compaction, WebUI, channel, CLI/TUI and documentation behavior in their current ownership layers. Preserve deleted/renamed upstream paths only if a real local caller still requires the behavior; migrate such callers rather than retaining dead compatibility aliases.

### Slice ownership

- Parent: `agent/loop.py`, collaboration store/repository/projections, session authorization, gateway composition, shared DTO/mutation contracts, version metadata, release documentation and integration checks.
- Backend integration: remaining agent/tool/channel/provider conflicts and their existing callers, including exact-instance lifecycle operations. Confirm interfaces with the parent before changing shared files.
- Frontend integration: WebUI and channel-owned React surfaces, translations and inherited desktop/mobile behavior. Reuse the repository's dialogs, mutation transport and project Pair Code flow.
- Tester: permanent behavioral test updates/authoring only, after shared contracts are established. No test/lint/build/formatter runs inside implementation slices; parent runs the integrated gates once all changes are complete.

JSON translations require structural, key-level merging. Preserve both upstream copy and local project/member/instance keys, with an explicit choice for real value collisions; concatenating JSON objects or accepting an entire side is not a valid resolution.

## WeChat lifecycle contract

Three distinct actions appear for each exact WeChat instance. Administrative actions remain server-authorized and bind to the displayed extension target and current revision over the existing authenticated WebSocket mutation plane.

### Delete instance

Confirmation identifies the selected instance and states that account/instance binding is removed but historical conversations and other instances remain. Coordinate cancellation of outstanding login sessions, runtime stop, instance config removal, credential invalidation and collaboration authority cleanup. Revoked authority must reject late login completion and subsequent derived work. Remove only known account-state files/records; never recursively delete an arbitrary `stateDir` or shared directory. Cover default, additional and last-instance cases without recreating deleted credentials.

The current exact-instance configuration owner is the channel adapter/config mutation service, with channel-owned normalization in `weixin/instances.py`. Extend that path rather than writing config from React or introducing a second persistent config store. If the operation spans config and collaboration state, order revocation/cancellation so an interrupted cleanup cannot leave a supposedly deleted instance authorized, and expose a real failure rather than reporting completion prematurely.

### Rescan WeChat account

An explicit replacement action starts `mode=replace`, the existing `instance_id`, and `force=true` on the first click. The existing short-lived connector clears only in-memory pending credentials; persisted account state changes only after successful scan confirmation. Preserve this cancellation/failure boundary while incorporating upstream authentication recovery. New-account login, replacement and automatic auth recovery must not silently target the default or create an extra instance.

### Re-pair project

Retain the WeChat account and select the project and permitted assignee. Reuse the existing actor-authorized Pair Code create/verify/consume flow. For an already-assigned instance, explicitly confirm unlinking the old project before issuing the replacement Pair Code; warn that normal delivery remains suspended until the new Pair Code is verified and consumed. Revoke active superseded challenges even when the instance has no assignment. Do not turn a user-provided actor/admin field or sender label into authorization. Successful pairing reactivates the exact instance and refreshes the visible status.

## Persistence and compatibility

The local King migration upgrades collaboration schema 13 to 14 without altering anyone's active-project preference, memberships, workspace or grants. The built-in home is initially marked King; subsequent administrator designation remains globally unique. Ordinary members and OIDC administrators do not inherit the local owner's cross-project tool privilege. Upstream integration must retain revalidation on tools and inherited work.

Rehearse the integrated migration on an isolated copy of the latest GG state, not the stale planning inventory and not live production files. Compare user/project/membership/assignment/binding identifiers, immutable content and JSONL history; restrict expected deltas to legitimate migration metadata and new fields. Any additional persisted shape required during implementation must have a preserving migration and update the release acceptance contract; no reset or lenient decoder shortcut.

## Release identity and rollback

Use the fork's next minor version 0.8.0 after a fresh collision check. Build the frontend from the integrated source and package its exact output into the wheel. Stage under a new filename, hash the local and remote wheel, and verify installed package payloads against that artifact. Account for decompressed gzip payloads when reviewing asset deltas.

The current GG rollback receipt uses TOML `path`; the existing rollback reinstall parser ignores it. Back up and correct that source selection before relying on the guard, and exercise command construction against the current exact wheel path without reinstalling the healthy service. Do not allow a bare PyPI `nanobot-ai` fallback to masquerade as rollback to this fork.

Stage artifacts and dependencies before the outage. During the maintenance window stop snapshot automation, stop `nanobot.service`, and take the final complete state/receipt/guard backup while stopped. Install the exact new wheel with the retained receipt requirements plus any newly required dependencies of enabled channels. Start and require healthy/ready, WebSocket running and zero restarts. On failure, restore the exact old wheel with its compatible stopped-state backup; preserve the failed state separately.

Only after local runtime checks and authenticated public UI acceptance mark the new release known-good. Re-arm the monotonic snapshot timer with a snapshot service run and prove that NEXT is finite. Public acceptance must not delete, reconnect, re-pair or send messages through existing production WeChat accounts.
