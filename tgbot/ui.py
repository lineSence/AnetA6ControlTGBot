"""Keyboards. One design system for every screen.

Rules (see docs/UX.md):
- a label is short and starts with an emoji from the shared set;
- a row holds two or three buttons;
- the primary action is in the first row;
- every screen has a way back and, where data is live, a refresh button;
- a destructive action never sits next to a navigation button.
"""
from __future__ import annotations

import hashlib

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from .printer import fmt_size, file_items
from .uxkit import clamp_label, progress

# Callback for labels that do nothing. It is answered by the handler.
NOOP = "m:noop"
HOME = "m:main"

BTN_BACK = "◀ Назад"
BTN_HOME = "🏠 Меню"
BTN_REFRESH = "🔄 Обновить"
BTN_CANCEL = "❌ Отмена"


def btn(label, data) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=clamp_label(label), callback_data=data)


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(t, d) for t, d in row] for row in rows if row
    ])


def cycle(lst, cur):
    """Next value in a list. An unknown current value returns the first item."""
    items = list(lst or [])
    if not items:
        return cur
    try:
        return items[(items.index(cur) + 1) % len(items)]
    except ValueError:
        return items[0]


def progress_bar(frac, width=12):
    """Kept for ws.py. The implementation lives in uxkit."""
    return progress(frac, width)


def macro_callback(index, name):
    digest = hashlib.sha1(str(name).encode("utf-8")).hexdigest()[:8]
    return f"mc:{index}:{digest}"


def toggle(label, on) -> str:
    """A switch shows its state in the label, so no extra text is needed."""
    return f"{'✅' if on else '⬜'} {label}"


def nav_row(back=None, refresh=None, home=False):
    """Last row of a screen: back, home, refresh. Never a dead end."""
    row = []
    if back:
        row.append((BTN_BACK, back))
    if home:
        row.append((BTN_HOME, HOME))
    if refresh:
        row.append((BTN_REFRESH, refresh))
    return row


def confirm_kb(ok_data, ok_label="✅ Подтвердить", cancel_data=HOME, cancel_label=BTN_CANCEL):
    """Confirmation screen: the safe button is first, so a miss-tap is safe."""
    return kb([[(cancel_label, cancel_data), (ok_label, ok_data)]])


def page_row(prefix, page, pages):
    """Pagination row with a page counter, always three cells wide."""
    left = ("⬅️", f"{prefix}{page - 1}") if page > 0 else ("·", NOOP)
    right = ("➡️", f"{prefix}{page + 1}") if page + 1 < pages else ("·", NOOP)
    return [left, (f"{page + 1}/{pages}", NOOP), right]


def help_kb():
    return kb([
        [("📂 Как печатать", "h:print"), ("🎬 Анимации", "h:anims")],
        [("🌡 Нагрев", "h:temp"), ("🧩 Макросы", "h:macros")],
        [("🛡 Безопасность", "h:safety"), ("🩺 Диагностика", "m:diag")],
        nav_row(home=True),
    ])


def state_row(state, klippy):
    """The first row is always what the user needs right now."""
    if klippy in ("shutdown", "error"):
        return [("🔄 Восстановить", "p:recover"), ("🩺 Диагностика", "m:diag")]
    if state == "printing":
        return [("⏸ Пауза", "m:pause"), ("⏹ Отменить", "m:cancel")]
    if state == "paused":
        return [("▶️ Продолжить", "m:resume"), ("⏹ Отменить", "m:cancel")]
    return [("🖨 Печать файла", "m:files:0")]


async def main_kb(pc):
    st = await pc.status()
    info = await pc.info()
    state = (st.get("print_stats") or {}).get("state")
    klippy = info.get("state") if info else "offline"

    rows = [state_row(state, klippy)]
    rows += [
        [("📊 Статус", "m:status"), ("📷 Камера", "m:cam")],
        [("🌡 Температура", "m:temp"), ("🕹 Движение", "m:move")],
        [("🎬 Анимации", "m:anims"), ("🧩 Макросы", "m:macros")],
        [("⚙️ Тюнинг", "m:tune"), ("📜 История", "m:history")],
        [("⚡ Питание", "m:power"), ("❓ Помощь", "h:top")],
        nav_row(refresh=HOME),
        [("🚨 Аварийный стоп", "m:estop")],
    ]
    return kb(rows)


async def status_kb(pc):
    """Status screen: live data, so refresh and camera are one tap away."""
    st = await pc.status()
    info = await pc.info()
    state = (st.get("print_stats") or {}).get("state")
    klippy = info.get("state") if info else "offline"
    return kb([
        state_row(state, klippy),
        [("📷 Камера", "m:cam"), (BTN_REFRESH, "m:status")],
        nav_row(back=HOME),
    ])


def history_kb():
    return kb([
        [("📊 Статус", "m:status"), (BTN_REFRESH, "m:history")],
        nav_row(back=HOME),
    ])


def diag_kb():
    return kb([
        [("🐞 Ошибки", "m:errors"), (BTN_REFRESH, "m:diag")],
        nav_row(back="h:top", home=True),
    ])


def errors_kb():
    return kb([
        [("🩺 Диагностика", "m:diag"), (BTN_REFRESH, "m:errors")],
        nav_row(back="h:top", home=True),
    ])


