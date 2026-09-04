#!/usr/bin/env bash
# 仅在 systemd 判定崩溃循环(OnFailure)时触发
set -uo pipefail
G=/root/miko-guard/good
PY=/root/.local/share/uv/tools/nanobot-ai/bin/python
SITE=/root/.local/share/uv/tools/nanobot-ai/lib/python3.13/site-packages
logger -t miko-guard "检测到崩溃循环, 开始回滚"

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
sleep 20
if ! systemctl is-active --quiet nanobot; then
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
