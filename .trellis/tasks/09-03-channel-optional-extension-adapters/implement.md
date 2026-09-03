# Channel and optional extension adapters — implementation plan

## Ordered work

1. **Freeze IDs and lifecycle mapping**
   - Package/instance canonical ID constructors and safe revisions.
   - Manifest/config/dependency/manager status projection.
   - Per-package failure isolation and bounded instance consumption.

2. **Read-only adapters**
   - ChannelExtensionAdapter snapshots without runtime imports.
   - Standalone OptionalFeatureExtensionAdapter excluding channel-owned extras.
   - Runtime composition and snapshot parity tests.

3. **Lifecycle service boundary**
   - Narrow ChannelManager exact-instance status/action port.
   - Shared dependency preparation service for startup and actions.
   - Existing config validation/persistence/connector service injection.

4. **Canonical actions**
   - Configure, install, enable, disable, reconnect/connector completion.
   - System-admin/revision/ack/install-policy no-side-effect gates.
   - Single config mutation and manager call per action.

5. **Settings compatibility cutover**
   - Canonical feature/channel rows and opaque action fields.
   - Migrate configure/connect/pairing and feature actions.
   - Preserve custom channel panels and mobile layout.
   - Delete duplicate feature inventory/actions/status overlays and pass-through callbacks.

6. **CLI and onboarding cutover**
   - Manifest-only list/status/catalog.
   - Selected lazy runtime/connector login paths.
   - Remove eager `discover_all` only after every caller migrates.

7. **Hardening and verification**
   - Feishu/Weixin multi-instance and legacy migration.
   - Pair-Code/runtime routing regressions.
   - Full backend/frontend/type/lint/build and browser/CLI smoke.

## Parallel slices after activation

- Channel package/instance snapshot and tests.
- Optional-feature split and dependency tests.
- Manager lifecycle port/actions and tests.
- Settings backend compatibility/auth and tests.
- Channel frontend opaque fields/custom-panel tests.
- CLI/onboarding deferred-import cutover and tests.

Main owns cross-slice ID/action contracts and final duplicate deletion.

## Validation commands

```bash
uv run --no-sync pytest -q \
  tests/extensions \
  tests/channels \
  nanobot/channels/feishu/tests \
  nanobot/channels/weixin/tests \
  nanobot/channels/websocket/tests/test_websocket_http_routes.py
uv run ruff check nanobot tests
uv run --no-sync basedpyright
uv run --no-sync pytest -q
cd webui && bun run test
cd webui && bun run lint
cd webui && bun run build
```

## Review gates

- Inventory test proves no runtime/SDK import before action work starts.
- Multi-instance IDs/revisions/status fixed before lifecycle actions.
- Authorization/install policy no-side-effect tests before Settings/CLI cutover.
- Migrate all callers before deleting direct APIs.
- No secret/path/installer/runtime detail in any payload/diagnostic.
- Final search shows one channel lifecycle owner and no duplicate optional-feature package.

## Rollback

Read-only adapters are additive. Action/caller cutover retains config formats and can restore old callers without data migration. Manager remains live owner throughout; no runtime task/state directory/session is moved or deleted.
