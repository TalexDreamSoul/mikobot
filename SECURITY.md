# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in nanobot, please report it by:

1. **DO NOT** open a public GitHub issue
2. Create a private security advisory on GitHub or contact the repository maintainers (xubinrencs@gmail.com)
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We aim to respond to security reports within 48 hours.

## Security Best Practices

### Trust model for shared projects

This fork is a single-host, self-hosted workbench, not a mutually distrustful SaaS
tenant sandbox. The host administrator, configured providers/MCP servers, and installed
in-process extensions remain trusted infrastructure. An extension permission label or
`NANOBOT_WORKSPACE_SANDBOX_ENFORCED` display hint is not proof of per-project isolation.

Project authorization is checked server-side from identity, membership and recorded
route provenance. Internal jobs inherit the source session; missing or revoked scope
never upgrades them to host authority. Tool calls revalidate that capability, including
after a model request and in queued subagents. Revocation prevents subsequent operations;
it cannot undo a completed side effect or retract content already read by a client.

Private profiles and automatic archives are separated by **user and project** under
`<config-dir>/users/<user-id>/projects/<project-id>/`. Project workspaces contain shared
files. Member media is stored under that user's project-specific media root and signed
for a session whose authorization is checked on fetch. Do not move private journals into
shared project directories. Review legacy mixed memory/profile files before upgrading a
deployment for unrelated users; this change preserves historical data instead of
attempting destructive automatic declassification.

Shared project documents and Skills can contain untrusted instructions. Filesystem and
process boundaries do not eliminate prompt injection or make a trusted MCP server safe.
Use explicit project capability allowlists and separate OS deployments for hostile users.

### 1. API Key Management

**CRITICAL**: Never commit API keys to version control.

```bash
# ✅ Best: Use environment variable references in config (never writes the key to disk)
# In ~/.nanobot/config.json:
#   "apiKey": "${ANTHROPIC_API_KEY}"
# Then supply the key at runtime via env var or Docker secret.

# ✅ Good: Store in config file with restricted permissions
chmod 600 ~/.nanobot/config.json

# ❌ Bad: Hardcoding keys in code or committing them
```

**Recommendations:**
- **Prefer environment variable references** (`${VAR}`) in config — the config file stores the `${VAR}` placeholder, and the plaintext value only exists in memory at runtime. See [Configuration: Environment Variables for Secrets](https://nanobot.wiki/docs/latest/use-nanobot/configuration/#environment-variables-for-secrets) for details.
- When plaintext keys are stored in `~/.nanobot/config.json`, set file permissions to `0600` (`chmod 600`)
- Consider using an OS keyring/credential manager for production deployments
- Rotate API keys regularly
- Use separate API keys for development and production

### 2. Channel Access Control

**IMPORTANT**: Always configure `allowFrom` lists for production use.

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["123456789", "987654321"]
    },
    "whatsapp": {
      "enabled": true,
      "allowFrom": ["1234567890"]
    }
  }
}
```

**Security Notes:**
- In `v0.1.4.post3` and earlier, an empty `allowFrom` allowed all users. Since `v0.1.4.post4`, empty `allowFrom` denies all access by default — set `["*"]` to explicitly allow everyone.
- Get your Telegram user ID from `@userinfobot`
- Use WhatsApp sender IDs as full phone numbers with country code and no leading `+`
- Review access logs regularly for unauthorized access attempts

### 3. Shell Command Execution

The `exec` tool can execute shell commands. While dangerous command patterns are blocked, you should:

- ✅ **Enable the bwrap sandbox** (`"tools.exec.sandbox": "bwrap"`) for kernel-level isolation (Linux only)
- ✅ Review all tool usage in agent logs
- ✅ Understand what commands the agent is running
- ✅ Use a dedicated user account with limited privileges
- ✅ Never run nanobot as root
- ❌ Don't disable security checks
- ❌ Don't run on systems with sensitive data without careful review

**Exec sandbox (bwrap):**

On Linux, set `"tools.exec.sandbox": "bwrap"` to wrap every shell command in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox. This uses Linux kernel namespaces to restrict what the process can see:

- Every Bubblewrap mode isolates the process and IPC namespaces, preventing host
  process inspection through `/proc` from bypassing the filesystem boundary.
- The host's configured mode retains explicitly allowed extra binds and networking.
- Member shell calls require a supported Linux Bubblewrap backend automatically.
  They bind only the shared project read-write and exact authorized attachments
  read-only, clear inherited credentials, and isolate networking. Configured host
  extra binds and ambient sandbox hints cannot widen a member sandbox.
- Member calls cannot select a host shell executable or login startup script before
  sandbox entry. Host-managed CLI Apps cannot bypass this boundary.
- Unsupported platforms, unavailable backends, or sandbox startup failures are errors;
  there is no silent unsandboxed fallback. On macOS/Windows, use a Linux sandbox-capable
  deployment for member code execution; ordinary scoped file tools remain available.

Requires `bwrap` and permitted Linux namespace creation. It is preinstalled in the
repository Docker image; see [Deployment](docs/deployment.md#docker-compose) for
the explicitly privileged nested-sandbox override and its trade-offs.

Enabling the sandbox also automatically activates `restrictToWorkspace` for file tools.

**Blocked patterns:**
- `rm -rf /` - Root filesystem deletion
- Fork bombs
- Filesystem formatting (`mkfs.*`)
- Raw disk writes
- Other destructive operations

### 4. File System Access

File operations have path traversal protection, but:

- ✅ Enable `restrictToWorkspace` or the bwrap sandbox to confine file access
- ✅ Run nanobot with a dedicated user account
- ✅ Use filesystem permissions to protect sensitive directories
- ✅ Regularly audit file operations in logs
- ❌ Don't give unrestricted access to sensitive files


Member file tools additionally deny host profile/history, global media, and other
members' private roots. Only exact own-profile files, read-only own journals, authorized
attachments, and approved built-in Skills supplement the project workspace. Symlink
containment applies to those extra capabilities as well. Shell regex checks are early
diagnostics, never the process-isolation mechanism.

### 5. Network Security

**API Calls:**
- All external API calls use HTTPS by default
- Timeouts are configured to prevent hanging requests
- The OpenAI-compatible API server must set `api.api_key` when binding to `0.0.0.0` or `::`; otherwise startup fails to prevent unauthenticated network access
- Consider using a firewall to restrict outbound connections if needed

**WhatsApp:**
- Keep the neonize session database under `~/.nanobot/whatsapp-auth` secure (mode 0700).
- Use `nanobot channels login whatsapp --force` to remove and recreate the local session database when rotating linked devices.

### 6. Dependency Security

**Critical**: Keep dependencies updated!

```bash
# Check for vulnerable dependencies
pip install pip-audit
pip-audit

