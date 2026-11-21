#!/usr/bin/env python3
"""
token 恢复脚本：从 xiaomusic 的 auth.json 备份重建 .mi.token
=============================================================
当对话捕获全废（All retries failed / 设备列表空 / 喊了没反应）时，
多数是 .mi.token 丢了。本脚本从已有 auth.json（或备份）重建三字段 token。

用法（容器内执行）：
  docker exec -i <container> python3 - < restore_token.py

环境变量：
  XIAOPI_AUTH_BACKUPS  候选备份路径，逗号分隔
                       默认 /app/conf/auth.json.bak.workbuddy,/app/conf/auth.json.bak,/app/conf/auth.json
  XIAOPI_CONF_DIR      配置目录，默认 /app/conf
"""
import json
import os
from pathlib import Path

CONF = Path(os.environ.get("XIAOPI_CONF_DIR", "/app/conf"))
TARGET = CONF / ".mi.token"
CANDIDATES = [
    Path(p.strip()) for p in os.environ.get(
        "XIAOPI_AUTH_BACKUPS",
        "/app/conf/auth.json.bak.workbuddy,/app/conf/auth.json.bak,/app/conf/auth.json",
    ).split(",") if p.strip()
]

src = next((p for p in CANDIDATES if p.exists()), None)
if not src:
    print("!! 找不到任何 auth.json 备份。请在 xiaomusic Web UI 重新登录小米账号。")
    raise SystemExit(1)

d = json.loads(src.read_text(encoding="utf-8"))
missing = [k for k in ("passToken", "userId", "deviceId") if not d.get(k)]
if missing:
    print(f"!! 备份 {src} 缺字段 {missing}，无法重建。请重新登录。")
    raise SystemExit(1)

out = {k: d[k] for k in ("passToken", "userId", "deviceId")}
TARGET.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
print(f"已重建 {TARGET} (来源 {src}, userId={out['userId']})")
print("请重启 xiaomusic 容器，并确认日志出现 login('micoapi') 返回 True")
