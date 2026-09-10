"""UX kit: one place for screen texts, labels and interface rules.

Design rules (see docs/UX.md):
- one screen lives in one message and is edited in place;
- every screen has a title, a path and a way back;
- every callback query gets an answer, so the spinner never hangs;
- long operations show a placeholder first;
- button labels are short and use one emoji system.
"""
from __future__ import annotations

import html
import time

BAR_FULL = "█"
BAR_EMPTY = "░"

# Telegram truncates long labels on phones.
MAX_LABEL = 22
# More buttons in a row are hard to tap.
MAX_ROW = 3
DEFAULT_BAR_WIDTH = 12
# Telegram cuts toasts at 200 characters.
TOAST_LIMIT = 190

# Short command list for setMyCommands. Every command is also a button.
BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("menu", "Пульт принтера"),
    ("status", "Статус печати"),
    ("files", "Файлы и запуск"),
    ("anims", "Анимации на экране"),
    ("errors", "Последние ошибки"),
    ("diag", "Диагностика"),
    ("help", "Помощь"),
)


def esc(value) -> str:
    """Escape text for Telegram HTML mode. Use it for every outside value."""
    return html.escape("" if value is None else str(value), quote=False)


def code(value) -> str:
    """Monospace value the user may want to copy."""
    return f"<code>{esc(value)}</code>"


def clamp_label(text, limit: int = MAX_LABEL) -> str:
    """Keep a button label short so it is not cut in the middle of a word."""
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    return s[: max(1, limit - 1)].rstrip() + "…"


def crumbs(*parts) -> str:
    """Build a path line: пульт › файлы."""
    return " › ".join(str(p) for p in parts if p)


def screen(title, body=None, path=None, meta=None) -> str:
    """Build one screen.

    Zones: bold title, path in italics, body, meta in italics.
    `body` lines must already be HTML-safe: pass user values through esc().
    """
    out = [f"<b>{esc(title)}</b>"]
    if path:
        out.append(f"<i>{esc(path)}</i>")
    if body:
        lines = [body] if isinstance(body, str) else [str(x) for x in body if x is not None]
        text = "\n".join(lines).strip("\n")
        if text:
            out.append("")
            out.append(text)
    if meta:
        out.append("")
        out.append(f"<i>{esc(meta)}</i>")
    return "\n".join(out)


def field(icon, label, value) -> str:
    """One body line: icon, label and an escaped value."""
    return f"{icon} {esc(label)}: {esc(value)}"


def pct(frac) -> str:
    try:
        value = max(0.0, min(1.0, float(frac)))
    except (TypeError, ValueError):
        value = 0.0
    return f"{value * 100:.1f}%"


def progress(frac, width: int = DEFAULT_BAR_WIDTH) -> str:
    """Text progress bar. Width is clamped so the bar always fits one line."""
    try:
        value = max(0.0, min(1.0, float(frac)))
    except (TypeError, ValueError):
        value = 0.0
    w = max(4, min(20, int(width or DEFAULT_BAR_WIDTH)))
    n = min(w, max(0, int(value * w)))
    return BAR_FULL * n + BAR_EMPTY * (w - n)


def eta_seconds(duration, frac):
    """Seconds left for a print. None when the estimate makes no sense."""
    try:
        done = float(duration or 0)
        value = float(frac or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0.01 or done <= 0:
        return None
    left = done / value - done
    if left <= 0:
        return None
    return left


def is_not_modified(exc) -> bool:
    """Telegram rejects an edit that changes nothing. That is not an error."""
    return "not modified" in str(exc).lower()


def is_allowed(cfg, user) -> bool:
    """Reject updates without a user: channel posts and anonymous admins."""
    ids = getattr(cfg, "allowed_user_ids", None) or set()
    return bool(user) and getattr(user, "id", None) in ids


def deny_text(user_id) -> str:
    """Access denied screen. The id helps the owner add the user."""
    return screen(
        "🔒 Доступ закрыт",
        [
            "Этот бот работает только со своим владельцем.",
            "",
            f"Ваш ID: {code(user_id)}",
            "Покажите этот ID владельцу принтера. Он добавит его в allowed_user_ids.",
        ],
    )


async def ack(cq, text=None, alert: bool = False) -> None:
    """Answer a callback query. Always call it, or the spinner hangs."""
    try:
        if text is None:
            await cq.answer()
        else:
            await cq.answer(str(text)[:TOAST_LIMIT], show_alert=bool(alert))
    except Exception:
        pass


async def render(cq, bot, text, markup=None, parse_mode: str | None = "HTML") -> bool:
    """Rewrite the current screen in place. Send a new one only if edit fails."""
    message = getattr(cq, "message", None)
    if message is not None:
        try:
            await message.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
            return True
        except Exception as exc:
            if is_not_modified(exc):
                return True
    chat = getattr(getattr(message, "chat", None), "id", None)
    if chat is None:
        return False
    try:
        await bot.send_message(chat, text, reply_markup=markup, parse_mode=parse_mode)
        return True
    except Exception:
        return False


class Debounce:
    """Drop repeated events inside a time window. Used against double taps."""

    def __init__(self, window: float = 0.8, capacity: int = 1024):
        self.window = max(0.0, float(window))
        self.capacity = max(16, int(capacity))
        self._seen: dict[object, float] = {}

    def hit(self, key, now=None) -> bool:
        """True when the event may pass, False when it repeats too fast."""
        moment = float(now if now is not None else time.time())
        if self.window <= 0:
            return True
        last = self._seen.get(key)
        if last is not None and moment - last < self.window:
            return False
        self._seen[key] = moment
        if len(self._seen) > self.capacity:
            self.purge(moment)
        return True

    def purge(self, now=None) -> int:
        """Drop old keys so the dictionary cannot grow forever."""
        moment = float(now if now is not None else time.time())
        ttl = max(self.window, 60.0)
        stale = [key for key, ts in self._seen.items() if moment - ts > ttl]
        for key in stale:
            self._seen.pop(key, None)
        extra = len(self._seen) - self.capacity
        if extra > 0:
            oldest = sorted(self._seen.items(), key=lambda kv: kv[1])[:extra]
            for key, _ in oldest:
                self._seen.pop(key, None)
        return len(stale)