# Update to latest secure versions
pip install --upgrade nanobot-ai
```

**Important Notes:**
- Keep `litellm` updated to the latest version for security fixes
- Run `pip-audit` regularly after enabling the channels used in production; their manifest-declared dependencies are installed into the same environment
- Subscribe to security advisories for nanobot and its dependencies

### 7. Production Deployment

For production use:

1. **Isolate the Environment**
   ```bash
   # Run in a container or VM
   docker run --rm -it python:3.11
   pip install nanobot-ai
   ```

2. **Use a Dedicated User**
   ```bash
   sudo useradd -m -s /bin/bash nanobot
   sudo -u nanobot nanobot gateway
   ```

3. **Set Proper Permissions**
   ```bash
   chmod 700 ~/.nanobot
   chmod 600 ~/.nanobot/config.json
   chmod 700 ~/.nanobot/whatsapp-auth
   ```

4. **Enable Logging**
   ```bash
   # Configure log monitoring
   tail -f ~/.nanobot/logs/nanobot.log
   ```

5. **Use Rate Limiting**
   - Configure rate limits on your API providers
   - Monitor usage for anomalies
   - Set spending limits on LLM APIs

6. **Regular Updates**
   ```bash
   # Check for updates weekly
   pip install --upgrade nanobot-ai
   ```

### 8. Development vs Production

**Development:**
- Use separate API keys
- Test with non-sensitive data
- Enable verbose logging
- Use a test Telegram bot

**Production:**
- Use dedicated API keys with spending limits
- Restrict file system access
- Enable audit logging
- Regular security reviews
- Monitor for unusual activity

### 9. Data Privacy

- **Logs may contain sensitive information** - secure log files appropriately
- **LLM providers see your prompts** - review their privacy policies
- **Chat history is stored locally** - protect the `~/.nanobot` directory
- **API keys are in plain text** - use OS keyring for production

### 10. Incident Response

If you suspect a security breach:

1. **Immediately revoke compromised API keys**
2. **Review logs for unauthorized access**
   ```bash
   grep "Access denied" ~/.nanobot/logs/nanobot.log
   ```
3. **Check for unexpected file modifications**
4. **Rotate all credentials**
5. **Update to latest version**
6. **Report the incident** to maintainers

## Security Features

### Built-in Security Controls

✅ **Input Validation**
- Path traversal protection on file operations
- Dangerous command pattern detection
- Input length limits on HTTP requests

✅ **Authentication**
- Allow-list based access control — in `v0.1.4.post3` and earlier empty `allowFrom` allowed all; since `v0.1.4.post4` it denies all (`["*"]` explicitly allows all)
- Failed authentication attempt logging

✅ **Resource Protection**
- Command execution timeouts (60s default)
- Output truncation (10KB limit)
- HTTP request timeouts (10-30s)

✅ **Secure Communication**
- HTTPS for all external API calls
- TLS for Telegram API
- WhatsApp session secrets stay in the local session database

## Known Limitations

⚠️ **Current Security Limitations:**

1. **No Rate Limiting** - Users can send unlimited messages (add your own if needed)
2. **Plain Text Config** - API keys stored in plain text in `config.json` (prefer `${VAR}` env references when possible, or use keyring for production)
3. **No Session Management** - No automatic session expiry
4. **Limited Command Filtering** - Only blocks obvious dangerous patterns (enable the bwrap sandbox for kernel-level isolation on Linux)
5. **No Audit Trail** - Limited security event logging (enhance as needed)

## Security Checklist

Before deploying nanobot:

- [ ] API keys stored securely (not in code)
- [ ] Config file permissions set to 0600
- [ ] `allowFrom` lists configured for all channels
- [ ] Running as non-root user
- [ ] Exec sandbox enabled (`"tools.exec.sandbox": "bwrap"`) on Linux deployments
- [ ] File system permissions properly restricted
- [ ] Dependencies updated to latest secure versions
- [ ] Logs monitored for security events
- [ ] Rate limits configured on API providers
- [ ] Backup and disaster recovery plan in place
- [ ] Security review of custom skills/tools

## Updates

**Last Updated**: 2026-07-21

For the latest security updates and announcements, check:
- GitHub Security Advisories: https://github.com/HKUDS/nanobot/security/advisories
- Release Notes: https://github.com/HKUDS/nanobot/releases

## License

See LICENSE file for details.
