# Error Handling

> How errors are handled in this project.

---

## Overview

There is **no global exception middleware**. Each transport edge maps exceptions to its own
response shape, and each domain raises its own exception type. The governing rule is
"Explicit over magical" from [`.agent/design.md`](../../../.agent/design.md):

> Error handling should raise clear exceptions rather than silently correcting bad input.

Handling is layered:

| Layer | Behavior |
|---|---|
| Domain / store | Raise a typed domain exception. Never return a sentinel for a real failure. |
| Edge (HTTP, CLI, adapter) | Catch the domain type, map to a status + a **display-safe** message. |
| Unexpected `Exception` | `logger.exception(...)` with full detail, return a **fixed constant** message. |

The paired invariant: everything the caller sees must be safe to display; everything needed
for debugging goes to the log. See [`logging-guidelines.md`](./logging-guidelines.md).

---

## Error Types

Two families exist. Pick the one that matches your layer.

### 1. Edge exceptions that carry an HTTP status

The dominant pattern. Same three-line constructor everywhere — copy it verbatim:

```python
class ChannelConnectError(Exception):
    """User-facing channel connection failure."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
```

Real instances, all with this identical shape:

| Class | File |
|---|---|
| `ChannelConnectError` | `nanobot/channels/connect.py` |
| `OptionalFeatureError` | `nanobot/optional_features.py` |
| `CliAppError(ValueError)` | `nanobot/apps/cli/service.py` |
| `WebUISettingsError(ValueError)` | `nanobot/webui/settings_contracts.py` |

`ExtensionRegistryError` (`nanobot/extensions/registry.py`) adds a stable machine-readable
`code` as the first positional argument: `ExtensionRegistryError(code, message, *, status=400)`.

Subclass `ValueError` when the failure is genuinely an invalid-input failure and callers may
reasonably catch `ValueError`; otherwise subclass `Exception`. Both appear in the tree.

### 2. Domain hierarchies without a status

Persistence and store layers raise semantic exceptions and leave the status mapping to the
edge. `nanobot/collaboration/store.py`:

```python
class CollaborationStoreError(RuntimeError): ...
class CollaborationStoreFormatError(CollaborationStoreError): ...   # corrupt / over-bound state
class CollaborationNotFoundError(CollaborationStoreError): ...
class CollaborationPermissionError(CollaborationStoreError): ...
class CollaborationConflictError(CollaborationStoreError): ...
```

`nanobot/triggers/local_store.py` uses the same base-plus-subclasses shape
(`TriggerStoreError` / `TriggerNotFoundError` / `TriggerDisabledError`).

Module-private exceptions used purely for control flow are underscore-prefixed and stay in
their module: `_ChannelActionStaleError` (`nanobot/extensions/adapters/channels.py`),
`_PatchError` (`nanobot/agent/tools/apply_patch.py`), `_CodexHTTPError`
(`nanobot/providers/openai_codex_provider.py`).

Where the standard library already has the right meaning, extend it rather than inventing a
parallel type: `WorkspaceBoundaryError(PermissionError)`
(`nanobot/security/workspace_policy.py`), `UnsafeURLRequestError(httpx.RequestError)`
(`nanobot/security/network.py`), `ConfigLoadError(ValueError)` (`nanobot/config/errors.py`).

---

## Error Handling Patterns

### Narrow-then-broad at every edge

Catch the specific domain type first and pass its message through; catch broad `Exception`
last, log it, and return a constant. `_channel_connect` in `nanobot/webui/settings_system.py` is the
reference implementation:

```python
try:
    payload_value = cast(object, await connector.handle(action, query))
    ...
    payload = _sanitize_connector_payload(cast(Mapping[str, object], payload_value))
except ChannelConnectError as exc:
    return SettingsRouteResult.failure(exc.status, safe_extension_message(exc.message))
except Exception:
    self.logger.exception("failed to run channel connector")
    return SettingsRouteResult.failure(500, "channel connection could not be completed")
```

`_extension_action` in `nanobot/webui/nanobot_features_api.py` shows the same ordering when re-raising into a
single edge type:

```python
except OptionalFeatureError:
    raise
except ExtensionRegistryError as exc:
    raise OptionalFeatureError(safe_extension_message(exc), status=exc.status) from None
except ValueError:
    raise OptionalFeatureError("extension action request is invalid", status=400) from None
except Exception:
    raise OptionalFeatureError("extension action could not be completed", status=500) from None
```

Note the `from None` when the original exception must not leak through chained tracebacks to
a client-visible surface, and `from exc` when the chain stays internal
(`ExtensionRegistry.execute` in `nanobot/extensions/registry.py`).

### Validate the shape of anything dynamic before using it

Third-party SDK results, wire payloads, and adapter returns are untrusted boundaries
(`.agent/design.md`, "Type dynamic boundaries at the edge"). Check, then narrow — do not
`cast` and hope:

```python
if not isinstance(result_value, Mapping):
    return self._failure(request, package.id, "Channel action could not be completed.", ...)
result = cast(Mapping[str, object], result_value)
```

(`ChannelExtensionAdapter._runtime_result` in `nanobot/extensions/adapters/channels.py`.) Every `cast` must be backed by a runtime
check on the same path.

