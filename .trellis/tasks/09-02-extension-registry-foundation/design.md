# Extension registry foundation — technical design

## Files and ownership

Create a small edge subsystem:

- `nanobot/extensions/contracts.py`: closed enums, immutable descriptors, ID and safe-text helpers, action values, adapter protocol.
- `nanobot/extensions/registry.py`: explicit adapter registration, snapshot assembly, validation, failure isolation, and action dispatch.
- `nanobot/extensions/__init__.py`: minimal public exports only.
- `tests/extensions/test_registry.py`: behavioral contract tests.

No current loader imports `nanobot.extensions` in this child. The package is inert until later adapters are wired.

## Contract types

Use `StrEnum` for JSON-stable closed vocabularies:

- `ExtensionSource`
- `ExtensionComponentKind`
- `ExtensionTrust`
- `ExtensionExecution`
- `ExtensionLifecycle`
- `ExtensionAction`

Use `@dataclass(frozen=True, slots=True)` for:

- `ExtensionConfigurationTarget`
- `ExtensionDiagnostic`
- `ExtensionComponentDescriptor`
- `ExtensionPackageDescriptor`
- `ExtensionAdapterSnapshot`
- `ExtensionSnapshot`
- `ExtensionActionContext`
- `ExtensionActionRequest`
- `ExtensionActionResult`

`ExtensionAdapter` is a runtime-checkable `Protocol` only if runtime validation is actually required; otherwise ordinary structural typing is sufficient.

### Bounds

Centralize conservative limits:

- ID segment: 128 characters
- display name: 256
- description: 2,000
- version/revision label: 256
- permission item: 256; at most 128
- capability item: 128; at most 256
- diagnostic/action message: 1,000
- action values: at most 64 keys, string keys only, recursively JSON-compatible with bounded nesting and aggregate scalar count

Reject invalid trusted constructor inputs rather than silently truncating identity or behavior fields. Safe error normalization may truncate untrusted exception text.

### IDs

Helpers:

```python
def extension_package_id(source: ExtensionSource, name: str) -> str: ...
def extension_component_id(
    package_id: str,
    kind: ExtensionComponentKind,
    name: str,
) -> str: ...
```

Canonical forms:

```text
ext:<source>:<name>
ext:<source>:<name>/<kind>:<component-name>
```

A segment matches `[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?`. Reject `..`, doubled structural delimiters, slash/backslash, whitespace, control characters, and non-ASCII. The helpers lowercase only enum values, never user names; callers must supply canonical names so visually different identifiers are not silently merged.

### Descriptor validation

`ExtensionPackageDescriptor.__post_init__` checks:

- its ID equals `extension_package_id(source, name)`
- name/display/description/version/revision/permissions are bounded
- component owner IDs equal the package ID
- component IDs equal the canonical derived ID
- no duplicate component IDs
- package lifecycle/actions are internally valid
- an external executable package does not claim `isolated=True` in this warning-only contract unless a future contract version adds a verified enforcement source

`ExtensionComponentDescriptor` validates its local fields but final owner/ID consistency is package-owned.

Use tuples/frozensets for stable immutability. Sort capabilities, permissions, actions, and components during validated construction only when their semantics are set-like; preserve explicitly meaningful display order nowhere in this foundation contract.

## Adapter protocol

```python
class ExtensionAdapter(Protocol):
    @property
    def name(self) -> str: ...

    def snapshot(self) -> ExtensionAdapterSnapshot: ...

    async def execute(
        self,
        request: ExtensionActionRequest,
    ) -> ExtensionActionResult: ...
```

Adapter names use the same ID-segment validator. `ExtensionAdapterSnapshot` repeats the adapter name so the registry can reject a mismatched result.

Adapters return complete package trees. They do not yield incrementally; atomicity keeps a late failure from publishing half an owner graph.

## Registration

`ExtensionRegistry` stores registrations in an instance-local dictionary:

```python
@dataclass(frozen=True, slots=True)
class _Registration:
    token: object
    adapter: ExtensionAdapter
```

`register(adapter)` validates and reads the adapter name once, rejects duplicates, stores a fresh opaque token, and returns a disposer closure. The disposer removes the row only when the stored token still matches and may be called repeatedly. No module-global registry exists.

## Snapshot assembly

1. Copy registrations, sorted by adapter name, so adapter callbacks cannot mutate the active iteration.
2. Call each `snapshot()` independently.
3. Validate the returned adapter name and its entire package/component tree into a temporary local index.
4. Detect collisions against already accepted package/component IDs.
5. Only after all validation passes, merge that adapter's full rows.
6. On any exception, discard the temporary rows and append one safe diagnostic.
7. Return packages sorted by package ID and diagnostics sorted by adapter name.

A collision is attributed to the lexically later adapter because adapters are processed by stable adapter name. This makes equivalent registration sets deterministic regardless of registration order.

Do not catch `BaseException`; cancellation/system-exit conditions must propagate. Catch ordinary `Exception` at the adapter boundary.

## Safe diagnostics

`safe_extension_message(value)`:

- converts to one line
- removes ASCII control characters
- replaces detected POSIX absolute paths, Windows drive paths, and home-directory forms with `<path>`
- caps output length
- returns a generic fallback when empty

It is defense in depth, not a secret scanner. Contracts prohibit adapters from supplying secret values; tests verify common path/control/length cases.

Server logging of full adapter exceptions belongs to later integration, where the project logger is available. This foundation registry stores/returns only the safe message.

## Action dispatch

`execute(request)` always builds a fresh validated snapshot and target index before any mutation. This avoids dispatching to an adapter whose package disappeared or revision changed after the UI read it.

Validation order:

1. `request.context.is_system_admin` must be true.
2. Target ID resolves to exactly one package or component and owning adapter.
3. Requested action exists in that exact target's supported actions.
4. Action values pass bounded JSON validation.
5. For external executable `enable` or `install`:
   - `risk_acknowledged` is true
   - target package revision is non-empty
   - `expected_revision` exactly matches it
6. Dispatch to the owning adapter only.
7. Validate that the result target IDs equal the request target/owner and its action equals the request action.
8. Normalize the result message and return it.

Define a narrow `ExtensionRegistryError` carrying a stable error code and HTTP-neutral status suggestion. Later API adapters translate it; this package imports no WebUI types.

No optimistic state is stored. The next snapshot derives authoritative state from the family owner.

## Test strategy

Tests use tiny fixture adapters and observable call counters. High-signal cases:

- valid and invalid canonical IDs at every boundary
- immutable descriptor contents
- package/component ownership mismatch
- duplicate within one adapter and across adapters
- adapter registration order independence
- stale disposer cannot remove a different registration
- exception and malformed-snapshot isolation
- path/control/oversize diagnostic redaction
- no action callback for unauthorized, unsupported, missing, stale, unacknowledged, or malformed requests
- exact owning adapter receives one valid request
- cross-target action result rejection

Avoid source-text assertions and mocks of implementation internals. Assert only public contract behavior and callback effects.

## Compatibility and rollback

This child creates only new files and exports. No configuration or persisted state changes. Rollback removes the package and tests. Later children must not begin until these contracts and their tests pass review.
