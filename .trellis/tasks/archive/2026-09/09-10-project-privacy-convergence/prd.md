# Project privacy and runtime safety convergence

## Goal
Implement the five-part convergence approved after the architecture review: reliable project authorization, explicit data ownership, consistent transitions, honest feature surfaces, and documented host/member workflows. The user explicitly requests local mocked full testing, especially sandbox/privacy/security.

## Background
The clean source baseline is 6cd4022. Previously exercised defects: WebSocket cron/continuation/recovery classified as host (agent/loop.py:904-971); restore deletes member ownership (2141-2170); channel project changes reuse history (1133-1161); deleted assignments can reauthorize automation (collaboration/store.py:859-891); global profile/memory crosses project boundaries (agent/context.py:253-280, agent/memory.py:582-608); codex export advertises missing mcp serve (cli/commands.py:732-778). User approved convergence in response to the preceding final analysis and five-step plan.

## Requirements
- R1: Distinguish authenticated host, external member, and internally generated turn. Internal turns inherit persisted authorization; missing, inconsistent, or revoked scoped authorization never becomes host authority. Support proxy and OIDC identities.
- R2: Project changes cannot replay another project's history/provider state. WebUI chats pin their initial authorized project. Non-WebUI keys include project scope. Host legacy behavior remains.
- R3: Host personal profile, history, media, credentials and memory are unavailable in member turns and derived subagent/compact/Dream flows. Member/project memory has an explicit separate owner. Cross-session reads/messages require the same authorized user AND project.
- R4: Each protected operation is server-authorized. Revoked membership, disabled/deleted/reassigned channel, deleted/rebound conversation refuse later execution/read access. Preserve historical files without destructive or ambiguous migration.
- R5: Member shell/CLI execution cannot run directly on the host; an unavailable/unsupported configured sandbox fails closed. Enforce scoped file/media/skill roots, negative traversal/symlink/cross-user tests, namespace and credential isolation. Do not claim path guards or ambient sandbox environment labels are process isolation.
- R6: Pairing/configured/enabled/running are distinct states; failed runtime application remains visible and retryable through existing lifecycle controls. Remove the unsupported MCP bridge export option, retain working config interchange.
- R7: Separate member use from host administration in navigation; clarify Projects versus working directories and Apps versus runtime inventory. Document member discovery, pairing, revocation, supported deployment security and limitations.
- Runtime provenance is an explicit producer-owned `InboundMessage.source`, persisted across pending recovery. Member private state is user-and-project scoped outside shared directories; consolidation and private Dream tools enforce the same owner boundary.
- R8: Isolated regressions plus full backend and frontend tests, lint/types/build, actual CLI and browser smoke. No real credentials, account mutations, user runtime/config/history, or outbound model calls.

## Acceptance
- A1: Human -> cron/goal continuation/recovery/subagent stays in project and respects revocation, both proxy and OIDC; host still works.
- A2: A -> B reassignment/default selection cannot replay A data; same-user cross-project and other-user reads/messages deny.
- A3: Synthetic host/member secret markers never cross prompt, memory journal, Dream, tool read or media boundaries.
- A4: Removing assignment or membership prevents future scoped execution and access without modifying original user history.
- A5: Unsandboxed member exec/CLI returns an actionable denial before process creation; supported sandbox invocation constrains files/processes/network/env; unsupported platforms cannot fall through.
- A6: Failed activation/reload is not displayed as healthy running; unsupported bridge cannot be exported.
- A7: Member UI exposes only usable surfaces and documents the complete project/channel flow; host controls remain usable.
- A8: All available full test/quality commands pass; skipped native capabilities and exact smoke evidence are reported honestly.

## Non-goals
No SaaS tenancy, Postgres, new container orchestrator or native macOS sandbox backend; no content-based prompt filter as an authorization boundary; no new model/provider or automation framework. No automatic deletion of user history, secrets or deployments; no publish/commit/deploy.
