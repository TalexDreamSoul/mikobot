#!/usr/bin/env bash
# 启动前校验; 发现问题就地修复, 让本次启动能成功
set -uo pipefail
G=/root/miko-guard/good
PY=/root/.local/share/uv/tools/nanobot-ai/bin/python
SITE=/root/.local/share/uv/tools/nanobot-ai/lib/python3.13/site-packages

# 1) config.json 必须是合法 JSON
if ! $PY -c "import json;json.load(open(\"/root/.nanobot/config.json\"))" 2>/dev/null; then
  if [ -f "$G/config.json" ]; then
    cp -f "$G/config.json" /root/.nanobot/config.json
    logger -t miko-guard "config.json 非法, 已回滚到 known-good"
  fi
fi

# 2) agent 自改过的文件必须能编译
if [ -d "$G/selfedits" ]; then
  find "$SITE/nanobot" -name "*.py" -newer "$G/stamp" 2>/dev/null | while read -r f; do
    if ! $PY -m py_compile "$f" 2>/dev/null; then
      rel="${f#$SITE/}"
      if [ -f "$G/selfedits/$rel" ]; then
        cp -f "$G/selfedits/$rel" "$f"; logger -t miko-guard "回滚语法错误文件: $rel"
      else
        rm -f "$f"; logger -t miko-guard "删除损坏的新增文件: $rel"
      fi
    fi
  done
fi
exit 0
