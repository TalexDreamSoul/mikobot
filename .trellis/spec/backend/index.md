# Backend Development Guidelines

> Conventions for backend development in nanobot.

---

## Overview

nanobot is a lightweight AI agent framework: a small async agent loop that receives messages
from chat channels, calls an LLM provider, executes tools, and manages session memory. Python
3.11+, `asyncio` throughout, `loguru` for logging, Pydantic for configuration, no ORM.

These files record **what the code actually does today**, including known tech debt. They are
auto-injected into `trellis-implement` and `trellis-check` sub-agent prompts, so an
aspirational pattern written here produces code that does not match the tree.

Upstream sources of truth, in precedence order:

1. [`AGENTS.md`](../../../AGENTS.md) / [`CLAUDE.md`](../../../CLAUDE.md) — project overview and commands
2. [`.agent/design.md`](../../../.agent/design.md) — architectural constraints
3. [`.agent/security.md`](../../../.agent/security.md) — security boundaries that must not be bypassed
4. [`.agent/gotchas.md`](../../../.agent/gotchas.md) — traps that have bitten this team
5. [`CONTRIBUTING.md`](../../../CONTRIBUTING.md) — contribution flow and code style
6. `pyproject.toml`, `.github/workflows/ci.yml` — the enforced configuration

If a spec file here disagrees with `.agent/design.md`, `.agent/design.md` wins and the spec
should be fixed.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | The 26 subpackages, channel/provider/tool layout, test placement | Documented |
| [Database Guidelines](./database-guidelines.md) | No ORM; config JSON, dual-backend collaboration store, migrations, RLS, locks | Documented |
| [Error Handling](./error-handling.md) | Domain exception families, edge mapping, redaction, known leaks | Documented |
| [Quality Guidelines](./quality-guidelines.md) | Lint/type/test commands, forbidden patterns, review checklist | Documented |
| [Extension Registry](./extension-registry.md) | Canonical package/component IDs, adapters, snapshots, actions, bounds, trust disclosure | Documented |
| [Logging Guidelines](./logging-guidelines.md) | loguru, brace formatting, levels, what must never be logged | Documented |

---

## Pre-Development Checklist

Before writing code:

1. **Locate the owner.** Which subpackage owns this behavior? See
   [Directory Structure](./directory-structure.md). If the answer is `agent/loop.py` or
   `agent/runner.py`, check whether it can live in a channel, tool, skill, or MCP server
   instead.
2. **Check the boundary.** Does the change touch paths, outbound HTTP, shell execution, or
   tenant authorization? Read [`.agent/security.md`](../../../.agent/security.md) first.
3. **Check for an existing pattern.** Find two or three sibling implementations and match them.
   Do not invent a new shape for a problem the tree already solves.
4. **Check for a persistence contract.** Any change to configuration, the collaboration store,
   or session/memory files must follow [Database Guidelines](./database-guidelines.md) —
   especially the local/PostgreSQL equivalence invariant.
5. **Plan the test.** Which single regression test would have caught this? Where does it live —
   `tests/`, or the channel's own `nanobot/channels/<name>/tests/`?

---

## Quality Check

Before declaring the work done:

```bash
uv run --no-sync ruff check nanobot tests conftest.py
uv run --no-sync basedpyright
uv run --no-sync python -m pytest
```

- `ruff check` covers `tests` and `conftest.py`, not just `nanobot/`.
- `basedpyright` runs in **strict** mode and must report zero errors.
- Bare `pytest` collects **both** `tests/` and `nanobot/channels/` (`testpaths`). Channel
  package tests are part of CI.
- Never run `ruff format`.

Then walk the [Code Review Checklist](./quality-guidelines.md#code-review-checklist).

---

**Language**: All documentation should be written in **English**.
