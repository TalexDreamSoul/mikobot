# Database Guidelines

> Database patterns and conventions for this project.

---

## There is no ORM

nanobot uses **no SQLAlchemy, no Django ORM, no Alembic**, and no database server. There is no
model base class that maps to tables, no session/unit-of-work, and no query builder. Do not
introduce one.

Persistence is three separate, deliberately different layers, each with its own durability and
migration story:

| Layer | Where | Format | Migration |
|---|---|---|---|
| Configuration | `nanobot/config/` → `~/.nanobot/config.json` | Pydantic models, camelCase JSON | `_migrate_config` (key moves) |
| Collaboration control plane | `nanobot/collaboration/` | local JSON store | `_migrate_vN` chain |
| Agent state | `nanobot/agent/memory.py`, `nanobot/session/`, `nanobot/cron/`, `nanobot/triggers/` | JSONL / JSON / markdown | ad hoc, tolerant decoding |

---

## Layer 1 — Configuration

Config is a Pydantic tree rooted at `Config` in `nanobot/config/schema.py`. Every model
subclasses `Base` from `nanobot/config_base.py`:

```python
class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
```

Rules:

- Declare every setting explicitly in `schema.py`. `.agent/design.md`, "Explicit over magical":
  configuration must be declared, not discovered.
- Python fields are `snake_case`; the persisted JSON is camelCase. `save_config` dumps with
  `model_dump(mode="json", by_alias=True)`.
- Constrain values in the schema (`Field(default=3, ge=0, le=10)`, `pattern=r"^[a-z]{2,3}$"`),
  not in the consumer.
- Secrets that live in dedicated token stores are **excluded** from the saved config.
  `save_config` re-projects `openaiCodex` and `xaiGrok` down to `{"proxy", "extra_body"}` only.
- `${VAR}` references are resolved at load time and **raise** when the variable is missing —
  this is not shell default-value syntax (see [`.agent/gotchas.md`](../../../.agent/gotchas.md)).

Config format changes go in `_migrate_config` (`nanobot/config/loader.py`), which moves keys
into their new home in place and is idempotent. It is a plain dict transform with no version
counter — it must remain safe to run on already-migrated data. When a whole section is removed
from the schema, `_migrate_config` drops the stale key so an existing `config.json` keeps
loading; add a regression test that loads and re-saves a file carrying the old section.

---

## Layer 2 — Collaboration control plane

`nanobot/collaboration/` persists users, projects, memberships, channel assignments, pairing
challenges, and conversation bindings in one JSON document under
`get_runtime_subdir("collaboration") / "collaboration.json"`. `build_collaboration_repository()`
in `nanobot/collaboration/repository.py` wraps the synchronous `CollaborationStore` in
`AsyncLocalCollaborationRepository`, which runs each complete store operation in a worker
thread so the event loop never blocks on file I/O.

The contract is a `@runtime_checkable Protocol`, `CollaborationRepository`, not a base class.
Callers (`AgentLoop`, `ChannelManager`, the WebUI handler, the CLI) depend on the Protocol only.
Adding a Protocol method means implementing it in `store.py` and exposing it through
`local_repository.py`; the store method owns validation and authorization, the async wrapper
owns nothing but the thread hop.

### Domain models

Entities in `nanobot/collaboration/models.py` are `@dataclass(frozen=True, slots=True)` with
`StrEnum` for closed value sets (`MembershipRole`, `TaskStatus`, `PairingPurpose`, ...).
Timestamps are integer epoch milliseconds named `created_at_ms` / `updated_at_ms`. IDs are
application-generated strings.

The store owns the record codec against those models: `_user(data)` decodes a camelCase record
and `_encode_user(x)` encodes one. Decoders are **strict**: `_user` rejects a record whose key
set is not within the supported field set, raising
`CollaborationStoreFormatError("record has unsupported shape")`. Do not make a decoder lenient
to accept unknown keys — that is how corrupt state spreads.

---

## Schema Migrations

### Local JSON store — chained `_migrate_vN`

`nanobot/collaboration/store.py` holds a module-level `_SCHEMA` version constant and one
function per upgrade step. `_normalize` walks the chain from whatever version is on disk to
current, re-checking the version after every step:

```python
_SCHEMA = 7

def _normalize(data: object) -> _StoreState:
    root = _mapping(data, "unsupported collaboration store schema")
    version = root.get("schemaVersion")
    if version == 1:
        root = _migrate_v1(root)
    if root.get("schemaVersion") == 2:
        root = _migrate_v2(root)
    ...
    if root.get("schemaVersion") == 6:
        root = _migrate_v6(root)
    if set(root) != set(_empty()) or root.get("schemaVersion") != _SCHEMA:
        raise CollaborationStoreFormatError("unsupported collaboration store schema")
```

