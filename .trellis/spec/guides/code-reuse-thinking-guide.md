# Code Reuse Thinking Guide

> **Purpose**: Stop and think before creating new code - does it already exist?

> **Precedence**: `.agent/design.md` holds this repo's binding architectural
> constraints. Where it and this guide disagree, `.agent/design.md` wins. Two
> places to know about: channel/provider duplication (see
> [Repo Exemption](#repo-exemption-channel-and-provider-implementations)) and
> opportunistic refactoring (see [After Batch Modifications](#after-batch-modifications)).

---

## The Problem

**Duplicated code is the #1 source of inconsistency bugs.**

When you copy-paste or rewrite existing logic:
- Bug fixes don't propagate
- Behavior diverges over time
- Codebase becomes harder to understand

This holds for ordinary application code. It does **not** hold uniformly across
`nanobot/channels/` and `nanobot/providers/`, where the repo deliberately trades
duplication for independence.

---

## Which Rule Applies

| Where you are | Rule |
|---|---|
| `nanobot/channels/<platform>/`, `nanobot/providers/*_provider.py` | [Repo Exemption](#repo-exemption-channel-and-provider-implementations) — duplication is the default; extracting needs justification |
| Anywhere else under `nanobot/` | This guide's default — search first, extract on the 3rd copy |
| Any location, same untyped payload field read in 2+ places | Always extract a decoder (Pattern 4) — both documents agree |

---

## Before Writing New Code

### Step 1: Search First

```bash
# Search for similar function names
grep -r "functionName" .

# Search for similar logic
grep -r "keyword" .
```

### Step 2: Ask These Questions

| Question | If Yes... |
|----------|-----------|
| Does a similar function exist? | Use or extend it |
| Is this pattern used elsewhere? | Follow the existing pattern |
| Could this be a shared utility? | Create it in the right place |
| Am I copying code from another file? | **STOP** - extract to shared, **unless** both files are sibling channel or provider implementations (see [Repo Exemption](#repo-exemption-channel-and-provider-implementations)) |

---

## Common Duplication Patterns

### Pattern 1: Copy-Paste Functions

**Bad**: Copying a validation function to another file

**Good**: Extract to shared utilities, import where needed

### Pattern 2: Similar Components

**Bad**: Creating a new component that's 80% similar to existing

**Good**: Extend existing component with props/variants

### Pattern 3: Repeated Constants

**Bad**: Defining the same constant in multiple files

**Good**: Single source of truth, import everywhere

### Pattern 4: Repeated Payload Field Extraction

**Bad**: Multiple consumers cast the same JSON/event fields locally:

```typescript
const description = (ev as { description?: string }).description;
const context = (ev as { context?: ContextEntry[] }).context;
```

This is duplicated contract logic even when the code is only two lines. Each
consumer now has its own definition of what a valid payload means.

**Good**: Put the decoder, type guard, or projection next to the data owner:

```typescript
if (isThreadEvent(ev)) {
  renderThreadEvent(ev);
}
```

**Rule**: If the same untyped payload field is read in 2+ places, create a
shared type guard / normalizer / projection before adding a third reader.

---

## When to Abstract

**Abstract when**:
- Same code appears 3+ times
- Logic is complex enough to have bugs
- Multiple people might need this

**Don't abstract when**:
- Only used once
- Trivial one-liner
- Abstraction would be more complex than duplication
- The copies are sibling channel or provider implementations (below)

---

## Repo Exemption: Channel and Provider Implementations

`nanobot/channels/` holds one self-contained package per platform (17 today);
`nanobot/providers/` holds one module per vendor. Inside those two directories
`.agent/design.md` overrides the rules above:

> Channels and providers are allowed to repeat similar logic (send retries,
> media handling, message splitting). Do not introduce complex base classes or
> shared helpers just to eliminate duplication across channel files. Each
> channel file should remain self-contained and readable on its own. The same
> applies to provider implementations.

**Why**: every platform SDK has its own error taxonomy, rate-limit signal, and
size limits. A helper "shared" across all of them needs a parameter or hook per
platform, and then no channel reads correctly on its own while all of them break
together. Duplication is the cheaper failure mode here — one channel breaks
alone.

### Where the line falls

| Layer | Owns | Examples |
|---|---|---|
| `nanobot/utils/helpers.py` | Platform-agnostic primitives | `split_message`, `safe_filename`, `sanitize_surrogates` |
| `nanobot/channels/base.py` | Abstract interface + cross-channel policy | `_handle_message` auth/pairing → bus, `is_allowed`, `supports_streaming` |
| `nanobot/channels/manager.py` | Delivery policy across all channels | `_send_with_retry` backoff loop, dispatch |
| `nanobot/providers/base.py` | Shared wire types + protocol-level normalizers | `LLMUsage`, `LLMResponse`, `_enforce_role_alternation` |
| Each channel / provider file | Everything platform-shaped | SDK call sequence, retry/backoff, media upload, chunk limits, formatting |

### Example 1: Send retries

**Bad**: `telegram/runtime.py` has `_call_with_retry` — hoist it into
`BaseChannel` so every channel gets retries.

**Good**: Leave it in Telegram. It exists for `python-telegram-bot`'s
`RetryAfter` and pool timeouts, exceptions no other channel's SDK raises.
Cross-channel retry *policy* already has exactly one owner:
`ChannelManager._send_with_retry` runs the backoff loop and asks
`BaseChannel.should_retry_send_error` whether a given error is worth retrying.
A channel that needs different retry behavior overrides that hook; it does not
grow a shared implementation.

### Example 2: Message splitting

**Bad**: Feishu's `_fallback_text_chunks(limit=3500)` overlaps
`split_message(content, 2000)` that Discord uses — unify them.

**Good**: Discord imports the shared `split_message` because it wants the generic
primitive. Feishu keeps its own because card payloads chunk differently. The
platform-agnostic primitive is shared; the platform-shaped variant stays local.

### Decision rule for these two directories

Extract only when the logic is platform-agnostic **and** already has an obvious
owner (`utils/helpers.py`, the base class, or `ChannelManager`). If removing the
duplication requires a new flag, parameter, or subclass hook per platform, that
is the signal to leave it duplicated.

### What the exemption does not cover

It covers **cross-file** duplication between sibling channels or providers. It
does not license:

- Repeated constants inside one package (Pattern 3)
- Repeated untyped payload extraction (Pattern 4) — `.agent/design.md` "Type
  dynamic boundaries at the edge" independently requires a parser or normalizer
  at the owning edge
- A second discovery path, inventory, or lifecycle store for state that an
  existing owner already holds

Outside `nanobot/channels/` and `nanobot/providers/`, this guide's default rules
apply unchanged.

---

## After Batch Modifications

When you've made similar changes to multiple files:

1. **Review**: Did you catch all instances?
2. **Search**: Run grep to find any missed
3. **Consider**: Should this be abstracted?

**Scope limit**: step 3 is a note for later, not work for this diff.
`.agent/design.md` ("Minimal change that solves the real problem") forbids
bundling an unrelated refactor into a feature or bugfix. If the answer to step 3
is yes, record it and raise it as its own scoped change.

### Reducers Should Use Exhaustive Structure

When state is derived from action-like values (`action`, `kind`, `status`,
`phase`), prefer a reducer with one `switch` over scattered `if/else` updates.

```typescript
// BAD - action-specific state transitions are hard to audit
if (action === "opened") { ... }
else if (action === "comment") { ... }
else if (action === "status") { ... }

// GOOD - one reducer owns the transition table
switch (event.action) {
  case "opened":
    ...
    return;
  case "comment":
    ...
    return;
}
```

This matters when the event log is the source of truth. A reducer is the
documented replay model; display code and commands should not duplicate pieces
of that replay model.

---

## Checklist Before Commit

- [ ] Searched for existing similar code
- [ ] No copy-pasted logic that should be shared
- [ ] No new base class or shared helper introduced only to de-duplicate sibling channel / provider files
- [ ] No repeated untyped payload field extraction outside a shared decoder
- [ ] Constants defined in one place
- [ ] Similar patterns follow same structure
- [ ] Reducer/action transitions live in one reducer or command dispatcher

---

## Gotcha: Python if/elif/else Exhaustive Check

**Problem**: Python's if/elif/else chains have no compile-time exhaustive check. When you add a new value to a `Literal` type (e.g., `Platform`), existing if/elif/else chains silently fall through to `else` with wrong defaults.

**Symptom**: New platform works partially — some methods return Claude defaults instead of platform-specific values. No error is raised.

**Example** (`cli_adapter.py`):
```python
# BAD: "gemini" falls through to else, returns "claude"
@property
def cli_name(self) -> str:
    if self.platform == "opencode":
        return "opencode"
    else:
        return "claude"  # gemini silently gets "claude"!

# GOOD: explicit branch for every platform
@property
def cli_name(self) -> str:
    if self.platform == "opencode":
        return "opencode"
    elif self.platform == "gemini":
        return "gemini"
    else:
        return "claude"
```

**Prevention**: When adding a new value to a Python `Literal` type, search for ALL if/elif/else chains that switch on that type and add explicit branches. Don't rely on `else` being correct for new values.

---

## Gotcha: Asymmetric Mechanisms Producing Same Output

**Problem**: When two different mechanisms must produce the same file set (e.g., recursive directory copy for init vs. manual `files.set()` for update), structural changes (renaming, moving, adding subdirectories) only propagate through the automatic mechanism. The manual one silently drifts.

**Symptom**: Init works perfectly, but update creates files at wrong paths or misses files entirely.

**Prevention**:
- **Best**: Eliminate the asymmetry — have the manual path call the automatic one (e.g., `collectTemplateFiles()` calls `getAllScripts()` instead of maintaining its own list)
- **If asymmetry is unavoidable**: Add a regression test that compares outputs from both mechanisms
- When migrating directory structures, search for ALL code paths that reference the old structure

**Real example**: `trellis update` had a manual `files.set()` list for 11 scripts that `getAllScripts()` already tracked. Fix: replaced the manual list with a `for..of getAllScripts()` loop. See `update.ts` refactor in v0.4.0-beta.3.

---

## Template File Registration (Trellis-specific)

When adding new files to `src/templates/trellis/scripts/`:

**Single registration point**: `src/templates/trellis/index.ts`

1. Add `export const xxxScript = readTemplate("scripts/path/file.py");`
2. Add to `getAllScripts()` Map

That's it. `commands/update.ts` uses `getAllScripts()` directly — no manual sync needed.

**Why this matters**: Without registration in `getAllScripts()`, `trellis update` won't sync the file to user projects. Bug fixes and features won't propagate.

**History**: Before v0.4.0-beta.3, `update.ts` had its own hand-maintained file list that frequently fell out of sync with `getAllScripts()`. This caused 11 Python files to be silently skipped during `trellis update`. The fix was to eliminate the duplicate list and use `getAllScripts()` as the single source of truth.

### Quick Checklist for New Scripts

```bash
# After adding a new .py file, verify it's in getAllScripts():
grep -l "newFileName" src/templates/trellis/index.ts  # Should match
```

### Template Sync Convention

`.trellis/scripts/` (dogfooded) and `packages/cli/src/templates/trellis/scripts/` (template) must stay identical. After editing `.trellis/scripts/`, always sync:

```bash
rsync -av --delete --exclude='__pycache__' .trellis/scripts/ packages/cli/src/templates/trellis/scripts/
```

**Gotcha**: Running rsync with wrong source/destination paths can create nested garbage directories (e.g., `.trellis/scripts/packages/cli/...`). Always double-check paths before running.
