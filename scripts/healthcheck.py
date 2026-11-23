#!/usr/bin/env python3
"""
本地自检脚本（纯标准库，无需网络、无需 xiaomusic）
====================================================
用途：部署前快速确认这个 skill 包是完整、可用、无隐私泄漏的。

用法：
    python3 scripts/healthcheck.py

检查项：
  1. 必需文件/目录齐全
  2. 所有 .py 语法可编译
  3. config.example.json 是合法 JSON 且关键字段齐全
  4. 脱敏扫描：无写死的真实 IP / token / 私人路径
  5. netprobe 基础函数可导入
  6. 发布包内不含 config.json / .bridge_token 等运行期凭证文件
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

REQUIRED = [
    "SKILL.md", "README.md", "README.en.md", "CONTRIBUTING.md", "CHANGELOG.md",
    "assets/banner.svg", "assets/config.example.json", "assets/dialect.example.md",
    "assets/xiaopi_plugin.py",
    "references/setup.md", "references/pitfalls.md", "references/dialect.md",
    "scripts/bridge.py", "scripts/deploy.py", "scripts/netprobe.py", "scripts/auth_token.py",
    "scripts/patch_xiaomusic.py", "scripts/restore_token.py", "scripts/healthcheck.py",
    ".github/ISSUE_TEMPLATE/bug_report.yml", ".github/ISSUE_TEMPLATE/feature_request.yml",
]

CONFIG_KEYS = ["xiaomusic_url", "xiaomusic_port", "port", "listen", "auth", "wake_keyword",
               "assistant_name", "owner_name", "llm"]

# 脱敏红线：这些模式不应出现在发布包里（占位符 <...> 和 x.x 形式不算）
PRIVATE_PATTERNS = [
    (re.compile(r"192\.168\.\d+\.\d+"), "真实内网 IP"),
    (re.compile(r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"), "容器网段 IP"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"(?<!<)老张(?!>)*"), "私人姓名"),
    (re.compile(r"/config/WorkBuddy/"), "宿主私有路径"),
    (re.compile(r"/mnt/sata"), "宿主挂载路径"),
]

ok = True
def report(flag: bool, msg: str):
    global ok
    print(("  OK   " if flag else "  FAIL ") + msg)
    if not flag:
        ok = False

print("=== 1. 必需文件 ===")
for rel in REQUIRED:
    report((ROOT / rel).exists(), rel)

print("\n=== 2. Python 语法 ===")
for py in sorted(ROOT.rglob("*.py")):
    if ".git" in py.parts:
        continue
    try:
        ast.parse(py.read_text(encoding="utf-8"))
        report(True, f"{py.relative_to(ROOT)}")
    except SyntaxError as e:
        report(False, f"{py.relative_to(ROOT)}: {e}")

print("\n=== 3. 配置模板 ===")
cfg_path = ROOT / "assets" / "config.example.json"
try:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    report(True, "config.example.json 是合法 JSON")
    for k in CONFIG_KEYS:
        report(k in cfg, f"字段存在: {k}")
except Exception as e:
    report(False, f"配置解析失败: {e}")

print("\n=== 4. 脱敏扫描 ===")
scan_files = [f for f in ROOT.rglob("*")
              if f.is_file() and ".git" not in f.parts
              and "__pycache__" not in f.parts
              and f != Path(__file__).resolve()  # 跳过自身，避免规则字面量自指误报
              and f.suffix in (".py", ".md", ".json", ".yml")]
leaks = []
for f in scan_files:
    text = f.read_text(encoding="utf-8", errors="ignore")
    for pat, label in PRIVATE_PATTERNS:
        for m in pat.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            leaks.append((str(f.relative_to(ROOT)), line, label, m.group()[:40]))
if leaks:
    for f, line, label, val in leaks:
        report(False, f"{f}:{line} 疑似{label}: {val}")
else:
    report(True, "未发现写死的私人信息")

print("\n=== 5. netprobe 可导入 ===")
try:
    sys.path.insert(0, str(HERE))
    import netprobe  # noqa: F401
    report(True, "netprobe 模块可导入")
except Exception as e:
    report(False, f"导入失败: {e}")

print("\n=== 6. 运行期凭证未入库 ===")
for name in ("config.json", ".bridge_token"):
    hits = [f for f in ROOT.rglob(name) if f.is_file() and ".git" not in f.parts]
    report(not hits, f"未打包 {name}" + (f"（发现 {hits[0]}）" if hits else ""))

print("\n" + "=" * 40)
if ok:
    print("自检通过：包完整、语法正常、无隐私泄漏。")
    sys.exit(0)
else:
    print("自检未通过：请按上面的 FAIL 项修复。")
    sys.exit(1)
