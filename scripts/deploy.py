#!/usr/bin/env python3
"""
部署编排脚本（在 WorkBuddy 容器/本机运行）
==========================================
自动探测 docker 通道（本地 docker / ssh 网关 / ssh NAS），把补丁脚本送进
xiaomusic 容器执行，重启并验证。适配任意音箱型号、任意容器名/IP。

用法（口令/端口/助手名默认从 config.json 读取，改配置后直接重跑即可同步）：
  python3 deploy.py --dry-run                        # 无损预演
  python3 deploy.py                                  # 正式部署
  python3 deploy.py --wake <新口令> --bridge-port <新端口>   # 命令行临时覆盖

  若自动探测不到 docker，手动指定通道：
  python3 deploy.py --docker-cmd "ssh root@172.17.0.1 docker" ...

优先级：命令行 > 环境变量(XIAOPI_*) > config.json > 内置默认。
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCH_SCRIPT = HERE / "patch_xiaomusic.py"
CONFIG_PATH = Path(os.environ.get("XIAOPI_CONFIG", str(HERE / "config.json")))

# 网络自动发现（可选导入）
try:
    from netprobe import detect_docker as _nb_detect, find_container, suggest_bridge_urls
except ImportError:
    _nb_detect = None


def load_cfg() -> dict:
    """读取 config.json 作为唯一配置源：口令/端口/助手名改这里，部署自动同步到插件。
    返回空 dict 表示没有配置文件（用内置默认值）。"""
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[警告] 读取 {CONFIG_PATH} 失败: {e}；将用内置默认值")
    return {}


def run(cmd, capture=True, check=False, stdin_data=None):
    """执行命令，返回 (returncode, stdout, stderr)"""
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    p = subprocess.run(cmd, input=stdin_data, text=True, capture_output=capture)
    if check and p.returncode != 0:
        raise RuntimeError(f"命令失败 {' '.join(cmd)}: {p.stderr[:200]}")
    return p.returncode, p.stdout, p.stderr


def detect_docker(explicit: str | None) -> list[str] | None:
    """返回可用的 docker 命令前缀。优先用 netprobe 的探测，回退到本地硬探测。"""
    if explicit:
        return shlex.split(explicit)
    if _nb_detect:
        return _nb_detect(None)
    for c in (["docker"],
              ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", "root@172.17.0.1", "docker"]):
        try:
            rc, out, err = run(c + ["ps", "--format", "{{.Names}}"], capture=True)
            if rc == 0 and err.strip() == "":
                print(f"[探测] docker 通道可用: {' '.join(c)}")
                return c
        except Exception:
            continue
    return None


def main():
    cfg = load_cfg()
    # 默认值优先级：环境变量 > config.json > 内置默认；命令行显式指定时最高（argparse 覆盖）
    def cv(key, fallback):
        return cfg.get(key, fallback)

    ap = argparse.ArgumentParser()
    ap.add_argument("--docker-cmd", help="docker 命令前缀，如 'ssh root@172.17.0.1 docker'")
    ap.add_argument("--container", default=os.environ.get("XIAOPI_CONTAINER", cv("container", "xiaomusic")))
    ap.add_argument("--bridge", default=os.environ.get("XIAOPI_BRIDGE_URL", ""),
                    help="写入 xiaomusic 插件的桥接地址；留空=自动推导容器内可达地址")
    ap.add_argument("--bridge-port", type=int,
                    default=int(os.environ.get("XIAOPI_PORT",
                                               str(cfg.get("port", 8900)))))
    ap.add_argument("--wake", default=os.environ.get("XIAOPI_WAKE", cv("wake_keyword", "小皮小皮")),
                    help="语音口令；不传=用 config.json 的 wake_keyword")
    ap.add_argument("--plugin", default=os.environ.get("XIAOPI_PLUGIN", cv("plugin", "xiaopi")))
    ap.add_argument("--name", default=os.environ.get("XIAOPI_NAME", cv("assistant_name", "小皮")),
                    help="助手名（插件失败提示用）；不传=用 config.json 的 assistant_name")
    ap.add_argument("--app-dir", default=os.environ.get("XIAOPI_APP_DIR", "/app"))
    ap.add_argument("--skip-restart", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只探测和推导，不执行打补丁/重启")
    args = ap.parse_args()

    if cfg:
        print(f"[配置] 从 {CONFIG_PATH.name} 读取：口令={args.wake!r} 端口={args.bridge_port} 助手名={args.name!r}")

    docker = detect_docker(args.docker_cmd)
    if not docker:
        print("!! 找不到可用的 docker 通道，请用 --docker-cmd 指定")
        sys.exit(1)

    name = find_container(docker, "xiaomusic") if find_container else args.container
    rc, out, err = run(docker + ["ps", "--format", "{{.Names}}"])
    if name not in out.split():
        print(f"!! 容器 {name} 不存在。现有容器: {out.strip()}")
        sys.exit(1)
    print(f"[1/4] 容器 {name} 在线（自动发现）")

    # 自动推导 BRIDGE_URL（xiaomusic 容器内可达的桥接地址）
    bridge = args.bridge.strip()
    if not bridge and suggest_bridge_urls:
        candidates = suggest_bridge_urls(args.bridge_port, docker, name)
        bridge = candidates[0] if candidates else ""
        print(f"[自动] 桥接地址推导: {bridge}")
        print(f"       其他候选（若不通可手动指定 --bridge）: {candidates[1:]}")
    if not bridge:
        print("!! 无法推导桥接地址，请用 --bridge 手动指定")
        sys.exit(1)

    if args.dry_run:
        print("[dry-run] 探测完成，未做任何修改。推导结果:")
        print(f"  docker 通道 : {' '.join(docker)}")
        print(f"  容器        : {name}")
        print(f"  桥接地址    : {bridge}")
        print(f"  桥接端口    : {args.bridge_port}")
        print(f"  语音口令    : {args.wake}")
        print(f"  助手名      : {args.name}")
        return

    # 传补丁脚本到容器 stdin 执行
    patch_src = PATCH_SCRIPT.read_text(encoding="utf-8")
    env_prefix = (
        f"XIAOPI_BRIDGE_URL={shlex.quote(bridge)} "
        f"XIAOPI_WAKE={shlex.quote(args.wake)} "
        f"XIAOPI_NAME={shlex.quote(args.name)} "
        f"XIAOPI_PLUGIN={shlex.quote(args.plugin)} "
        f"XIAOPI_APP_DIR={shlex.quote(args.app_dir)} "
    )
    # 用 sh -c 把环境变量 export 后交给 python3 -
    exec_cmd = docker + ["exec", "-i", name, "sh", "-c",
                         f"{env_prefix}exec python3 -"]
    rc, out, err = run(exec_cmd, stdin_data=patch_src)
    print("[2/4] 容器内补丁执行结果:")
    print(out.strip() or "(空)")
    if err.strip():
        print("stderr:", err.strip()[:500])

    if not args.skip_restart:
        print("[3/4] 重启容器 ...")
        run(docker + ["restart", name])
        time.sleep(12)
        print("[4/4] 重启后检查 login 状态:")
        rc, out, err = run(docker + ["logs", "--since", "20s", name])
        for line in out.splitlines():
            if any(k in line.lower() for k in ("login", "token", "device", "error", "xiaopi")):
                print("  ", line[:180])
    print("完成。下一步：启动 bridge.py，然后真人喊一句验证。")


if __name__ == "__main__":
    main()
