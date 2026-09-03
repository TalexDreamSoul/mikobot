# Provider and hook extension adapters

## Goal

Project LLM, image-generation, and transcription providers as one canonical provider package per provider family, and give long-lived agent hooks a stable, truthful, read-only extension identity, without changing provider matching order, model routing, preset fallback, transcription resolution, or hook execution order, and without turning `AgentLoop` into a registry.

## User Value

- Operators see which provider families are actually installed, which capabilities each one implements, and whether it is configured, from the same tree that shows tools, Skills, MCP servers, and channels.
- Provider rows report truthful refresh semantics — next-turn LLM resolution, one delegated image reload, per-request transcription — instead of implying a hot reload that does not exist.
- Long-lived hooks stop being anonymous. An operator can see that in-process hook code is registered, who owns it, and that changing it requires a process restart.
- Provider inventory carries no API key, key hint, endpoint, proxy, header, or OAuth identity, so the extension tree is safe to show without re-deriving the Models page's redaction rules.

## Confirmed Facts

- The enum members this child must produce have zero producers today. `ExtensionSource.PROVIDER_REGISTRY` (`nanobot/extensions/contracts.py:74`) and `ExtensionComponentKind.LLM_PROVIDER`, `IMAGE_PROVIDER`, `TRANSCRIPTION_PROVIDER`, `HOOK` (`nanobot/extensions/contracts.py:86-89`) appear only in `contracts.py`; `nanobot/extensions/adapters/` contains only `agent_plugins.py`, `capabilities.py`, `channels.py`, `common.py`, `optional_features.py`, and `tools.py`.
- Three independent provider registries exist with different shapes. `PROVIDERS` is a static tuple of 46 frozen `ProviderSpec` values (`nanobot/providers/registry.py:146`). `TRANSCRIPTION_PROVIDERS` is a static tuple of 7 frozen specs with aliases and default models (`nanobot/audio/transcription_registry.py:45-84`). `_IMAGE_GEN_PROVIDERS` is a mutable module-level dict of 11 classes populated by import-time side effect (`nanobot/providers/image_generation.py:241-253`, registration calls at `:2119-2127`).
- Their name spaces overlap completely in one direction: every image provider name and every transcription provider name is also an LLM provider name. Eleven names are shared by LLM and image (`aihubmix`, `custom`, `gemini`, `minimax`, `modelscope`, `ollama`, `openai`, `openai_codex`, `openrouter`, `stepfun`, `zhipu`), seven by LLM and transcription (`assemblyai`, `groq`, `openai`, `openrouter`, `siliconflow`, `stepfun`, `xiaomi_mimo`), and three by all three. A per-registry package would therefore emit colliding `ext:provider_registry:<name>` IDs, which the foundation rejects as a whole-adapter failure.
- Every provider name across all three registries is a valid canonical ID segment; `extension_package_id(ExtensionSource.PROVIDER_REGISTRY, name)` succeeds for all of them.
- Settings already collapses alias specs into their canonical family row. Exactly one alias exists: `opencode_zen` with `settings_alias_for="opencode"` (`nanobot/providers/registry.py:249`), grouped by `_provider_settings_rows` (`nanobot/webui/settings_models.py:490-521`).
- Exactly one spec is transcription-only, `assemblyai`, and `config/schema.py` already excludes transcription-only specs from LLM paths (`nanobot/config/schema.py:611,629,674`).
- User-defined providers are not registry members. They are built at runtime by `create_dynamic_spec` (`nanobot/providers/registry.py:803-821`) and enumerated by `_dynamic_provider_items` (`nanobot/webui/settings_models.py:394`).
- Existing provider projections deliberately emit values the extension descriptor must not carry: `_image_generation_provider_rows` and `_transcription_provider_rows` emit `api_key_hint`, `api_base`, and `default_api_base` (`nanobot/webui/settings_capabilities.py:100-140`).
- Image generation is a single global selection with an explicit enabled flag: `ImageGenerationToolConfig.enabled`, `.provider`, `.model` (`nanobot/agent/tools/image_generation.py:49-58`). Transcription is a single `TranscriptionConfig.provider` (`nanobot/config/schema.py:54-57`) whose adapter class is imported per call by `TranscriptionProviderSpec.load_adapter` (`nanobot/audio/transcription_registry.py:36-42`).
- Hooks live in `AgentLoop` as two bare lists: `self._extra_hooks` and `self._hook_factories`, assigned directly from constructor arguments (`nanobot/agent/loop.py:387-388`), with no wrapper, ID, or descriptor.
- They are consumed exactly once per turn (`nanobot/agent/loop.py:1434-1437`) and ordered by `build_agent_turn_hook` (`nanobot/agent/turn_hooks.py:67-90`) as progress hook, registered factories, registered hooks, turn factories, turn hooks, with per-factory exception isolation (`nanobot/agent/turn_hooks.py:70-74`) and ephemeral suppression (`nanobot/agent/turn_hooks.py:53-54`).
- The complete set of long-lived hooks is decided entirely by four composition sites, and `AgentLoop.from_config` injects none of its own — it forwards `**extra` unchanged (`nanobot/agent/loop.py:450-500`). The sites are `nanobot/nanobot.py:143`, `nanobot/cli/gateway_runtime.py:506-507`, `nanobot/cli/commands.py:352`, and `nanobot/cli/agent.py:179`.
- Three of those four sites already construct the canonical registry a few lines later in the same function: `nanobot/nanobot.py:146`, `nanobot/cli/gateway_runtime.py:550`, `nanobot/cli/commands.py:358`, and `nanobot/cli/agent.py:185`.
- Per-turn hooks are already distinguishable and out of scope by design: `nanobot/api/server.py:426` passes `hooks=[usage_capture]` to `process_direct`, which lands in `turn_hooks`, not `registered_hooks`.
- The plan documents both sides of the hook constraint: `implement.md:16` asks for "explicit metadata wrappers for long-lived hook registrations while preserving callable constructor compatibility", while `implement.md:110` says of `nanobot/agent/loop.py` "avoid registry logic; only explicit hook metadata plumbing is acceptable if no edge-owned alternative exists". The parent PRD records that hooks have "no stable descriptor or independent lifecycle identity" (`.trellis/tasks/09-02-unified-extension-platform/prd.md:20`).

