# Core capability extension adapters — implementation plan

## Ordered implementation

1. **Tool provenance**
   - Add immutable optional `ToolRegistrationMetadata` to `ToolRegistry`.
   - Preserve `register(tool)` while maintaining metadata on register/overwrite/unregister.
   - Retain entry-point names in `ToolLoader` and attach metadata at the exact successful registration point.
   - Add the read-only core-tool adapter and prove main-registry-only ownership.

2. **Agent Plugin safe metadata**
   - Expose current content revision and valid Skill/MCP member names from one discovery pass.
   - Reuse a precomputed fingerprint for marker verification without changing marker format.
   - Add deterministic canonical component-name encoding.
   - Add Agent Plugin package projection and enable/disable adapter action.

3. **Skills and MCP adapters**
   - Project only effective workspace/builtin Skills from `SkillsLoader` and avoid Agent Plugin duplication.
   - Add safe content revisions and bounded diagnostics.
   - Project configured MCP servers from config with safe hashed revisions and runtime-status overlay.
   - Verify configured/plugin collision ownership and no secret/path projection.

4. **Runtime composition**
   - Add `build_core_extension_registry` with explicit ToolRegistry/config/MCP callbacks.
   - Construct the registry in real application compositions after default tool loading.
   - Pass the Gateway registry explicitly into WebUI Settings services.
   - Leave subagent/Dream/filtered/test registries outside main inventory.

5. **Settings backend cutover**
   - Add registry compatibility projection for Agent Plugin MCP/App rows.
   - Add system-admin and server-derived actor enforcement to every global MCP mutation.
   - Dispatch Agent Plugin enable/disable through canonical request fields.
   - Invoke the existing MCP reload bridge once after registry success.
   - Delete direct Agent Plugin discovery and `plugin-*` lifecycle-prefix dispatch.

6. **Compatibility warning UI**
   - Extend safe TypeScript row/action contracts.
   - Add bilingual revision-bound executable-risk confirmation before enable.
   - Ensure cancel is a no-op and disable needs no acknowledgement.
   - Preserve configured MCP requests and chat mention filtering.

7. **Hardening and cleanup**
   - Remove unused imports/helpers and duplicate projection/action code.
   - Check every ToolLoader/ToolRegistry and Settings caller.
   - Run focused tests, complete backend/frontend suites, strict types, lint, build, and browser smoke.

## Parallel ownership after activation

After shared contracts/provenance shapes are fixed, implementation can fan out:

- ToolRegistry/ToolLoader provenance and core-tool adapter.
- Agent Plugin revision/components/adapter.
- Effective Skills and configured MCP adapters.
- Backend Settings registry composition/action cutover.
- Frontend warning/types/tests.

Main owns integration files and resolves the shared registry-construction boundary. Test authorship stays separate from implementation ownership.

## Validation commands

```bash
uv run --no-sync pytest -q \
  tests/extensions \
  tests/agent/test_agent_plugins.py \
  tests/agent/test_tool_loader_entrypoints.py \
  tests/agent/test_tool_loader_scopes.py \
  tests/agent/test_mcp_connection.py \
  tests/webui/test_mcp_presets_api.py \
  tests/webui/test_settings_routes.py
uv run ruff check nanobot tests
uv run --no-sync basedpyright
uv run --no-sync pytest -q
cd webui && bun run test
cd webui && bun run lint
cd webui && bun run build
```

Browser smoke must verify desktop/mobile Agent Plugin enable warning, cancel, confirm, stale revision, non-admin rejection, disable, reload-failure messaging, and Chinese text.

## High-risk boundaries

- `ToolLoader.load`: preserve order, scope, enabled checks, construction, wrapping, and collision semantics exactly.
- `ToolRegistry.register`: optional metadata must not change overwrite or definition-cache behavior.
- Agent Plugin fingerprint helpers: no second full package hash, marker-format change, root leak, or loss of replacement revocation.
- `SkillsLoader.list_skills`: registry projection must not alter precedence or runtime loading.
- `MCPProvider`: adapters receive callbacks/status only; no second connection/process owner.
- Settings identity: actor/admin facts are server-derived; client never supplies them.
- Existing MCP routes: configured MCP must not accidentally enter Agent Plugin canonical dispatch.
- WebUI warning: acknowledgement binds to the exact current revision but is disclosure, not a safety claim.

## Review gates

- Freeze ToolRegistrationMetadata and core adapter IDs before parallel edits.
- Verify Agent Plugin discovery computes one revision and existing marker tests still pass before adding actions.
- Verify pure adapter snapshots and redaction before composition.
- Verify backend admin/revision/ack no-dispatch tests before frontend wiring.
- Migrate every existing Agent Plugin action caller before deleting the prefix branch.
- Final search finds no independent Agent Plugin WebUI discovery/action lifecycle path.

## Rollback

- Provenance and read-only adapters are additive.
- Agent Plugin metadata fields do not change on-disk state.
- Settings cutover uses the existing marker and route names; rollback can restore the prior caller without data migration.
- Reload failure does not roll marker state back, matching existing restart-required semantics.
- UI warning removal does not alter installed/enable state.
