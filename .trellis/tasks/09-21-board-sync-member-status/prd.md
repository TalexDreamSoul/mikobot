# 项目看板同步与成员可见状态

## Goal

把「老板在任意渠道加的任务」落到**项目看板**（WebUI 项目页可见），并顺手收口三处既有的边界/体验缺陷：
成员会话看不到自己渠道实例的状态、成员记忆相对路径落错目录、`search_sessions` 在授权前就读了别的会话正文。

## Background（线上证据，2026-09-21，GG `nanobot-ai 0.7.3`）

- **任务没进项目**：微信私聊 `weixin.wechat-cea490:o9cq800…` 说「加个任务 明天早上十点提醒我 root」，
  机器人用 `apply_patch` 写进了自己的 `memory/TASKS.md`（20:06-20:07 日志）；三个项目看板 `tasks` 全为 0。
  根因是**约定**而非代码：`/root/.nanobot/workspace/AGENTS.md` 写「`memory/TASKS.md` —— 任务台账，唯一事实来源」，
  而项目看板（`ProjectTask` + `projects` 工具的 `create_task` / WebUI `项目管理 → 任务` 面板）从未被写入。
- **能力本身可用**（实测，用安装包真实代码 + 真实 store）：
  - 微信 DM 作用域（`kind=bound` → Tuff）`create_task` 不传 `project_id` → 落在 Tuff ✓；
  - 群会话作用域（`kind=isolated`，无项目）显式传 `project_id` → 落在 QingQi ✓，随后删除，无残留 ✓；
  - 前端 `webui/src/lib/api.ts:564` → `GET /api/collaboration/projects/<id>`（`webui/ws_http.py:1391` 处理器返回
    `tasks: [project_task_payload(...)]`），面板按 `todo/doing/done` 三列渲染（`ProjectTasksPanel.tsx:41`）。
- **新群**：`Handoff`（`oc_07e9b450…`）已解散且机器人不在其中（API `230002`）；新群 **MikoGroup**
  `oc_852926c7a63135fc7f1d2986ffcb14d7`，机器人 `Miko`（app `cli_aa1e62…`，`ou_900edf75…`）**已在群内**（`GET /im/v1/chats` 列出）。
  群会话按设计是 `isolated`（绑定群到项目会让非成员（吕姐）的回合被 `route_denied` 直接拒绝，故**不绑定**）。

## 决策记录（2026-09-21，用户确认「这些你都处理吧」）

1. **双写**：`memory/TASKS.md` 仍是本地台账；**每条任务同时**用 `projects create_task` 落到项目看板，
   状态变化用 `update_task` 同步；群内指派的任务显式传该群项目的 `project_id`（青奇协作群 → QingQi `col_612635bb…`）。
   不绑定群（见上：绑定会拒绝非成员）。
2. **存量回填**：按台账标题一次性回填（幂等），落在老板默认项目 Tuff（`col_90e7865d…`）。
3. **成员可见状态**：`my` 工具新增只读 `channels` 键，**成员只见自己那一个渠道实例**，字段仅
   `enabled/running/state/instance_id`（不含 `owner`/`error`）。
4. **成员记忆相对路径**：不改解析语义（共享工作区仍是 cwd），在渲染的身份段里写清：
   相对路径（含 `memory/…`）落在项目工作区，私有 profile 只能按绝对路径访问。
5. **搜索收敛**：`WebuiSessionAccess.search` 增加调用方授权谓词，**授权前不读**任何会话正文；工具层去掉重复过滤。

## 交付物与验收

| 项 | 验收方式 |
|---|---|
| 看板回填 | `list_tasks(Tuff)` = 52 条；二次运行 `created=0 skipped=52`（幂等） |
| 默认项目路径 | 真实 `ProjectsTool` + 微信 DM 作用域：不传 `project_id` 落 Tuff ✓；探针任务随后删除，计数回到 52 |
| 群任务路径 | 真实 `ProjectsTool` + isolated 群作用域 + 显式 `project_id`：创建成功 ✓、随后删除 ✓ |
| 约定落地 | `AGENTS.md` / `SOUL.md` 双写规则（备份 `/root/miko-guard/persona-backup-20260921_204550`） |
| 成员可见状态 | 新增键 + 单元测试：成员只见自身实例、仅四个字段；宿主全见；缺自身实例返回 `{}` |
| 记忆相对路径 | 模板分支内显式说明；渲染测试不回归 |
| 搜索收敛 | 单元测试：未授权会话即使命中也不出现在结果里，且**其文件从未被读取**；授权结果不变 |

## 非目标

- 不把成员回合的 cwd/记忆切到项目（沿用 `09-21-session-privacy-enforcement` 决策 1 的边界）。
- 不为群会话自动绑定项目（会拒绝非成员发送者）。
- 不改 `TASKS.md` / `JOURNAL.md` 的既有日程仪式（08:00 排期、21:30 对账）。