## Open Decision — where hook registration identity lives

The provider half of this child is mechanical. The hook half is not, and it needs a human decision before implementation starts. `implement.md:16` asks for metadata-bearing hook registration; `implement.md:110` forbids registry logic inside `nanobot/agent/loop.py`, which is where hooks currently live, and permits "explicit hook metadata plumbing" only "if no edge-owned alternative exists". An edge-owned alternative does exist, so the escape hatch is not automatically available, and the three viable shapes trade completeness against boundary discipline differently.

**Option A — Composition-layer hook inventory (recommended).** A `HookExtensionAdapter` is constructed at each of the four composition sites from the same hook and factory values already passed to `AgentLoop`, and registered into the registry that those sites already build. `nanobot/agent/loop.py` and `nanobot/agent/turn_hooks.py` are untouched.
*Cost:* the declared descriptors and the executed hook values are two uses of one literal, and a future edit could change one without the other; an SDK or embedder that constructs `AgentLoop`/`Nanobot` with its own `hooks=`/`hook_factories=` and does not register the adapter gets an inventory that silently omits those hooks.
*Mitigation:* one shared helper returns both the hook values and their descriptors so each site writes the literal once, plus a test asserting all four production sites obtain hooks through that helper and that the descriptor set equals the value set.

**Option B — Typed metadata wrapper accepted by `AgentLoop`.** Add a `RegisteredHook`/`RegisteredHookFactory` wrapper beside `AgentHook` in `nanobot/agent/hook.py`, let the `AgentLoop` constructor accept wrapper-or-callable, unwrap into `_extra_hooks`/`_hook_factories`, and expose a read-only descriptor accessor the adapter reads.
*Cost:* changes the `AgentLoop` constructor signature and normalization at `nanobot/agent/loop.py:307-308` and `:387-388`, which spends the `implement.md:110` escape hatch even though Option A qualifies as an edge-owned alternative; adds a union type to a hot constructor; every consumer of the stored lists must tolerate unwrapping; the wrapper type becomes public SDK surface that later needs compatibility care.
*Benefit:* inventory cannot drift from what the loop actually runs, and SDK-supplied hooks are inventoried correctly with no extra caller work.

