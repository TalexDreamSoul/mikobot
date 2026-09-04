# Runtime extension adapters — implementation plan

## Grandchild map

1. **Channel and optional-feature adapters**
   - Canonical ChannelPlugin/package and instance components.
   - ChannelManager/config/dependency/connector lifecycle delegation.
   - Standalone optional extras.
   - Settings/CLI/onboarding caller cutover and duplicate deletion.

2. **Provider and hook adapters**
   - Grouped LLM/image/transcription provider packages.
   - Existing image reload delegation and provider Settings compatibility.
   - Explicit persistent hook metadata/composition with unchanged execution order.

3. **Installed CLI-app adapter**
   - Durable installed package/executable/Skill projection.
   - Generated Agent Plugin ownership marker/exclusion.
   - Update/uninstall/test action cutover and Apps/ThreadShell compatibility.
   - Catalog-only install remains family-owned.

## Ordering

Channel/optional and provider/hook depend only on the frozen registry/core runtime and may be implemented independently after their own approval. CLI app work additionally depends on the AgentPlugin adapter ownership-exclusion contract.

## Shared validation

Each grandchild runs focused family tests, Ruff, BasedPyright, and applicable frontend tests/build. Final runtime-adapters integration runs:

```bash
uv run --no-sync pytest -q
uv run ruff check nanobot tests
uv run --no-sync basedpyright
cd webui && bun run test
cd webui && bun run lint
cd webui && bun run build
```

Actual smoke:

- disabled channel inventory without optional SDK import
- multi-instance channel enable/disable/reconnect status
- selected image reload and transcription next-request behavior
- persistent hook order and ephemeral exclusion
- installed CLI app row/action/generated Skill single ownership
- desktop/mobile compatibility surfaces and administrator denial

## Review gates

- Freeze package/component IDs and per-family lifecycle mapping before parallel edits.
- Snapshot parity before action cutover.
- Authorization/no-side-effect tests before any package-manager/config/runtime action.
- Migrate all family callers before deleting old lifecycle branches.
- Search for direct duplicate inventory/action calls after each grandchild.
- No adapter may expose secrets/paths/commands or imply sandboxing.

## High-risk boundaries

- Channel runtime imports and instance state paths.
- Provider registry order and factory/model selection.
- Image runtime control versus transcription per-request semantics.
- Hook ordering, ephemeral suppression, and public SDK hooks.
- CLI package-manager argv/state/generated Skill ownership.
- Global Settings administrator and remote-install policy.

## Rollback

All projections are additive before cutover. Family action rollback restores the old caller and removes adapter dispatch in the same grandchild. Existing config, credentials, sessions, channel state, provider state, and CLI installed-state formats remain compatible; only CLI generated manifests receive additive ownership metadata.
