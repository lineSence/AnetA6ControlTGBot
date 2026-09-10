from __future__ import annotations
import asyncio, hashlib, logging, os, re, shutil, time
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ErrorEvent
from aiogram.exceptions import TelegramRetryAfter
from . import __version__, errorlog, anims
from .printer import file_items, fmt_dur, fmt_size, MRResult
from .safety import Safety
from .ui import (
    HOME, anim_kb, confirm_kb, cycle, diag_kb, errors_kb, files_kb, help_kb,
    history_kb, kb, macro_kb, main_kb, move_kb, nav_row, power_kb, status_kb,
    temp_kb, tune_kb,
)
from .uxkit import (
    ack, code, crumbs, esc, eta_seconds, field, is_allowed, pct, progress,
    render, screen,
)

log = logging.getLogger("tgbot")
router = Router()

LOCKS = {}
SESSIONS = {}
RENAMING = {}
PENDING = {}
MOVE_BY_CHAT = {}
DIAG = {"started": time.time(), "last_error": None, "last_error_id": None}
SAFETY = Safety()

# Whole-word tokens plus name prefixes. Substring matching gave false hits.
DANGEROUS_MACRO_TOKENS = frozenset({
    "G28", "SAVE_CONFIG", "FIRMWARE_RESTART", "RESTART", "M112",
    "BED_MESH", "PID_CALIBRATE", "TESTZ", "SHUTDOWN",
})
DANGEROUS_MACRO_PREFIXES = ("PROBE_", "BED_MESH", "PID_CALIBRATE", "TESTZ")

# Telegram rejects messages longer than 4096 characters.
TG_TEXT_LIMIT = 3800

def chat_lock(c):
    if c not in LOCKS: LOCKS[c] = asyncio.Lock()
    return LOCKS[c]

def move_state(cfg, chat):
    state = MOVE_BY_CHAT.setdefault(chat, dict(cfg.move_default or {"xy": 10, "z": 1}))
    return state

def clear_temp_file(path):
    try:
        if path and os.path.exists(path): os.remove(path)
    except Exception:
        pass

def macro_digest(name):
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]

def macro_is_dangerous(name: str) -> bool:
    """Match whole name parts. PRESTART stays safe, MY_G28_MACRO does not."""
    upper = str(name or "").upper()
    if upper.startswith(DANGEROUS_MACRO_PREFIXES):
        return True
    return any(
        re.search(rf"(?:^|[^A-Z0-9]){re.escape(token)}(?:$|[^A-Z0-9])", upper)
        for token in DANGEROUS_MACRO_TOKENS
    )

def allowed(cfg, user) -> bool:
    """Same check as the access middleware. Kept as a second line of defence."""
    return is_allowed(cfg, user)

def chunk_text(text, size=TG_TEXT_LIMIT):
    """Split text so every part fits into one Telegram message."""
    text = str(text or "")
    if not text:
        return [""]
    parts, buf, length = [], [], 0
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if buf:
                parts.append("".join(buf)); buf, length = [], 0
            parts.append(line[:size])
            line = line[size:]
        if length + len(line) > size and buf:
            parts.append("".join(buf)); buf, length = [], 0
        buf.append(line); length += len(line)
    if buf:
        parts.append("".join(buf))
    return parts or [""]

async def send_long(target, text, **kwargs):
    """Send long text as several messages instead of failing."""
    for part in chunk_text(text):
        await target(part, **kwargs)

def set_pending(chat, payload):
    """Store a confirmation with a timestamp."""
    payload["ts"] = time.time()
    PENDING[chat] = payload
    return payload

def take_pending(chat, kind, max_age=None):
    """Pop a confirmation and check its kind and age."""
    p = PENDING.pop(chat, None)
    if not p or p.get("kind") != kind:
        return None
    if max_age and time.time() - float(p.get("ts") or 0) > float(max_age):
        return None
    return p

def purge_state(cfg=None, now=None) -> int:
    """Drop stale confirmations, rename prompts, sessions and locks."""
    now = float(now if now is not None else time.time())
    pending_ttl = float(getattr(cfg, "pending_ttl_seconds", 900) or 900)
    session_ttl = float(getattr(cfg, "session_ttl_seconds", 3600) or 3600)
    removed = 0
    for chat, p in list(PENDING.items()):
        if now - float((p or {}).get("ts") or 0) > pending_ttl:
            PENDING.pop(chat, None); removed += 1
    for chat, r in list(RENAMING.items()):
        if now - float((r or {}).get("ts") or 0) > pending_ttl:
            RENAMING.pop(chat, None); removed += 1
    removed += anims.purge_sessions(SESSIONS, session_ttl, now)
    for chat, lock in list(LOCKS.items()):
        if not lock.locked() and chat not in PENDING and chat not in SESSIONS and chat not in RENAMING:
            LOCKS.pop(chat, None)
    return removed

async def janitor_loop(cfg, interval=300):
    """Background cleanup so per-chat dictionaries cannot grow forever."""
    while True:
        await asyncio.sleep(interval)
        try:
            purge_state(cfg)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("janitor failed")

PATH_ROOT = "пульт"

POWER_NAMES = {
    "restart": "Restart Klipper",
    "fw": "Firmware restart",
    "reboot": "Reboot хоста",
    "shutdown": "Shutdown хоста",
}

# Every destructive screen says what will happen, in plain words.
POWER_IMPACT = {
    "restart": "Klipper перезапустится. Активная печать прервётся.",
    "fw": "Плата перезапустит прошивку. Активная печать прервётся.",
    "reboot": "Хост перезагрузится. Бот пропадёт на одну-две минуты.",
    "shutdown": "Хост выключится. Включить его можно только руками.",
}

HELP_TOPICS = {
    "top": (
        "❓ Помощь",
        [
            "Бот управляет принтером через Moonraker.",
            "Главный способ работы — кнопки. Команды нужны для быстрого доступа:",
            "",
            "/menu · /status · /files · /anims · /errors · /diag · /help",
            "",
            "Выберите тему ниже.",
        ],
    ),
    "print": (
        "📂 Как напечатать файл",
        [
            "1. Пришлите файл .gcode в этот чат.",
            "2. Я загружу его в Moonraker и покажу кнопку «Печатать».",
            "3. Подтвердите запуск — печать начнётся сразу.",
            "",
            "Файлы, уже загруженные в Moonraker, лежат в разделе «Файлы».",
            "Во время печати в меню появляются «Пауза» и «Отменить».",
        ],
    ),
    "anims": (
        "🎬 Анимации на экране",
        [
            "1. Пришлите GIF, фото или короткое видео.",
            "2. Настройте заливку, инверсию и число кадров кнопками.",
            "3. Нажмите «Загрузить» — я соберу конфиг и перезапущу Klipper.",
            "",
            "⭐ помечает анимацию, которая включается при старте.",
            "Во время печати менять анимации нельзя.",
        ],
    ),
    "temp": (
        "🌡 Нагрев",
        [
            "Кнопки-пресеты задают целевую температуру сразу.",
            "Ноль выключает нагрев. «Остудить всё» гасит и хотенд, и стол.",
            "",
            "Первая цифра — температура сейчас, вторая — цель.",
        ],
    ),
    "macros": (
        "🧩 Макросы Klipper",
        [
            "Список берётся из printer.cfg автоматически.",
            "Опасные макросы (G28, BED_MESH, PID_CALIBRATE и похожие) требуют подтверждения.",
            "",
            "Во время печати макросы блокируются.",
        ],
    ),
    "safety": (
        "🛡 Безопасность",
        [
            "🚨 Аварийный стоп срабатывает сразу, без подтверждения.",
            "После него Klipper восстанавливают кнопкой в меню.",
            "",
            "Питание, удаление и опасные макросы требуют подтверждения.",
            "Подтверждение живёт ограниченное время и отменяется само.",
            "Доступ есть только у ID из allowed_user_ids.",
        ],
    ),
}


