---
name: xiaomi-speaker-workbuddy-bridge
description: 把任意型号小米/小爱音箱（通过 xiaomusic 容器）接入 WorkBuddy 或任意 LLM，实现"语音下任务、音箱播报反馈"。This skill should be used when the user wants to voice-enable WorkBuddy through a Xiaomi/Ai speaker, set up xiaomusic speech capture and TTS bridging, add voice commands/tasks/dialect understanding, or troubleshoot "喊了没反应"/TTS 无声/token 丢失/对话捕获 400. Supports any speaker model, any LLM with OpenAI-compatible API, and any WorkBuddy environment.
agent_created: true
---

# 小米音箱接入 WorkBuddy（通用版）

## Overview

把一台小爱音箱（**任意型号**，经 xiaomusic 容器管理）改造成 WorkBuddy 的"喉舌"：主人语音下任务，音箱播报回答，任务/对话落进工作空间。

链路三段式，理解各段归属是排障关键：

```
语音 -> [小米云端 ASR: 声音转文字，不可控] -> xiaomusic 捕获原话
     -> 口令 -> 插件 -> 桥接服务 -> [LLM: 可控] -> /playtts -> 音箱出声
```

- **听**（声音→文字）：小米云端，**改不了**（除非刷机）
- **懂**（文字→意思）：LLM + 方言表，**完全可控**
- **说**（回答→语音）：小米原生 TTS

## When to Use

- 用户要把小米/小爱音箱接入 WorkBuddy、接入任意大模型、做语音助手/语音任务
- 用户说"喊了没反应""音箱不回答""TTS 没声音""小皮/小 X 掉线了"
- 用户要配置语音口令、语音任务单、方言理解、多型号适配

## Quick Start（5 步）

### 1. 前置确认
- 目标机器跑着 xiaomusic（Docker），Web UI 能用小米账号登录且设备列表能看到音箱
- 确认语音唤醒链路：喊"小爱同学"后，xiaomusic 能捕获到问题文本（这是后续一切的前提）

### 2. 填配置
- 复制 `assets/config.example.json` 为 `config.json`（放 scripts/ 同目录或设 `XIAOPI_CONFIG`）
- `xiaomusic_url` 填 **`auto`**（默认）——IP 变了也能自适应；只有 auto 探测不到时才手填
- 填 `llm.api/model/key`（任意 OpenAI 兼容 API；密钥也可只放环境变量 `XIAOPI_LLM_KEY`）
- `did` 留空自动发现；多设备时 `device_match` 填型号/别名关键词（如 LX06）
- **口令/端口/助手名都是活的**：只改 `wake_keyword` / `port` / `assistant_name`，重跑 `deploy.py` 即全链路同步（口令注册进 xiaomusic、端口写进插件、助手名进提示），无需手改容器
- `workspace_dir` 留空 = 脚本同目录 `data/`；要落进 WorkBuddy 工作空间就填绝对路径
- 依赖：`pip install aiohttp`

### 2.5 网络自适应（不同网络环境必读）
- 先跑 `python3 scripts/netprobe.py` 看探测报告：自身 IP、网关、docker 通道、xiaomusic 地址、桥接地址候选
- **音箱 IP 不用管**：音箱通过 miotDID + 小米云端受控，我们的链路里没有音箱 IP
- 三个会变的量都由 `netprobe.py` 自动发现；`deploy.py --dry-run` 可无损预演推导结果
- 网络变化后：`curl 'http://127.0.0.1:<port>/health?reset=1'` 强制重探 xiaomusic 地址

### 3. 部署插件到 xiaomusic 容器
- 首选：`python3 scripts/deploy.py --bridge <桥接可达地址>`，自动探测 docker 通道、打补丁、重启、验证
- 找不到 docker 时手动指定：`--docker-cmd "ssh root@<网关> docker"`
- 手工方式见 `references/setup.md`

### 4. 启动桥接服务
- 前台：`python3 scripts/bridge.py`
- **在 WorkBuddy 里用受管后台方式启动**（不要用 nohup setsid，会继承受限环境导致 errno 38）；启动时清除继承来的无关敏感环境变量
- 验证：`curl http://127.0.0.1:<port>/health`
- 访问口令：首次启动自动生成脚本同目录 `.bridge_token`（权限 600），`deploy.py` 会把同一口令注入插件；从旧版升级后**必须重跑一次 `deploy.py`**，否则插件不带口令会 401

