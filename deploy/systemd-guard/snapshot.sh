#!/usr/bin/env bash
# 把当前状态标记为 known-good。仅在网关健康运行时调用。
set -euo pipefail
G=/root/miko-guard/good
PY=/root/.local/share/uv/tools/nanobot-ai/bin/python
TOOL=/root/.local/share/uv/tools/nanobot-ai
systemctl is-active --quiet nanobot || { echo "网关未运行, 跳过快照"; exit 0; }
curl -sf --max-time 5 http://127.0.0.1:18790/health >/dev/null || { echo "健康检查失败, 跳过快照"; exit 0; }

cp -f /root/.nanobot/config.json "$G/config.json"

# 安装来源。rollback 的最后手段据此重装, 否则它只会拉 PyPI 上的同名包 ——
# 若当前是从 git 装的, 版本号可能相同, 回滚会静默把代码换掉且无任何提示。
cp -f "$TOOL/uv-receipt.toml" "$G/uv-receipt.toml" 2>/dev/null || true
$PY -c 'import importlib.metadata as m;print(m.version("nanobot-ai"))' > "$G/version" 2>/dev/null || true

# 协作存储。它有自己的 schemaVersion 且只升不降, 新版本迁移过之后旧版本
# 会因 schemaVersion 不认识而拒绝启动, 所以必须与代码一起回滚。
rm -rf "$G/collaboration"
[ -d /root/.nanobot/collaboration ] && cp -a /root/.nanobot/collaboration "$G/collaboration"

# 只备份"与官方 wheel 不一致"的文件(即 agent 自改过的)，官方部分可随时重装还原
rm -rf "$G/selfedits"; mkdir -p "$G/selfedits"
$PY - "$G/selfedits" <<"PYEOF"
import importlib.metadata as m, hashlib, base64, pathlib, shutil, sys, os
dest=pathlib.Path(sys.argv[1]); d=m.distribution("nanobot-ai"); n=0
for f in d.files or []:
    if f.hash is None: continue
    p=pathlib.Path(d.locate_file(f))
    try: data=p.read_bytes()
    except Exception: continue
    h=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    if h!=f.hash.value:
        out=dest/str(f); out.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,out); n+=1
print(f"已快照 {n} 个自改文件")
PYEOF
cd /root/.nanobot/workspace && git add -A >/dev/null 2>&1 && git commit -q -m "snapshot: known-good $(date +%F_%T)" >/dev/null 2>&1 || true
date +%s > "$G/stamp"
echo "known-good 快照完成 (版本 $(cat "$G/version" 2>/dev/null || echo 未知))"
