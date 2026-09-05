#!/usr/bin/env bash
# 由 systemd 的 OnFailure= 触发。
#
# OnFailure 在单元「失败」时就会触发, 而失败不只有崩溃一种: 一次例行的
# systemctl stop/restart, 若关闭耗时超过 TimeoutStopSec, systemd 会 SIGKILL
# 并把结果记为 failed —— 与崩溃循环无法区分。本脚本因此不能把「被触发」当成
# 「需要回滚」: 早期版本一上来就覆盖 selfedits, 结果在一次升级维护中把旧版本
# 的文件粘进了新安装, 包版本报新、代码却是旧的, 只有比对哈希才看得出来。
#
# 所以顺序是: 先尝试把服务拉起来并确认它真的在服务, 只有确认起不来才动状态。
set -uo pipefail
G=/root/miko-guard/good
PY=/root/.local/share/uv/tools/nanobot-ai/bin/python
SITE=/root/.local/share/uv/tools/nanobot-ai/lib/python3.13/site-packages

PORT=$($PY -c "
import json
try:
    print(json.load(open('/root/.nanobot/config.json')).get('gateway', {}).get('port', 18790))
except Exception:
    print(18790)
" 2>/dev/null || echo 18790)

# 「活着」以网关真的应答为准, 而不是 systemd 的 active —— 进程在但不服务时,
# is-active 仍然是 active, 那正是最需要回滚的情形。
gateway_healthy() {
  curl -sf --max-time 5 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1
}

# 等待至多 $1 秒。真崩溃会很快退出, 所以一旦单元停在 failed 就立即返回,
# 不必把时间耗满; 反过来启动慢也不会被误判。只认 failed: activating 与
# inactive 都可能只是启动途中的一瞬, 据此提前判死会把慢启动当成崩溃。
wait_for_gateway() {
  local deadline=$(( SECONDS + $1 ))
  while [ "$SECONDS" -lt "$deadline" ]; do
    gateway_healthy && return 0
    if [ "$(systemctl show nanobot -p ActiveState --value 2>/dev/null)" = "failed" ]; then
      return 1
    fi
    sleep 3
  done
  return 1
}

systemctl reset-failed nanobot 2>/dev/null || true
systemctl start nanobot 2>/dev/null || true

if wait_for_gateway 90; then
  logger -t miko-guard "网关在未回滚的情况下已恢复服务, 不改动任何状态(可能只是关闭超时被判为失败)"
  exit 0
fi

logger -t miko-guard "网关无法自行恢复, 开始回滚"

[ -f "$G/config.json" ] && cp -f "$G/config.json" /root/.nanobot/config.json

# 协作存储与代码必须一起回滚: 它的 schemaVersion 只升不降, 新版本迁移过之后
# 旧版本会拒绝加载。先把当前状态挪开而不是删掉, 以免回滚本身造成数据丢失。
if [ -d "$G/collaboration" ]; then
  if [ -d /root/.nanobot/collaboration ]; then
    mv /root/.nanobot/collaboration "/root/.nanobot/collaboration.rollback-$(date +%s)"
  fi
  cp -a "$G/collaboration" /root/.nanobot/collaboration
  logger -t miko-guard "已回滚协作存储"
elif [ -d /root/.nanobot/collaboration ]; then
  # known-good 时还不存在, 说明是新版本创建的; 旧版本不认识它的 schemaVersion。
  mv /root/.nanobot/collaboration "/root/.nanobot/collaboration.rollback-$(date +%s)"
  logger -t miko-guard "已移走 known-good 之后新建的协作存储"
fi

if [ -d "$G/selfedits" ]; then
  (cd "$G/selfedits" && find . -type f -name "*.py" | while read -r r; do
      cp -f "$r" "$SITE/${r#./}" 2>/dev/null || true
   done)
fi
systemctl reset-failed nanobot 2>/dev/null || true
systemctl start nanobot 2>/dev/null || true

if ! wait_for_gateway 60; then
  logger -t miko-guard "回滚后仍失败, 执行重装(将丢弃所有自改)"
  cp -a "$SITE/nanobot" /root/miko-guard/broken-$(date +%s) 2>/dev/null || true

  # 从 known-good 的 uv-receipt 还原安装来源。硬编码一份 --with 清单会与
  # receipt 漂移, 且无法表达"从 git 安装"这种来源。
  ARGS=$($PY - "$G/uv-receipt.toml" <<"PYEOF" 2>/dev/null
import sys, tomllib, shlex
try:
    with open(sys.argv[1], "rb") as fh:
        reqs = tomllib.load(fh)["tool"]["requirements"]
except Exception:
    sys.exit(1)
out, target = [], None
for r in reqs:
    spec = r.get("name", "")
    if r.get("extras"):
        spec += "[" + ",".join(r["extras"]) + "]"
    spec += r.get("specifier", "")
    if r.get("url"):
        spec = r["name"] + " @ " + r["url"]
    if r.get("name") == "nanobot-ai":
        target = spec
    else:
        out += ["--with", spec]
if not target:
    sys.exit(1)
print(shlex.join([target] + out))
PYEOF
)
  if [ -n "${ARGS:-}" ]; then
    logger -t miko-guard "按 known-good receipt 重装: ${ARGS%% *}"
    # shellcheck disable=SC2086
    PATH=/root/.local/bin:$PATH uv tool install --reinstall $ARGS >/dev/null 2>&1
  else
    logger -t miko-guard "receipt 不可用, 回退到内置清单(可能与实际来源不一致)"
    PATH=/root/.local/bin:$PATH uv tool install --reinstall nanobot-ai \
      --with "lark-oapi>=1.5.0,<2.0.0" --with "wecom-aibot-sdk-python>=0.1.5" \
      --with "qrcode[pil]>=8.0" --with "pycryptodome>=3.20.0" \
      --with "python-telegram-bot[socks,webhooks]>=22.6,<23.0" --with "socksio>=1.0.0,<2.0.0" \
      --with "python-socks[asyncio]>=2.8.0,<3.0.0" \
      --with "aiohttp>=3.9.0,<4.0.0" --with "qq-botpy>=1.2.0,<2.0.0" >/dev/null 2>&1
  fi

  # 重装后版本必须与 known-good 一致; 不一致说明来源变了(例如 git 装的被 PyPI 同名包顶替)
  WANT=$(cat "$G/version" 2>/dev/null || echo "")
  GOT=$($PY -c "import importlib.metadata as m;print(m.version(\"nanobot-ai\"))" 2>/dev/null || echo "")
  [ -n "$WANT" ] && [ "$WANT" != "$GOT" ] && \
    logger -t miko-guard "警告: 重装后版本 $GOT 与 known-good $WANT 不一致, 代码来源可能已被替换"

  systemctl reset-failed nanobot; systemctl start nanobot
fi
logger -t miko-guard "回滚流程结束, 状态: $(systemctl is-active nanobot)"
