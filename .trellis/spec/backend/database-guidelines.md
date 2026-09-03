# Database Guidelines

> Database patterns and conventions for this project.

---

## There is no ORM

nanobot uses **no SQLAlchemy, no Django ORM, no Alembic**. There is no model base class that
maps to tables, no session/unit-of-work, and no query builder. Do not introduce one.

Persistence is three separate, deliberately different layers, each with its own durability and
migration story:

| Layer | Where | Format | Migration |
|---|---|---|---|
| Configuration | `nanobot/config/` → `~/.nanobot/config.json` | Pydantic models, camelCase JSON | `_migrate_config` (key moves) |
| Collaboration control plane | `nanobot/collaboration/` | local JSON store **or** PostgreSQL | `_migrate_vN` chain / `MIGRATIONS` tuple |
| Agent state | `nanobot/agent/memory.py`, `nanobot/session/`, `nanobot/cron/`, `nanobot/triggers/` | JSONL / JSON / markdown | ad hoc, tolerant decoding |

PostgreSQL access is raw `psycopg` 3 async with parameterized SQL. `psycopg[binary,pool]` is a
required dependency, but the PostgreSQL backend is opt-in via configuration.

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
- `NANOBOT_COLLABORATION_POSTGRES_DSN` and `NANOBOT_COLLABORATION_POSTGRES_MIGRATION_DSN` are
  read from the environment into the model but never serialized back to JSON.
- `${VAR}` references are resolved at load time and **raise** when the variable is missing —
  this is not shell default-value syntax (see [`.agent/gotchas.md`](../../../.agent/gotchas.md)).

Config format changes go in `_migrate_config` (`nanobot/config/loader.py`), which moves keys
into their new home in place and is idempotent. It is a plain dict transform with no version
counter — it must remain safe to run on already-migrated data.

---

## Layer 2 — Collaboration control plane (dual backend)

`nanobot/collaboration/` is the only subsystem with two interchangeable persistence backends,
selected by `build_collaboration_repository(config)` in `nanobot/collaboration/repository.py`:

```python
if config.backend == "local":
    return AsyncLocalCollaborationRepository(local_store)
from .postgres.repository import PostgresCollaborationRepository
from .postgres.session import PostgresSession
return PostgresCollaborationRepository(PostgresSession(config))
```

The contract is a `@runtime_checkable Protocol`, `CollaborationRepository`, not a base class.
`PostgresCollaborationRepository` proves conformance statically via a no-op assertion function
so BasedPyright fails the build if the Protocol drifts:

```python
def _assert_collaboration_repository_implementation(
    implementation: type[CollaborationRepository],
) -> None:
    """Make protocol conformance a static type-checking requirement."""

_assert_collaboration_repository_implementation(PostgresCollaborationRepository)
```

### The equivalence invariant

**Local and PostgreSQL must be semantically equivalent.** Any behavior change to one is
incomplete until the other matches:

- The same operation raises the same exception type in both backends
  (`CollaborationNotFoundError`, `CollaborationPermissionError`, `CollaborationConflictError`,
  `CollaborationStoreFormatError`).
- The same uniqueness constraints hold — for example a channel claim is globally unique by
  `(channel_type, instance_id)` in both.
- The same authorization outcome. In PostgreSQL that is enforced twice: once by application
  logic and again by row-level security. In local mode only the application layer exists, so
  the application check is not optional.
- Adding a Protocol method means implementing it in **both** `local_repository.py` /
  `store.py` and the PostgreSQL mixins.

### Domain models

Entities in `nanobot/collaboration/models.py` are `@dataclass(frozen=True, slots=True)` with
`StrEnum` for closed value sets (`OrganizationRole`, `MembershipRole`, `TaskStatus`,
`VaultKind`, `PairingPurpose`, ...). Timestamps are integer epoch milliseconds named
`created_at_ms` / `updated_at_ms`. IDs are application-generated strings, never database
sequences.

Each backend owns its own row/record codec against those models:

| Direction | Local JSON | PostgreSQL |
|---|---|---|
| decode | `_user(data)` in `store.py` (camelCase keys) | `decode_user_row(row)` in `postgres/rows.py` (snake_case columns) |
| encode | `_encode_user(x)` in `store.py` | inline parameterized `INSERT`/`UPDATE` |

