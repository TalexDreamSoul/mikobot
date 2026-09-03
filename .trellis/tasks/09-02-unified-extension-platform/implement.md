# Unified extension platform — implementation plan

## Parent/child delivery map

1. **Extension registry foundation**
   - Add immutable package/component contracts, closed enums, ID constructors, diagnostics, action requests/results, adapter protocol, registry assembly, failure isolation, and contract tests.
   - No production adapter, API, UI, or runtime behavior change.

2. **Core capability adapters**
   - Adapt Agent Plugins, plugin/workspace/builtin Skills, Agent Plugin/configured MCP servers, built-in tools, and Python entry-point tools.
   - Expose already-discovered tool provenance without a second import pass.
   - Move Agent Plugin enable/disable and MCP reconciliation to canonical registry actions; remove the duplicate Agent Plugin lifecycle branch from MCP preset actions after all callers migrate.

3. **Runtime extension adapters**
   - Adapt channels, standalone optional features, LLM providers, image providers, transcription providers, registered hook factories, and installed CLI apps.
   - Add explicit metadata wrappers for long-lived hook registrations while preserving callable constructor compatibility.
   - Move each supported lifecycle action through the registry, one family at a time, and remove each duplicate route only after caller migration.

4. **Extensions control plane**
   - Add system-admin-only snapshot/action endpoints and safe payload types.
   - Add Settings navigation, package list/detail, filters, lifecycle actions, configuration links, trust labels, and revision-bound warning acknowledgement.
   - Complete English and Simplified Chinese text and mobile layout.

5. **Cutover and hardening**
   - Remove all migrated duplicate lifecycle controls and dead projection code.
   - Verify authorization, redaction, stale revisions, adapter failure isolation, hot reload/restart states, and cross-family identity stability.
   - Run full backend/frontend suites, production build, and desktop/mobile browser smoke.

## Ordering

The registry foundation is a strict prerequisite for every adapter. Core and runtime adapter work may proceed independently only after the contract is frozen. The control plane consumes both adapter groups. Cutover/hardening runs last.

## Foundation checklist

- Define `nanobot/extensions/` as a small edge subsystem.
- Implement closed enums and frozen/slot-based descriptors.
- Implement validated package/component ID constructors.
- Implement safe bounded description, permission, revision, and diagnostic normalization.
- Implement complete-tree validation and deterministic ordering.
- Implement explicit adapter registration with idempotent disposers.
- Implement snapshot failure isolation without accepting partial adapter output.
- Implement action lookup, authorization fact, supported-action checks, current-revision checks, risk acknowledgement checks, and normalized results.
- Export only the minimal public contract.
- Add focused behavior tests for every invariant; do not touch product loaders.

## Core adapter checklist

- Add provenance to tool discovery results without changing registration order.
- Group Agent Plugin Skills/MCP components under their package fingerprint.
- Add synthetic owners for builtin/workspace Skills and configured MCP servers.
- Classify built-in versus Python entry-point tools truthfully.
- Reuse `MCPProvider` runtime statuses and reload owner.
- Route Agent Plugin enable/disable through the registry with expected revision and warning acknowledgement.
- Migrate WebUI callers and delete the Agent Plugin special case in MCP preset lifecycle code.
- Verify package replacement revokes enablement and invalidates stale acknowledgement.

## Runtime adapter checklist

- Adapt dependency-free channel manifests without importing optional SDK runtimes.
- Overlay channel desired/configured/runtime states and existing supported actions.
- Fold channel-owned optional dependency rows into the channel package; retain standalone extras separately.
- Adapt LLM, image, and transcription provider metadata and configuration destinations without secrets.
- Introduce stable metadata wrappers for registered long-lived hook factories and migrate internal hook registrations.
- Adapt installed CLI apps and their generated Skills; keep catalog candidates outside the installed tree.
- Migrate lifecycle routes family by family, updating every caller before deleting the old path.

## Control-plane checklist

- Add extension snapshot/action services to the existing Settings router boundary.
- Reuse existing local-owner/OIDC-system-admin authorization.
- Keep ordinary collaboration capability projection separate from global inventory.
- Add revision-bound mutation payloads and explicit executable-risk acknowledgement.
- Add TypeScript contracts and API functions.
- Add Extensions Settings navigation and responsive list/detail surface.
- Link components to Models, Image, Voice, Channels, MCP, Skills, or Apps configuration sections.
- Remove duplicate action buttons only after their registry action is live.
- Add complete `en` and `zh-CN` translations.

## Verification commands

```bash
uv run --no-sync pytest -q <focused extension tests>
uv run ruff check nanobot tests
uv run --no-sync basedpyright
uv run --no-sync pytest -q
cd webui && bun run test
cd webui && bun run lint
cd webui && bun run build
```

Browser verification must exercise:

- desktop and mobile Extensions navigation
- all family filters and package details
- data-only versus executable trust labels
- cancel and confirm warning flows
- stale revision conflict
- Agent Plugin hot enable/disable
- restart-required Python tool/hook rows
- configuration links for channels and providers
- Chinese localization with no English literals on the new surface

## High-risk files and boundaries

- `nanobot/agent/tools/loader.py`: importing entry points twice or changing tool precedence is a regression.
- `nanobot/agent/plugins.py`: package fingerprint and Skill path containment must remain authoritative.
- `nanobot/agent/tools/mcp.py`: connection ownership, tool cleanup, and hot reload must remain single-owner.
- `nanobot/channels/registry.py` and `nanobot/channels/manager.py`: descriptor inspection must not import disabled runtime SDKs or change instance routing.
- `nanobot/providers/registry.py` and `nanobot/providers/factory.py`: provider priority/model matching must not depend on control-plane ordering.
- `nanobot/agent/loop.py`: avoid registry logic; only explicit hook metadata plumbing is acceptable if no edge-owned alternative exists.
- `nanobot/webui/settings_routes.py`: global action authorization and redaction remain system-admin boundaries.
- Settings controller/UI: remove duplicate lifecycle state rather than creating a second client store.

## Review gates

- Contract review before any family adapter: IDs, enums, ownership, state/action grammar, and safe diagnostics must be frozen.
- One family at a time: snapshot parity first, action cutover second, duplicate deletion third.
- No family may derive enabled state from the registry; the real runtime/config owner remains authoritative.
- No external executable can be enabled/installed through the registry without current-revision acknowledgement.
- No UI claim may imply isolation, permission enforcement, publisher verification, or safety.
- Final diff must contain no permanent compatibility alias or dual mutation path.

## Rollback points

- Foundation is additive and can be removed with no state migration.
- Each adapter remains additive until its action caller cutover.
- A failed family cutover restores its old caller and removes its adapter action in the same rollback; descriptors may remain read-only only if they are still authoritative and non-duplicative.
- UI can be removed without changing installed extension state.
- No step rewrites package files, channel credentials, provider credentials, or user sessions.
