from __future__ import annotations
import asyncio, contextlib, logging, os
from aiogram import Bot, Dispatcher
from . import __version__
from .config import load
from .logsetup import setup
from .printer import PrinterClient
from .ws import MoonrakerWS, health_loop
from .handlers import router

log = logging.getLogger("tgbot")

async def main():
    cfg = load()
    if not cfg.bot_token:
        raise RuntimeError("Не задан bot_token в config.yaml или TG_BOT_TOKEN.")
    if not cfg.allowed_user_ids:
        raise RuntimeError("allowed_user_ids пуст.")
    setup(cfg)
    os.makedirs(os.path.join(cfg.config_dir, "anims"), exist_ok=True)
    os.makedirs(cfg.backup_dir, exist_ok=True)

    bot = Bot(cfg.bot_token)
    dp = Dispatcher()
    dp.include_router(router)

    pc = PrinterClient(cfg.moonraker)
    ws = MoonrakerWS(cfg, bot)
    ws_task = asyncio.create_task(ws.run())
    health_task = asyncio.create_task(health_loop(pc, bot, cfg, ws))

    log.info("bot v%s starting", __version__)
    try:
        await dp.start_polling(bot, pc=pc, cfg=cfg, ws=ws)
    finally:
        for t in (ws_task, health_task): t.cancel()
        await asyncio.gather(ws_task, health_task, return_exceptions=True)
        await pc.close()
        with contextlib.suppress(Exception):
            await bot.session.close()
        log.info("bot stopped cleanly")

if __name__ == "__main__":
    asyncio.run(main())