To add a schema version:

1. Bump `_SCHEMA`.
2. Add the new top-level collections to `_empty()` — `_normalize` asserts the key set matches
   `_empty()` exactly, so a missing key is a hard failure.
3. Write `_migrate_v<N-1>` that takes the previous shape and returns the new one, setting
   `migrated["schemaVersion"]` to the next number (the final step sets it to `_SCHEMA`).
4. Append the `if root.get("schemaVersion") == N-1:` arm to `_normalize`.
5. Add a migration test asserting the upgrade preserves existing user data.

Migrations must **preserve, not reset**. `_migrate_v1` docstring: "Upgrade the original
project-only store without dropping user data." `_migrate_v6`: "Mark existing challenges
unbound rather than rebinding credentials." When a new field cannot be derived, write an
explicit legacy sentinel (`_LEGACY_UNBOUND_CHANNEL_REVISION`) rather than guessing. A migration
that drops whole collections must write a backup of the previous document next to the store
before rewriting it and log how many records it discarded.

`_load` re-saves the file whenever the on-disk version differs from `_SCHEMA`, so migration is
lazy and happens on first read.

---

## Concurrency and Durability

### File locks

Every multi-process JSON store guards read-modify-write with `filelock.FileLock` on a sibling
`.lock` file. The convention is `path.with_suffix(f"{path.suffix}.lock")`:

```python
# nanobot/collaboration/store.py
self._lock = FileLock(str(self.path.with_suffix(f"{self.path.suffix}.lock")))
```

```python
# nanobot/webui/settings_services.py — WebUISettingsConfig
def update(self, mutation: Callable[[Config], _T]) -> _T:
    """Apply and atomically persist one path-scoped read-modify-write operation."""
    with self._lock, self._file_lock:
        config = load_config(self.path)
        result = mutation(config)
        save_config(config, self.path)
        return result
```

`nanobot/extensions/management.py` uses the identical `<suffix>.lock` convention for CLI-side
config mutation, as do the channel QR-credential writers.

**Reload inside the critical section.** Never load config, compute, then save — a concurrent
writer's change is lost. Acquire the lock, reload, mutate, save.

`CollaborationStore` deliberately keeps **no cache**: every method reads under the file lock, so
independently started CLI, gateway, and WebUI processes observe one coherent state.

### Atomic writes

Persisted files are written temp-file → `flush` → `fsync(file)` → `os.replace` →
`fsync(directory)`. This is required, not optional: `nanobot/agent/memory.py` depends on it for
crash durability and `.agent/gotchas.md` forbids replacing it with a plain `open(..., "w")`.

Prefer the shared `_write_text_atomic` in `nanobot/utils/helpers.py` for new code.
`nanobot/collaboration/store.py` and `nanobot/agent/memory.py` inline the sequence; three other
modules define a local `_atomic_write`. Do not consolidate them as a side effect of an
unrelated change (see [`quality-guidelines.md`](./quality-guidelines.md)).

### Bounds

Stores enforce a maximum document size before parsing and before writing (`_MAX_FILE_BYTES` in
`store.py`), raising `CollaborationStoreFormatError` rather than loading unbounded input. Any
new store needs the same bound.

---

## Collaboration control-plane contracts

### 1. Scope / Trigger

This contract applies to cross-layer changes involving projects, project membership, channel
instances, channel assignments, Pairing Challenges, OIDC settings, or collaboration state. The
boundary is security-sensitive: a transport credential is kept in server-owned configuration,
while collaboration persistence stores ownership and assignment references.

Authorization has two levels. A **system administrator** is the local owner or an OIDC subject
listed in `admin_subjects`; the gateway records that on `User.is_admin` from the authenticated
principal, never from a payload. A **project owner** manages one project's members and
assignments. Everyone else is a member of the projects they were added to.

### 2. Signatures

- `CollaborationRepository.create_pairing_challenge(actor_user_id, *, project_id,
  channel_type, instance_id, assignee_user_id=None, ttl_seconds=600)`
- `CollaborationRepository.verify_pairing_challenge(code, *, channel_type, instance_id,
  sender_id)`
- `CollaborationRepository.consume_pairing_challenge(actor_user_id, challenge_id)`
- `CollaborationRepository.resolve_channel_assignment(channel_type, instance_id)` is the
  runtime lookup channels use; it takes no actor because the instance is the subject.
- `WebUISettingsConfig.update(mutation)` performs a path-scoped read-modify-write under the
  config file lock.
- QR credential writers (`save_registration_result` in `nanobot/channels/feishu/runtime.py` and
  `WeixinChannel._persist_connect_credentials` in `nanobot/channels/weixin/runtime.py`) use the
  same `<config>.lock` convention before loading and saving configuration.

