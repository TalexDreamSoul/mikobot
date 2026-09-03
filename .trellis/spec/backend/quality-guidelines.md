# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

Quality here means: the smallest diff that solves the real problem, passing lint and strict
types, with the closest regression test. From
[`.agent/design.md`](../../../.agent/design.md) and
[`CONTRIBUTING.md`](../../../CONTRIBUTING.md):

- **Simple** — prefer the smallest change that solves the real problem.
- **Clear** — optimize for the next reader, not for cleverness.
- **Decoupled** — keep boundaries clean and avoid unnecessary new abstractions.
- **Honest** — do not hide complexity, but do not create extra complexity either.
- **Durable** — easy to maintain, test, and extend.

Baseline: Python 3.11+, `asyncio` throughout, line length 100.

---

## The Commands CI Runs

Reproduce CI exactly (`.github/workflows/ci.yml`, `pyproject.toml`):

```bash
uv sync --all-extras --dev
uv run --no-sync python -m scripts.install_channel_dependencies --all-channels

uv run --no-sync ruff check nanobot tests conftest.py     # E, F, I, N, W (E501 ignored)
uv run --no-sync basedpyright                             # strict mode, must be 0 errors
uv run --no-sync python -m pytest                         # bare pytest, see testpaths below
```

Keep `--no-sync` on the final commands: channel dependencies come from package manifests and
are installed by the previous step; a later sync would prune them.

Three details that are easy to get wrong:

1. **`ruff check` covers `tests` and `conftest.py`, not just `nanobot/`.** The command in
   `AGENTS.md` (`ruff check nanobot/`) is the quick local form; CI is broader.
2. **CI runs bare `pytest`**, so `testpaths = ["tests", "nanobot/channels"]` applies. Tests
   inside channel packages (`nanobot/channels/<name>/tests/`) are collected and must pass.
   This is the single most commonly missed check when changing a channel.
3. **BasedPyright is `typeCheckingMode = "strict"`** over `include = ["nanobot"]` with
   `exclude = ["**/tests"]`. Test files are not type-checked; production code has nowhere to
   hide.

Matrix: Python 3.11 (minimum) and 3.14 on Ubuntu, plus Windows 3.14. Lint, types, and coverage
run only on the 3.14 + coverage job. Coverage config sets `fail_under = 75`.

---

## Forbidden Patterns

### Do not run `ruff format`

`CONTRIBUTING.md` mentions it, but [`.agent/gotchas.md`](../../../.agent/gotchas.md) is
explicit: **do not run it** — the tree predates it and reformatting destroys git blame.
Only `ruff check`. Never mix mechanical formatting, line wrapping, import re-sorting, or quote
churn into a feature or bugfix diff.

### Do not `cast` to silence the type checker

From `.agent/design.md`:

> `typing.cast` performs no runtime validation. Every new cast must be supported by a runtime
> check on the same path or by an explicit invariant that is clear from construction and
> control flow (and documented locally when it is not obvious). If input can violate the
> claimed type, handle that invalid case before casting; never use `cast` only to silence
> BasedPyright.

`cast` is used ~1,300 times, so it is normal — but the paired runtime check is the rule.
Correct shape (`nanobot/extensions/adapters/channels.py`):

```python
if not isinstance(result_value, Mapping):
    return self._failure(request, package.id, "Channel action could not be completed.", ...)
result = cast(Mapping[str, object], result_value)
```

Same principle for `Any`: stable first-party dependencies must be typed where they are stored
or passed. Do not declare an internal service, context field, or callback result as `Any` and
then recover its type with consumer-side casts. Use the concrete type or a narrow `Protocol`;
reserve `Any` for genuinely dynamic boundaries (wire payloads, persisted records, third-party
SDK objects), and normalize those at the owning edge with a parser or `TypedDict`.

### Do not suppress diagnostics without a reason

