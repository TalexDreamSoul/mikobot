# Extension registry foundation — implementation plan

## Ordered work

1. **Contracts**
   - Create the `nanobot/extensions` package.
   - Add closed enums and centralized limits.
   - Add canonical package/component ID validation and constructors.
   - Add safe bounded string and JSON action-value validation.
   - Add frozen descriptors, snapshots, diagnostics, action values, and adapter protocol.

2. **Registry**
   - Add explicit adapter registration and registration-specific idempotent disposers.
   - Add atomic per-adapter snapshot validation.
   - Add deterministic package/component assembly and collision attribution.
   - Add bounded adapter diagnostics.
   - Add fresh-snapshot action lookup and pre-dispatch validation.
   - Add action result target validation and safe normalization.

3. **Tests**
   - Defend every observable invariant from the child PRD.
   - Cover valid behavior and malicious/malformed adapter behavior.
   - Confirm no production loader/config/API imports or runtime behavior changed.

4. **Quality gate**
   - Run focused extension tests.
   - Run Ruff on new package/tests.
   - Run strict BasedPyright.
   - Inspect the final diff for accidental integration work or unnecessary abstractions.

## Validation commands

```bash
uv run --no-sync pytest -q tests/extensions/test_registry.py
uv run ruff check nanobot/extensions tests/extensions
uv run --no-sync basedpyright
```

The full project suite is not required until a production adapter changes runtime behavior, but existing import smoke tests should be run if adding the package export exposes a cycle.

## Review checklist

- Every enum member is required by the parent PRD; no speculative family/action vocabulary.
- IDs are constructed centrally and cannot contain path syntax.
- Descriptors cannot retain mutable mappings/lists supplied by callers.
- Snapshot failure is atomic per adapter.
- Registration order cannot change accepted output or collision attribution.
- Disposer identity prevents a stale cleanup callback from deleting a later registration.
- Action dispatch performs no callback before authorization, target, action, value, revision, and warning checks pass.
- Trust warning requirements never claim sandboxing or permission enforcement.
- Safe messages are bounded, but programming/configuration errors still fail visibly.
- No global singleton, background task, cache, persistence, logger dependency, WebUI type, or runtime loader integration is introduced.

## Rollback

All changes are additive new subsystem/test files. If the contract proves wrong before adapters land, remove the new package and tests; no runtime or persisted state rollback is needed.
