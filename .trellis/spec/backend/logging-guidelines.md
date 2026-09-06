# Logging Guidelines

> How logging is done in this project.

---

## Overview

nanobot uses **loguru**, never the standard library `logging` module directly. Import it the
same way in every module (83 modules do):

```python
from loguru import logger
```

There is no per-module logger factory and no `getLogger(__name__)`. The sink is configured
once at CLI startup in `nanobot/cli/commands.py`, immediately after the console encoding fix:

```python
logger.remove()
_log_handler_id = logger.add(
    sys.stderr,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <5}</level> | "
        "<cyan>{extra[channel]}</cyan> | "
        "<level>{message}</level>"
    ),
    level="INFO",
    colorize=None,
    filter=lambda record: record["extra"].setdefault("channel", "-") or True,
)
```

`configure_logging(verbose)` in `nanobot/cli/gateway.py` removes that handler and re-adds an
identical one at `DEBUG` when `--verbose` is passed. **Do not add a second sink anywhere else.**

There is **no loguru file sink**. Log files under `<data-dir>/logs/` are produced by redirecting
the gateway process's stdout/stderr (`GatewayRuntimePaths.log_path` in
`nanobot/gateway/runtime.py`, launchd `stdout_path`/`stderr_path` in
`nanobot/gateway/service.py`). Do not implement file logging by calling `logger.add(<path>)`;
there is no rotation policy and a second sink would duplicate every record.

Library visibility is toggled as a whole via `_set_nanobot_logs` in
`nanobot/cli/log_control.py` (`logger.enable("nanobot")` / `logger.disable("nanobot")`), which
is how the SDK and TUI paths silence framework output.

Third-party libraries that use stdlib `logging` are bridged, not re-implemented: call
`redirect_lib_logging(name, level=None)` from `nanobot/utils/logging_bridge.py`. The three
files that import stdlib `logging` (`nanobot/utils/logging_bridge.py`,
`nanobot/webui/websocket_logging.py`, `nanobot/webui/version_check.py`) do so only to bridge or
filter third-party records.

---

## Format

**Always use loguru's brace-style lazy formatting. Never f-strings.** This is exceptionless in
the current tree: 525 brace-style calls, **zero** f-string calls.

```python
logger.info("Tool call: {}({})", tc.name, args_str[:200])
logger.warning("Ignoring duplicate Agent Plugin identity '{}'", plugin.name)
logger.warning("Ignoring MCP server '{}' in Agent Plugin '{}'", name, plugin.name)
```

Rationale: arguments are only formatted if the level is enabled, and the message string stays
a stable, greppable literal.

Messages are plain sentences in English, not JSON. There is no structured-logging pipeline —
context is carried by `extra`, described below.

---

## Channel context binding

The format string reads `extra[channel]`, so every record has a channel field. `BaseChannel`
binds it once in `nanobot/channels/base.py`:

```python
self.logger = logger.bind(channel=self.name)
```

**Inside a channel implementation, use `self.logger`, not the module-level `logger`.** The
global filter defaults `extra["channel"]` to `"-"` for everything else, so non-channel code
can use the bare `logger` freely. `logger.bind(...)` is used for this single purpose — do not
introduce new bound keys without also updating the sink format.

---

## Log Levels

Only five levels are used. `critical`, `success`, and `trace` do not appear in `nanobot/` and
should not be introduced.

| Level | Uses | When |
|---|---|---|
| `warning` | ~457 | Recoverable degradation: an item was ignored, a refresh failed, a fallback was taken. The most common level by far. |
| `info` | ~198 | Normal lifecycle milestones a user would want to see: startup, tool calls, subagent spawn/complete, job scheduling. INFO is the default visible level. |
| `exception` | ~190 | Inside an `except` block where a traceback is needed. Always preferred over `logger.error(...)` when an exception is in scope. |
| `debug` | ~152 | Detail only useful with `--verbose`. |
| `error` | ~106 | A genuine failure where no exception object is in scope. |

Two rules follow from the distribution:

- **Use `logger.exception` inside `except`, not `logger.error`.** `exception` attaches the
  traceback; `error` throws it away.
