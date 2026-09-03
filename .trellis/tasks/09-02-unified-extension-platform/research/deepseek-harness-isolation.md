# DeepSeek Harness and nanobot plugin isolation research

## Reference correction

The referenced project is DeepSeek Harness (`deepseek-ai/deepseek-harness`), whose README describes an “everything-is-a-plugin” Cordis architecture. GitHub search returned no repository matching the exact phrase “DeepShield Harness.”

## Source-verified DeepSeek Harness patterns

- Composition: profiles stack ordered plugin bundles and patch layers; registrations are reversible effects owned by plugin lifecycles.
- Capability seams: service definition, provider, and consumer are separated. Filesystem, subprocess, sandbox, tools, credentials, and approvals have independent owners.
- Tool policy: `tools/pre-execute`, monotonic guards, one-shot approval, execution wrappers, and post-execute normalization form one ordered pipeline.
- Subprocess ownership: all child processes, terminals, language servers, and out-of-process subagents use one managed subprocess service with scrubbed environment, bounded output, and whole-tree termination.
- Sandbox policy: a shared per-call policy resolves read-only, workspace-write, or full access. Local backends select Linux Bubblewrap then Landlock, macOS Seatbelt, or Windows restricted tokens; unusable backends fail closed and report `full` or `partial` enforcement.
- Credentials: configuration stores secret references rather than values; providers resolve values at operation time.
- Dynamic plugins: host halves run in `node:vm`, receive a restricted Cordis façade, and have reversible lifecycles, but the project explicitly says this is not a security boundary and gives them bash-equivalent trust.
- Safety statement: DeepSeek Harness is developer-preview software, has not undergone a security audit, and warns not to rely on its sandbox as the sole control for untrusted workloads.

Primary sources:

- https://github.com/deepseek-ai/deepseek-harness
- https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/tool-execution-pipeline.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/extensions/cordis-host-runner/README.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sandbox/sandbox-local/README.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sandbox/sandbox-policy/README.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/subprocess/README.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/credentials/README.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/SAFETY.md

## Current nanobot boundary

- `nanobot/agent/plugins.py` already provides a useful package layer: Agent Plugins v1 manifests, contained static Skills, stdio MCP definitions, explicit enable/disable, private per-workspace plugin data, and activation markers bound to a full content fingerprint.
- `permissions` is informational only.
- Agent Plugin stdio processes are merged into ordinary `MCPServerConfig` and launched by the MCP SDK. They do not pass through `ExecTool`'s Bubblewrap path.
- The MCP SDK starts with a reduced environment, but still inherits host `HOME`, `PATH`, shell, terminal, and user variables. Plugin-provided values are merged on top.
- The gateway reads MCP stdio protocol traffic directly. A hostile producer therefore still needs frame and aggregate-buffer bounds to prevent gateway-memory denial of service.
- Python `nanobot.tools` entry points are loaded with `entry_points(...).load()` and execute in the gateway process.
- Existing workspace path checks are application guards. The project security guide explicitly says they are not a replacement for an OS sandbox.
- The image contains Bubblewrap, but the default Compose drops all capabilities; the optional bwrap override adds `SYS_ADMIN` and disables AppArmor/seccomp for compatibility. That override is too broad to present as a final SaaS isolation architecture without a dedicated worker boundary.

## Product decision and retained lessons

The operator explicitly chose not to make nanobot responsible for plugin safety and requested warnings rather than enforced isolation. OS sandboxing, resource enforcement, permission enforcement, and secret brokering are therefore out of scope for the current plugin task. The retained DeepSeek Harness lessons are its explicit descriptors, capability seams, deterministic lifecycle ownership, reversible registration effects, safe inventory, and candid bash-equivalent trust disclosure.