Suppressions must name the specific rule. There are 89 `# pyright: ignore[<rule>]`
(zero unqualified), overwhelmingly `reportPrivateUsage` / `reportMissingTypeStubs` /
`reportUnknownVariableType` against third-party or intentionally-shared-private code, and 133
`# type: ignore[<code>]` of which 96 are the single `[method-assign]` monkeypatch pattern.
Only 2 bare `# type: ignore` exist — do not add a third. `# noqa` is rare (47 total, always
rule-qualified — there is not a single bare `# noqa` in `nanobot/`) and should stay rare; 30 of
them are the `# noqa: E402` block in `nanobot/cli/commands.py`, where console encoding must be
configured before importing UI/logging libraries.

### Do not add abstraction to remove duplication

`.agent/design.md`, "Prefer duplication over premature abstraction":

> Channels and providers are allowed to repeat similar logic (send retries, media handling,
> message splitting). Do not introduce complex base classes or shared helpers just to
> eliminate duplication across channel files. Each channel file should remain self-contained
> and readable on its own. The same applies to provider implementations.

This is a real, deliberate state of the codebase, not an oversight. Atomic file writing is a
good illustration of the current mixed reality: a shared `_write_text_atomic` exists in
`nanobot/utils/helpers.py` and is imported by `config/loader.py`, `pairing/store.py`,
`providers/xai_oauth.py`, and `agent/tools/mcp_oauth.py` — each with a
`# pyright: ignore[reportPrivateUsage]` because the helper is underscore-private. Alongside it,
three modules define their own `_atomic_write` (`nanobot/utils/run_records.py`,
`nanobot/cron/service.py`, `nanobot/triggers/local_store.py`) and two more inline the same
temp-write/fsync/rename sequence (`nanobot/collaboration/store.py`, `nanobot/agent/memory.py`).
Prefer `_write_text_atomic` in new code; do **not** consolidate the existing duplicates as part
of an unrelated change.

Add structure only when it removes real complexity, protects an important boundary, or matches
an established local pattern. If a new abstraction is introduced, it must clearly reduce
complexity rather than move it around.

### Do not grow the core

`nanobot/agent/loop.py` and `nanobot/agent/runner.py` are the critical path. New capabilities
belong in `channels/`, `agent/tools/`, skills, or MCP servers. Runtime state fan-out follows
the same boundary: `AgentLoop` may publish generic events from `nanobot/bus/runtime_events.py`,
but WebUI/WebSocket wire details belong in `nanobot/session/webui_turns.py` or the channel
adapter.

### Other forbidden patterns

- Direct `httpx.get` / `requests.get` in tools. Route through the web fetch utilities or
  replicate `validate_url_target` from `nanobot/security/network.py`
  ([`.agent/security.md`](../../../.agent/security.md)).
- New path handling that bypasses the workspace path resolver
  (`nanobot/agent/tools/path_utils.py`, `filesystem.py`).
- Replacing an atomic write (`temp + fsync + rename + directory fsync`) with a plain
  `open(..., "w")`. `nanobot/agent/memory.py` depends on this for crash durability.
- Assuming `/` path separators. Windows is explicitly supported; use `pathlib.Path`.
- Raw `str(exc)` in a client-visible response. See
  [`error-handling.md`](./error-handling.md).

---

## Required Patterns

- Configuration is declared explicitly in `nanobot/config/schema.py` Pydantic models. No
  reading `os.environ` ad hoc in feature code.
- New config models subclass `Base` from `nanobot/config_base.py` so camelCase JSON aliases
  come for free.
- Errors are raised as typed exceptions, not silently corrected.
- Failures converted to user-facing messages are logged with `logger.exception` and returned as
  a fixed constant. See [`logging-guidelines.md`](./logging-guidelines.md).
- Dynamic boundaries are normalized once at the owning edge.
- `from __future__ import annotations` at the top of new modules — the dominant convention
  (~280 of 437 modules); every recently added subsystem uses it.
- Every module has a one-line docstring stating what it owns.

---

## Testing Requirements

### Placement

Tests mirror the package: `tests/agent/`, `tests/providers/`, `tests/webui/`, and so on.
**Channel tests live inside the channel package** (`nanobot/channels/<name>/tests/`) and are
collected by CI via `testpaths`. Put a channel's test next to its channel.

### Style

- `asyncio_mode = "auto"` is set, so the marker is optional — but the dominant convention is to
  write `@pytest.mark.asyncio` explicitly on async tests (2,700+ occurrences). A handful of
  files omit it. **Match the file you are editing.**
