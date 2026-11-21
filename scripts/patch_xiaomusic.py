#!/usr/bin/env python3
"""
xiaomusic 容器内补丁脚本（幂等，可重复运行）
============================================
用法（从能 docker exec 的机器执行）：
  docker exec -i <container> python3 - < patch_xiaomusic.py

或在 Python 里通过 subprocess 把本文件内容传给容器 stdin。

做的事：
  1. 写插件 /app/plugins/<plugin>.py（口令 -> POST 桥接服务）
  2. setting.json：注册口令 exec#<plugin>()、active_cmd 加 exec + 口令、关 edge-tts 用原生 TTS
  3. conversation.py：注入 cookies 补丁（补 userId/serviceToken，修 400）
  4. miaccount.py：注入 token 保留补丁（登录失败不再删 .mi.token，根治"喊了没反应"）
  5. 全部改前自动备份 *.bak.<stamp>

参数：通过环境变量传入（在 docker exec 时用 -e 或包装脚本导出）
  XIAOPI_BRIDGE_URL   桥接地址（必填，由 deploy.py 自动推导传入；形如 http://<容器IP>:<port>/speak）
  XIAOPI_WAKE         语音口令，默认 小皮小皮（由 deploy.py 从 config.json 同步）
  XIAOPI_NAME         助手名，默认 小皮（用于插件失败提示；由 deploy.py 从 config.json 同步）
  XIAOPI_PLUGIN       插件文件名/函数名，默认 xiaopi
  XIAOPI_APP_DIR      xiaomusic 应用目录，默认 /app
  XIAOPI_STAMP        备份后缀，默认日期
"""
import ast
import json
import os
import re
from pathlib import Path

APP = Path(os.environ.get("XIAOPI_APP_DIR", "/app"))
# 必填：由 deploy.py 自动推导传入；手工运行时必须显式设置
BRIDGE_URL = os.environ.get("XIAOPI_BRIDGE_URL", "")
if not BRIDGE_URL:
    raise SystemExit("!! 必须设置 XIAOPI_BRIDGE_URL（用 deploy.py 会自动推导，或先用 netprobe.py 查候选）")
WAKE = os.environ.get("XIAOPI_WAKE", "小皮小皮")
NAME = os.environ.get("XIAOPI_NAME", "小皮")
PLUGIN = os.environ.get("XIAOPI_PLUGIN", "xiaopi")
STAMP = os.environ.get("XIAOPI_STAMP", "workbuddy")

PLUGIN_FILE = APP / "plugins" / f"{PLUGIN}.py"
SETTING = APP / "conf" / "setting.json"
CONVERSATION = APP / "xiaomusic" / "conversation.py"
MIACCOUNT = APP / ".venv" / "lib" / "python3.14" / "site-packages" / "miservice" / "miaccount.py"

actions = []


def backup(p: Path):
    if p.exists():
        bak = p.with_suffix(p.suffix + f".bak.{STAMP}")
        if not bak.exists():
            bak.write_bytes(p.read_bytes())
            actions.append(f"备份 {p.name} -> {bak.name}")


def patch_setting():
    backup(SETTING)
    if not SETTING.exists():
        actions.append("!! setting.json 不存在，跳过口令配置")
        return
    d = json.loads(SETTING.read_text(encoding="utf-8"))
    kw = d.setdefault("user_key_word_dict", {})
    kw[WAKE] = f"exec#{PLUGIN}()"
    ac = d.get("active_cmd", [])
    # 兼容 active_cmd 为字符串（老版本）或列表两种格式
    ac = list(ac) if isinstance(ac, (list, tuple)) else ([ac] if isinstance(ac, str) and ac else [])
    for k in ("exec", WAKE):
        if k not in ac:
            ac.append(k)
    d["active_cmd"] = ac
    # 关 edge-tts（微软 403），用小米原生 TTS
    d["edge_tts_voice"] = ""
    SETTING.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    actions.append(f"口令注册: {WAKE} -> exec#{PLUGIN}()；active_cmd 已补；edge_tts_voice 置空")


