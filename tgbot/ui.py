from __future__ import annotations
import hashlib
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .printer import fmt_size, file_items, fmt_dur

def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
    ])

def cycle(lst, cur):
    return lst[(lst.index(cur) + 1) % len(lst)]

def progress_bar(frac, width=12):
    frac = max(0.0, min(1.0, float(frac)))
    n = min(width, max(0, int(frac * width)))
    return "█" * n + "░" * (width - n)

def macro_callback(index, name):
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"mc:{index}:{digest}"

async def main_kb(pc):
    st = await pc.status()
    info = await pc.info()
    state = (st.get("print_stats") or {}).get("state")
    klippy = info.get("state") if info else "offline"

    rows = [
        [("📊 Статус", "m:status"), ("🎬 Анимации", "m:anims")],
        [("📂 Файлы/старт", "m:files:0"), ("🧩 Макросы", "m:macros")],
        [("🌡 Температура", "m:temp"), ("🕹 Движение", "m:move")],
        [("⚙️ Тюнинг", "m:tune"), ("⚡ Питание", "m:power")],
        [("📷 Камера", "m:cam"), ("📜 История", "m:history")],
    ]

    if klippy in ("shutdown", "error"):
        rows.append([("🔄 Восстановить Klipper", "p:recover"), ("🔃 Reboot хоста", "p:conf:reboot")])
    elif state == "printing":
        rows.append([("⏸ Пауза", "m:pause"), ("⏹ Отмена", "m:cancel")])
    elif state == "paused":
        rows.append([("▶️ Продолжить", "m:resume"), ("⏹ Отмена", "m:cancel")])
    else:
        rows.append([("⏹ Отмена", "m:cancel")])

    rows.append([("🚨 АВАРИЙНЫЙ СТОП", "m:estop")])
    return kb(rows)

async def files_kb(pc, p):
    items = file_items(await pc.files())
    per = 6
    pages = max(1, (len(items) + per - 1) // per)
    p = max(0, min(p, pages - 1))
    rows = [[(f"▶ {name[:26]} · {fmt_size(sz)}", f"f:conf:{i}")]
            for i, (idx, name, sz) in enumerate(items[p*per:(p+1)*per], start=p*per)]
    nav = []
    if p > 0: nav.append(("⬅️", f"m:files:{p-1}"))
    nav.append(("⬅️ Меню", "m:main"))
    if p < pages - 1: nav.append(("➡️", f"m:files:{p+1}"))
    rows.append(nav)
    return kb(rows), p, pages

async def macro_kb(pc, p):
    macros = await pc.macros()
    per = 6
    pages = max(1, (len(macros) + per - 1) // per)
    p = max(0, min(p, pages - 1))
    rows = [[(f"⚙ {m[:40]}", macro_callback(i, m))]
            for i, m in enumerate(macros[p*per:(p+1)*per], start=p*per)]
    nav = []
    if p > 0: nav.append(("⬅️", f"m:macros:{p-1}"))
    nav.append(("⬅️ Меню", "m:main"))
    if p < pages - 1: nav.append(("➡️", f"m:macros:{p+1}"))
    rows.append(nav)
    return kb(rows), p, pages

async def temp_kb(pc):
    st = await pc.status()
    ex, bed = st.get("extruder", {}), st.get("heater_bed", {})
    return kb([
        [(f"Хотенд {ex.get('temperature',0):.0f}/{ex.get('target',0):.0f}", "m:noop")],
        [("0", "t:hot:0"), ("200", "t:hot:200"), ("215", "t:hot:215"), ("240", "t:hot:240")],
        [(f"Стол {bed.get('temperature',0):.0f}/{bed.get('target',0):.0f}", "m:noop")],
        [("0", "t:bed:0"), ("60", "t:bed:60"), ("80", "t:bed:80"), ("100", "t:bed:100")],
        [("🔄", "m:temp"), ("⬅️ Меню", "m:main")]
    ])

def move_kb(move):
    return kb([
        [(f"XY шаг: {move['xy']:g} мм", "v:xydist")],
        [("⬆️ Y+", "v:y+")],
        [("⬅️ X-", "v:x-"), ("🏠 Home", "v:home"), ("➡️ X+", "v:x+")],
        [("⬇️ Y-", "v:y-")],
        [(f"Z шаг: {move['z']:g} мм", "v:zdist")],
        [("⬆️ Z+", "v:z+"), ("⬇️ Z-", "v:z-")],
        [("⬅️ Меню", "m:main")]
    ])

async def tune_kb(pc):
    st = await pc.status()
    th, gm = st.get("toolhead", {}), st.get("gcode_move", {})
    sp = round((th.get("speed_factor") or 1) * 100)
    fl = round((th.get("extrude_factor") or 1) * 100)
    ho = gm.get("homing_origin") or [0,0,0]
    z = ho[2] if len(ho) > 2 else 0
    return kb([
        [("🚀 Скорость −", "u:speed-"), (f"{sp}%", "u:noop"), ("Скорость +", "u:speed+")],
        [("💧 Поток −", "u:flow-"), (f"{fl}%", "u:noop"), ("Поток +", "u:flow+")],
        [("💨 Вент ВКЛ", "u:fan:on"), ("💨 Вент ВЫКЛ", "u:fan:off")],
        [("⬇️ Z−0.05", "u:z-"), (f"Z-off: {z:.2f}", "u:noop"), ("⬆️ Z+0.05", "u:z+")],
        [("🔄", "u:tune"), ("⬅️ Меню", "m:main")]
    ])

def power_kb():
    return kb([
        [("🔄 Restart Klipper", "p:conf:restart")],
        [("🔁 Firmware restart", "p:conf:fw")],
        [("🔃 Reboot хоста", "p:conf:reboot")],
        [("⛔ Shutdown хоста", "p:conf:shutdown")],
        [("⬅️ Меню", "m:main")]
    ])

def anim_kb(idx):
    rows = []
    for it in idx["items"]:
        star = "⭐" if idx.get("default") == it["prefix"] else "☆"
        rows.append([
            (f"▶ {it['name'][:14]}", f"play:{it['prefix']}"),
            (star, f"def:{it['prefix']}"),
            ("✏️", f"ren:{it['prefix']}"),
            ("🗑", f"del:{it['prefix']}")
        ])
    rows.append([("⏹ Остановить", "stop")])
    rows.append([("⬅️ Меню", "m:main")])
    return kb(rows)

def preview_kb(s):
    return kb([
        [("Заливка −", "pv:fill-"), (f"{s['fill']:.2f}", "pv:noop"), ("Заливка +", "pv:fill+")],
        [(f"Инверсия: {'вкл' if s['invert'] else 'выкл'}", "pv:invert"), (f"Режим: {s['mode']}", "pv:mode")],
        [(f"Вписать: {s['fit']}", "pv:fit"), (f"Кадры: {s['frames']}", "pv:frames")],
        [("✅ Загрузить", "pv:apply"), ("❌ Отмена", "pv:cancel")]
    ])
