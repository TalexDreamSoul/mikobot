# Member-scoped claimable channels

## Goal

Give an ordinary member a tenant-scoped way to see the channel instances they may claim, so the self-service connect → claim → assign flow works again without the host-wide inventory that closing the `nanobot-features` leak removed.

## User Value

- A member who connects their own WeChat or Feishu account can still find that instance and attach it to their bot.
- A member sees only instances they are entitled to claim, instead of every tenant's instances on the host.
- The bot management screen states why a list is empty rather than silently showing nothing.

## Confirmed Facts

- `webui/src/components/projects/BotManagementPanel.tsx:86` calls `fetchNanobotFeatures`, filters `type === "channel"`, and at `:147-165` flattens every package's `instances` into `{channelType, instanceId, label, status}` options for the claim dropdown.
- That inventory is host-wide. Before `09-03-tenant-authorization-gaps` closed it, the dropdown listed **every tenant's** channel instances to **every** member — the enumeration leak itself, not merely a side effect of it.
- After the fix, `/api/settings/nanobot-features` returns no inventory to a non-administrator, so `channelOptions` is empty and the claim flow has no selectable instance.
- No tenant-scoped substitute exists today. `nanobot/collaboration/store.py:1053` `list_bot_channels(actor_user_id, bot_id)` and `:1071` `list_bot_project_channels` both list channels **already assigned to a bot**, which is the wrong end of the flow: the member needs instances that are not yet claimed.
- Self-service connect does not record who created an instance. `nanobot/channels/weixin/connect.py:60-65` branches on `mode == "create"` but persists no creator, so "instances this member created" is not currently answerable from stored state.
- The connect session does know the actor: `weixin/connect.py:173` `_require_session_actor` rejects operating another user's connect session. Creator identity exists in the session but is dropped at persistence time.
- `nanobot/webui/ws_http.py:991-1000` deliberately elevates a non-administrator for `mode=create` connect. That product decision — members provision their own instances — is what makes the missing ownership record a gap rather than a non-feature.
- Claims are keyed `(channel_type, instance_id)` and are globally unique; `nanobot/collaboration/store.py:1982-1998` exposes only a `channel_claimed` boolean for the "is this taken by anyone" question, deliberately not the claimant.

## Requirements

### R1 — Record who provisioned an instance

- A channel instance created through self-service connect records the authenticated actor that created it, at the moment it is persisted.
- The record lives in collaboration persistence alongside the existing claim data, not in the channel config file, which is host-owned and secret-bearing.
- Instances that predate this record have no creator. They must not become claimable by an arbitrary member as a side effect; unattributed instances stay administrator-only.
- Local JSON and PostgreSQL backends carry the same field and the same visibility rule.

### R2 — A query that answers only the member's own question

- Add a listing that returns the instances one authenticated member may claim: created by them, not yet claimed, and within an organization they belong to.
- It must never return another member's instances, unattributed legacy instances, or already-claimed instances.
- The payload carries only what the dropdown needs — channel type, instance id, a display label, and runtime status. No config values, no credentials, no host paths, no revisions.
- A system administrator keeps a way to see the full inventory; this requirement narrows the member view, not the administrator view.

### R3 — Bot management reads the member-scoped source

- `BotManagementPanel` builds its claim options from the new listing instead of `fetchNanobotFeatures`.
- An empty list is distinguishable from an unauthorized or failed one, and the screen says which it is.
- English and Simplified Chinese, desktop and mobile.

### R4 — The closed leak stays closed

- This task must not reopen host inventory to members as a shortcut. The new listing is additive and independently authorized.
- A regression test proves a member still cannot enumerate another tenant's instances through either endpoint.

## Acceptance Criteria

- [ ] AC1: A member who provisions an instance through self-service connect sees exactly that instance in the claim dropdown, and no other member's instance appears.
- [ ] AC2: A second member in the same organization sees an empty claim list for the first member's unclaimed instance.
- [ ] AC3: Instances created before the ownership record exists are not offered to any member.
- [ ] AC4: An already-claimed instance disappears from the claim list.
- [ ] AC5: The claim payload contains no config values, credentials, host paths, or extension revisions.
- [ ] AC6: `/api/settings/nanobot-features` still returns no inventory to a non-administrator after this task.
- [ ] AC7: Bot management distinguishes "nothing to claim" from "not permitted" and from "request failed", in English and Simplified Chinese.
- [ ] AC8: Local JSON and PostgreSQL backends enforce the same ownership and visibility invariants.
- [ ] AC9: Full `pytest` (both `testpaths`), `ruff`, `basedpyright`, and WebUI tests pass.

## Out of Scope

- Transferring instance ownership between members, or an administrator reassigning a creator.
- Backfilling creators for existing instances. There is no evidence to backfill from; they stay administrator-only.
- Reworking the Pair Code claim mechanism itself, which already validates ownership server-side.
- Reopening `nanobot-features` read scope in any form.

## Key Decisions

- The dropdown breakage is accepted rather than deferred. Leaving the host inventory member-readable until a replacement existed would have kept a verified cross-tenant enumeration open, and isolation is the stated bottom line.
- Creator identity is recorded at provisioning time rather than inferred later. Inference would have to guess from config mtime or connect logs, which is exactly the kind of unattributed guess that made the shared media directory unmigratable.
- Unattributed legacy instances fail closed. Making them claimable by whoever asks first would turn a UI gap into a takeover path.

## Risks and Operational Constraints

- Between `09-03-tenant-authorization-gaps` shipping and this task landing, members cannot claim instances through the WebUI. An administrator can still assign channels, so the flow is degraded, not absent. Release notes should say so.
- Adding a field to collaboration persistence needs an additive local migration and a matching PostgreSQL migration with RLS policy and grant in the same change, per `.trellis/spec/backend/database-guidelines.md`.
