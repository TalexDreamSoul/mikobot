# Personal Assistant Vaults

The personal-assistant layer separates **identity** (Persona) from the security boundary (**Vault**).

## Privacy model

- Every durable personal object belongs to one `owner_user_id` and one `vault_id`.
- Session keys are vault-scoped. Memory, session search, cross-session messages, and inbound media reject cross-vault access.
- A Persona selects a default Vault for the owner's conversations. Personas do not grant access themselves.
- Sharing is explicit, revocable, and defaults to read-only. A task-specific grant never exposes the whole Vault.

## WebUI

Open **Projects → My day** to create Vaults, create/select assistant identities, and manage private tasks.

The endpoint `GET /api/personal` returns only the authenticated user's Vaults, Personas, and tasks. Mutations require an authenticated WebSocket session.

## Task calendar export

`GET /api/personal/ics` returns a JSON envelope containing an RFC 5545 iCalendar export. Only confirmed tasks are emitted as `VTODO`; proposed tasks remain in the private inbox until confirmed.

## Feishu recordings

Feishu audio is transcribed by the existing channel ingress. An authorized transcript is copied to an append-only recording journal under its owner/Vault runtime root. A daily digest must use that Vault-specific journal only.

To enable cross-chat collection with Lark CLI, configure an authenticated `lark-cli` installation and explicit delivery/scheduling settings. Nanobot does not install or discover external credentials automatically.

## Timark sync

Timark is the separate local Worker at `/Users/tagzixian/Workspace/Projects/timark`. Its confirmed schedule API requires a running Worker and a Management Token. The integration must use `POST /api/v1/schedules`, then `POST /api/v1/schedules/:id/confirm`, respecting returned `ETag` values for mutations. Store its token outside the repository and never put it in task records, logs, or prompts.