**Option C — First-party-only, read-only hook rows.** Inventory only the known first-party long-lived hooks by construction-site constant, report them as operator-trusted in-process and `restart_required`, and state explicitly that externally supplied hooks are not inventoried.
*Cost:* parent R1 requires coverage of "registered hook factories" and parent R5 exists to disclose operator-installed executable code; an externally supplied hook is exactly the code that most warrants disclosure, and this option leaves it invisible. The inventory would be complete-looking but incomplete.
*Benefit:* smallest diff, zero risk to the loop, no drift possible because nothing dynamic is claimed.

**Recommendation:** Option A, combined with Option C's honesty rules — every hook row is read-only, `restart_required`, and declares only `INSPECT`. Option A satisfies `implement.md:16`'s intent by making registration explicit and metadata-bearing while honoring `implement.md:110` literally, and the shared-helper mitigation converts the drift risk into a test assertion. Escalate to Option B only if a decision-maker wants SDK and embedder hooks inventoried without caller cooperation, and accept the constructor change explicitly at that point.

**This decision is not made by this PRD.** A human owner must choose before the hook requirements below are implemented; R5 and R6 are written against Option A and must be revised if B or C is chosen. The provider requirements R1 through R4 are independent of this choice and can proceed immediately.

## Requirements

### R1 — One canonical package per provider family

- One `PROVIDER_REGISTRY` package represents one provider family and owns only the capability components that family actually implements: at most one `LLM_PROVIDER`, one `IMAGE_PROVIDER`, and one `TRANSCRIPTION_PROVIDER` component.
- The package name is the canonical family name; alias specs are members of their canonical family, never a second package, matching the existing Settings grouping rule.
- A transcription-only spec contributes no `LLM_PROVIDER` component.
- User-defined dynamic providers are not `PROVIDER_REGISTRY` packages; if projected at all in this child they are configured-source packages, and the documented rule is tested.
- Discovery reads only the three existing registries and existing settings projections; it constructs no provider client, opens no connection, performs no network call, and imports no transcription or image adapter class.

### R2 — Truthful provider classification

- Trust and execution are separate axes: a family whose capability calls a hosted endpoint is remote-service and remote execution; a locally deployed family is classified by what it actually is, not by its vendor name.
- Local-deployment, gateway, OAuth, direct, and transcription-only facts derive from the existing spec flags rather than from name matching.
- Provider packages are first-party registry metadata, not operator-installed executable code, and must not carry an executable-risk warning or `isolated: false` unless the family genuinely executes in-process operator-supplied code.
- Configured, unconfigured, and unavailable states derive from the existing settings configured-check, not from a new credential probe.

### R3 — Safe provider descriptors and revisions

- Descriptors and revisions exclude API keys, key hints, endpoints and `api_base`, default endpoints, proxy settings, extra headers, request-body overrides, user-selected models, OAuth account identity, expiry, tokens, and flow state, and absolute paths.
- Revisions derive only from non-secret structural facts — family name, implemented capability kinds, spec-level structural flags, and safe configured booleans — and never from `provider_signature` or serialized configuration.
- Configuration destinations are stable route keys for the existing Models, Image, and Voice sections; they carry no state or credential.
- One malformed family yields a bounded local diagnostic and cannot suppress unrelated provider packages.

