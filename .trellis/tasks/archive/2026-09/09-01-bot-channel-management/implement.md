# Bot and channel management — implementation plan

## Ordered implementation

1. **Localization and shared application scope**
   - Add `projects`, `bots`, `organizations`, `pairing`, and `loginSecurity` English/zh-CN keys.
   - Replace project-page JSX literals with `useTranslation` calls.
   - Lift collaboration summary/selection from `ProjectsView` into an application-level provider.
   - Add the organization footer switcher and header bot-switcher shells behind real API data.

2. **Collaboration domain and migrations**
   - Add Bot, bot-project, bot-channel, project-channel, capability-profile, and pairing-challenge models/repository methods.
   - Add user default organization/bot preferences.
   - Implement local JSON migration and invariant validation.
   - Add PostgreSQL migration, row decoders, CRUD mixins, grants, forced RLS, constraints, and concurrency checks.
   - Keep local and PostgreSQL observable behavior identical.

3. **Pair-Code operation proofs**
   - Add purpose-bound challenge creation/poll/consume APIs.
   - Intercept assignment codes in private channel ingress with exact runtime-instance metadata.
   - Atomically claim channels and assign bots/projects only after verified challenges.
   - Preserve existing sender-access Pair Codes.

4. **Weixin multi-instance cutover**
   - Add instance config helpers and management callbacks matching the generic contract.
   - Make state resolution and QR connect sessions instance-aware.
   - Migrate legacy single config/state to `default`.
   - Add multiple runtime lifecycle/status support and collision checks.

5. **Authorization and settings APIs**
   - Add system-admin resolution for local owner and configured OIDC admin subjects.
   - Split safe user-scoped channel projections from global secret-bearing settings mutations.
   - Add OIDC settings payload, masked secret update semantics, discovery validation, and restart-required response.
   - Add bot/channel/assignment/capability/Skill metadata endpoints and mutations.

6. **WebUI management surfaces**
   - Complete sidebar organization and bot switchers for desktop, collapsed, and mobile layouts.
   - Add bot list/detail/editor and project bot-assignment/channel-route UI.
   - Add user-scoped multi-instance channel management with staged/claimed/assigned states.
   - Add Pair Code modal/polling flow for channel claims and bot-project assignment.
   - Add Login & Security settings and safe Skill/MCP inspection.

7. **Verification and release**
   - Run focused migration, RLS, authorization, Pair Code, Weixin, settings, and UI tests.
   - Run complete Python suite, BasedPyright, Ruff, WebUI tests, ESLint, and production build.
   - Browser-drive Chinese desktop and mobile flows: organization switch, bot switch, OIDC settings validation, two Weixin instances, pair/claim, bot-project/channel assignment, and Skill inspection.
   - Rebuild the persistent local service, commit, push, and publish a new patch release only after all checks pass.

## Validation commands

```bash
uv run --with pytest --with pytest-asyncio pytest -q
uv run --with basedpyright basedpyright
uv run --with ruff ruff check nanobot tests
cd webui && bun run test
cd webui && bun run lint
cd webui && bun run build
```

PostgreSQL tests additionally run against separate runtime and migration DSNs with a real PostgreSQL server.

## High-risk boundaries

- `nanobot/collaboration/postgres/ddl.py`: migration idempotency, RLS, grants, and concurrent assignment invariants.
- `nanobot/collaboration/store.py`: lossless JSON migration and cross-reference validation.
- `nanobot/channels/base.py` and `nanobot/channels/manager.py`: Pair Code interception must not leak codes to groups or bypass existing access policy.
- `nanobot/channels/weixin/`: token/state isolation across instances.
- `nanobot/webui/ws_http.py` and settings routes: system-admin versus user-scoped authorization and secret redaction.
- `webui/src/App.tsx` / Sidebar / collaboration provider: selection changes must not rewrite historical sessions.

## Rollback points

- Each persistence migration is additive and transactional before frontend adoption.
- Existing channel credentials remain untouched when ownership/assignment setup fails.
- OIDC updates persist only after full model and discovery validation.
- UI switchers are not enabled until preference and authorization APIs are available.