Decoders are **strict**: `_user` rejects a record whose key set is not within the supported
field set, raising `CollaborationStoreFormatError("record has unsupported shape")`. Do not make
a decoder lenient to accept unknown keys — that is how corrupt state spreads.

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
explicit legacy sentinel (`_LEGACY_UNBOUND_CHANNEL_REVISION`) rather than guessing.

`_load` re-saves the file whenever the on-disk version differs from `_SCHEMA`, so migration is
lazy and happens on first read.

### PostgreSQL — append-only `MIGRATIONS` tuple

`nanobot/collaboration/postgres/ddl.py` holds the DDL as string constants and one ordered
tuple:

```python
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, INITIAL_SCHEMA_DDL),
    (2, MIGRATION_2_DDL),
    ...
    (6, MIGRATION_6_DDL),
)
```

`migrate(connection, runtime_role)` in `postgres/migrations.py` applies every unapplied version
inside **one transaction** guarded by `pg_advisory_xact_lock`, recording each in
`collaboration_schema_migrations`.

Rules:

- **Append only.** Never edit an already-released `MIGRATION_N_DDL` or renumber. Add a new
  entry.
- **Additive and idempotent DDL.** Use `ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT
  EXISTS`, `DROP POLICY IF EXISTS` before `CREATE POLICY`. A new `NOT NULL` column needs a
  `DEFAULT`, exactly as `MIGRATION_6_DDL` does:

  ```sql
  ALTER TABLE nanobot_collaboration.collaboration_pairing_challenges
      ADD COLUMN IF NOT EXISTS channel_revision varchar(256) NOT NULL DEFAULT 'legacy-unbound';
  ```
- **A new table needs the full treatment in the same migration**: `ENABLE ROW LEVEL SECURITY`,
  `FORCE ROW LEVEL SECURITY`, one policy per `SELECT`/`INSERT`/`UPDATE`/`DELETE`, and an entry
  in `_RUNTIME_BUSINESS_GRANTS`. A table without policies under `FORCE ROW LEVEL SECURITY` is
  invisible to the runtime role; a table without `FORCE` is a tenancy hole.
- **Two roles, never one.** `migrate` refuses to run when the migration role equals the runtime
  role, and refuses a runtime role that is `rolsuper`, `rolbypassrls`, or a member of
  `nanobot_collaboration_policy_owner`. Keep those guards.
- Migration and runtime use separate DSNs (`postgres_migration_dsn` / `postgres_dsn`).

---

## PostgreSQL Conventions

Read `nanobot/collaboration/postgres/ddl.py` before writing any DDL. The house style:

- **Schema qualified.** Everything lives in `nanobot_collaboration`, with
  `REVOKE CREATE ON SCHEMA nanobot_collaboration FROM PUBLIC`.
- **Table names**: `collaboration_<plural_noun>`, snake_case
  (`collaboration_organization_memberships`, `collaboration_bot_channel_assignments`).
- **Column names**: snake_case, matching the dataclass field names
  (`created_by_user_id`, `default_vault_id`, `created_at_ms`).
- **IDs**: `varchar(128) PRIMARY KEY`, application-generated. No `serial`, no `uuid` column
  type.
- **Timestamps**: `created_at_ms` / `updated_at_ms` as `bigint` epoch milliseconds, not
  `timestamptz`. The only `timestamptz` is `collaboration_schema_migrations.applied_at`.
- **Every column is bounded and checked.** Explicit `varchar(n)`; `CHECK (length(btrim(x)) > 0)`
  for required text; `CHECK (role IN ('owner','admin','member'))` mirroring the `StrEnum`;
  `CHECK (updated_at_ms >= created_at_ms)`.
- **Tenancy is enforced by referential integrity**, not only by queries: org-scoped tables
  carry `organization_id` and use a composite foreign key into
  `collaboration_organization_memberships (organization_id, user_id)`. `ON DELETE CASCADE` for
  org-owned children, `ON DELETE RESTRICT` where a dangling reference would lose ownership.
