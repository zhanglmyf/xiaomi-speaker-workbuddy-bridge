import aiohttp

# 由 deploy.py 注入实际值；这里是通用占位，不会直接使用本文件
BRIDGE_URL = "__XIAOPI_BRIDGE_URL__"
ASSISTANT_NAME = "__XIAOPI_NAME__"


async def __FUNC_NAME__():
    """把小爱捕获的原话转发给桥接服务"""
    global log, xiaomusic
    query = (xiaomusic.command_handler.last_cmd or "").strip()
    log.info(f"[xiaopi] 转发: {query}")
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(BRIDGE_URL, json={"text": query}) as resp:
                body = await resp.text()
                log.info(f"[xiaopi] 桥接返回 {resp.status}: {body[:200]}")
    except Exception as e:
        log.warning(f"[xiaopi] 转发失败: {type(e).__name__}: {e}")
        did = xiaomusic.get_cur_did()
        try:
            await xiaomusic.do_tts(did, f"{ASSISTANT_NAME}连接失败了，请稍后再试")
        except Exception:
            pass
