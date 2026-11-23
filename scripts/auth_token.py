#!/usr/bin/env python3
"""桥接访问口令解析（bridge.py 与 deploy.py 共用，避免两处行为漂移）。

优先级：环境变量 XIAOPI_BRIDGE_TOKEN > config.auth.token >
        环境变量 XIAOPI_AUTH_TOKEN_FILE / config.auth.token_file 指向的文件。

文件不存在时（且调用方允许生成）创建 0600 随机口令文件，这样新安装用户
无需手工生成密钥；deploy.py 会读取同一个文件，把口令写进 xiaomusic 插件。
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def token_path(cfg: dict, base_dir: Path) -> Path:
    auth = cfg.get("auth") or {}
    raw = (
        os.environ.get("XIAOPI_AUTH_TOKEN_FILE", "").strip()
        or str(auth.get("token_file", "") or "").strip()
        or ".bridge_token"
    )
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (base_dir / p)


def resolve_token(cfg: dict, base_dir: Path, generate: bool = True, log=None) -> str:
    """返回可用的访问口令；没有任何来源且不允许生成时返回空串。"""
    env_token = os.environ.get("XIAOPI_BRIDGE_TOKEN", "").strip()
    if env_token:
        return env_token

    auth = cfg.get("auth") or {}
    inline = str(auth.get("token", "") or "").strip()
    if inline:
        return inline

    path = token_path(cfg, base_dir)
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        if generate:
            token = secrets.token_urlsafe(24)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token + "\n", encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
            msg = f"已生成桥接访问口令文件 {path}（权限 600）；部署插件时会自动同步"
            if log:
                log.info(msg)
            else:
                print("[安全] " + msg)
            return token
    except OSError as e:
        msg = f"读取/生成访问口令文件失败 {path}: {type(e).__name__}: {e}"
        if log:
            log.warning(msg)
        else:
            print("[警告] " + msg)
    return ""
