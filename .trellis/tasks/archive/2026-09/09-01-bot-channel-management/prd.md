# Bot and channel management

## Goal

Make the WebUI a complete Chinese-first control plane for organizations, deployable bots, channel instances, authentication, project assignment, and bot capability inspection without exposing credentials.

## Confirmed Facts

- Project-management components currently contain English JSX literals under `webui/src/components/projects/`.
- The sidebar header only renders the nanobot logo and collapse control; its footer renders Settings and connection state (`webui/src/components/Sidebar.tsx`).
- Organizations are currently loaded only inside `useCollaborationProjects`, so the sidebar cannot switch the active organization without lifting that state into the application shell.
- `Persona` is a prompt identity routed to a vault, while channel instances are runtime transports. Neither model represents a deployable bot that groups projects, channels, and capabilities.
- Generic channel infrastructure already supports multiple instances through `ChannelManagementSpec`; Feishu implements it, while Weixin still uses a single config/state directory.
- Existing Pair Codes prove control of an external channel sender. They are single-use and time-bounded, but are not yet tied to bot or project assignments.
- OIDC Authorization Code + PKCE exists in `OidcAuthConfig`, but WebUI Settings does not expose the login configuration.
- Existing Skill APIs intentionally hide absolute filesystem paths and already return safe Markdown details.

## Requirements

### R1 — Complete localization

- Move every user-facing literal in project, organization, personal-task, member, extension, and context-source panels into the existing i18n system.
- Provide complete English and Simplified Chinese keys. Other locales use the English fallback until translated.
- New bot, organization, channel, pairing, and login-management surfaces follow the same rule.

### R2 — Global organization and bot switchers

- Add an organization switcher directly above Settings in the expanded sidebar footer.
- Add an active-bot switcher immediately to the right of the sidebar logo.
- Collapsed and mobile layouts provide equivalent accessible controls.
- Switching organization filters projects and bots. Switching bot changes the default bot used by new conversations without changing historical sessions.
- Persist the current user's default organization, project, and bot so selections follow the user across devices.

### R3 — First-class bot domain

- Introduce a first-class `Bot` entity instead of overloading Persona or channel instances.
- A bot belongs to one organization, has an owner, name, optional avatar, optional Persona, lifecycle state, and timestamps.
- Organization owners/admins can manage organization bots. Members can inspect and use bots explicitly assigned to projects they can access.
- A bot can be assigned to multiple projects and can have multiple channel instances.

### R4 — User-owned multi-instance channels

- Represent channel ownership and bot assignment in collaboration persistence using the unique key `(channel_type, instance_id)`.
- One user can claim multiple instances of the same channel type, assign each instance to one owned bot, and assign different instances to different bots.
- Secrets remain in server-owned channel configuration and are always redacted from API responses and logs.
- Extend Weixin to the generic multi-instance contract, including isolated state directories, QR sessions, runtime names, lifecycle status, and reconnect/replace behavior.

### R5 — Pair-Code-gated assignment

- A newly configured channel instance cannot route traffic or be assigned until the authenticated user claims it with a fresh Pair Code obtained by messaging that exact instance.
- Linking a channel to a bot and assigning that bot to a project each consume a fresh single-use Pair Code tied to the authenticated user and one claimed channel instance of the bot.
- The backend, not the browser, validates code ownership, expiry, target channel instance, organization membership, bot ownership, and project membership.
- Group messages never disclose Pair Codes; existing private-message behavior remains.

### R6 — Bot, channel, workspace, and project visibility

- Bot detail shows owner, organization, assigned projects/workspaces, channel instances, runtime status, pairing state, and last safe error.
- Project detail shows assigned bots and the subset of each bot's channels enabled for that project.
- Channel detail shows type, instance name, owner, bot, connection/runtime state, and safe setup metadata.

### R7 — Bot capability visibility

- Bot detail shows effective Skills and MCP servers globally and per project.
- Skill information includes name, source, enabled/available status, safe logical path, bounded file list, requirements, and Markdown detail.
- Never expose absolute host paths, environment values, tokens, secrets, or files outside the approved Skill directory.

### R8 — Login and OAuth settings

- Add a Login & Security section to Settings for WebUI OIDC configuration: enabled state, issuer, client ID, masked client secret, redirect URI, scopes, token auth method, session/flow TTLs, and explicit system-admin subjects.
- Validate issuer discovery and redirect safety before saving; return field-level errors and mark gateway restart required.
- Existing model-provider and MCP OAuth flows remain in their current sections, with a consolidated status summary in Login & Security.
- Only the local owner or an OIDC subject explicitly listed as a system admin may read or mutate global login/channel configuration.

### R9 — Chinese-first channel management UX

- Replace the single global channel card view with user/bot-scoped channel-instance management while preserving server-wide runtime controls for system admins.
- Show clear actions for create, connect, claim with Pair Code, assign to bot, assign bot to project, reconnect, disable, and remove.
- Desktop and mobile surfaces must remain usable without horizontal overflow.

## Acceptance Criteria

- [ ] AC1: With `zh-CN` selected, the project page and all new management surfaces contain no English UI literals.
- [ ] AC2: The sidebar organization switcher appears immediately above Settings; the bot switcher appears beside the logo and both remain accessible in collapsed/mobile layouts.
- [ ] AC3: A user can create two Weixin instances, distinguish their state directories/runtime states, claim each with separate Pair Codes, and assign them to different bots.
- [ ] AC4: A user can own multiple instances of any channel type whose plugin supports multi-instance management.
- [ ] AC5: An unclaimed instance, expired code, reused code, wrong-instance code, or cross-user code is rejected by the backend.
- [ ] AC6: Project owners can assign a bot and select only that bot's claimed channels after a fresh Pair Code; unauthorized users cannot mutate the assignment.
- [ ] AC7: Bot detail accurately lists organization, projects/workspaces, channels, effective Skills/MCP servers, safe logical Skill paths, and bounded file metadata without secrets.
- [ ] AC8: A system admin can validate and save OIDC settings from WebUI; ordinary OIDC users cannot read or mutate global authentication/channel secrets.
- [ ] AC9: Existing single-instance channel configurations and existing Personas migrate without losing connectivity or prompt identity behavior.
- [ ] AC10: Local JSON and PostgreSQL backends enforce the same ownership, assignment, Pair Code, and deletion invariants.
- [ ] AC11: Full Python/WebUI tests, strict type checks, lint, production build, and real browser desktop/mobile smoke flows pass.

## Out of Scope

- Implementing new third-party OAuth protocols beyond the existing generic OIDC, model-provider OAuth, and MCP OAuth mechanisms.
- Displaying raw channel credentials, OAuth tokens, client secrets, environment values, or absolute filesystem paths.
- Automatically pairing users from group messages.
- Reassigning historical sessions when the active bot or organization changes.

## Risks and Operational Constraints

- OIDC configuration can lock administrators out; local password/token access remains a break-glass path and saving requires successful validation.
- Channel secrets remain process configuration, while collaboration persistence stores only ownership and assignment references.
- Weixin state must be physically isolated per instance to prevent token and context-file collisions.
- PostgreSQL changes require an additive migration and matching forced-RLS policies; local JSON requires a lossless schema migration.