### R4 — Truthful provider lifecycle and actions

- LLM provider components declare inspect and configure only, and report that changes take effect through the existing next-turn model-runtime resolution rather than a hot reload.
- The image component of the currently selected and enabled image provider may declare `RELOAD` only by delegating exactly once to the existing image runtime-control owner; every other image component declares no reload.
- Transcription components declare no reload, because the provider adapter is resolved and constructed per request.
- An image reload failure preserves the authoritative configuration state and reports the truthful restart-required or failed result; no second tool or process owner is created and no rollback is invented.
- Provider matching order, model-prefix routing, gateway and local fallback, presets and fallback presets, transcription aliases and default models, and image registration order are unchanged and asserted by test.

### R5 — Long-lived hook inventory

*(Written against Option A; revise if the open decision selects B or C.)*

- One `HOOK` component per registered long-lived hook or hook factory, owned by a package that identifies the composition that registered it, with a stable ID derived from a validated identity rather than an object address or repr.
- Identity for a first-party hook uses an explicit declared name; identity for an externally supplied callable uses a validated module and qualified-name pair. Identity collisions fail visibly rather than merging two hooks into one row.
- Hooks are classified operator-trusted, in-process, `isolated: false`, with lifecycle `restart_required` and `INSPECT` as the only declared action. No enable, disable, reload, or reconnect is offered.
- Per-turn caller hooks, SDK capture hooks, API usage-capture hooks, subagent hooks, and the progress hook are runtime values, are excluded from inventory, and the exclusion is tested.
- Descriptors carry no callable, bound object, closure, workspace path, or configuration value.

### R6 — Hook order and loop boundary preservation

*(Written against Option A; revise if the open decision selects B or C.)*

- Observable hook order is unchanged: progress hook, registered factories, registered hooks, turn factories, turn hooks.
- Per-factory exception isolation, ephemeral suppression, and the explicit opt-in that runs registered hooks for ephemeral turns are unchanged.
- `AgentLoop` and `AgentRunner` gain no registry, no descriptor storage, and no adapter reference; they continue to execute the hook values they are given.
- Public constructors continue to accept plain `AgentHook` instances and plain factory callables with unchanged behavior for callers that do not participate in inventory.
- A shared composition helper is the single place a production site declares a long-lived hook, and a test asserts every production composition site uses it and that its descriptor set matches its value set exactly.

### R7 — Composition, authorization, and safe failure

- Provider and hook adapters are registered through the existing explicit composition entry point and receive their inputs as arguments; no global singleton, package scan, or import-time registration is added.
- Any mutating provider action requires server-derived local-owner or system-administrator identity before side effects; hooks declare no mutating action.
- Provider OAuth login, logout, and credential persistence remain provider-specific Settings actions and do not become generic extension lifecycle.
- All descriptors, diagnostics, and action results are bounded and redacted; raw exceptions stay in server logs.
- No sandbox, permission enforcement, publisher verification, or safety guarantee is introduced.

### R8 — Verification

- Provider tests cover family grouping across the three registries, absence of ID collisions for every shared name, alias grouping, transcription-only exclusion, capability-component presence and absence, configured and unavailable states, revision stability under credential change, and the absence of every forbidden secret or endpoint field.
- Regression tests assert unchanged provider matching order, model routing, preset and fallback resolution, transcription alias and default-model resolution, and image registration order.
- Lifecycle tests cover the single delegated image reload, the absence of LLM and transcription reload, and truthful image reload failure.
- Hook tests cover exact ordering across all five positions, ephemeral suppression, the ephemeral opt-in, factory exception isolation, stable IDs, collision failure, exclusion of per-turn and subagent hooks, restart-required lifecycle, and inventory-versus-execution equality at every production composition site.
- Run focused tests, the full Python suite, Ruff, strict BasedPyright, the WebUI suite, and the production build.