- **Do not log at `info` in a hot path.** INFO is on by default, so per-message or per-token
  logging belongs at `debug`.

---

## What to Log

- The full failure detail on any caught exception you are converting into a user-facing
  message — this is how the detail survives redaction. Pair every one of these with a fixed
  constant returned to the caller (see [`error-handling.md`](./error-handling.md)).
- Lifecycle transitions: channel start/stop, gateway startup, cron job scheduling and firing,
  MCP server connect/disconnect, subagent spawn/completion.
- Skipped or ignored input, with enough identity to find it: `"Ignoring Agent Plugin manifest
  in '{}': invalid name"`.
- Restart-required and configuration-changed events.

The canonical pairing is in `nanobot/extensions/adapters/channels.py`, where **every** failure
path is the same two lines — full detail to the log, constant to the caller:

```python
except Exception:
    logger.exception("Channel dependency preparation failed")
    return self._failure(request, package.id, "Channel dependencies could not be prepared.")
```

```python
except Exception:
    logger.exception("Channel enablement configuration update failed")
    return self._failure(request, package.id, "Channel could not be enabled.")
```

```python
except Exception:
    logger.warning("Channel metadata refresh failed after enablement")
```

Note that the log message and the returned message are deliberately *different* strings: the
log names the internal operation, the return names the user-visible outcome.

---

## What NOT to Log

### Secrets and credentials

Never log API keys, channel tokens, `client_secret`, OAuth tokens, or
`Authorization` headers — not even truncated. Use the existing maskers when a hint is needed:
`masked_api_secret` / `mask_secret_hint` (`nanobot/webui/settings_capabilities.py`,
`nanobot/webui/settings_models.py`), `_mask_value` (`nanobot/cli/onboard.py`).

### Raw URLs

Server URLs routinely embed credentials (`https://user:token@host/sse`, `?token=...`) and some
deployments put opaque tokens in the path. Redact before logging:

- `_redact_url(url)` in `nanobot/agent/tools/mcp.py` — keeps scheme + host + `/...` placeholder.
- `_redact_url_for_log(url)` in `nanobot/agent/tools/web.py` — origin only, no userinfo, path,
  query, or fragment.

### Absolute local paths

Local filesystem paths are treated as disclosure in anything that can reach a user surface;
`safe_extension_message` (`nanobot/extensions/contracts.py`) rewrites them to `<path>`. Logs
are more permissive than responses, but prefer workspace-relative paths or a bare filename.

### Message content

Conversation text, attachments, and transcripts are not logged. `nanobot/llm_usage/` is
explicitly a "content-free LLM usage backend" — keep it that way. When logging a model or tool
payload for debugging, truncate hard and stay at `debug`:

```python
logger.info("Tool call: {}({})", tc.name, args_str[:200])
logger.info("{} output:\n{}", label, output[:_LOG_OUTPUT_LIMIT])   # _LOG_OUTPUT_LIMIT = 4000
```

(`nanobot/agent/progress_hook.py`, `nanobot/optional_features.py`.)

### Unbounded subprocess or provider output

Always slice to an explicit module constant, as `_LOG_OUTPUT_LIMIT` does. An unbounded dump
can also become context pollution — see [`.agent/gotchas.md`](../../../.agent/gotchas.md),
"Context Pollution Persists".

---

## Anti-Patterns

- `logger.info(f"...")` — f-strings defeat lazy formatting and are absent from the tree.
- `logging.getLogger(__name__)` in `nanobot/` code — use loguru.
- A second `logger.add(...)` sink outside `cli/commands.py` and `cli/gateway.py`.
- `logger.error("...", exc)` inside an `except` block — use `logger.exception("...")`.
- `print(...)` for diagnostics. `print` and `rich.Console` are reserved for deliberate CLI
  *output* (`nanobot/cli/*.py` each own a module-level `console = Console()`); everything
  diagnostic goes through `logger`.
- Logging the same failure at two levels of the call stack. Log where you have the detail
  (usually the innermost `except`), and let the outer layer return the constant message.