- `pytest.mark.parametrize` is used heavily (~190 sites); prefer it over copy-pasted cases.
- Integration tests that need external services skip themselves rather than failing. Pattern
  from `tests/collaboration/test_postgres_repository.py`:

  ```python
  runtime_dsn = os.getenv("NANOBOT_TEST_POSTGRES_DSN")
  migration_dsn = os.getenv("NANOBOT_TEST_POSTGRES_MIGRATION_DSN")
  if not runtime_dsn or not migration_dsn:
      pytest.skip("requires NANOBOT_TEST_POSTGRES_DSN and NANOBOT_TEST_POSTGRES_MIGRATION_DSN")
  ```
- Shared fixtures go in the root `conftest.py`. Only two other conftest files exist; do not add
  a third without a strong reason.
- Tests are excluded from BasedPyright, so they may be looser — but they still pass `ruff`.

### How much to test

`.agent/design.md`: a bugfix "should make the protected invariant clear, change the smallest
surface that enforces it, and add only the closest regression test." One focused test that
would have caught the bug beats a broad new suite.

Choose the verification by changed surface (`docs/architecture.md`):

| Change | Minimum useful verification |
|---|---|
| Provider behavior | Provider unit tests or a mocked API path |
| Channel behavior | The channel's in-package tests plus a `nanobot gateway` startup path |
| WebUI backend behavior | `tests/webui/` plus, for routing/settings changes, a browser-level check through the gateway |
| Tool behavior | Tool unit tests, and an agent-run path when the schema or model-facing behavior changes |
| Persistence | Migration test + both backends where the operation exists in both |
| Security boundary | An explicit negative test that the boundary rejects |

Security-relevant changes need a test that the guard *denies*, not only that the happy path
works.

### Model-facing surfaces are code

Prompt templates (`nanobot/templates/*.md`), skills (`nanobot/skills/`), and tool
descriptions/schemas change agent behavior as directly as Python does. Treat edits to them like
runtime changes: keep them narrow and add a focused regression test where possible
([`.agent/gotchas.md`](../../../.agent/gotchas.md)).

---

## Code Review Checklist

Scope and shape:

- [ ] Is this the smallest change that solves the real problem?
- [ ] Are there unrelated refactors, renames, or formatting churn mixed in? If so, split them
      into a separate PR.
- [ ] Does the diff change ownership boundaries *and* behavior at once? Split it.
- [ ] Could this live in a channel, tool, skill, or MCP server instead of the agent core?
- [ ] Does any new abstraction remove complexity, or only relocate it?

Correctness:

- [ ] Every new `cast` has a runtime check or a stated invariant on the same path.
- [ ] No new `Any` on a stable first-party type.
- [ ] Dynamic input (wire payload, persisted record, SDK object) is validated at the edge, not
      threaded raw through the core.
- [ ] Errors raise typed exceptions; bad input is rejected, not silently corrected.
- [ ] No raw `str(exc)` or absolute local path in a user-facing message.
- [ ] `logger.exception` inside `except`; brace-style formatting, no f-strings; no secrets,
      tokens, raw URLs, or message content in logs.

Boundaries:

- [ ] Path handling goes through the workspace resolver with explicit read/write capability.
- [ ] Outbound HTTP passes the SSRF guards.
- [ ] Persistence writes stay atomic and, where shared across processes, hold the file lock.
- [ ] Local and PostgreSQL collaboration backends still agree. See
      [`database-guidelines.md`](./database-guidelines.md).

Verification:

- [ ] `ruff check nanobot tests conftest.py` clean.
- [ ] `basedpyright` clean (strict).
- [ ] Bare `pytest` clean — including `nanobot/channels/*/tests/`.
- [ ] The closest regression test exists and would fail without the fix.
- [ ] Windows-affecting change verified against `pathlib` / PowerShell assumptions.
- [ ] If the user-facing boundary changed, `docs/` updated.

Documentation:

- [ ] New config fields appear in `nanobot/config/schema.py` and, if user-facing, in
      `docs/configuration.md`.
- [ ] New extension points follow the existing registry/discovery pattern rather than ad hoc
      wiring.
