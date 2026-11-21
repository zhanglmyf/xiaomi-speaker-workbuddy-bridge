#!/usr/bin/env python3
"""
小皮桥接服务 · 通用版 (xiaopi bridge, portable)
==================================================
把 xiaomusic 管理的任意小米音箱接入 WorkBuddy / 任意 LLM：
  语音原话 -> 桥接 -> LLM -> /playtts 播报 -> 语音任务单/对话存档

设计目标：拿到任何一台 WorkBuddy / 任意环境都能直接改配置运行。
  - 音箱型号/DID：自动发现，无需手填
  - LLM：OpenAI 兼容 API（智谱 GLM / DeepSeek / OpenRouter / Ollama 本地 均可）
  - 地址/端口/口令/助手名/工作空间：全部配置驱动
  - 无硬编码密钥、无硬编码路径

用法：
  1. 复制 config.example.json 为 config.json，填好 LLM 与 xiaomusic 地址
  2. python3 bridge.py                       # 前台
  3. 或交给 WorkBuddy 以受管后台方式启动（见 SKILL.md）

环境变量覆盖：
  XIAOPI_CONFIG   配置文件路径（默认脚本同目录 config.json）
"""
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

try:
    import aiohttp
    from aiohttp import web
except ImportError:
    sys.stderr.write("缺少依赖 aiohttp，请先执行: pip install aiohttp\n")
    raise

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("XIAOPI_CONFIG", str(BASE_DIR / "config.json")))
LOG_FILE = BASE_DIR / "bridge.log"

# 网络自动发现模块（可选导入；缺失时回退到静态配置）
try:
    from netprobe import discover_xiaomusic
except ImportError:
    discover_xiaomusic = None


