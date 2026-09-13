# Implementation and validation

1. Confirm baseline and fix scoped principal/session lifetime. Core source and regression authoring run independently against the documented behavior contracts.
2. Close collaboration membership/assignment/binding revocation and WebUI object authorization; preserve old data.
3. Isolate member prompt/memory/archive/subagent context and file/media roots. Require real supported process isolation for member arbitrary code.
4. Remove dead MCP export; make runtime projection honest; separate member/host navigation and project/channel workflows.
5. Main integrates shared boundary files and runs each regression first, then full backend pytest (including channel packages), ruff check, basedpyright, frontend vitest/lint/tsc/build.
6. Run CLI and browser smoke with temporary config/workspaces and synthetic HTTP model response. Inspect negative privacy/sandbox transitions and log precise evidence.
7. Update existing user/security/spec documentation after behavior is proven; remove throwaway artifacts; report skips/limitations. No commit or deployment.

Commands (use existing .venv, no unintended dependency pruning):
- .venv/bin/python -m pytest tests/agent tests/collaboration tests/security tests/tools tests/webui
- .venv/bin/python -m pytest (full testpaths, isolated HOME)
- .venv/bin/ruff check nanobot tests conftest.py
- .venv/bin/basedpyright
- bun run test (webui)
- bun run lint (webui)
- bun run build (webui)

Risks: namespace compatibility, established WebSocket public IDs, mixed historical memory, active turn revocation, shell environment/bind leakage, incomplete optional test dependencies. Fail closed on missing authority; do not weaken tests or claim mock verification is native sandbox proof.
