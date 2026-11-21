#!/usr/bin/env python3
"""
网络自动发现模块 (netprobe)
============================
解决"每个网络环境不一样、IP 各不相同"的问题。

核心认知：
- 音箱自身 IP **从不参与**——xiaomusic 通过小米云端 + miotDID 控制音箱，
  我们只跟 xiaomusic 打交道，因此无需知道音箱 IP。
- 真正随环境变化、需要自动发现的是三样：
  1. xiaomusic 地址（WorkBuddy -> xiaomusic）
  2. 桥接可达地址（xiaomusic 容器 -> WorkBuddy 桥接，即 BRIDGE_URL）
  3. docker 通道（本地 docker / ssh 网关 / ssh NAS）

本模块全部用标准库实现，无第三方依赖。既可被 bridge.py 导入，也可独立 CLI 运行：

  python3 netprobe.py                          # 打印探测报告
  python3 netprobe.py --container xiaomusic     # 指定容器名
  python3 netprobe.py --docker-cmd "ssh root@172.17.0.1 docker"
"""
import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_PORT = 58090
HEALTH_PATH = "/device_list"   # 用它验证 xiaomusic：返回 JSON 且含 devices 即成立


def _run(cmd: list[str] | str, timeout: int = 6):
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)


# ---------- 自身网络 ----------
def self_ip() -> str:
    """取本机在默认路由网段的 IP（UDP connect 不真的发包）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def gateway_ip() -> str:
    """读 /proc/net/route 取默认网关（Linux）；失败返回网段首地址"""
    try:
        for line in open("/proc/net/route", encoding="utf-8"):
            parts = line.split()
            if len(parts) > 2 and parts[1] == "00000000":
                g = parts[2]
                return socket.inet_ntoa(bytes.fromhex(g)[::-1])
    except Exception:
        pass
    ip = self_ip()
    parts = ip.split(".")
    return ".".join(parts[:3] + ["1"]) if len(parts) == 4 else "172.17.0.1"


# ---------- docker 通道 ----------
def detect_docker(explicit: str | None = None) -> list[str] | None:
    """探测可用的 docker 命令前缀：显式 > 本地 > ssh 网关 > ssh 常见 NAS"""
    if explicit:
        c = shlex.split(explicit)
        rc, _, _ = _run(c + ["ps"])
        return c if rc == 0 else None
    candidates = [
        ["docker"],
        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", "root@172.17.0.1", "docker"],
    ]
    # 可选：通过环境变量指定额外 SSH 目标（如某台 NAS），避免写死具体 IP
    extra = os.environ.get("XIAOPI_SSH_TARGET", "").strip()
    if extra:
        candidates.append(["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", f"root@{extra}", "docker"])
    for c in candidates:
        rc, out, err = _run(c + ["ps", "--format", "{{.Names}}"], timeout=6)
        if rc == 0 and not err.strip():
            return c
    return None


def find_container(docker: list[str], keyword: str = "xiaomusic") -> str | None:
    rc, out, _ = _run(docker + ["ps", "--format", "{{.Names}}"])
    names = [n for n in out.split() if n]
    if keyword in names:
        return keyword
    for n in names:
        if keyword.lower() in n.lower():
            return n
    return None


def container_net(docker: list[str], name: str) -> dict:
    """docker inspect 拿容器 IP 与端口映射（用 JSON 解析，避免 Go template 经 ssh 被 shell 展开）"""
    info = {"ip": "", "ports": {}, "name": name}
    rc, out, _ = _run(docker + ["inspect", name])
    if rc != 0 or not out.strip():
        return info
    try:
        data = json.loads(out)[0]
        nets = data.get("NetworkSettings", {}).get("Networks", {})
        for v in nets.values():
            if v.get("IPAddress"):
                info["ip"] = v["IPAddress"]
                break
        pb = data.get("HostConfig", {}).get("PortBindings", {}) or {}
        for container_port, binds in pb.items():
            host_port = binds[0]["HostPort"] if binds else ""
            info["ports"][container_port.split("/")[0]] = host_port
    except Exception:
        pass
    return info


# ---------- xiaomusic 探测 ----------
def probe_xiaomusic(base_url: str, timeout: float = 2.5) -> str | None:
    """返回可用 base_url 或 None。判定：GET /device_list 返回 JSON 且含 devices"""
    url = base_url.rstrip("/") + HEALTH_PATH
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "netprobe"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
            if isinstance(data, dict) and "devices" in data:
                return base_url.rstrip("/")
    except Exception:
        return None


def discover_xiaomusic(
    explicit: str | None = None,
    docker: list[str] | None = None,
    container: str | None = None,
    port: int = DEFAULT_PORT,
) -> dict:
    """生成候选地址列表并并发探测，返回第一个可用结果 + 全部候选诊断"""
    candidates = []
    if explicit:
        candidates.append(explicit.rstrip("/"))

    # 1) 通过 docker inspect 精确拿容器 IP 与端口映射（最可靠）
    ip, host_port = "", ""
    if docker:
        name = container or find_container(docker, "xiaomusic")
        if name:
            net = container_net(docker, name)
            ip = net["ip"]
            host_port = net["ports"].get(str(8090), "") or net["ports"].get(str(port), "")
            candidates.append(f"http://{ip}:8090")           # 容器内网直连

    # 2) 本机 / 网关
    candidates += [f"http://127.0.0.1:{port}", f"http://127.0.0.1:{host_port}"] if host_port else [f"http://127.0.0.1:{port}"]
    gw = gateway_ip()
    candidates.append(f"http://{gw}:{host_port or port}")     # 网关=宿主机
    candidates.append(f"http://{self_ip()}:{host_port or port}")

    # 去重保序
    seen, ordered = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)

    found = None
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(probe_xiaomusic, c): c for c in ordered}
        for fut in as_completed(futs):
            r = fut.result()
            if r and not found:
                found = r
    return {"found": found, "candidates": ordered, "container_ip": ip, "host_port": host_port}


# ---------- 桥接可达地址推导 ----------
def suggest_bridge_urls(listen_port: int = 8900, docker: list[str] | None = None,
                        container: str | None = None) -> list[str]:
    """推导"xiaomusic 容器内能访问 WorkBuddy 桥接"的地址候选（优先级从高到低）"""
    urls = []
    # 同宿主机容器间：用 WorkBuddy 容器 IP（docker 默认网段 172.17.x.x）
    me = self_ip()
    if me and not me.startswith("127."):
        urls.append(f"http://{me}:{listen_port}/speak")
    # 若 xiaomusic 与 WorkBuddy 在同一 docker 网，容器 IP 直连最稳
    if docker:
        name = container or find_container(docker, "xiaomusic")
        if name:
            net = container_net(docker, name)
            if net["ip"]:
                seg = ".".join(net["ip"].split(".")[:3])
                urls.append(f"http://{seg}.1:{listen_port}/speak")  # 网关侧
    urls.append(f"http://127.0.0.1:{listen_port}/speak")            # 同容器兜底
    out, s = [], set()
    for u in urls:
        if u not in s:
            s.add(u)
            out.append(u)
    return out


def report(args):
    print("=== 网络自动发现报告 ===")
    print(f"自身 IP      : {self_ip()}")
    print(f"默认网关     : {gateway_ip()}")
    d = detect_docker(args.docker_cmd)
    print(f"docker 通道  : {' '.join(d) if d else '未发现'}")
    container = args.container or (find_container(d, "xiaomusic") if d else None)
    print(f"xiaomusic容器: {container or '未发现'}")
    if d and container:
        net = container_net(d, container)
        print(f"  容器IP      : {net['ip']}")
        print(f"  端口映射    : {net['ports']}")
    res = discover_xiaomusic(args.xiaomusic_url, d, container, args.port)
    print(f"xiaomusic地址: {res['found'] or '未发现'}")
    print(f"  候选探测    : {', '.join(res['candidates'])}")
    print("桥接可达地址候选（写入 xiaomusic 插件的 BRIDGE_URL）:")
    for u in suggest_bridge_urls(args.bridge_port, d, container):
        print("  -", u)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--container", default=None)
    ap.add_argument("--docker-cmd", default=None)
    ap.add_argument("--xiaomusic-url", default=os.environ.get("XIAOMUSIC_URL"))
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--bridge-port", type=int, default=8900)
    report(ap.parse_args())