def write_plugin():
    backup(PLUGIN_FILE)
    PLUGIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    code = f'''import aiohttp

BRIDGE_URL = "{BRIDGE_URL}"


async def {PLUGIN}():
    global log, xiaomusic
    query = (xiaomusic.command_handler.last_cmd or "").strip()
    log.info(f"[xiaopi] 转发: {{query}}")
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(BRIDGE_URL, json={{"text": query}}) as resp:
                body = await resp.text()
                log.info(f"[xiaopi] 桥接返回 {{resp.status}}: {{body[:200]}}")
    except Exception as e:
        log.warning(f"[xiaopi] 转发失败: {{type(e).__name__}}: {{e}}")
        did = xiaomusic.get_cur_did()
        try:
            await xiaomusic.do_tts(did, "{NAME}连接失败了，请稍后再试")
        except Exception:
            pass
'''
    ast.parse(code)
    PLUGIN_FILE.write_text(code, encoding="utf-8")
    actions.append(f"插件写入 {PLUGIN_FILE} (BRIDGE_URL={BRIDGE_URL})")


def patch_conversation():
    """注入 cookies 补丁：get_latest_ask 缺 userId/serviceToken 会 400"""
    if not CONVERSATION.exists():
        actions.append("!! conversation.py 不存在，跳过 cookies 补丁")
        return
    src = CONVERSATION.read_text(encoding="utf-8")
    if "xiaopi-patch" in src:
        actions.append("conversation.py 已有补丁，跳过（幂等）")
        return
    backup(CONVERSATION)
    # 定位 cookies 构造处；不同版本写法可能不同，这里用宽松正则找 deviceId
    m = re.search(r"cookies\s*=\s*\{[^\n]*deviceId[^\n]*\}", src)
    if not m:
        # 退化：找 "deviceId" 赋值行，在其后注入
        m = re.search(r"^\s*cookies\s*=.*$", src, re.M)
    if not m:
        actions.append("!! 未找到 cookies 构造，需人工补 cookies 补丁")
        return
    original = m.group(0)
    inject = '''cookies = {
                "deviceId": xiaomusic.auth_manager.mina_service.account.token.get("deviceId", ""),
                "userId": xiaomusic.auth_manager.mina_service.account.token.get("userId", ""),
                "serviceToken": (xiaomusic.auth_manager.mina_service.account.micoapi[1]
                                 if getattr(xiaomusic.auth_manager.mina_service.account, "micoapi", None) else ""),
            }  # [xiaopi-patch] 补齐 cookies，修 get_latest_ask 400'''
    src2 = src[:m.start()] + inject + src[m.end():]
    try:
        ast.parse(src2)
    except SyntaxError as e:
        actions.append(f"!! cookies 补丁语法校验失败，未写入: {e}")
        return
    CONVERSATION.write_text(src2, encoding="utf-8")
    actions.append("conversation.py cookies 补丁已注入（语法校验通过）")


def patch_miaccount():
    """根治顽疾：登录异常不再删 token 文件"""
    if not MIACCOUNT.exists():
        actions.append(f"!! miaccount.py 不存在 ({MIACCOUNT})，跳过 token 保留补丁")
        return
    src = MIACCOUNT.read_text(encoding="utf-8")
    if "xiaopi-patch" in src:
        actions.append("miaccount.py 已有补丁，跳过（幂等）")
        return
    backup(MIACCOUNT)
    old = '''        except Exception as e:
            self.token = None
            if self.token_store:
                self.token_store.save_token()
            _LOGGER.exception("Exception on login %s: %s", self.username, e)
            return False'''
    new = '''        except Exception as e:
            # [xiaopi-patch] 登录异常保留旧 token 文件，避免 .mi.token 被清导致对话捕获失效
            _LOGGER.warning("[xiaopi-patch] login exception for %s, KEEP token file: %s", self.username, e)
            return False'''
    if old not in src:
        actions.append("!! miaccount.py 未找到目标 except 块，需人工补 token 保留补丁（见 references/pitfalls.md）")
        return
    src2 = src.replace(old, new, 1)
    try:
        ast.parse(src2)
    except SyntaxError as e:
        actions.append(f"!! token 补丁语法校验失败，未写入: {e}")
        return
    MIACCOUNT.write_text(src2, encoding="utf-8")
    actions.append("miaccount.py token 保留补丁已注入（语法校验通过）")


if __name__ == "__main__":
    write_plugin()
    patch_setting()
    patch_conversation()
    patch_miaccount()
    print("=== patch result ===")
    for a in actions:
        print("-", a)
    print("=== 请重启容器使配置生效 ===")
