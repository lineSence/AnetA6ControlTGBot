"""Middlewares: access control and protection against double taps.

Access used to be checked inside every handler. One central place is safer:
a new handler cannot forget the check.
"""
from __future__ import annotations

import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from .uxkit import Debounce, ack, deny_text, is_allowed

log = logging.getLogger("tgbot")

DENY_TOAST = "Доступ закрыт"
FLOOD_TOAST = "Секунду, обрабатываю…"


class AccessMiddleware(BaseMiddleware):
    """Let only allowed users through. Tell strangers their id once per window."""

    def __init__(self, cfg, notice_window: float = 300.0):
        self.cfg = cfg
        self.notice = Debounce(notice_window)
        self.denied = 0

    async def __call__(self, handler, event, data):
        cfg = data.get("cfg") or self.cfg
        user = getattr(event, "from_user", None)
        if is_allowed(cfg, user):
            return await handler(event, data)

        self.denied += 1
        user_id = getattr(user, "id", None)
        log.warning("access denied for user %s", user_id)
        if isinstance(event, CallbackQuery):
            await ack(event, DENY_TOAST, alert=True)
        elif isinstance(event, Message) and self.notice.hit(user_id):
            try:
                await event.answer(deny_text(user_id))
            except Exception:
                pass
        return None


class AntiFloodMiddleware(BaseMiddleware):
    """Ignore the same button pressed twice in a row within a short window."""

    def __init__(self, window: float = 0.8):
        self.taps = Debounce(window)
        self.dropped = 0

    async def __call__(self, handler, event, data):
        key = self._key(event)
        if key is not None and not self.taps.hit(key):
            self.dropped += 1
            if isinstance(event, CallbackQuery):
                await ack(event, FLOOD_TOAST)
            return None
        return await handler(event, data)

    @staticmethod
    def _key(event):
        user = getattr(getattr(event, "from_user", None), "id", None)
        if user is None:
            return None
        if isinstance(event, CallbackQuery):
            return ("cb", user, getattr(event, "data", "") or "")
        if isinstance(event, Message):
            text = getattr(event, "text", None)
            if not text:
                return None
            return ("msg", user, text)
        return None


def setup(dp, cfg):
    """Attach middlewares to messages and callback queries."""
    access = AccessMiddleware(cfg, float(getattr(cfg, "deny_notice_seconds", 300) or 300))
    flood = AntiFloodMiddleware(float(getattr(cfg, "antiflood_seconds", 0.8) or 0))
    for observer in (dp.message, dp.callback_query):
        observer.middleware(access)
        observer.middleware(flood)
    log.info("middlewares ready: access + antiflood")
    return access, flood