def path(*parts):
    """Breadcrumbs for a screen: the user always sees where they are."""
    return crumbs(PATH_ROOT, *parts)


async def show(cq, bot, text, kbc=None):
    """One screen lives in one message: rewrite it in place."""
    await render(cq, bot, text, kbc)


async def loading(cq, bot, title, where=None):
    """Instant feedback before a slow step, so the screen never looks frozen."""
    await render(cq, bot, screen(f"⏳ {title}…", ["Секунду."], path=where), None)


def ttl_meta(cfg):
    minutes = max(1, int(float(getattr(cfg, "pending_ttl_seconds", 900) or 900) // 60))
    return f"подтверждение действует {minutes} мин"


def start_text(user):
    """First screen: what this bot is, and the two things you can send it."""
    name = str(getattr(user, "first_name", "") or "").strip()
    hello = f"Привет, {esc(name)}!" if name else "Привет!"
    return screen(
        "🖨 Пульт принтера",
        [
            f"{hello} Отсюда управляют печатью, нагревом и анимациями на экране.",
            "Пришлите файл .gcode — предложу напечатать.",
            "Пришлите GIF или фото — соберу анимацию для дисплея.",
        ],
        path=path(),
        meta=f"версия {__version__} · /help — справка",
    )


def menu_screen():
    return screen(
        "🖨 Пульт принтера",
        ["Выберите раздел кнопкой ниже.", "Красная кнопка внизу — аварийный стоп."],
        path=path(),
    )


def help_screen(topic="top"):
    title, body = HELP_TOPICS.get(topic, HELP_TOPICS["top"])
    return screen(title, body, path=path("помощь", "" if topic == "top" else topic))


def whoami_text(user, chat):
    return screen(
        "🪪 Ваш доступ",
        [
            field("👤", "Имя", getattr(user, "first_name", "—")),
            f"🆔 Ваш ID: {code(getattr(user, 'id', '—'))}",
            f"💬 Чат: {code(chat)}",
            "",
            "Этот ID должен быть в allowed_user_ids в config.yaml.",
        ],
        path=path("доступ"),
    )


def unknown_text(raw):
    """Never stay silent: silence looks like a broken bot."""
    first = [f"Не понял: {code(str(raw)[:80])}"] if raw else ["Пустое сообщение."]
    return screen(
        "🤔 Такой команды нет",
        first + ["", "Я работаю кнопками. Откройте меню ниже или наберите /help."],
        path=path(),
    )


def files_screen(p, pages, total):
    if not total:
        return screen(
            "📂 Файлов нет",
            ["В Moonraker нет ни одного .gcode.", "Пришлите файл в этот чат — я загружу его сам."],
            path=path("файлы"),
        )
    return screen(
        "📂 Файлы для печати",
        ["Выберите файл. Перед запуском спрошу подтверждение."],
        path=path("файлы"),
        meta=f"файлов: {total} · страница {p + 1} из {pages}",
    )


def macros_screen(p, pages, total):
    if not total:
        return screen(
            "🧩 Макросов нет",
            ["Klipper не вернул ни одного макроса.", "Проверьте printer.cfg и состояние Klipper в /diag."],
            path=path("макросы"),
        )
    return screen(
        "🧩 Макросы Klipper",
        ["Опасные макросы спрошу подтвердить."],
        path=path("макросы"),
        meta=f"макросов: {total} · страница {p + 1} из {pages}",
    )


def anims_screen(idx):
    items = idx.get("items") or []
    if not items:
        return screen(
            "🎬 Анимаций нет",
            ["Пришлите GIF, фото или короткое видео.", "Я покажу превью и соберу анимацию для экрана."],
            path=path("анимации"),
        )
    return screen(
        "🎬 Анимации на экране",
        ["▶ показать · ⭐ включать при старте", "✏️ переименовать · 🗑 удалить"],
        path=path("анимации"),
        meta=f"анимаций: {len(items)}",
    )


def temp_screen():
    return screen(
        "🌡 Температуры",
        ["Выберите пресет. Ноль выключает нагрев.", "В кнопках сверху: текущая / целевая."],
        path=path("температура"),
    )


def move_screen(move):
    return screen(
        "🕹 Движение",
        [
            "Шаг меняется кнопками «Шаг XY» и «Шаг Z».",
            "После включения принтера сначала нужен Home.",
        ],
        path=path("движение"),
        meta=f"шаг XY {move['xy']:g} мм · шаг Z {move['z']:g} мм",
    )


def tune_screen():
    return screen(
        "⚙️ Тюнинг на ходу",
        ["Скорость и поток меняются шагом 10%.", "Z-offset — шаг 0.05 мм, работает и во время печати."],
        path=path("тюнинг"),
    )


def power_screen():
    return screen(
        "⚡ Питание и перезапуск",
        ["Действия затрагивают весь принтер.", "Перед выполнением спрошу подтверждение."],
        path=path("питание"),
    )


def recover_screen():
    return screen(
        "🔄 Восстановление Klipper",
        [
            "Klipper в состоянии shutdown или error.",
            "",
            "Restart — перезапуск службы, подходит в большинстве случаев.",
            "Firmware restart — если плата сообщает об ошибке MCU.",
            "",
            "Причину смотрите в /errors.",
        ],
        path=path("восстановление"),
    )


def file_confirm_screen(cfg, filename, where="файлы"):
    return screen(
        "▶ Запустить печать?",
        [
            field("📄", "Файл", filename),
            "",
            "Принтер начнёт печать сразу после подтверждения.",
            "Проверьте: стол чистый, сопло свободно, пруток загружен.",
        ],
        path=path(where, "запуск"),
        meta=ttl_meta(cfg),
    )


def power_confirm_screen(cfg, action):
    return screen(
        f"⚠️ {POWER_NAMES.get(action, action)}?",
        [
            POWER_IMPACT.get(action, "Действие затронет весь принтер."),
            "",
            "Если идёт печать, действие будет отклонено.",
        ],
        path=path("питание", "подтверждение"),
        meta=ttl_meta(cfg),
    )


def macro_confirm_screen(cfg, name):
    return screen(
        "⚠️ Опасный макрос",
        [
            field("🧩", "Макрос", name),
            "",
            "Макрос может двигать оси или менять конфигурацию.",
            "Запускайте только если знаете, что он делает.",
        ],
        path=path("макросы", "подтверждение"),
        meta=ttl_meta(cfg),
    )


def anim_delete_screen(cfg, name):
    return screen(
        "🗑 Удалить анимацию?",
        [
            field("🎬", "Анимация", name),
            "",
            "Файл анимации будет удалён, Klipper перезапустится.",
            "Если что-то пойдёт не так, я верну всё обратно сам.",
        ],
        path=path("анимации", "удаление"),
        meta=ttl_meta(cfg),
    )


def error_screen(title, lines, retry=None, where=None):
    """Errors say what happened, what to do next, and give a way back."""
    rows = []
    if retry:
        rows.append([("🔄 Повторить", retry)])
    rows.append([("🩺 Диагностика", "m:diag")])
    rows.append(nav_row(back=HOME))
    return screen(title, lines, path=where or path()), kb(rows)

STATE_TITLES = {
    "printing": "🖨 Печать идёт",
    "paused": "⏸ Печать на паузе",
    "complete": "✅ Печать завершена",
    "cancelled": "⛔ Печать отменена",
    "error": "🚨 Ошибка печати",
}


async def status_text(pc):
    st = await pc.status()
    info = await pc.info()
    if not st and not info:
        return screen(
            "❌ Нет связи",
            [
                "Moonraker или Klipper не ответили.",
                "",
                "Проверьте питание платы и службу moonraker.",
                "Подробности: /diag",
            ],
            path=path("статус"),
        )

    ps = st.get("print_stats", {})
    ds = st.get("display_status", {})
    ex, bed = st.get("extruder", {}), st.get("heater_bed", {})
    klippy_state = info.get("state", "offline") if info else "offline"
    klippy_msg = (info.get("state_message") or info.get("message")) if info else None
    state = ps.get("state", "standby")

    body = [
        field("🧠", "Klipper", klippy_state),
        field("📄", "Файл", ps.get("filename") or "—"),
    ]
    if klippy_msg:
        body.append(f"❗ {esc(klippy_msg)}")
    if state in ("printing", "paused"):
        frac = float(ds.get("progress") or 0)
        done = float(ps.get("total_duration") or 0)
        left = eta_seconds(done, frac)
        body += ["", f"{progress(frac)} {pct(frac)}", field("⏱", "Прошло", fmt_dur(done))]
        if left:
            body.append(field("🏁", "Осталось", "~" + fmt_dur(left)))
    body += [
        "",
        field("🌡", "Хотенд", f"{ex.get('temperature', 0):.1f} / {ex.get('target', 0):.0f} °C"),
        field("🛏", "Стол", f"{bed.get('temperature', 0):.1f} / {bed.get('target', 0):.0f} °C"),
    ]
    return screen(
        STATE_TITLES.get(state, "📊 Принтер свободен"),
        body,
        path=path("статус"),
        meta=time.strftime("обновлено %H:%M:%S"),
    )

async def diag_text(pc, ws, cfg):
    du = shutil.disk_usage("/")
    info = await pc.info()
    wst = "n/a" if ws is None else ("🟢" if ws.connected else "🔴")
    last = await asyncio.to_thread(errorlog.recent, cfg, 1)
    last_text = "—"
    if last:
        last_text = f"#{last[0][0]} {last[0][5]}"
    body = [
        field("🏷", "Версия бота", __version__),
        field("⏱", "Аптайм", fmt_dur(time.time() - DIAG["started"])),
        field("🔌", "WebSocket", wst),
        field("♻️", "Переподключений", getattr(ws, "reconnects", 0)),
        field("🌐", "Moonraker", cfg.moonraker),
        field("🧠", "Klipper", info.get("state", "offline") if info else "offline"),
        field("💾", "Свободно на диске", f"{du.free // 1024 // 1024} МБ"),
        field("🚨", "Последняя ошибка", last_text),
    ]
    return screen(
        "🩺 Диагностика",
        body,
        path=path("диагностика"),
        meta=time.strftime("проверено %H:%M:%S"),
    )

async def errors_text(cfg):
    rows = await asyncio.to_thread(errorlog.recent, cfg, 10)
    if not rows:
        return screen(
            "🟢 Ошибок нет",
            ["Журнал пуст. Это хороший знак.", "", "Ошибки печати и связи попадают сюда сами."],
            path=path("ошибки"),
        )
    body = []
    for eid, ts, source, state, filename, message, details, ack_flag in rows:
        body.append(f"<b>#{eid}</b> · {esc(ts)} · {esc(source)}")
        body.append(esc(str(message)[:160]))
        body.append(field("📄", "Файл", filename or "—"))
        body.append("")
    body.append("Подробности одной записи: <code>/error 12</code>")
    return screen("🚨 Последние ошибки", body, path=path("ошибки"))

async def history_text(pc):
    jobs = await pc.history(10)
    if not jobs:
        return screen(
            "📜 История пуста",
            ["Здесь появятся последние печати.", "", "Начните с кнопки «🖨 Печать файла»."],
            path=path("история"),
        )
    body = []
    for j in jobs:
        em = {"completed": "✅", "cancelled": "⛔", "error": "🚨"}.get(j.get("status"), "❔")
        body.append(f"{em} {esc(str(j.get('filename', '?'))[:40])} · {fmt_dur(j.get('total_duration'))}")
    return screen(
        "📜 Последние печати",
        body,
        path=path("история"),
        meta=f"записей: {len(jobs)}",
    )

# One command name can be typed several ways. Buttons stay the main path.
COMMAND_ALIASES = {
    "/start": "start", "start": "start",
    "/menu": "menu", "menu": "menu", "меню": "menu",
    "/status": "status", "статус": "status",
    "/files": "files", "файлы": "files",
    "/anims": "anims", "анимации": "anims",
    "/macros": "macros", "макросы": "macros",
    "/temp": "temp", "температура": "temp",
    "/history": "history", "история": "history",
    "/diag": "diag",
    "/errors": "errors",
    "/error": "error",
    "/stop": "stop", "стоп": "stop",
    "/whoami": "whoami", "/id": "whoami",
    "/help": "help", "help": "help", "помощь": "help",
}


@router.message(F.text)
async def on_text(msg: Message, bot, pc, cfg, ws):
    if not allowed(cfg, msg.from_user): return
    chat = msg.chat.id

    if chat in RENAMING:
        idx = anims.load_index(cfg)
        p = (RENAMING.pop(chat) or {}).get("prefix")
        it = next((x for x in idx["items"] if x["prefix"] == p), None)
        if it:
            it["name"] = msg.text.strip()[:40] or "Анимация"
            anims.save_index(cfg, idx)
            await msg.answer(
                screen("✏️ Имя обновлено", [field("🎬", "Новое имя", it["name"])], path=path("анимации")),
                reply_markup=anim_kb(idx),
                parse_mode="HTML",
            )
        return

    raw = (msg.text or "").strip()
    first = raw.split()[0].split("@")[0].lower() if raw else ""
    cmd = COMMAND_ALIASES.get(first, "")

    if cmd == "start":
        await msg.answer(start_text(msg.from_user), reply_markup=await main_kb(pc), parse_mode="HTML")
    elif cmd == "menu":
        await msg.answer(menu_screen(), reply_markup=await main_kb(pc), parse_mode="HTML")
    elif cmd == "status":
        await msg.answer(await status_text(pc), reply_markup=await status_kb(pc), parse_mode="HTML")
    elif cmd == "files":
        kbc, p, pages, total = await files_kb(pc, 0)
        await msg.answer(files_screen(p, pages, total), reply_markup=kbc, parse_mode="HTML")
    elif cmd == "anims":
        idx = anims.load_index(cfg)
        await msg.answer(anims_screen(idx), reply_markup=anim_kb(idx), parse_mode="HTML")
    elif cmd == "macros":
        kbc, p, pages, total = await macro_kb(pc, 0)
        await msg.answer(macros_screen(p, pages, total), reply_markup=kbc, parse_mode="HTML")
    elif cmd == "temp":
        await msg.answer(temp_screen(), reply_markup=await temp_kb(pc), parse_mode="HTML")
    elif cmd == "history":
        await msg.answer(await history_text(pc), reply_markup=history_kb(), parse_mode="HTML")
    elif cmd == "diag":
        await msg.answer(await diag_text(pc, ws, cfg), reply_markup=diag_kb(), parse_mode="HTML")
    elif cmd == "errors":
        await send_long(msg.answer, await errors_text(cfg), parse_mode="HTML")
    elif cmd == "error":
        parts = raw.split()
        if len(parts) != 2 or not parts[1].isdigit():
            await msg.answer(
                screen("🚨 Просмотр ошибки", ["Формат: <code>/error 12</code>", "", "Список последних: /errors"], path=path("ошибки")),
                reply_markup=errors_kb(),
                parse_mode="HTML",
            )
        else:
            row = await asyncio.to_thread(errorlog.get, cfg, int(parts[1]))
            if not row:
                await msg.answer(
                    screen("🚨 Запись не найдена", [f"Ошибки #{esc(parts[1])} нет в журнале.", "", "Список последних: /errors"], path=path("ошибки")),
                    reply_markup=errors_kb(),
                    parse_mode="HTML",
                )
            else:
                eid, ts, source, state, filename, message, details, ack_flag = row
                # Plain text on purpose: a Klipper log would break HTML markup.
                await send_long(
                    msg.answer,
                    f"🚨 Ошибка #{eid}\n🕒 {ts}\nИсточник: {source}\n"
                    f"Состояние: {state or '—'}\n📄 {filename or '—'}\n"
                    f"❌ {message}\n\nЛог:\n{(details or '—')[-6000:]}"
                )
    elif cmd == "stop":
        r = await pc.gcode("ANIMS_STOP_ALL")
        await msg.answer("⏹ Анимация остановлена." if r.ok else f"❌ Не удалось остановить: {r.error}")
    elif cmd == "whoami":
        await msg.answer(whoami_text(msg.from_user, chat), parse_mode="HTML")
    elif cmd == "help":
        await msg.answer(help_screen("top"), reply_markup=help_kb(), parse_mode="HTML")
    else:
        await msg.answer(unknown_text(raw), reply_markup=await main_kb(pc), parse_mode="HTML")

@router.message(F.animation | F.photo | F.video | F.document)
async def on_media(msg: Message, bot, pc, cfg):
    if not allowed(cfg, msg.from_user): return
    if msg.document:
        fn = (msg.document.file_name or "").lower()
        if fn.endswith((".gcode",".g",".gc",".ngc")):
            await handle_gcode_upload(bot, msg, pc, cfg)
            return
        if msg.document.mime_type != "image/gif" and not fn.endswith(".gif"):
            await msg.answer(
                screen(
                    "🤔 Не тот формат",
                    [
                        "Я принимаю .gcode для печати и GIF, фото или видео для анимации.",
                        "",
                        f"Пришли: {code(fn or '—')}",
                    ],
                    path=path(),
                ),
                parse_mode="HTML",
            )
            return
    await anims.start_session(bot, cfg, msg, msg.chat.id, SESSIONS)

async def handle_gcode_upload(bot, msg, pc, cfg):
    raw_name = msg.document.file_name or "upload.gcode"
    safe_name = os.path.basename(raw_name)
    tmp_path = f"/tmp/up_{msg.chat.id}_{os.getpid()}_{safe_name}"
    try:
        f = await bot.get_file(msg.document.file_id)
        await bot.download_file(f.file_path, tmp_path)
        r = await pc.upload_gcode(tmp_path, safe_name)
        if not r.ok:
            eid = await errorlog.register_async(cfg, "moonraker", r.error or "upload failed", str(r.raw or ""), filename=safe_name)
            await bot.send_message(msg.chat.id, f"❌ Ошибка загрузки #{eid}: {r.error}")
            return
        item = r.result.get("item", {}) if isinstance(r.result, dict) else {}
        sz = fmt_size(item.get("size",0)) if isinstance(item, dict) else ""
        log.info("gcode uploaded: %s (%s)", safe_name, sz)
        set_pending(msg.chat.id, {"kind":"gcode", "filename":safe_name})
        await bot.send_message(
            msg.chat.id,
            screen(
                "📦 Файл загружен",
                [
                    field("📄", "Файл", safe_name),
                    field("💾", "Размер", sz or "—"),
                    "",
                    "Запустить печать сейчас?",
                ],
                path=path("файлы", "загрузка"),
                meta=ttl_meta(cfg),
            ),
            reply_markup=confirm_kb("g:start", "✅ Печатать", HOME),
            parse_mode="HTML",
        )
    except Exception as e:
        eid = await errorlog.register_async(cfg, "upload", str(e), repr(e), filename=safe_name)
        await bot.send_message(msg.chat.id, f"❌ Ошибка загрузки #{eid}: {e}")
    finally:
        clear_temp_file(tmp_path)

async def jog(pc, axis, sign, move):
    d = move["z"] * sign if axis == "z" else move["xy"] * sign
    feed = 3000 if axis != "z" else 300
    return await pc.gcode(f"G91\nG1 {axis.upper()}{d:g} F{feed}\nG90")

def _macro_from_data(data):
    try:
        _, idx, digest = data.split(":", 2)
        return int(idx), digest
    except Exception:
        return None, None

@router.callback_query()
async def on_cb(cq: CallbackQuery, bot, pc, cfg, ws):
    if not allowed(cfg, cq.from_user):
        await cq.answer()
        return
    if cq.message is None:
        await ack(cq, "Экран устарел. Наберите /menu", alert=True)
        return

    chat, data = cq.message.chat.id, cq.data or ""
    idx = anims.load_index(cfg)

    if data.startswith("pv:"):
        s = SESSIONS.get(chat)
        if not s:
            await cq.answer("Сессия устарела")
            return
        a = data[3:]
        if a == "noop":
            await cq.answer(); return
        if a == "cancel":
            clear_temp_file(s.get("src"))
            anims.close_session(SESSIONS, chat)
            if s.get("msg_id"):
                try: await bot.delete_message(chat, s["msg_id"])
                except Exception: pass
            await cq.answer("Отменено")
            return
        if a == "apply":
            await cq.answer()
            async with chat_lock(chat):
                ok = await anims.apply_session(bot, cfg, chat, s, pc, SAFETY)
            clear_temp_file(s.get("src"))
            anims.close_session(SESSIONS, chat)
            if not ok and s.get("last_error_id"):
                DIAG["last_error_id"] = s["last_error_id"]
            return
        if a == "fill-": s["fill"] = max(0.10, round(s["fill"] - 0.05, 2))
        elif a == "fill+": s["fill"] = min(0.60, round(s["fill"] + 0.05, 2))
        elif a == "invert": s["invert"] = not s["invert"]
        elif a == "mode": s["mode"] = "dither" if s["mode"] == "threshold" else "threshold"
        elif a == "fit": s["fit"] = cycle(cfg.fits, s["fit"])
        elif a == "frames": s["frames"] = cycle(cfg.frames, s["frames"])
        await cq.answer()
        await anims.refresh_preview(bot, cfg, chat, s)
        return

    if data == "stop":
        r = await pc.gcode("ANIMS_STOP_ALL")
        await cq.answer("Остановлено" if r.ok else f"Ошибка: {r.error}")
        return

    if data.startswith("play:"):
        p = data.split(":",1)[1]
        r = await pc.gcode(f"{p.upper()}_START")
        it = next((x for x in idx["items"] if x["prefix"] == p), None)
        await cq.answer((f"▶ {it['name'] if it else p}") if r.ok else f"❌ {r.error}")
        if not r.ok:
            await errorlog.register_async(cfg, "animation", r.error or "play failed", str(r.raw or ""), filename=it["name"] if it else p)
        return

    if data.startswith("def:"):
        p = data.split(":",1)[1]
        idx["default"] = None if idx.get("default") == p else p
        anims.save_index(cfg, idx)
        anims.regen_autostart(cfg, idx)
        await ack(cq, "⭐ Автозапуск обновлён")
        await show(cq, bot, anims_screen(idx), anim_kb(idx))
        return

    if data.startswith("ren:"):
        RENAMING[chat] = {"prefix": data.split(":",1)[1], "ts": time.time()}
        await ack(cq, "Жду новое имя")
        await bot.send_message(
            chat,
            screen(
                "✏️ Новое имя анимации",
                ["Пришлите имя одним сообщением.", "Не длиннее 40 символов."],
                path=path("анимации", "переименование"),
            ),
            parse_mode="HTML",
        )
        return

    if data.startswith("del:"):
        p = data.split(":",1)[1]
        it = next((x for x in idx["items"] if x["prefix"] == p), None)
        if not it:
            await ack(cq, "Анимация уже удалена", alert=True)
            await show(cq, bot, anims_screen(idx), anim_kb(idx))
            return
        # Destructive step: ask on a screen, never inside a toast.
        set_pending(chat, {"kind": "anim_del", "prefix": p})
        await ack(cq)
        await show(cq, bot, anim_delete_screen(cfg, it["name"]),
                   confirm_kb(f"adel:{p}", "🗑 Удалить", "m:anims"))
        return

    if data.startswith("adel:"):
        p = data.split(":",1)[1]
        pend = take_pending(chat, "anim_del", getattr(cfg, "pending_ttl_seconds", 900))
        if not pend or pend.get("prefix") != p:
            await ack(cq, "Подтверждение устарело", alert=True)
            await show(cq, bot, anims_screen(idx), anim_kb(idx))
            return
        await ack(cq, "Удаляю…")
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_idle(pc)
            if not ok:
                await bot.send_message(chat, f"❌ Удаление запрещено: {reason}")
                return
            it = next((x for x in idx["items"] if x["prefix"] == p), None)
            if not it: return
            backup = None
            try:
                backup = anims.snapshot_anims(cfg)
                fp = os.path.join(anims.anims_dir(cfg), it["file"])
                if os.path.exists(fp): os.remove(fp)
                idx["items"] = [x for x in idx["items"] if x["prefix"] != p]
                if idx.get("default") == p: idx["default"] = None
                anims.save_index(cfg, idx)
                anims.regen_stop_all(cfg, idx)
                anims.regen_autostart(cfg, idx)
                rr, err = await anims.restart_and_wait(pc)
                if not rr:
                    anims.rollback_anims(cfg, backup)
                    old = anims.load_index(cfg)
                    anims.regen_stop_all(cfg, old)
                    anims.regen_autostart(cfg, old)
                    await anims.restart_and_wait(pc)
                    eid = await errorlog.register_async(cfg, "animation", f"delete restart: {err}", err, filename=it["name"])
                    await bot.send_message(chat, f"❌ Не удалось удалить — выполнен откат #{eid}: {err}")
                    return
                await bot.send_message(chat, f"«{it['name']}» удалена.")
            except Exception as e:
                if backup:
                    try:
                        anims.rollback_anims(cfg, backup)
                    except Exception:
                        log.exception("delete rollback failed")
                eid = await errorlog.register_async(cfg, "animation", str(e), repr(e), filename=it.get("name"))
                await bot.send_message(chat, f"❌ Ошибка удаления #{eid}: {e}")
            finally:
                anims.cleanup_backups(cfg)
        return

    if data == "m:noop":
        await ack(cq)
        return
    if data == "m:main":
        await ack(cq)
        await show(cq, bot, menu_screen(), await main_kb(pc))
        return
    if data == "m:status":
        await ack(cq)
        await show(cq, bot, await status_text(pc), await status_kb(pc))
        return
    if data == "m:history":
        await ack(cq)
        await loading(cq, bot, "Читаю историю", path("история"))
        await show(cq, bot, await history_text(pc), history_kb())
        return
    if data == "m:cam":
        await ack(cq, "Снимаю кадр…")
        if not await ws.send_cam(caption=time.strftime("📷 Камера · %H:%M:%S")):
            await bot.send_message(
                chat,
                screen(
                    "📷 Кадр не получен",
                    ["Камера не ответила.", "", "Проверьте camera_url в конфиге и службу камеры."],
                    path=path("камера"),
                ),
                parse_mode="HTML",
            )
        return
    if data.startswith("h:"):
        await ack(cq)
        await show(cq, bot, help_screen(data[2:] or "top"), help_kb())
        return
    if data == "m:diag":
        await ack(cq)
        await show(cq, bot, await diag_text(pc, ws, cfg), diag_kb())
        return
    if data == "m:errors":
        await ack(cq)
        await show(cq, bot, await errors_text(cfg), errors_kb())
        return
    if data == "m:anims":
        await ack(cq)
        await show(cq, bot, anims_screen(idx), anim_kb(idx))
        return
    if data == "m:macros" or data.startswith("m:macros:"):
        await ack(cq)
        p = int(data.split(":")[2]) if data.startswith("m:macros:") else 0
        await loading(cq, bot, "Читаю макросы", path("макросы"))
        kbc, p, pages, total = await macro_kb(pc, p)
        await show(cq, bot, macros_screen(p, pages, total), kbc)
        return

    if data.startswith("mc:run:"):
        try:
            _, _, idx_s, digest = data.split(":", 3)
            i = int(idx_s)
        except Exception:
            await cq.answer("Некорректная команда"); return
        pending = take_pending(chat, "macro", getattr(cfg, "pending_ttl_seconds", 900))
        if not pending:
            await ack(cq, "Подтверждение устарело. Откройте макросы заново", alert=True)
            kbc, page, pages, total = await macro_kb(pc, 0)
            await show(cq, bot, macros_screen(page, pages, total), kbc)
            return
        if int(pending.get("index", -1)) != i or pending.get("digest") != digest:
            await ack(cq, "Подтверждение не совпадает. Откройте макросы заново", alert=True); return
        macros = await pc.macros()
        if i >= len(macros) or macro_digest(macros[i]) != digest:
            await ack(cq, "Макрос изменился. Откройте макросы заново", alert=True); return
        await ack(cq, "Выполняю…")
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_idle(pc)
            if not ok:
                await show(
                    cq, bot,
                    screen("❌ Макрос запрещён", [field("🧩", "Макрос", macros[i]), "", esc(reason)], path=path("макросы")),
                    kb([[("🧩 К макросам", "m:macros")], nav_row(back=HOME)]),
                )
                return
            r = await pc.gcode(macros[i])
        if r.ok:
            await show(
                cq, bot,
                screen("🧩 Макрос выполнен", [field("🧩", "Макрос", macros[i])], path=path("макросы"), meta=time.strftime("готово в %H:%M:%S")),
                kb([[("🧩 К макросам", "m:macros")], nav_row(back=HOME)]),
            )
        else:
            eid = await errorlog.register_async(cfg, "macro", r.error or "macro failed", str(r.raw or ""), filename=macros[i])
            text, markup = error_screen(
                "⚠️ Макрос не выполнен",
                [field("🧩", "Макрос", macros[i]), field("🚨", "Ошибка", r.error or "неизвестно"), "", f"Запись #{eid}. Подробности: /error {eid}"],
                retry="m:macros",
                where=path("макросы"),
            )
            await show(cq, bot, text, markup)
        return

    if data.startswith("mc:"):
        i, digest = _macro_from_data(data)
        if i is None:
            await ack(cq, "Некорректный макрос", alert=True); return
        macros = await pc.macros()
        if i >= len(macros) or macro_digest(macros[i]) != digest:
            await ack(cq, "Список макросов изменился. Обновляю", alert=True)
            kbc, page, pages, total = await macro_kb(pc, 0)
            await show(cq, bot, macros_screen(page, pages, total), kbc)
            return
        name = macros[i]
        if cfg.dangerous_macros_require_confirmation and macro_is_dangerous(name):
            set_pending(chat, {"kind":"macro","index":i,"digest":digest,"name":name})
            await ack(cq)
            await show(cq, bot, macro_confirm_screen(cfg, name),
                       confirm_kb(f"mc:run:{i}:{digest}", "✅ Выполнить", "m:macros"))
            return
        await ack(cq, "Выполняю…")
        ok, reason = await SAFETY.require_idle(pc)
        if not ok:
            await show(
                cq, bot,
                screen("❌ Макрос запрещён", [field("🧩", "Макрос", name), "", esc(reason)], path=path("макросы")),
                kb([[("🧩 К макросам", "m:macros")], nav_row(back=HOME)]),
            )
            return
        r = await pc.gcode(name)
        if r.ok:
            await show(
                cq, bot,
                screen("🧩 Макрос выполнен", [field("🧩", "Макрос", name)], path=path("макросы"), meta=time.strftime("готово в %H:%M:%S")),
                kb([[("🧩 К макросам", "m:macros")], nav_row(back=HOME)]),
            )
        else:
            eid = await errorlog.register_async(cfg, "macro", r.error or "macro failed", str(r.raw or ""), filename=name)
            text, markup = error_screen(
                "⚠️ Макрос не выполнен",
                [field("🧩", "Макрос", name), field("🚨", "Ошибка", r.error or "неизвестно"), "", f"Запись #{eid}. Подробности: /error {eid}"],
                retry="m:macros",
                where=path("макросы"),
            )
            await show(cq, bot, text, markup)
        return

    if data.startswith("m:files:"):
        await ack(cq)
        try:
            await loading(cq, bot, "Читаю список файлов", path("файлы"))
            kbc, p, pages, total = await files_kb(pc, int(data.split(":")[2]))
            await show(cq, bot, files_screen(p, pages, total), kbc)
        except Exception as e:
            eid = await errorlog.register_async(cfg, "ui", str(e), repr(e))
            text, markup = error_screen(
                "❌ Список не открылся",
                [f"Ошибка #{eid}: Moonraker не ответил.", "", "Попробуйте ещё раз или откройте диагностику."],
                retry="m:files:0",
                where=path("файлы"),
            )
            await show(cq, bot, text, markup)
        return

    if data.startswith("f:conf:"):
        k = int(data.split(":")[2])
        items = file_items(await pc.files())
        if k >= len(items):
            await ack(cq, "Список устарел. Обновляю", alert=True)
            kbc, p, pages, total = await files_kb(pc, 0)
            await show(cq, bot, files_screen(p, pages, total), kbc)
            return
        filename = items[k][1]
        set_pending(chat, {"kind":"file","filename":filename})
        await ack(cq)
        await show(cq, bot, file_confirm_screen(cfg, filename),
                   confirm_kb("f:start", "✅ Печатать", "m:files:0"))
        return

    if data == "f:start" or data == "g:start":
        expected = "file" if data == "f:start" else "gcode"
        p = take_pending(chat, expected, getattr(cfg, "pending_ttl_seconds", 900))
        if not p:
            await ack(cq, "Подтверждение устарело. Выберите файл заново", alert=True)
            kbc, page, pages, total = await files_kb(pc, 0)
            await show(cq, bot, files_screen(page, pages, total), kbc)
            return
        await ack(cq, "Запускаю…")
        await loading(cq, bot, "Запускаю печать", path("файлы", "запуск"))
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_idle(pc)
            if not ok:
                await show(
                    cq, bot,
                    screen(
                        "❌ Запуск отклонён",
                        [field("📄", "Файл", p["filename"]), "", esc(reason), "", "Дождитесь конца печати или отмените её."],
                        path=path("файлы", "запуск"),
                    ),
                    await main_kb(pc),
                )
                return
            r = await pc.print_start(p["filename"])
        if r.ok:
            await show(
                cq, bot,
                screen(
                    "▶ Печать запущена",
                    [field("📄", "Файл", p["filename"]), "", "Прогресс виден в разделе «Статус»."],
                    path=path("файлы"),
                    meta=time.strftime("запуск в %H:%M:%S"),
                ),
                await main_kb(pc),
            )
        else:
            eid = await errorlog.register_async(cfg, "moonraker", r.error or "print start failed", str(r.raw or ""), filename=p["filename"])
            text, markup = error_screen(
                "❌ Печать не началась",
                [
                    field("📄", "Файл", p["filename"]),
                    field("🚨", "Ошибка", r.error or "неизвестно"),
                    "",
                    f"Запись #{eid}. Подробности: /error {eid}",
                ],
                retry="m:files:0",
                where=path("файлы"),
            )
            await show(cq, bot, text, markup)
        return

    if data == "m:pause":
        await ack(cq, "Ставлю на паузу…")
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_printing(pc)
            r = await pc.pause() if ok else MRResult(False, error=reason)
        if not r.ok:
            await errorlog.register_async(cfg, "moonraker", r.error or "pause failed", str(r.raw or ""))
            text, markup = error_screen(
                "⏸ Пауза не вышла",
                [field("🚨", "Причина", r.error or "неизвестно")],
                retry="m:pause",
                where=path("статус"),
            )
            await show(cq, bot, text, markup)
            return
        await show(cq, bot, await status_text(pc), await status_kb(pc))
        return

    if data == "m:resume":
        await ack(cq, "Продолжаю…")
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_paused(pc)
            r = await pc.resume() if ok else MRResult(False, error=reason)
        if not r.ok:
            await errorlog.register_async(cfg, "moonraker", r.error or "resume failed", str(r.raw or ""))
            text, markup = error_screen(
                "▶ Продолжение не вышло",
                [field("🚨", "Причина", r.error or "неизвестно")],
                retry="m:resume",
                where=path("статус"),
            )
            await show(cq, bot, text, markup)
            return
        await show(cq, bot, await status_text(pc), await status_kb(pc))
        return

    if data == "m:cancel":
        await ack(cq, "Отменяю печать…")
        async with chat_lock(chat):
            st = await pc.status()
            info = await pc.info()
            pstate = (st.get("print_stats") or {}).get("state")
            klippy = info.get("state") if info else "offline"

            if klippy in ("shutdown", "error"):
                r = MRResult(False, error=f"Klipper={klippy}; отмена через Moonraker сейчас недоступна")
            elif pstate in ("printing", "paused"):
                r = await pc.cancel()
            else:
                r = MRResult(False, error="нет активной печати")

        if r.ok:
            await show(
                cq, bot,
                screen(
                    "⏹ Отмена отправлена",
                    ["Жду подтверждение от Klipper.", "", "Состояние обновится автоматически."],
                    path=path("статус"),
                    meta=time.strftime("отправлено в %H:%M:%S"),
                ),
                await main_kb(pc),
            )
        else:
            eid = await errorlog.register_async(cfg, "moonraker", r.error or "cancel failed", str(r.raw or ""))
            text, markup = error_screen(
                "❌ Отмена не выполнена",
                [field("🚨", "Причина", r.error or "неизвестно"), "", f"Запись #{eid}. Подробности: /error {eid}"],
                retry="m:cancel",
                where=path("статус"),
            )
            await show(cq, bot, text, markup)
        return

    if data == "m:temp":
        await ack(cq)
        await show(cq, bot, temp_screen(), await temp_kb(pc))
        return

    if data == "t:off":
        await ack(cq, "Выключаю нагрев…")
        async with chat_lock(chat):
            r1 = await pc.gcode("SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0")
            r2 = await pc.gcode("SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=0")
        if not (r1.ok and r2.ok):
            await errorlog.register_async(cfg, "gcode", r1.error or r2.error or "heaters off failed", str(r1.raw or r2.raw or ""))
        await asyncio.sleep(0.2)
        await show(cq, bot, temp_screen(), await temp_kb(pc))
        return

    if data.startswith("t:hot:"):
        target = int(data.split(":")[2])
        r = await pc.gcode(f"SET_HEATER_TEMPERATURE HEATER=extruder TARGET={target}")
        await ack(cq, f"Хотенд → {target} °C" if r.ok else f"Не вышло: {r.error}", alert=not r.ok)
        if not r.ok:
            await errorlog.register_async(cfg, "gcode", r.error or "heater failed", str(r.raw or ""))
        await asyncio.sleep(0.2)
        await show(cq, bot, temp_screen(), await temp_kb(pc))
        return

    if data.startswith("t:bed:"):
        target = int(data.split(":")[2])
        r = await pc.gcode(f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={target}")
        await ack(cq, f"Стол → {target} °C" if r.ok else f"Не вышло: {r.error}", alert=not r.ok)
        if not r.ok:
            await errorlog.register_async(cfg, "gcode", r.error or "heater failed", str(r.raw or ""))
        await asyncio.sleep(0.2)
        await show(cq, bot, temp_screen(), await temp_kb(pc))
        return

    if data == "m:move":
        await ack(cq)
        mv = move_state(cfg, chat)
        await show(cq, bot, move_screen(mv), move_kb(mv))
        return

    if data == "v:xydist":
        mv = move_state(cfg, chat); mv["xy"] = cycle(cfg.xy_steps, mv["xy"])
        await ack(cq, f"Шаг XY → {mv['xy']:g} мм")
        await show(cq, bot, move_screen(mv), move_kb(mv))
        return

    if data == "v:zdist":
        mv = move_state(cfg, chat); mv["z"] = cycle(cfg.z_steps, mv["z"])
        await ack(cq, f"Шаг Z → {mv['z']:g} мм")
        await show(cq, bot, move_screen(mv), move_kb(mv))
        return

    if data == "v:home":
        await ack(cq, "Паркую оси…")
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_motion(pc)
            r = await pc.gcode("G28") if ok else MRResult(False, error=reason)
        if not r.ok:
            await errorlog.register_async(cfg, "motion", r.error or "home failed", str(r.raw or ""))
            text, markup = error_screen(
                "🏠 Парковка не вышла",
                [field("🚨", "Причина", r.error or "неизвестно")],
                retry="v:home",
                where=path("движение"),
            )
            await show(cq, bot, text, markup)
            return
        mv = move_state(cfg, chat)
        await show(cq, bot, move_screen(mv), move_kb(mv))
        return

    if data.startswith("v:"):
        a = data[2:]
        if len(a) < 2:
            await ack(cq, "Некорректное движение", alert=True); return
        axis = a[0]
        sign = 1 if a[1] == "+" else -1
        mv = move_state(cfg, chat)
        step = mv["z"] if axis == "z" else mv["xy"]
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_motion(pc)
            r = await jog(pc, axis, sign, mv) if ok else MRResult(False, error=reason)
        if r.ok:
            await ack(cq, f"{axis.upper()} {'+' if sign > 0 else '−'}{step:g} мм")
        else:
            await ack(cq, f"Не вышло: {r.error}", alert=True)
            await errorlog.register_async(cfg, "motion", r.error or "jog failed", str(r.raw or ""))
        return

    if data in ("m:tune","u:tune"):
        await ack(cq)
        await show(cq, bot, tune_screen(), await tune_kb(pc))
        return

    if data in ("u:speed-","u:speed+","u:flow-","u:flow+"):
        st = await pc.status()
        th = st.get("toolhead", {})
        if data.startswith("u:speed"):
            cur = max(10, min(200, round((th.get("speed_factor") or 1)*100) + (10 if data.endswith("+") else -10)))
            r = await pc.gcode(f"M220 S{cur}")
        else:
            cur = max(10, min(200, round((th.get("extrude_factor") or 1)*100) + (10 if data.endswith("+") else -10)))
            r = await pc.gcode(f"M221 S{cur}")
        label = "Скорость" if data.startswith("u:speed") else "Поток"
        await ack(cq, f"{label} → {cur} %" if r.ok else f"Не вышло: {r.error}", alert=not r.ok)
        if not r.ok:
            await errorlog.register_async(cfg, "gcode", r.error or "tune failed", str(r.raw or ""))
        await show(cq, bot, tune_screen(), await tune_kb(pc))
        return

    if data in ("u:fan:on","u:fan:off"):
        on = data.endswith("on")
        r = await pc.gcode("M106 S255" if on else "M106 S0")
        await ack(
            cq,
            ("Вентилятор включён" if on else "Вентилятор выключен") if r.ok else f"Не вышло: {r.error}",
            alert=not r.ok,
        )
        if not r.ok:
            await errorlog.register_async(cfg, "gcode", r.error or "fan failed", str(r.raw or ""))
        await show(cq, bot, tune_screen(), await tune_kb(pc))
        return

    if data in ("u:z+","u:z-"):
        adj = 0.05 if data == "u:z+" else -0.05
        r = await pc.gcode(f"SET_GCODE_OFFSET Z_ADJUST={adj} MOVE=1")
        await ack(cq, f"Z-оффсет {adj:+.2f} мм" if r.ok else f"Не вышло: {r.error}", alert=not r.ok)
        if not r.ok:
            await errorlog.register_async(cfg, "gcode", r.error or "z offset failed", str(r.raw or ""))
        await show(cq, bot, tune_screen(), await tune_kb(pc))
        return

    if data == "m:power":
        await ack(cq)
        await show(cq, bot, power_screen(), power_kb())
        return

    if data == "p:recover":
        await ack(cq)
        await show(cq, bot, recover_screen(),
                   kb([[("🔄 Restart Klipper","p:conf:restart")],
                       [("🔁 Firmware restart","p:conf:fw")],
                       nav_row(back="m:power", home=True)]))
        return

    if data.startswith("p:conf:"):
        a = data.split(":")[2]
        if a not in POWER_NAMES:
            await ack(cq, "Неизвестная операция", alert=True); return

        if not cfg.power_actions_require_confirmation:
            await ack(cq, "Выполняю…")
            await _execute_power(cq, bot, pc, cfg, chat, a)
        else:
            await ack(cq)
            set_pending(chat, {"kind": "power", "action": a})
            await show(cq, bot, power_confirm_screen(cfg, a),
                       confirm_kb(f"p:do:{a}", "✅ Выполнить", "m:power"))
        return

    if data.startswith("p:do:"):
        a = data.split(":")[2]
        if cfg.power_actions_require_confirmation:
            p = take_pending(chat, "power", getattr(cfg, "pending_ttl_seconds", 900))
            if not p or p.get("action") != a:
                await ack(cq, "Подтверждение устарело. Выберите операцию заново", alert=True)
                await show(cq, bot, power_screen(), power_kb())
                return
        await ack(cq, "Выполняю…")
        await _execute_power(cq, bot, pc, cfg, chat, a)
        return

    if data == "m:estop":
        # UX exception on purpose: no confirmation screen. Safety beats one extra tap.
        async with chat_lock(chat):
            r = await pc.emergency_stop()
        eid = None
        if not r.ok:
            eid = await errorlog.register_async(cfg, "moonraker", r.error or "emergency stop failed", str(r.raw or ""))
        log.critical("EMERGENCY STOP by %s ok=%s", cq.from_user.id, r.ok)
        await ack(cq, "АВАРИЙНЫЙ СТОП" if r.ok else f"Не вышло. Запись #{eid}", alert=not r.ok)
        if r.ok:
            body = screen(
                "🚨 Аварийный стоп выполнен",
                [
                    "Моторы и нагрев сброшены.",
                    "",
                    "Klipper восстановите кнопкой «Восстановление».",
                ],
                path=path("питание"),
                meta=time.strftime("%H:%M:%S"),
            )
            await bot.send_message(chat, body, reply_markup=power_kb(), parse_mode="HTML")
        else:
            body = screen(
                "🚨 Стоп не выполнен",
                [
                    field("🚨", "Ошибка", r.error or "неизвестно"),
                    "",
                    f"Запись #{eid}. Снимите питание принтера вручную.",
                ],
                path=path("питание"),
            )
            await bot.send_message(chat, body, parse_mode="HTML")
        return

    await ack(cq)

async def _execute_power(cq, bot, pc, cfg, chat, action):
    funcs = {
        "restart": pc.restart,
        "fw": pc.firmware_restart,
        "reboot": pc.reboot_host,
        "shutdown": pc.shutdown_host,
    }
    if action not in funcs:
        await bot.send_message(chat, "❌ Неизвестная операция.")
        return

    ok, reason = await SAFETY.require_power_action(pc, action)
    if not ok:
        eid = await errorlog.register_async(cfg, "safety", reason, reason)
        await bot.send_message(chat, f"❌ Операция запрещена #{eid}: {reason}")
        return

    async with chat_lock(chat):
        r = await funcs[action]()

    if r.ok:
        text = {
            "restart": "✅ Restart Klipper выполнен. Жду состояние ready…",
            "fw": "✅ Firmware restart выполнен. Жду состояние ready…",
            "reboot": "✅ Команда reboot хоста отправлена.",
            "shutdown": "✅ Команда shutdown хоста отправлена.",
        }[action]
        await bot.send_message(chat, text)
    else:
        eid = await errorlog.register_async(cfg, "moonraker", r.error or f"{action} failed", str(r.raw or ""))
        await bot.send_message(chat, f"❌ {action}: {r.error} · #{eid}")

@router.errors()
async def on_error(event: ErrorEvent):
    exc = event.exception
    DIAG["last_error"] = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, TelegramRetryAfter):
        log.warning("Telegram flood limit: retry after %s", exc.retry_after)
        return
    log.exception("handler error: %s", exc)

    # Never leave a tap unanswered: say where to look.
    update = getattr(event, "update", None)
    cq = getattr(update, "callback_query", None)
    msg = getattr(update, "message", None)
    try:
        if cq is not None:
            await ack(cq, "Ошибка. Подробности в /errors", alert=True)
        elif msg is not None:
            await msg.answer("❌ Ошибка при обработке. Посмотрите /errors или /diag.")
    except Exception:
        log.debug("error notice failed", exc_info=True)
