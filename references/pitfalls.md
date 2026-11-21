# 踩坑实录（排障必读）

按"症状 → 根因 → 修法"组织。补丁脚本已覆盖①②，但理解原理对排障至关重要。

## 坑① `.mi.token` 反复被删 → 喊了没反应（最顽固）

- **症状**：修好一阵又失效；设备列表空、对话捕获 `All retries failed`、口令不触发
- **根因**：miservice 的 `miaccount.py` 中 `login()` 在 except 块里调 `self.token_store.save_token()`（无参 = **删除 token 文件**）。小米改接口后 `resp["userId"]`/`resp["passToken"]` 抛 KeyError → 每次 xiaomiio 域重登失败就删一次凭证
- **修法**：打补丁，登录异常时保留旧 token：
  ```python
  except Exception as e:
      # [xiaopi-patch] 保留旧 token 文件
      _LOGGER.warning("[xiaopi-patch] login exception for %s, KEEP token file: %s", self.username, e)
      return False
  ```
- **恢复**：`scripts/restore_token.py` 从 auth.json 备份重建三字段 token → 重启容器 → 看 `login('micoapi') 返回 True`
- **教训**：修一次坏一次时，去找"谁在删"，而不是反复重建

## 坑② 对话捕获 400：cookies 不全

- **症状**：`get_latest_ask_from_xiaoai` 返回 400
- **根因**：请求只带 `deviceId`，缺 `userId` 和 `serviceToken`
- **修法**：`conversation.py` 里把 cookies 补全，来源 `auth_manager.mina_service.account.token` 与 `micoapi[1]`
- **注意**：老代码结构可能不同，补丁脚本用正则宽松匹配；打完必须 `ast.parse` 校验语法

## 坑③ 假 OK 播报（接口 OK，音箱没响）

- **症状**：`/playtts` 返回 `{"ret":"OK"}` 但无声
- **根因**：OK 只代表"请求收到"。真正下发看日志 `do_tts ok`（伴随 `Call MiIOService tts.`）；出现 `miotspec/action: Login failed` 就是没播
- **修法**：凭证问题按坑①恢复；**排障永远看 `do_tts ok`，别信接口返回值**
- 附带：`edge_tts_voice` 非空时用 edge-tts，微软 403 会导致无声 → 置空用原生 TTS

## 坑④ bridge 进程跑久卡死（errno 38）

- **症状**：约 2 小时后 `/health` 空响应，内部全部 `Function not implemented`（errno 38）；独立 python 测试正常
- **根因**：后台启动方式继承了受限环境（如 nohup setsid 继承 PYTHONPATH shim）
- **修法**：按 PID 精确杀掉，用**受管后台方式**重启（WorkBuddy 的 `run_in_background`），不要 nohup setsid
- **诊断技巧**：curl NAS/GLM 都通但 bridge 内部 errno 38 → 基本可定进程卡死

## 坑⑤ 杀进程把自己杀了（出现两次）

- `pkill -f 'python3 bridge.py'` 匹配到自己的 shell 命令行 → 自杀
- `ps aux | grep '[b]ridge\.py'` 取 PID 时，若自己的命令行含 "bridge.py"（如 ast.parse 路径）也会自杀（exit 143）
- **正确做法**：取 PID 时过滤当前 shell（`grep -v $$`），或让命令文本不含目标文件名；最好用受管后台任务的 PID

## 坑⑥ 环境琐事

- xiaomusic 容器内**没有 curl**：测 TTS 从 WorkBuddy 本机 `curl http://<host>:<port>/playtts?did=<DID>&text=...`
- 精简 ash 宿主机（iStoreOS 等）无 base64、`ls --time-style`：传脚本用 `ssh ... 'docker exec -i <c> python3 -' <<'PYEOF'`
- 挂载文件系统 append 可能 `OSError: Function not implemented`：历史写 `/tmp`，工作空间写入包 try/except
- 浏览器访问 xiaomusic 用 **http**（https 会 SSL 报错）

## 坑⑦ 老型号能力边界

- LX06 等老型号：`get_ask_by_mina=True` 返回 code 900 → 必须回退 `False`（走对话历史轮询）
- 唤醒词固件固化，官方不支持任意改名；"米家 App 录三遍声纹自定义唤醒词"是内容农场假信息
- 真要自定义唤醒词只能刷机（Open-XiaoAI，LX06 需拆机、有变砖风险、识别率官方自认不稳），且唤醒后语音不进原生链路、需另搭 ASR——性价比低

## 坑⑧ setting.json 字段类型陷阱

- 老版本 `active_cmd` 可能是**字符串**而非列表，直接 `.append()` 会报 `AttributeError: 'str' object has no attribute 'append'`
- 修法：先归一化为 list 再追加（补丁脚本已处理）

## 坑⑩ 网络变化 / IP 各不相同

- **误区**：以为要填音箱 IP。实际上音箱通过 miotDID + 小米云端受控，**链路里从不直连音箱 IP**
- **真正会变的三个量**：xiaomusic 地址、桥接可达地址（容器内如何访问 WorkBuddy）、docker 通道
- **解法**：`xiaomusic_url` 填 `auto`（bridge.py 自动发现）；`deploy.py` 自动推导容器/通道/桥接地址；`netprobe.py` 独立排查
- **docker inspect 经 ssh 的坑**：Go template 里的 `{{...}}` 会被远端 shell 展开导致取不到值 → 改为 `docker inspect <name>` 拿完整 JSON 用 Python 解析
- **IP 变了之后**：`curl 'http://127.0.0.1:<port>/health?reset=1'` 强制重探 xiaomusic 地址
- **探测判定**：`/device_list` 返回 JSON 且含 `devices` 字段才算命中，比 ping/端口通更可靠

## 坑⑨ 方言的边界

- 小米云端 ASR 方言支持：粤语（唯一双向对话）+ 四川/东北/河南等十余种**官话识别**
- **吴语/绍兴话不在支持列表**（网传"支持上海话/苏州话"是内容农场假信息）
- 结论：纯方言过不了"听"这关；可行路径 = 口音普通话 + 方言词 + 理解层纠偏表
- `dialect.md` 只注入系统提示提升"懂"，不能让 ASR 听清纯方言
