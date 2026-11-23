# 详细部署流程与任意型号适配

## 1. 环境探查（先做，别急着改）

**先跑自动发现**（无需手填任何 IP）：

```bash
python3 scripts/netprobe.py
# 指定 docker 通道时：
python3 scripts/netprobe.py --docker-cmd "ssh root@<网关> docker"
```

报告会给出：自身 IP、默认网关、docker 通道、xiaomusic 容器 IP 与端口映射、xiaomusic 可达地址、桥接地址候选。

### 关键认知：音箱 IP 从不参与

音箱通过 **miotDID + 小米云端**受控，xiaomusic 也是通过小米云端 API 下发指令。
我们的链路里**没有任何一步直连音箱 IP**，所以音箱 IP 变化、换网络、换路由器都不影响本方案。
真正随环境变化、需要"活"的只有三个量，全部由 `netprobe.py` 自动发现：

| 会变的量 | 自动发现方式 |
|----------|--------------|
| xiaomusic 地址 | 本机 127.0.0.1 → 网关 → docker inspect 容器 IP:端口 → 网段并发探测，`/device_list` 返回含 devices 即命中 |
| 桥接可达地址（写入插件的 BRIDGE_URL） | 取 WorkBuddy 自身 IP（容器内网直连优先）+ 端口，给出候选 |
| docker 通道 | 本地 docker → ssh 网关 → ssh 常见 NAS，自动选通的那个 |

`deploy.py --dry-run` 可无损预演以上推导，不修改任何东西。

## 2. 前置：语音捕获必须通

补丁只解决"捕获到文本之后"的事。若喊完 xiaomusic 连问题文本都抓不到，先修小米账号：
1. 用浏览器打开 `http://<xiaomusic-host>:<port>`（**必须 http，不是 https**，否则 SSL 报错）
2. 用小爱账号登录，确认设备列表能看到目标音箱、状态 online
3. 看容器日志有没有 `login('micoapi') 返回 True`

> LX06 等老型号不支持 mina 对话接口（`get_ask_by_mina=True` 返回 code 900），必须走对话历史轮询（`get_ask_by_mina=False`）。

## 3. 配置解释（config.json）

| 字段 | 作用 | 示例 |
|------|------|------|
| `xiaomusic_url` | xiaomusic 地址；`auto`/留空=自动发现 | `auto`（推荐）|
| `xiaomusic_port` | auto 探测时的 xiaomusic 端口（换了宿主端口才改） | `58090` |
| `did` | 音箱 miotDID；留空自动发现 | `""` 或 `"123456789"`（示例，实际请自动发现） |
| `device_match` | 多台设备时按型号/别名筛选 | `"LX06"` / `"小爱音箱Pro"` |
| `llm.api` | OpenAI 兼容端点 | 智谱/DeepSeek/OpenRouter/Ollama |
| `llm.model` | 模型名 | `glm-4-flash` / `qwen2.5:7b` |
| `llm.key` | 密钥；可留空改用环境变量 | `XIAOPI_LLM_KEY` |
| `port` | 桥接监听端口 | `8900` |
| `listen` | 桥接监听地址；单机填 `127.0.0.1`，跨容器/局域网才填 `0.0.0.0` | `0.0.0.0` |
| `auth.required` | 是否启用访问口令（默认开启） | `true` |
| `auth.token_file` | 口令文件（相对脚本目录）；不存在时首次启动自动生成 0600 随机口令 | `.bridge_token` |
| `assistant_name` / `owner_name` | 助手名 / 主人称呼 | 写进系统提示 + 插件失败提示 |
| `wake_keyword` | 语音口令 | `小皮小皮` |
| `workspace_dir` | 任务单/存档落点 | 留空用 `data/` |
| `dialect_file` | 方言表名（相对脚本目录） | `dialect.md` |
| `history_in_tmp` | 历史写 /tmp（规避挂载 FS append 异常） | `true` |

### 端口和口令都是"活的"——只改 config.json 一处

`port`、`wake_keyword`、`assistant_name` 是**单一配置源**：`deploy.py` 会自动从 `config.json` 读取它们，部署时同步注册到 xiaomusic（口令写进 `setting.json`、端口写进插件 `BRIDGE_URL`、助手名写进失败提示），桥接服务自身也读同一份配置。