### Failure isolation in registries and adapters

A snapshot assembled from many independent adapters must not be destroyed by one failing
adapter. Collect the failure as a bounded diagnostic and continue:
`ExtensionRegistry._assemble_snapshot` catches per-adapter `Exception`, appends an
`ExtensionDiagnostic(code="adapter_snapshot_failed", ...)`, and `continue`s.
`ChannelExtensionAdapter.snapshot` (`nanobot/extensions/adapters/channels.py`) does the same
with `_add_diagnostic(diagnostics, "channel_config_unavailable")` when config loading fails.

This is the only sanctioned use of a broad swallow: the failure must become a **visible,
bounded diagnostic**, never a silent `pass`.

### Do not silently correct bad input

`load_config` raises `ValueError` when a `${VAR}` reference has no environment variable rather
than substituting an empty string (`nanobot/config/loader.py`, and see
[`.agent/gotchas.md`](../../../.agent/gotchas.md)). Follow that: reject, do not repair.

---

## API Error Responses

WebUI/gateway handlers return a transport-neutral result and let the dispatcher serialize it:

```python
# nanobot/webui/settings_contracts.py
SettingsRouteResult.failure(status, error)
SettingsRouteResult.success(payload, ...)
```

Lower-level HTTP responses use `http_error(status, message)` /
`http_json_response(...)` from `nanobot/webui/http_utils.py`.

**Every message that reaches a client must pass through redaction.** The shared helper is
`safe_extension_message` (`nanobot/extensions/contracts.py`), which bounds length, strips
control characters, and replaces local filesystem paths with `<path>`:

```python
def safe_extension_message(value: object) -> str:
    """Return one bounded diagnostic line without common local path disclosures."""
```

`ExtensionDiagnostic.__post_init__` applies it automatically to its `message`
(`nanobot/extensions/contracts.py`), so diagnostics are safe by construction.

Domain-specific redaction helpers exist where the shape is known:
`_redact_url` (`nanobot/agent/tools/mcp.py`), `_redact_url_for_log`
(`nanobot/agent/tools/web.py`), `masked_api_secret`
(`nanobot/webui/settings_capabilities.py`), `mask_secret_hint`
(`nanobot/webui/settings_models.py`).

### Status conventions in use

| Status | Meaning here |
|---|---|
| 400 | Invalid request/field values (default for every edge exception) |
| 403 | Authorization denied — e.g. controlling an unclaimed channel instance |
| 404 | Target does not exist, or capability not supported by this channel |
| 409 | Stale revision / concurrent-write conflict (`expected_revision` mismatch, stale OIDC snapshot) |
| 500 | Unexpected internal failure — always a constant message |
| 502 | A delegated adapter or upstream failed (`adapter_execution_failed`) |
| 503 | A required subsystem is unavailable (e.g. extension registry not built) |

---

## Common Mistakes

### Never put raw `str(exc)` in a client-visible response

An unexpected exception's `str()` can contain local absolute paths, DSNs, tokens embedded in
URLs, and third-party internals. This is the single most important rule here.

**Known defect — document, do not imitate, do not extend.** Three call sites currently do
exactly this:

```python
# nanobot/webui/settings_system.py - _cli_apps_action() and _mcp_presets()
status = getattr(exc, "status", 500)
message = getattr(exc, "message", str(exc))   # ← raw str(exc) for any non-edge exception
```

```python
# nanobot/webui/settings_capabilities.py - _start_api()
getattr(exc, "message", str(exc)),
```

The `getattr(exc, "message", ...)` fallback exists to accept any of the edge exception types
without importing them all, but the `str(exc)` default leaks unredacted text whenever the
raised exception is *not* one of those types. New code must catch the concrete edge exception
type and use `safe_extension_message(...)` (as the `except ChannelConnectError` arm in `settings_system.py` correctly does), or
return a constant. Do not copy the `getattr(exc, "message", str(exc))` idiom into new handlers.

### Other anti-patterns

- **`except Exception: pass`.** Eleven of these remain in `nanobot/`; they are not a pattern to
  follow. If a failure is genuinely ignorable, either narrow the exception type or record a
  bounded diagnostic/`logger.warning` so it stays visible.
- **Catching broad `Exception` before the specific type.** Ordering matters; the domain type
  must come first or its status is lost.
- **Returning `None`/`False`/`""` for a real failure.** Callers cannot distinguish "not found"
  from "broken". Raise the typed exception. (One deliberate exception exists and is documented:
  transcription adapters return `""` on provider errors so voice messages fail quietly instead
  of crashing the agent loop — see `docs/development.md`.)
- **Re-raising with `from exc` at a client-visible boundary.** Use `from None` so the chained
  traceback cannot be rendered outward.
- **Inventing a new exception hierarchy for a subsystem that already has one.** Extend
  `CollaborationStoreError`, `TriggerStoreError`, etc. rather than adding a parallel family.
- **Using `assert` for user-input validation.** Only 45 asserts exist in non-test `nanobot/`
  code, almost all `assert row is not None` after a query that structurally must return a row
  (`nanobot/collaboration/postgres/`). They narrow types for BasedPyright on paths that cannot
  fail. Raise for anything reachable from a request — `assert` is stripped under `-O`.