# ============ 配置加载 ============
DEFAULTS = {
    "xiaomusic_url": "auto",        # auto = 自动发现；也可填死地址如 http://<host>:58090
    "xiaomusic_port": 58090,        # auto 探测时用的 xiaomusic 端口（非默认端口的环境改这里）
    "did": "",                      # 空 = 启动时自动发现
    "device_match": "",             # 可选：按型号/别名关键词匹配，如 "LX06"
    "port": 8900,                   # 桥接监听端口；deploy 会自动同步到插件 BRIDGE_URL
    "listen": "0.0.0.0",
    "llm": {
        "api": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4-flash",
        "key": "",                  # 必填：从环境变量 XIAOPI_LLM_KEY 或本字段读
        "api_style": "openai",      # openai 兼容；其他风格可扩展
        "max_tokens": 300,
        "temperature": 0.7,
        "timeout_sec": 20,
    },
    "assistant_name": "小皮",
    "owner_name": "主人",
    "wake_keyword": "小皮小皮",
    "feedback": "收到，小皮正在想",
    "cooldown_sec": 3.0,
    "reply_max_chars": 120,
    "workspace_dir": "",            # 空 = 脚本同目录 data/（可移植）
    "history_in_tmp": True,         # 挂载文件系统 append 异常时保留 True
    "dialect_file": "dialect.md",   # 方言表（可选），相对脚本目录
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    user = {}
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            sys.stderr.write(f"配置文件解析失败 {CONFIG_PATH}: {e}\n")
            raise
    cfg = _deep_merge(DEFAULTS, user)
    # 密钥允许只放环境变量，避免写进文件
    env_key = os.environ.get("XIAOPI_LLM_KEY")
    if env_key:
        cfg["llm"]["key"] = env_key
    return cfg


CFG = load_config()

# xiaomusic 地址：auto 或空 = 自动发现（支持网络变化后重启自愈）
XIAOMUSIC = ""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("xiaopi")

PORT = int(CFG["port"])
LISTEN = CFG["listen"]
DIALECT_FILE = BASE_DIR / CFG["dialect_file"]

# 工作空间：显式配置优先，否则脚本同目录 data/（保证任何环境可写）
_WS = CFG.get("workspace_dir") or str(BASE_DIR / "data")
WS_DIR = Path(_WS).expanduser()
TASKS_FILE = WS_DIR / "tasks.md"
ARCHIVE_FILE = WS_DIR / "history.md"
HISTORY_FILE = Path("/tmp/xiaopi_history.jsonl") if CFG["history_in_tmp"] else (WS_DIR / "history.jsonl")

WAKE = CFG["wake_keyword"]
NAME = CFG["assistant_name"]
OWNER = CFG["owner_name"]
FEEDBACK = CFG["feedback"]
COOLDOWN_SEC = float(CFG["cooldown_sec"])
REPLY_MAX = int(CFG["reply_max_chars"])

# 任务指令正则（口令可配置）
def _task_regex():
    return re.compile(
        rf"^(?:{re.escape(WAKE)})?[，,、]?\s*(?:帮我)?(?:记(?:个|下|一)?任务|待办|记一下|记下来|记一条|提醒我)[：:，,、]?\s*(.+)$"
    )

TASK_RE = _task_regex()
CHECK_TASK_RE = re.compile(
    r"(?:(?:查|看|读|说|报)(?:一下|下)?(?:我的)?(?:任务|待办))|(?:(?:有)?(?:什么|哪些|啥)(?:任务|待办))|(?:任务单|待办单)"
)

SYSTEM_PROMPT = (
    f"你是「{NAME}」，运行在小米音箱上的语音助手，主人叫{OWNER}。\n"
    "规则：\n"
    f"1. 回答必须极度简短，用口语化的中文，2到4句话，总长不超过{REPLY_MAX}字，因为要念出来给主人听。\n"
    "2. 不用markdown、不用列表符号、不用括号注释，直接说人话。\n"
    "3. 涉及删除文件、执行系统命令、修改系统配置等危险操作，明确告诉主人「这个需要回电脑确认」，不要声称已经做了。\n"
    "4. 查不到或不确定的事，直说不确定，不要编造。\n"
    "5. 回答要像一个利落的朋友在回话，不要客套开场白。"
)

# 并发/冷却
_speak_lock = asyncio.Lock()
_last_text = ""
_last_ts = 0.0
_cached_did = ""


# ============ 工具函数 ============
def build_system_prompt() -> str:
    prompt = SYSTEM_PROMPT
    try:
        if DIALECT_FILE.exists():
            extra = DIALECT_FILE.read_text(encoding="utf-8").strip()
            if extra:
                prompt += "\n\n【方言理解规则（来自 dialect.md）】\n" + extra
    except Exception as e:
        log.warning(f"读取方言表失败(不影响回答): {type(e).__name__}: {e}")
    return prompt


def clean_for_tts(text: str) -> str:
    text = re.sub(r"[*#|>`\[\]_]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def status(ok: bool) -> str:
    return "OK" if ok else "FAIL"


_xiaomusic_url = ""   # 解析后的实际地址（auto 模式由 ensure_xiaomusic() 填充）


async def ensure_xiaomusic(session=None) -> str:
    """解析 xiaomusic 地址：静态配置直接返回；auto 则自动发现并缓存。
    网络变化后可调用本函数重新探测（health 端点会触发）。"""
    global _xiaomusic_url
    if _xiaomusic_url:
        return _xiaomusic_url
    configured = str(CFG.get("xiaomusic_url", "")).strip().lower()
    if configured and configured != "auto":
        _xiaomusic_url = CFG["xiaomusic_url"].rstrip("/")
        return _xiaomusic_url
    # 自动发现
    if discover_xiaomusic:
        try:
            res = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: discover_xiaomusic(
                    explicit=None, docker=None,
                    port=int(CFG.get("xiaomusic_port", 58090)),
                ),
            )
            if res.get("found"):
                _xiaomusic_url = res["found"]
                log.info(f"自动发现 xiaomusic: {_xiaomusic_url}")
                return _xiaomusic_url
            log.warning(f"自动发现未找到 xiaomusic，候选: {res.get('candidates')}；下次请求会重试")
        except Exception as e:
            log.warning(f"自动发现异常: {type(e).__name__}: {e}")
        # 探测失败不缓存兜底值，保留空串让下次重试（避免一次抖动锁死错误地址）
        return ""
    return _xiaomusic_url


async def discover_device(session: aiohttp.ClientSession) -> str:
    """从 xiaomusic /device_list 自动发现音箱 DID；匹配型号/别名；失败返回空。"""
    try:
        base = await ensure_xiaomusic(session)
        if not base:
            log.warning("xiaomusic 地址未知，跳过设备发现")
            return ""
        async with session.get(f"{base}/device_list", timeout=aiohttp.ClientTimeout(total=8)) as r:
            data = await r.json(content_type=None)
        devices = data.get("devices", []) if isinstance(data, dict) else []
        if not devices:
            log.warning("设备列表为空，请先在 xiaomusic Web UI 登录小米账号")
            return ""
        match = CFG.get("device_match", "").strip()
        cand = devices
        if match:
            filtered = [
                d for d in devices
                if match.lower() in str(d.get("hardware", "")).lower()
                or match.lower() in str(d.get("alias", "")).lower()
                or match.lower() in str(d.get("name", "")).lower()
            ]
            if filtered:
                cand = filtered
            else:
                log.warning(f"没有匹配 '{match}' 的设备，回退用第一个")
        online = [d for d in cand if d.get("presence") == "online"] or cand
        dev = online[0]
        did = str(dev.get("miotDID") or dev.get("deviceID") or "")
        log.info(f"自动发现设备: {dev.get('alias') or dev.get('name')} [{dev.get('hardware')}] DID={did}")
        return did
    except Exception as e:
        log.warning(f"设备自动发现失败: {type(e).__name__}: {e}")
        return ""


async def ensure_did(session: aiohttp.ClientSession) -> str:
    global _cached_did
    if CFG.get("did"):
        return str(CFG["did"])
    if not _cached_did:
        _cached_did = await discover_device(session)
    return _cached_did


# ============ LLM ============
async def llm_answer(session: aiohttp.ClientSession, query: str) -> str:
    llm = CFG["llm"]
    if not llm.get("key"):
        return "我还差一个大模型密钥没配好，请到电脑上的配置文件里填一下。"
    payload = {
        "model": llm["model"],
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": query},
        ],
        "max_tokens": int(llm.get("max_tokens", 300)),
        "temperature": float(llm.get("temperature", 0.7)),
    }
    headers = {"Authorization": f"Bearer {llm['key']}", "Content-Type": "application/json"}
    try:
        async with session.post(
            llm["api"], json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=float(llm.get("timeout_sec", 20))),
        ) as r:
            data = await r.json(content_type=None)
        if "choices" not in data:
            log.warning(f"LLM 返回异常: {str(data)[:300]}")
            return "抱歉，大脑返回的格式不对，请稍后再试。"
        content = data["choices"][0]["message"]["content"]
        return clean_for_tts(content)
    except Exception as e:
        log.warning(f"LLM 调用失败: {type(e).__name__}: {e}")
        return "抱歉，我这边暂时没能连上大脑，请稍后再试。"