- **Helper functions** are `nanobot_<predicate>` in the same schema, declared
  `LANGUAGE sql STABLE SET search_path = pg_catalog`, and executable only by the runtime role
  via `_RUNTIME_HELPER_GRANTS`. Policies call these instead of embedding recursive subqueries.
- **All SQL is parameterized** (`%s` placeholders). Identifiers that must be interpolated go
  through `psycopg.sql.Identifier`, never f-strings. String-literal DDL is `cast(LiteralString, ...)`.

### RLS and the request context

Tenant identity is set **per transaction**, never per connection, in
`nanobot/collaboration/postgres/session.py`:

```python
async with connection.transaction():
    await self._set_local(connection, "search_path", "pg_catalog")
    await self._set_local(connection, "nanobot.user_id", user_id)
    await self._set_local(connection, "statement_timeout", f"{self._command_timeout_ms}ms")
    ...
# _set_local -> SELECT pg_catalog.set_config(%s, %s, true)   # is_local=True
```

Policies read it through `nanobot_current_user_id()`. Because settings are transaction-local,
a pooled connection can never leak one tenant's identity into another's query.

`identity_transaction(channel, sender_id)` is the deliberate bootstrap hole: it sets
`nanobot.user_id` to `""` and exposes only the `collaboration_identities` lookup policy for the
exact `(channel, sender_id)` pair. Do not widen it.

**RLS is a backstop, not the authorization design.** The application-layer permission check
must produce the same answer; RLS exists so a missed check fails closed instead of leaking. If
you change one, change both, and test both.

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

Stores enforce a maximum document size before parsing and before writing
(`_MAX_FILE_BYTES` in `store.py`, `_MAX_JSON_BYTES` in `postgres/rows.py`), raising
`CollaborationStoreFormatError` rather than loading unbounded input. Any new store needs the
same bound.

---

## Collaboration control-plane contracts

### 1. Scope / Trigger

This contract applies to cross-layer changes involving organizations, projects, deployable bots,
channel instances, Pairing Challenges, OIDC settings, or local/PostgreSQL collaboration state.
The boundary is security-sensitive: a transport credential is kept in server-owned configuration,
while collaboration persistence stores ownership and assignment references.

### 2. Signatures

- `CollaborationRepository.create_pairing_challenge(actor_user_id, *, purpose,
  organization_id, bot_id, channel_type, instance_id, project_id=None, ttl_seconds=600)`
- `CollaborationRepository.verify_pairing_challenge(code, *, channel_type, instance_id,
  sender_id)`
- `CollaborationRepository.consume_pairing_challenge(actor_user_id, challenge_id)`
- `WebUISettingsConfig.update(mutation)` performs a path-scoped read-modify-write under the
  config file lock.
- QR credential writers (`save_registration_result` in `nanobot/channels/feishu/runtime.py` and
  `WeixinChannel._persist_connect_credentials` in `nanobot/channels/weixin/runtime.py`) use the
  same `<config>.lock` convention before loading and saving configuration.

### 3. Contracts (request / response / database)

- Pairing purposes are exactly `claim_channel` and `assign_bot_project`.
- A challenge is one-time, stores only `code_digest`, expires in 60–900 seconds, and binds one
  organization, bot, channel type, instance ID, and optional project ID.
- Verification requires the exact channel type, instance ID, and external sender. It binds the
  sender identity only after the digest and lifetime checks pass.
- Consumption is actor-bound and atomically creates the claim or project route. A channel claim
  is globally unique by `(channel_type, instance_id)`.
- Successful QR login persists `enabled=false` and `pairingRequired=true`; activation clears the
  marker only after the collaboration Pairing Challenge is consumed.
- Public channel projections may include `pairing_only=true`, configured-field names, and
  non-secret values. They must not include secrets, tokens, environment values, or absolute
  host paths.
- PostgreSQL uses additive migrations and forced RLS. Local JSON schema migration must preserve
  legacy projects, personas, shared defaults, and profile scope.
- `NANOBOT_COLLABORATION_POSTGRES_DSN` and
  `NANOBOT_COLLABORATION_POSTGRES_MIGRATION_DSN` may provide excluded DSN fields for
  container deployments. They are loaded into the model but never serialized to WebUI-saved
  JSON configuration.

### 4. Validation and error matrix