async def files_kb(pc, p, per=6):
    items = file_items(await pc.files())
    pages = max(1, (len(items) + per - 1) // per)
    p = max(0, min(p, pages - 1))
    rows = [[(f"▶ {name} · {fmt_size(sz)}", f"f:conf:{i}")]
            for i, (idx, name, sz) in enumerate(items[p * per:(p + 1) * per], start=p * per)]
    if not items:
        rows.append([("❓ Как загрузить файл", "h:print")])
    elif pages > 1:
        rows.append(page_row("m:files:", p, pages))
    rows.append(nav_row(back=HOME, refresh=f"m:files:{p}"))
    return kb(rows), p, pages, len(items)


async def macro_kb(pc, p, per=6):
    macros = await pc.macros()
    pages = max(1, (len(macros) + per - 1) // per)
    p = max(0, min(p, pages - 1))
    rows = [[(f"⚙ {m}", macro_callback(i, m))]
            for i, m in enumerate(macros[p * per:(p + 1) * per], start=p * per)]
    if not macros:
        rows.append([("❓ Что такое макросы", "h:macros")])
    elif pages > 1:
        rows.append(page_row("m:macros:", p, pages))
    rows.append(nav_row(back=HOME, refresh=f"m:macros:{p}"))
    return kb(rows), p, pages, len(macros)


async def temp_kb(pc):
    st = await pc.status()
    ex, bed = st.get("extruder", {}), st.get("heater_bed", {})
    return kb([
        [(f"🌡 Хотенд {ex.get('temperature', 0):.0f}/{ex.get('target', 0):.0f}", NOOP)],
        [("0", "t:hot:0"), ("200", "t:hot:200"), ("215", "t:hot:215"), ("240", "t:hot:240")],
        [(f"🛏 Стол {bed.get('temperature', 0):.0f}/{bed.get('target', 0):.0f}", NOOP)],
        [("0", "t:bed:0"), ("60", "t:bed:60"), ("80", "t:bed:80"), ("100", "t:bed:100")],
        [("❄️ Остудить всё", "t:off")],
        nav_row(back=HOME, refresh="m:temp"),
    ])


def move_kb(move):
    return kb([
        [(f"↔️ Шаг XY: {move['xy']:g} мм", "v:xydist")],
        [("⬆️ Y+", "v:y+")],
        [("⬅️ X−", "v:x-"), ("🏠 Home", "v:home"), ("➡️ X+", "v:x+")],
        [("⬇️ Y−", "v:y-")],
        [(f"↕️ Шаг Z: {move['z']:g} мм", "v:zdist")],
        [("⬆️ Z+", "v:z+"), ("⬇️ Z−", "v:z-")],
        nav_row(back=HOME),
    ])


async def tune_kb(pc):
    st = await pc.status()
    th, gm = st.get("toolhead", {}), st.get("gcode_move", {})
    sp = round((th.get("speed_factor") or 1) * 100)
    fl = round((th.get("extrude_factor") or 1) * 100)
    fan = (st.get("fan") or {}).get("speed") or 0
    ho = gm.get("homing_origin") or [0, 0, 0]
    z = ho[2] if len(ho) > 2 else 0
    return kb([
        [("🚀 Скорость −", "u:speed-"), (f"{sp}%", NOOP), ("Скорость +", "u:speed+")],
        [("💧 Поток −", "u:flow-"), (f"{fl}%", NOOP), ("Поток +", "u:flow+")],
        [(toggle("Вентилятор", fan > 0), "u:fan:on" if fan <= 0 else "u:fan:off")],
        [("⬇️ Z −0.05", "u:z-"), (f"Z-off {z:.2f}", NOOP), ("⬆️ Z +0.05", "u:z+")],
        nav_row(back=HOME, refresh="u:tune"),
    ])


def power_kb():
    return kb([
        [("🔄 Restart Klipper", "p:conf:restart")],
        [("🔁 Firmware restart", "p:conf:fw")],
        [("🔃 Reboot хоста", "p:conf:reboot")],
        [("⛔ Shutdown хоста", "p:conf:shutdown")],
        nav_row(back=HOME),
    ])


def anim_kb(idx):
    rows = []
    for it in idx.get("items", []):
        is_default = idx.get("default") == it["prefix"]
        rows.append([
            (f"▶ {it['name']}", f"play:{it['prefix']}"),
            ("⭐" if is_default else "☆", f"def:{it['prefix']}"),
            ("✏️", f"ren:{it['prefix']}"),
            ("🗑", f"del:{it['prefix']}"),
        ])
    if rows:
        rows.append([("⏹ Остановить показ", "stop")])
    else:
        rows.append([("❓ Как добавить", "h:anims")])
    rows.append(nav_row(back=HOME, refresh="m:anims"))
    return kb(rows)


def preview_kb(s):
    return kb([
        [("➖ Заливка", "pv:fill-"), (f"{s['fill']:.2f}", NOOP), ("➕ Заливка", "pv:fill+")],
        [(toggle("Инверсия", s["invert"]), "pv:invert"), (f"🎨 {s['mode']}", "pv:mode")],
        [(f"🖼 {s['fit']}", "pv:fit"), (f"🎞 Кадры: {s['frames']}", "pv:frames")],
        [(BTN_CANCEL, "pv:cancel"), ("✅ Загрузить", "pv:apply")],
    ])