# ============ 播报 ============
async def play_tts(session: aiohttp.ClientSession, text: str, did: str) -> bool:
    text = clean_for_tts(text)
    if not text:
        return False
    if not did:
        log.warning("没有可用 DID，跳过播报")
        return False
    try:
        base = await ensure_xiaomusic(session)
        if not base:
            log.warning("xiaomusic 地址未知，跳过播报")
            return False
        async with session.get(
            f"{base}/playtts",
            params={"did": did, "text": text},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            body = await r.text()
            ok = r.status == 200
            # 注意：接口 OK 不代表真响了，验证需看 xiaomusic 容器日志 do_tts ok
            log.info(f"播报[{status(ok)}] {text[:60]} -> {body[:80]}")
            return ok
    except Exception as e:
        log.warning(f"播报失败: {type(e).__name__}: {e}")
        return False


# ============ 存档 / 任务单 ============
def save_history(query: str, answer: str) -> None:
    rec = {"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "query": query, "answer": answer}
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"写历史失败(不影响播报): {e}")


def _ws_write(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    except Exception as e:
        log.warning(f"工作空间写入失败 {path.name}: {type(e).__name__}: {e}")
        return False


def _ws_append(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception as e:
        log.warning(f"工作空间追加失败 {path.name}: {type(e).__name__}: {e}")
        return False


def save_archive(query: str, answer: str, kind: str = "chat") -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if kind == "task":
        block = f"## [{now}] 🎯 任务\n- **语音**: {query}\n- **{NAME}**: {answer}\n\n"
    else:
        block = f"## [{now}] 💬 对话\n- **语音**: {query}\n- **{NAME}**: {answer}\n\n"
    _ws_append(ARCHIVE_FILE, block)


def load_tasks() -> list:
    try:
        if not TASKS_FILE.exists():
            return []
        return [l for l in TASKS_FILE.read_text(encoding="utf-8").splitlines() if l.strip().startswith("- ")]
    except Exception as e:
        log.warning(f"读任务单失败: {e}")
        return []


def add_task(content: str) -> str:
    now = time.strftime("%m-%d %H:%M")
    entry = f"- [ ] {content}　`{now}`"
    if not TASKS_FILE.exists():
        header = (
            f"# {NAME}语音任务单\n\n"
            f"> 语音喊「{WAKE}，记任务：xxx」自动写入。\n"
            "> WorkBuddy 会查阅并跟进这些任务。\n\n"
            "## 待办\n\n"
        )
        _ws_write(TASKS_FILE, header + entry + "\n")
    else:
        _ws_append(TASKS_FILE, entry + "\n")
    return entry


def task_summary() -> str:
    tasks = load_tasks()
    pending = [t for t in tasks if "[ ]" in t]
    done = [t for t in tasks if "[x]" in t or "[X]" in t]
    if not tasks:
        return "任务单是空的，还没记过任务。"
    parts = [f"任务单共{len(tasks)}条，未完成{len(pending)}条，已完成{len(done)}条。"]
    for t in pending[-3:]:
        content = t.replace("- [ ]", "").strip()
        content = re.sub(r"`[^`]*`", "", content).strip()
        parts.append(f"待办：{content}。")
    return "".join(parts)


def extract_task(text: str) -> str | None:
    m = TASK_RE.match(text.strip())
    if m:
        content = m.group(1).strip()
        content = re.sub(r"[。！!？?～~啊呀吧哈么吗呐]$", "", content).strip()
        return content if content else None
    return None


def is_task_query(text: str) -> bool:
    t = text.strip()
    if TASK_RE.match(t):
        return False
    return bool(CHECK_TASK_RE.search(t))


# ============ 请求处理 ============
async def handle_speak(request: web.Request) -> web.Response:
    global _last_text, _last_ts
    try:
        data = await request.json()
    except Exception:
        data = dict(request.query)

    text = (data.get("text") or data.get("query") or "").strip()
    if not text:
        return web.json_response({"ret": "ERR", "msg": "empty text"})

    now = time.time()
    if text == _last_text and (now - _last_ts) < COOLDOWN_SEC:
        log.info(f"冷却丢弃重复: {text[:40]}")
        return web.json_response({"ret": "OK", "msg": "duplicate, skipped"})
    if _speak_lock.locked():
        log.info(f"忙，丢弃: {text[:40]}")
        return web.json_response({"ret": "OK", "msg": "busy, skipped"})

    async with _speak_lock:
        _last_text, _last_ts = text, now
        log.info(f"收到: {text}")
        save_history(text, "[处理中]")

        async with aiohttp.ClientSession() as session:
            did = await ensure_did(session)
            # 1) 即时反馈
            await play_tts(session, FEEDBACK, did)
            # 2) 分流
            task_content = extract_task(text)
            if task_content:
                entry = add_task(task_content)
                answer = f"记下了，任务是{task_content}。已经写进任务单，电脑那边能看到。"
                kind = "task"
                log.info(f"记任务: {entry}")
            elif is_task_query(text):
                answer = task_summary()
                kind = "chat"
            else:
                answer = await llm_answer(session, text)
                kind = "chat"
            log.info(f"回答: {answer}")
            # 3) 播报
            await play_tts(session, answer, did)

        # 回填历史
        try:
            lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
            if lines:
                rec = json.loads(lines[-1])
                rec["answer"] = answer
                lines[-1] = json.dumps(rec, ensure_ascii=False)
                HISTORY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as e:
            log.warning(f"更新历史失败: {e}")

        save_archive(text, answer, kind=kind)
        return web.json_response({"ret": "OK", "query": text, "answer": answer, "kind": kind})


async def handle_health(request: web.Request) -> web.Response:
    global _xiaomusic_url
    # ?reset=1 强制重新探测 xiaomusic 地址（IP 变化后自愈）
    if request.query.get("reset") == "1":
        _xiaomusic_url = ""
    # 首次 health 也触发一次发现，让调用方一眼看到实际地址
    if not _xiaomusic_url:
        await ensure_xiaomusic()
    return web.json_response({
        "ret": "OK", "service": "xiaopi",
        "model": CFG["llm"]["model"], "did": _cached_did or CFG.get("did", ""),
        "xiaomusic": _xiaomusic_url,
        "workspace": str(WS_DIR), "ts": time.time(),
    })


async def handle_history(request: web.Request) -> web.Response:
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
        return web.json_response({"ret": "OK", "records": [json.loads(x) for x in lines[-20:]]})
    except Exception:
        return web.json_response({"ret": "OK", "records": []})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/speak", handle_speak)
    app.router.add_get("/history", handle_history)
    return app


if __name__ == "__main__":
    mode = "自动发现" if str(CFG.get("xiaomusic_url", "")).strip().lower() in ("", "auto") else "静态配置"
    log.info(f"{NAME}桥接服务启动 {LISTEN}:{PORT} | LLM {CFG['llm']['model']} | xiaomusic {mode}")
    log.info(f"方言表: {'已加载 ' + str(DIALECT_FILE) if DIALECT_FILE.exists() else '未配置（可选）'}")
    log.info(f"工作空间: {WS_DIR}")
    if not CFG["llm"].get("key"):
        log.warning("LLM 密钥未配置：请填 config.json 的 llm.key 或设环境变量 XIAOPI_LLM_KEY")
    web.run_app(build_app(), host=LISTEN, port=PORT, print=None)
