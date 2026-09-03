# Channel and optional extension adapters — technical design

## Files and ownership

Expected product boundaries:

- `nanobot/extensions/adapters/channels.py`: channel package/instance projection and action translation.
- `nanobot/extensions/adapters/optional_features.py`: standalone non-channel extras.
- `nanobot/extensions/runtime.py`: explicit adapter registration/dependency injection.
- `nanobot/channels/manager.py`: narrow public lifecycle/status port; sole runtime owner.
- `nanobot/optional_features.py`: retained generic dependency/extra mechanics; channel-specific branches removed after cutover.
- WebUI Settings/channel APIs and CLI/onboarding: compatibility translation/caller migration.

No channel runtime implementation or channel-owned WebUI module moves.

## Channel adapter dependencies

Construct `ChannelExtensionAdapter` with explicit services rather than globals:

```python
@dataclass(frozen=True, slots=True)
class ChannelExtensionServices:
    config_loader: Callable[[], Config]
    mutate_config: Callable[[Callable[[Config], T]], T]
    runtime_status: Callable[[], Mapping[str, Mapping[str, object]]]
    apply_runtime_action: Callable[[str, str, str], Awaitable[dict[str, object]]]
    install_dependencies: Callable[..., InstallResult]
    allow_package_install: Callable[[ExtensionActionContext], bool]
```

Validation/config/connector helpers remain channel-domain functions invoked by action handlers. The runtime port targets canonical channel type + raw instance ID and hides manager private dictionaries.

Snapshot is synchronous and side-effect-free: dependency-free manifest/config/local-state/status only. Runtime/network operations are action-only.

## Canonical model

Package:

```text
ext:channel_package:<canonical-channel-name>
```

Instance component:

```text
<package-id>/channel:<canonical-instance-id>
```

Use raw `(channel_type, instance_id)` only inside the adapter action target map. Runtime names such as `feishu.product` are status/routing outputs, never canonical ownership IDs.

Package/component projection:

- source `CHANNEL_PACKAGE`
- bundled trust `FIRST_PARTY`
- execution `IN_PROCESS`
- permissions enforced false
- configuration target `channels/<raw-channel-safe-key>` represented as canonical section/item, never a URL/path
- capabilities from manifest, bounded and path-free
- dependencies represented as configured/installed booleans or safe requirement names only

Revision hashes:

- canonical manifest name, declared capabilities/dependency requirement strings
- safe setup field names/kinds, never values
- raw instance ID, enabled/configured booleans and configured-field names
- no state path, secret, connector/session value, runtime error, or host path

An invalid raw name uses shared 128-bit canonicalization. Detect duplicate canonical IDs within a package before descriptor construction and isolate that package on conflict.

## Lifecycle mapping

For each instance:

1. Dependencies missing → `UNAVAILABLE`.
2. Desired disabled → `DISABLED` regardless of sibling runtime.
3. Desired enabled but required setup incomplete → `UNAVAILABLE` or `FAILED` with safe setup diagnostic.
4. Manager status `starting` → `ENABLING`; pairing-only → `RELOADING`; running → `ENABLED`; failed → `FAILED`; stopped while desired enabled → `FAILED`.
5. No manager row while desired enabled → `FAILED`.

Package lifecycle summarizes components by severity without changing individual truth: failed, enabling/reloading, enabled, unavailable, otherwise disabled.

## Actions

Registry prevalidates admin/action/revision/ack. Adapter maps target ID to raw manifest/instance and rechecks current revision from fresh config before mutation.

### Configure

- Decode bounded action values owned by current channel setup field contract.
- Call existing `save_channel_config_values`/channel validator under serialized config mutation.
- Preserve omitted/blank secret behavior and unrelated instance fields.
- Return configured desired state; never import/start runtime unless explicit enable follows.

### Install

- Require existing remote-install policy in addition to registry admin/ack.
- Reuse `install_extra`/manifest dependencies through one dependency service.
- Log full installer output server-side; return safe bounded success/failure and restart truth.
- Do not import/start runtime for install-only action.

### Enable

- Read latest config and verify setup/revision.
- Install missing dependency only when the action explicitly authorizes it and policy allows.
- Persist exact-instance enabled state once through `channel_set_config_enabled`/management update.
- Call manager runtime action once.
- On successful explicit start/connect, call existing metadata refresh through the selected runtime path.
- If live start fails, keep authoritative desired state and report failed/restart-required; no fake rollback.

### Disable

Persist exact-instance disabled state once, then stop/remove that runtime once. Siblings remain untouched.

### Reconnect / connector

Channel-owned connector start/poll/cancel remains a flow outside synchronous snapshot. Flow records the canonical target and server actor. Successful completion dispatches exact-instance enable/reconcile once. Pairing-only manager state is transient.

## Optional features

Build the set of channel-owned dependency group names from manifests first. `OptionalFeatureExtensionAdapter` then projects only remaining extras.

One package owns one `OPTIONAL_FEATURE` component. Lifecycle is installed/enabled when requirements are present, unavailable otherwise. Actions are inspect/install and supported disable/uninstall only where current generic behavior truly supports them. Preserve bundled aliases/hidden features and package installation policy.

## Compatibility cutover

### Settings

Render channel/feature canonical descriptors into existing `NanobotFeatureInfo` and channel instance payloads. Add opaque `extension_id`, revision, lifecycle, actions, trust/execution, and acknowledgement facts where needed.

Every mutation constructs server actor/admin context and dispatches registry actions. Existing channel-specific React modules consume their current configuration/status fields plus opaque IDs; no generic Extensions UI.

After cutover delete:

- direct channel discovery/actions inside optional-feature Settings paths
- duplicate runtime-status overlays
- generic `channel_feature_action` pass-through
- manager-side duplicate persistence once adapter owns sequence

### CLI/onboarding

List/status use manifests/registry only. Explicit login/connector selects exact manifest then lazily loads connector/runtime. Migrate every `discover_all` caller before removing eager discovery API.

## Tests and rollback

Tests use fake manifests/management/manager ports and real Feishu/Weixin helpers for multi-instance invariants. Test module import sets prove disabled inventory stays runtime-SDK-free.

All config formats stay unchanged. Before caller cutover adapters are additive. After cutover rollback restores the old caller and removes adapter dispatch in the same change; no channel credentials/state/sessions are migrated.