- **改口令**：只改 `config.json` 的 `wake_keyword` → 重跑 `python3 scripts/deploy.py` → 新口令生效。口令识别在桥接侧用正则（`assistant_name` 无关），插件注册在 xiaomusic 侧，两处由 deploy 一次同步，不会脱节。
- **改桥接端口**：只改 `config.json` 的 `port` → 改启动桥接服务的端口 → 重跑 `deploy.py` 同步插件 → 用新端口的 `/health` 验证。
- **改 xiaomusic 端口**：改 `xiaomusic_port`，或直接在 `xiaomusic_url` 填含端口的完整地址。
- 命令行参数（`--wake` / `--bridge-port` / `--name`）和环境变量（`XIAOPI_*`）可覆盖配置文件，用于临时调试，优先级：命令行 > 环境变量 > config.json > 内置默认。

**LLM 端点示例**：
- 智谱：`https://open.bigmodel.cn/api/paas/v4/chat/completions`，model `glm-4-flash`
- DeepSeek：`https://api.deepseek.com/v1/chat/completions`，model `deepseek-chat`
- Ollama 本地：`http://127.0.0.1:11434/v1/chat/completions`，model `qwen2.5:7b`，key 随便填

## 4. 部署补丁（插件 + 口令 + 两个根治补丁）

### 自动（推荐）——全部免手填
```bash
python3 scripts/deploy.py --dry-run   # 先无损预演，看推导的容器/通道/桥接地址
python3 scripts/deploy.py             # 确认无误后正式部署
```
容器名、docker 通道、桥接地址全部自动推导；只有自动推导不通时才用 `--bridge` / `--docker-cmd` 手动指定。

### 手工
```bash
ssh root@<gateway> 'docker exec -i xiaomusic python3 -' < scripts/patch_xiaomusic.py
# 需要传参数时用 -e：
ssh root@<gateway> 'docker exec -i \
  -e XIAOPI_APP_DIR=/app \
  -e XIAOPI_BRIDGE_URL=http://<workbuddy容器IP>:<port>/speak \
  -e XIAOPI_BRIDGE_TOKEN=<桥接访问口令> \
  -e XIAOPI_WAKE=<口令> \
  -e XIAOPI_NAME=<助手名> \
  -e XIAOPI_PLUGIN=xiaopi \
  xiaomusic python3 -' < scripts/patch_xiaomusic.py
```

**访问口令链路**：`bridge.py` 首次启动在脚本同目录生成 `.bridge_token`（权限 600）；`deploy.py` 读同一文件并把口令注入插件，插件每次 POST 带 `X-Bridge-Token` 头。验证：

```bash
curl -H "X-Bridge-Token: $(cat scripts/.bridge_token)" \
  -X POST http://127.0.0.1:8900/speak -H 'Content-Type: application/json' \
  -d '{"text":"测试"}'
```

口令不要贴进会话或日志。轮换时删除 `.bridge_token`，重启桥接服务，再重跑 `deploy.py`。

手工方式下 `<口令>`/`<助手名>`/`<port>` 要与 `config.json` 保持一致；用 `deploy.py` 则自动同步，不会漏。

补丁做 4 件事（全部幂等、改前备份 `*.bak.workbuddy`）：
1. 写插件 `/app/plugins/<plugin>.py`
2. `setting.json` 注册口令 `exec#<plugin>()`、`active_cmd` 加 `exec`+口令、`edge_tts_voice=""`
3. `conversation.py` 注入 cookies 补丁（修 400）
4. `miaccount.py` 注入 token 保留补丁（根治"喊了没反应"）

执行后 `docker restart xiaomusic` 生效。

## 5. 不同型号适配要点

- **唤醒词**：固件 DSP 固化，官方只支持预设词（小爱同学/小爱/你好小爱），**任意唤醒词改不了**；技巧是保持"小爱同学 + 自定义口令"两段式
- **DID**：脚本自动发现，无需关心型号；只有多设备同名时才需要 `device_match`
- **mina 接口**：老型号（LX06 等）不支持，必须 `get_ask_by_mina=False` 走轮询
- **TTS**：老型号用小米原生 TTS（把 `edge_tts_voice` 置空）；edge-tts 会被微软 403
- **方言**：小米云端 ASR 只支持粤语双向 + 川话/东北话等官话识别，**吴语/绍兴话不在列**；方言只能提升"理解"不能提升"听清"

## 6. 验证清单（逐项打勾）

- [ ] `/health` 返回 OK，且 `did` 非空
- [ ] 手动 `curl -X POST .../speak -d '{"text":"测试"}'` 有回答
- [ ] xiaomusic 日志出现 `do_tts ok`（不是 `Login failed`）
- [ ] 真人喊口令，音箱出声
- [ ] 任务单/存档文件正确落盘
- [ ] 重启 xiaomusic 后 token 仍在（验证 token 保留补丁生效）