| Condition | Required result |
| --- | --- |
| Wrong instance, purpose, sender, expired, or reused code | Not found/conflict; no assignment |
| Code verified by a second sender | Conflict; first verification remains authoritative |
| Unclaimed channel control by an ordinary user | HTTP 403 |
| Claimed channel control by its owner or organization admin | Allowed after repository visibility check |
| OIDC update with a stale snapshot | HTTP 409; current secret/config remains unchanged |
| Invalid OIDC discovery or redirect | HTTP 400 with field errors; no write |
| Project deletion | Remove project-scoped bot routes/profiles/challenges; retain global bot profile |
| Shared bot used by another member | Resolve that member's own persona/vault; never owner's persona vault |

### 5. Good / Base / Bad cases

- **Good**: QR login writes disabled credentials, the exact instance receives a fresh Pair Code,
  and activation occurs only after verified consumption.
- **Base**: Existing flat single-instance configuration is normalized to `default` without
  changing credentials or state location.
- **Bad**: Trusting a browser-supplied actor ID, enabling an unclaimed instance, copying every
  legacy profile into a bot, or writing a stale full config snapshot over a newer OIDC secret.

### 6. Tests required

- Local migration tests assert schema upgrade, shared default preservation, and profile scope.
- Pairing tests assert exact target matching, expiry, reuse, competing senders, claim uniqueness,
  route denial, and disabled-bot revocation.
- Channel tests assert preflight Pair Code handling before reactions/media/typing and isolated
  Weixin state directories.
- WebUI tests assert ordinary claimed owners can control their instance while unclaimed users
  receive 403, and that OIDC reads redact secrets and stale writes return 409.
- Run `uv run --no-sync pytest -q`, `uv run ruff check nanobot tests`, and
  `uv run --no-sync basedpyright`; PostgreSQL integration requires separate runtime and
  migration DSNs.

### 7. Wrong vs correct

**Wrong** — load `config.json`, mutate one channel, and save without the shared file lock; a
concurrent OIDC update can be lost.

**Correct** — acquire the path's `<suffix>.lock`, reload the latest config inside that critical
section, apply only the channel-instance mutation, and save the same path atomically.

---

## Testing persistence changes

- Local-backend tests run everywhere and are the baseline:
  `tests/collaboration/test_collaboration.py`.
- PostgreSQL tests **skip** unless two DSNs are exported, because they need a restricted runtime
  role and a separate migration admin role (`tests/collaboration/test_postgres_repository.py`):

  ```bash
  export NANOBOT_TEST_POSTGRES_DSN=...           # restricted runtime role
  export NANOBOT_TEST_POSTGRES_MIGRATION_DSN=... # migration/admin role
  ```

  Because they skip silently in CI, **run them locally** for any change to `postgres/`. A green
  CI run does not mean the PostgreSQL backend was exercised.
- Any behavior change to the repository Protocol needs a test in both backends.
- RLS changes need a negative test — assert the restricted role gets `InsufficientPrivilege` or
  an empty result, not just that the happy path works.
- Schema migration changes need a test that loads the *previous* shape and asserts existing data
  survives.

---

## Common mistakes

- Do not expose `client_secret`, channel tokens, or absolute skill/workspace paths in a projection.
- Do not use a legacy `allowFrom` entry as proof that a protected channel instance was claimed.
- Do not silently reset a user's valid shared organization/bot selection while repairing personal
  defaults.
- Do not add a field to a domain dataclass without updating **both** codecs (`store.py`
  `_<entity>` / `_encode_<entity>` and `postgres/rows.py` `decode_<entity>_row`) plus the DDL.
  The strict shape check will reject records the moment the two disagree.
- Do not edit a released `MIGRATION_N_DDL` in place, and do not renumber `MIGRATIONS`.
- Do not add a PostgreSQL table without RLS enabled, forced, policied, and granted.
- Do not set `nanobot.user_id` outside a transaction or with `is_local=False`; pooled
  connections would leak tenant identity.
- Do not load-then-save configuration outside the file lock.
- Do not make a decoder lenient to "just accept" an unknown key; raise
  `CollaborationStoreFormatError`.
- Do not assume CI covered PostgreSQL — those tests skip without DSNs.
