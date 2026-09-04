# Extension registry foundation

## Goal

Define the canonical extension package/component contracts, explicit adapter registry, deterministic snapshots, lifecycle-action validation, and invariant tests required by the unified extension platform without changing any current runtime loader, API, configuration, or UI behavior.

## User Value

- Later extension-family migrations share one reviewed vocabulary instead of inventing incompatible IDs, states, trust labels, and action semantics.
- A broken extension adapter cannot erase or corrupt the inventory produced by unrelated adapters.
- Administrative callers receive deterministic, bounded, safe results before any family-specific lifecycle work is connected.

## Confirmed Facts

- Current extension mechanisms have independent descriptor types and lifecycle owners; no shared package/component contract exists.
- Runtime owners already work and must remain unchanged in this foundation child.
- The parent task requires stable package ownership, truthful trust/execution classification, adapter-first migration, warning-only plugin risk disclosure, and no AgentLoop/AgentRunner registry logic.
- Python 3.11+ permits frozen slot dataclasses, `StrEnum`, and structural `Protocol` contracts without new dependencies.

## Requirements

### R1 — Closed immutable contracts

- Add closed enums for source, component kind, trust, execution, lifecycle, and lifecycle action.
- Add frozen, slot-based package/component/configuration/diagnostic/snapshot/action value objects with explicit bounded fields.
- Keep trust and execution separate; data, in-process, child-process, and remote components must remain distinguishable.
- Represent self-declared permissions together with an explicit unenforced fact; never infer enforcement from non-empty permissions.
- Descriptors contain no runtime objects, callables, secrets, environment mappings, command arguments, or absolute filesystem paths.

### R2 — Stable identity and ownership

- Construct package and component IDs only through validated helpers using the canonical namespaced format.
- Reject empty, oversized, non-ASCII, control-character, path-like, or delimiter-confusing ID segments.
- Every component must reference its exact package owner and have an ID derived from that owner, kind, and component name.
- Duplicate adapter names, package IDs, or component IDs fail deterministically and never overwrite an existing registration.

### R3 — Explicit adapter registry

- Define a narrow adapter protocol for a complete side-effect-free snapshot and asynchronous action execution.
- Adapter registration is explicit; there is no package scan, entry-point import, global singleton, or decorator side effect in this child.
- Registration returns an idempotent disposer that can remove only the exact registration that created it.
- Registry snapshot order is deterministic regardless of adapter registration order.

### R4 — Failure-isolated snapshots

- Validate one adapter's complete package tree before merging any of its rows.
- If an adapter raises, returns malformed ownership, or collides with an accepted ID, reject that adapter's complete snapshot and emit one bounded safe diagnostic.
- Continue collecting every unrelated adapter.
- Diagnostics preserve the adapter identity and safe reason while excluding traceback text, control characters, obvious absolute paths, and oversized messages.

### R5 — Lifecycle action validation

- Resolve action targets from a fresh validated snapshot and dispatch only to the adapter that owns the target.
- Require a system-administrator fact for every mutating action in this foundation contract.
- Reject actions not declared by the package/component.
- For external executable `enable` or `install`, require risk acknowledgement and the exact current non-empty revision.
- Reject stale revision, owner mismatch, unavailable target, malformed action values, and adapter results naming another target.
- Normalize successful and failed action messages to bounded safe text.

### R6 — Scope discipline

- Do not adapt production tools, Agent Plugins, Skills, MCP servers, channels, providers, hooks, CLI apps, or optional features yet.
- Do not add WebUI/HTTP routes, persistence, caches, audit storage, sandboxing, package installation, or runtime events.
- Do not modify `AgentLoop`, `AgentRunner`, current loaders, or existing configuration models.

### R7 — Verification

- Add focused tests for every enum/value-object invariant, ID boundary, ownership rule, duplicate, deterministic order, disposer behavior, adapter failure, redaction bound, action authorization, supported-action check, risk acknowledgement, stale revision, and result-target validation.
- Tests must prove current runtime imports and behavior are untouched by the foundation package.

## Acceptance Criteria

- [ ] AC1: Public contracts represent package ownership, components, source, trust, execution, lifecycle, supported actions, self-declared permissions, configuration destination, revision, and safe diagnostics without runtime or secret-bearing values.
- [ ] AC2: Canonical ID helpers accept valid names and reject empty, path-like, control-character, non-ASCII, oversized, or structurally ambiguous values.
- [ ] AC3: A component whose owner or derived ID disagrees with its package is rejected before snapshot publication.
- [ ] AC4: Adapter registration rejects duplicate names and returns an idempotent registration-specific disposer.
- [ ] AC5: Snapshot output is sorted deterministically and is identical for equivalent adapters registered in different orders.
- [ ] AC6: An exception, malformed tree, or ID collision rejects only the responsible adapter and emits a bounded redacted diagnostic; unrelated packages remain present.
- [ ] AC7: Mutation dispatch requires a system administrator, a current target, and a declared action, and it reaches only the owning adapter.
- [ ] AC8: External executable enable/install actions additionally require current-revision acknowledgement; missing or stale acknowledgement performs no adapter call.
- [ ] AC9: An adapter action result that references another package/component is rejected and safely reported.
- [ ] AC10: Focused tests, Ruff, and strict BasedPyright pass; no existing runtime file changes are required.

## Out of Scope

- Production family adapters and lifecycle cutovers.
- WebUI, HTTP/WS APIs, CLI commands, persistence, audit logs, caching, and telemetry.
- Plugin sandboxing, permission enforcement, publisher verification, dependency installation, or safety guarantees.
- Compatibility aliases for future canonical IDs.

## Key Decisions

- This child freezes the contract before any production family depends on it.
- The registry is an explicit edge service, not a global singleton or a replacement dependency-injection framework.
- Adapter snapshots are atomic per adapter; partial output is never published.
- Risk acknowledgement is represented for later callers but is disclosure, not a security control.