### 5. 真人验证
- 喊"小爱同学……<口令>，在吗"，确认音箱出声
- 接口返回 OK **不等于**真响了：必须看 xiaomusic 日志 `do_tts ok`（见 `references/pitfalls.md`）

## Capabilities

- **自动发现设备**：从 xiaomusic `/device_list` 取 DID，支持按型号/别名过滤，适配任意音箱
- **网络自适应（关键）**：`xiaomusic_url` 填 `auto` 即自动发现（本机/网关/docker inspect/网段多候选探测）；`deploy.py` 自动推导容器名、docker 通道、桥接可达地址。**音箱自身 IP 从不参与**——音箱只跟小米云端通信，我们只连 xiaomusic
- **任意 LLM**：OpenAI 兼容 API 即可（智谱/DeepSeek/OpenRouter/Ollama 本地），模型可换
- **口令/端口全可配（单一配置源）**：口令、桥接端口、xiaomusic 端口、助手名全在 `config.json`；`deploy.py` 自动同步口令到 xiaomusic、端口到插件，改一处全链路生效，不会两处脱节
- **访问口令保护**：`/speak` 与 `/history` 默认要求 `X-Bridge-Token`（或 `?token=`），`/health` 公开供监控；口令可来自环境变量、config 或 600 权限口令文件，不进源码、不进发布包。单机建议 `listen=127.0.0.1`
- **语音任务单**：「<口令>，记任务：xxx」写入 `tasks.md`；「查看任务」语音念概览
- **对话存档**：所有问答存档 `history.md`，WorkBuddy 可查阅
- **方言理解**：`dialect.md` 内容自动注入系统提示（只提升"懂"，不能让小米 ASR 识别纯方言）
- **即时反馈**：先播"收到，正在想"，再念 2-4 句摘要，避免干等
- **幂等部署**：补丁脚本可重复运行，改前自动备份

## 排障速查

| 症状 | 先看 | 详细 |
|------|------|------|
| 喊了没反应 | token 在不在、对话捕获是否 400 | `references/pitfalls.md` 坑①② |
| 接口 OK 但没声 | 容器日志 `do_tts ok` / `Login failed` | 坑③ |
| 桥接 health 无响应/errno 38 | 进程卡死，按 PID 重启 | 坑④ |
| 启动脚本把自己杀了 | pkill/grep 自匹配 | 坑⑤ |
| 方言听不懂 | 小米 ASR 不支持吴语，只能靠方言表提升理解 | `references/dialect.md` |

## Resources

### scripts/
- `netprobe.py` — 网络自动发现（自身IP/网关/docker通道/容器IP与端口/xiaomusic地址/桥接地址推导），可独立 CLI 运行
- `bridge.py` — 通用桥接服务（配置驱动、自动发现设备与 xiaomusic、LLM/TTS/任务/存档/方言）
- `deploy.py` — 一键部署编排（探测 docker 通道、送补丁、重启、验证）
- `patch_xiaomusic.py` — 容器内补丁（插件/口令/cookies/token 保留，幂等，自动备份）
- `restore_token.py` — 凭证丢失时从 auth.json 备份重建 `.mi.token`

### references/
- `setup.md` — 详细部署流程、任意型号适配、各环境 docker 通道
- `pitfalls.md` — 踩坑实录（含根因与修法，排障必读）
- `dialect.md` — 方言理解表用法与局限（小米 ASR 方言支持范围）

### assets/
- `config.example.json` — 配置模板（无真实密钥）
- `dialect.example.md` — 方言表模板（可选）
- `xiaopi_plugin.py` — 插件源码模板（由部署脚本生成，通常无需手动用）

## 铁律（若用户已定，遵守）

- 模型锁定类约定：若用户指定"模型固定、只有本人能改"，写入配置说明，不自行更换模型
- 危险操作（删文件/执行命令/改配置）只播报"需要回电脑确认"，不谎称已做
- 不打包任何真实 API Key、DID、账号凭证进 skill 文件