## Acceptance Criteria

- [ ] AC1: Every provider family appears exactly once with only the capability components it implements, and no `ext:provider_registry:*` ID collides despite the complete LLM/image/transcription name overlap (parent AC1; runtime-adapters AC4).
- [ ] AC2: Alias specs are grouped under their canonical family and the transcription-only spec contributes no LLM component (runtime-adapters AC4).
- [ ] AC3: Provider inventory constructs no provider client, opens no connection, performs no network call, and imports no transcription or image adapter class (parent AC1).
- [ ] AC4: No provider descriptor, revision, diagnostic, or action result contains an API key, key hint, endpoint, default endpoint, proxy, header, request override, selected model, OAuth identity or token, or absolute path (parent AC10; runtime-adapters AC5).
- [ ] AC5: Provider matching order, model routing, presets and fallbacks, transcription aliases and default models, and image registration order are unchanged under test (parent AC12).
- [ ] AC6: LLM reports next-turn refresh, transcription reports per-request resolution with no reload action, and only the selected enabled image provider declares reload, delegating exactly once (parent AC6; runtime-adapters AC6).
- [ ] AC7: An image reload failure preserves the authoritative configuration and returns a truthful restart-required or failed result with no second owner and no invented rollback (parent AC6).
- [ ] AC8: Every long-lived hook appears exactly once with a stable validated identity, operator-trusted in-process classification, `isolated: false`, `restart_required` lifecycle, and `INSPECT` as its only action (parent AC1, AC2, AC7; runtime-adapters AC7).
- [ ] AC9: Duplicate hook identity fails visibly rather than merging rows, and per-turn, SDK-capture, API usage-capture, subagent, and progress hooks are absent from inventory (parent AC1).
- [ ] AC10: Hook execution order, per-factory error isolation, ephemeral suppression, and the ephemeral opt-in are byte-for-byte unchanged, and `AgentLoop`/`AgentRunner` contain no registry, descriptor store, or adapter reference (parent design boundary; runtime-adapters AC7).
- [ ] AC11: Every production composition site declares its long-lived hooks through the shared helper, and its inventory set equals its executed set (runtime-adapters AC7).
- [ ] AC12: A malformed provider family or hook registration yields one bounded diagnostic and suppresses no unrelated package (parent AC3; runtime-adapters AC12).
- [ ] AC13: Any mutating provider action rejects a non-admin actor before side effects, and provider OAuth remains a family-specific action (parent AC9).
- [ ] AC14: Focused and full Python tests, Ruff, strict BasedPyright, WebUI tests, and the production build pass (parent AC12).

## Out of Scope

- Choosing the hook registration ownership option; that decision is escalated above and belongs to a human owner.
- Channel, optional-feature, CLI-app, Agent Plugin, Skill, MCP, and tool adapters.
- The unified Extensions page and generic snapshot/action endpoints.
- Generic provider OAuth or credential lifecycle, model routing refactor, transcription caching or reload, and new provider backends.
- Per-turn, SDK-capture, API usage-capture, and subagent hooks; new hook lifecycle events; hook enable/disable.
- Dynamic user-defined provider lifecycle management beyond truthful projection.
- Sandboxing, permission enforcement, publisher verification, signatures, marketplace, KMS, durable audit storage, billing, and quotas.

## Key Decisions

- One package per provider family with up to three capability components is required, not merely preferred: every image and transcription provider name is also an LLM provider name, so a per-registry package would collide on the canonical ID and fail the whole adapter snapshot.
- Alias grouping mirrors the rule the Models page already implements, so the extension tree and Settings never disagree about how many providers exist.
- Providers are registry metadata, not operator-installed executable code, and therefore carry no executable-risk warning; that warning is reserved for code the operator actually installed.
- Hooks are read-only and restart-bound in this child regardless of which registration option is chosen; nothing here claims a hook can be hot-swapped.
- The provider half does not block on the hook decision and may land first.
