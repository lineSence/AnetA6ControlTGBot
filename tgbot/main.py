"""Bot entry point.

On start the bot does three UX things:
1. attaches middlewares (access + anti double tap);
2. publishes the command list, so Telegram shows a hint menu;
3. sets the menu button to the command list.
"""
from __future__ import annotations
import asyncio, contextlib, logging, os

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

from . import __version__
from .config import load
from .logsetup import setup
from .middlewares import setup as setup_middlewares
from .printer import PrinterClient
from .uxkit import BOT_COMMANDS
from .ws import MoonrakerWS, health_loop
from .handlers import router, janitor_loop

log = logging.getLogger("tgbot")


async def publish_ui(bot) -> bool:
    """Show commands in the Telegram UI. A failure here must not stop the bot."""
    ok = True
    try:
        await bot.set_my_commands(
            [BotCommand(command=name, description=text) for name, text in BOT_COMMANDS],
            scope=BotCommandScopeDefault(),
        )
        log.info("bot commands published: %d", len(BOT_COMMANDS))
    except Exception:
        ok = False
        log.warning("set_my_commands failed", exc_info=True)
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception:
        ok = False
        log.warning("set_chat_menu_button failed", exc_info=True)
    return ok


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
    setup_middlewares(dp, cfg)
    dp.include_router(router)

    await publish_ui(bot)

    pc = PrinterClient.from_config(cfg)
    ws = MoonrakerWS(cfg, bot)
    ws_task = asyncio.create_task(ws.run())
    health_task = asyncio.create_task(health_loop(pc, bot, cfg, ws))
    janitor_task = asyncio.create_task(janitor_loop(cfg))

    log.info("bot v%s starting", __version__)
    try:
        await dp.start_polling(bot, pc=pc, cfg=cfg, ws=ws)
    finally:
        for t in (ws_task, health_task, janitor_task): t.cancel()
        await asyncio.gather(ws_task, health_task, janitor_task, return_exceptions=True)
        await pc.close()
        with contextlib.suppress(Exception):
            await bot.session.close()
        log.info("bot stopped cleanly")

if __name__ == "__main__":
    asyncio.run(main())