### 3. Contracts (request / response / database)

- A Pair Code hands one channel instance to one project on behalf of one member (the
  assignee). A project owner or administrator may name any assignee; a member may only pair an
  instance they connected themselves, into a project they belong to, for themselves.
- A challenge is one-time, stores only `code_digest`, expires in 60–900 seconds, and binds one
  project, assignee, channel type, and instance ID.
- Verification requires the exact channel type, instance ID, and external sender. It binds the
  sender identity to the assignee only after the digest and lifetime checks pass.
- Consumption is actor-bound and atomically writes the `ChannelAssignment`, which is globally
  unique by `(channel_type, instance_id)`. The assignee becomes a project member if they were
  not one already.
- `resolve_scope` routes an assigned instance's direct messages to the assigned project and
  admits only that project's members; an unassigned instance on a channel that requires
  assignment (`CHANNEL_ASSIGNMENT_REQUIRED_METADATA_KEY`) is denied, never routed to the
  sender's personal default project.
- Project capability allowlists (`allowed_skills`, `allowed_mcp_servers`) are the only
  per-project runtime restrictions. `None` means unrestricted.
- Successful QR login persists `enabled=false` and `pairingRequired=true`; activation clears the
  marker only after the collaboration Pairing Challenge is consumed.
- Public channel projections may include `pairing_only=true`, configured-field names, and
  non-secret values. They must not include secrets, tokens, environment values, or absolute
  host paths.
- The v9 migration collapses the retired organization/bot/vault document: users, identities,
  projects, memberships, provenance, and bindings survive; bot channel claims become
  assignments; everything else is dropped and counted in the log, with a `.v<N>.bak.json`
  copy written first.

### 4. Validation and error matrix

| Condition | Required result |
| --- | --- |
| Wrong instance, sender, expired, or reused code | Not found/conflict; no assignment |
| Code verified by a second sender | Conflict; first verification remains authoritative |
| Member pairs an instance they did not connect, or for someone else | Permission error |
| Unassigned channel control by an ordinary user | HTTP 403 |
| Channel control by the instance's assignee or an administrator | Allowed |
| OIDC update with a stale snapshot | HTTP 409; current secret/config remains unchanged |
| Invalid OIDC discovery or redirect | HTTP 400 with field errors; no write |
| Project deletion | Remove memberships, assignments, challenges, and bindings for it |
| Message on an assigned instance from a non-member | Isolated scope with `route_denied` |

### 5. Good / Base / Bad cases

- **Good**: QR login writes disabled credentials, the exact instance receives a fresh Pair Code,
  and activation occurs only after verified consumption.
- **Base**: Existing flat single-instance configuration is normalized to `default` without
  changing credentials or state location.
- **Bad**: Trusting a browser-supplied actor ID or admin flag, enabling an unassigned
  instance, or writing a stale full config snapshot over a newer OIDC secret.

### 6. Tests required

- Local migration tests assert the v9 collapse keeps users, projects, memberships, and turns
  claims into assignments.
- Pairing tests assert exact target matching, expiry, reuse, competing senders, assignment
  uniqueness, member self-service limits, and route denial.
- Channel tests assert preflight Pair Code handling before reactions/media/typing and isolated
  Weixin state directories.
- WebUI tests assert an assignee can control their instance while others receive 403, and
  that OIDC reads redact secrets and stale writes return 409.
- Run `uv run --no-sync pytest -q`, `uv run ruff check nanobot tests`, and
  `uv run --no-sync basedpyright`.

### 7. Wrong vs correct

**Wrong** — load `config.json`, mutate one channel, and save without the shared file lock; a
concurrent OIDC update can be lost.

**Correct** — acquire the path's `<suffix>.lock`, reload the latest config inside that critical
section, apply only the channel-instance mutation, and save the same path atomically.

---

## Testing persistence changes

- Store tests run everywhere and are the baseline: `tests/collaboration/test_collaboration.py`.
- Any behavior change to the repository Protocol needs a store-level test, not only a WebUI or
  loop test that happens to pass through it.
- Schema migration changes need a test that loads the *previous* shape and asserts existing data
  survives.

---

## Common mistakes

- Do not expose `client_secret`, channel tokens, or absolute skill/workspace paths in a projection.
- Do not use a legacy `allowFrom` entry as proof that a protected channel instance was claimed.
- Do not add a field to a domain dataclass without updating the codec (`store.py`
  `_<entity>` / `_encode_<entity>`) and `_empty()`. The strict shape check will reject records the
  moment they disagree.
- Do not load-then-save configuration outside the file lock.
- Do not make a decoder lenient to "just accept" an unknown key; raise
  `CollaborationStoreFormatError`.
