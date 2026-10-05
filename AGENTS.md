# Working on Mikobot

The Python gateway owns agent execution, sessions, tools, memory, and security policy. WebUI and TUI share that runtime; keep execution and policy out of the clients. This fork retains nanobot's package and CLI identifiers.

## Task-specific guidance

| When working on | Read |
| --- | --- |
| Core boundaries, extensions, or internal types | [`.agent/design.md`](.agent/design.md) |
| Refactoring, fallbacks, or test selection | [`.agent/simplify.md`](.agent/simplify.md) |
| Path permissions, HTTP/MCP, or shell isolation | [`.agent/security.md`](.agent/security.md) |
| Dependency setup, WebUI transport, config, Windows, prompts, or persistence | [`.agent/gotchas.md`](.agent/gotchas.md) |
| Reusing verification evidence | [`.agent/workflow.md`](.agent/workflow.md) |
| WebUI/host compatibility | [`.agent/review-guide.md`](.agent/review-guide.md) |
| Contribution or publication | [`CONTRIBUTING.md`](CONTRIBUTING.md), [`docs/releasing.md`](docs/releasing.md) |

## Development constraints

- Analyze required behavior, state ownership, and root cause before extending code. Refactor when the structure causes the problem; a smaller diff does not justify another fallback.
- Do not add tests for hypothetical internal states or unsupported combinations. Each new test needs a reachable path and a meaningful contract.
- Do not run `ruff format`; mechanical formatting obscures git blame and creates unrelated diffs. This takes precedence over optional touched-file formatting in `CONTRIBUTING.md`.
- Python 3.11+, asyncio, line length 100. Tests mirror `nanobot/`; channel packages contain their own tests as well.

## Fork boundaries

`nanobot/collaboration/` owns the local JSON project/member/channel control plane and is wired by the gateway. Active-project preference and globally unique King authority are separate. Only the authenticated local owner in King has cross-project tool authority. Member turns, derived work, tools and consolidation revalidate current private user/project/instance provenance; session overrides and sender labels never grant authority. Preserve `.agent/security.md` and the migration/locking contracts in `.trellis/spec/backend/`.

## Verification commands

```bash
uv sync --all-extras --dev
uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
uv run --no-sync ruff check nanobot tests conftest.py
uv run --no-sync basedpyright
uv run --no-sync python -m pytest
```

WebUI commands are declared in `webui/package.json`; its build goes to `nanobot/web/dist` and is bundled in the wheel. TUI commands are declared in `tui/package.json`. `nanobot gateway` runs the gateway. For shared-worktree changes, scope staging to the approved files and never reset or blanket-stage unrelated work.
