from __future__ import annotations
import asyncio, hashlib, logging, os, shutil, time
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ErrorEvent
from aiogram.exceptions import TelegramRetryAfter
from . import __version__, errorlog, anims
from .printer import file_items, fmt_dur, fmt_size, MRResult
from .safety import Safety
from .ui import kb, cycle, main_kb, files_kb, macro_kb, temp_kb, move_kb, tune_kb, power_kb, anim_kb

log = logging.getLogger("tgbot")
router = Router()

LOCKS = {}
SESSIONS = {}
RENAMING = {}
PENDING = {}
MOVE_BY_CHAT = {}
DIAG = {"started": time.time(), "last_error": None, "last_error_id": None}
SAFETY = Safety()

DANGEROUS_MACRO_WORDS = (
    "G28", "SAVE_CONFIG", "FIRMWARE_RESTART", "RESTART", "M112",
    "BED_MESH", "PID_CALIBRATE", "PROBE_", "TESTZ", "SHUTDOWN"
)

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
    upper = name.upper()
    return any(word in upper for word in DANGEROUS_MACRO_WORDS)

async def show(cq, bot, text, kbc):
    try:
        await cq.message.edit_text(text, reply_markup=kbc)
    except Exception:
        await bot.send_message(cq.message.chat.id, text, reply_markup=kbc)

async def status_text(pc):
    st = await pc.status()
    info = await pc.info()
    if not st and not info:
        return "❌ Нет связи с Moonraker/Klipper."

    ps = st.get("print_stats", {})
    ds = st.get("display_status", {})
    ex, bed = st.get("extruder", {}), st.get("heater_bed", {})
    klippy_state = info.get("state", "offline")
    klippy_msg = info.get("state_message") or info.get("message")
    lines = [
        f"📊 Печать: {ps.get('state', 'standby')}",
        f"🧠 Klipper: {klippy_state}",
        f"📄 Файл: {ps.get('filename') or '—'}",
    ]
    if klippy_msg:
        lines.append(f"❗ {klippy_msg}")
    if ps.get("state") in ("printing", "paused"):
        lines += [
            f"⏳ Прогресс: {(ds.get('progress') or 0)*100:.1f}%",
            f"⏱ Прошло: {fmt_dur(ps.get('total_duration'))}",
        ]
    lines += [
        f"🌡 Хотенд: {ex.get('temperature',0):.1f} / {ex.get('target',0):.0f}",
        f"🌡 Стол: {bed.get('temperature',0):.1f} / {bed.get('target',0):.0f}",
    ]
    return "\n".join(lines)

async def diag_text(pc, ws, cfg):
    du = shutil.disk_usage("/")
    info = await pc.info()
    wst = "n/a" if ws is None else ("🟢" if ws.connected else "🔴")
    last = await asyncio.to_thread(errorlog.recent, cfg, 1)
    last_text = "—"
    if last:
        last_text = f"#{last[0][0]} {last[0][5]}"
    return (
        f"🔎 Диагностика\nверсия бота: {__version__}\n"
        f"аптайм: {fmt_dur(time.time()-DIAG['started'])}\n"
        f"WS: {wst}\nWS reconnects: {getattr(ws,'reconnects',0)}\n"
        f"Moonraker: {cfg.moonraker}\nKlipper: {info.get('state','offline') if info else 'offline'}\n"
        f"диск свободно: {du.free//1024//1024} МБ\nпоследняя ошибка: {last_text}"
    )

async def errors_text(cfg):
    rows = await asyncio.to_thread(errorlog.recent, cfg, 10)
    if not rows: return "🟢 Журнал ошибок пуст."
    out = ["🚨 Последние ошибки:"]
    for eid, ts, source, state, filename, message, details, ack in rows:
        out.append(
            f"#{eid} {ts} · {source}\n"
            f"{str(message)[:160]}\n"
            f"📄 {filename or '—'}"
        )
    return "\n\n".join(out)

async def history_text(pc):
    jobs = await pc.history(10)
    if not jobs: return "Истории пока нет."
    out = ["📜 Последние печати:"]
    for j in jobs:
        em = {"completed":"✅","cancelled":"⛔","error":"🚨"}.get(j.get("status"),"❔")
        out.append(f"{em} {j.get('filename','?')[:28]} · {fmt_dur(j.get('total_duration'))}")
    return "\n".join(out)

@router.message(F.text)
async def on_text(msg: Message, bot, pc, cfg, ws):
    if msg.from_user.id not in cfg.allowed_user_ids: return
    chat = msg.chat.id

    if chat in RENAMING:
        idx = anims.load_index(cfg)
        p = RENAMING.pop(chat)
        it = next((x for x in idx["items"] if x["prefix"] == p), None)
        if it:
            it["name"] = msg.text.strip()[:40] or "Анимация"
            anims.save_index(cfg, idx)
            await msg.answer(f"Переименовано в «{it['name']}».", reply_markup=anim_kb(idx))
        return

    t = msg.text.strip().lower()
    if t in ("/menu","меню","menu","/start","start"):
        await msg.answer("🖨 Пульт принтера:", reply_markup=await main_kb(pc))
    elif t in ("/status","статус"):
        await msg.answer(await status_text(pc), reply_markup=await main_kb(pc))
    elif t in ("/history","история"):
        await msg.answer(await history_text(pc), reply_markup=await main_kb(pc))
    elif t == "/diag":
        await msg.answer(await diag_text(pc, ws, cfg))
    elif t == "/errors":
        await msg.answer(await errors_text(cfg))
    elif t.startswith("/error"):
        parts = t.split()
        if len(parts) != 2 or not parts[1].isdigit():
            await msg.answer("Использование: /error <id>")
        else:
            row = await asyncio.to_thread(errorlog.get, cfg, int(parts[1]))
            if not row:
                await msg.answer("Ошибка не найдена.")
            else:
                eid,ts,source,state,filename,message,details,ack = row
                await msg.answer(
                    f"🚨 Ошибка #{eid}\n🕒 {ts}\nИсточник: {source}\n"
                    f"Состояние: {state or '—'}\n📄 {filename or '—'}\n"
                    f"❌ {message}\n\nЛог:\n{(details or '—')[-6000:]}"
                )
    elif t in ("/stop","стоп"):
        r = await pc.gcode("ANIMS_STOP_ALL")
        await msg.answer("Анимация остановлена." if r.ok else f"❌ Не удалось остановить: {r.error}")
    elif t == "/help":
        await msg.answer(
            "/menu · /status · /history · /diag · /errors · /error <id>\n"
            "GIF/фото — анимация · .gcode — загрузка"
        )

@router.message(F.animation | F.photo | F.document)
async def on_media(msg: Message, bot, pc, cfg):
    if msg.from_user.id not in cfg.allowed_user_ids: return
    if msg.document:
        fn = (msg.document.file_name or "").lower()
        if fn.endswith((".gcode",".g",".gc",".ngc")):
            await handle_gcode_upload(bot, msg, pc, cfg)
            return
        if msg.document.mime_type != "image/gif" and not fn.endswith(".gif"):
            return
    await anims.start_session(bot, cfg, msg, msg.chat.id, SESSIONS)

async def handle_gcode_upload(bot, msg, pc, cfg):
    raw_name = msg.document.file_name or "upload.gcode"
    safe_name = os.path.basename(raw_name)
    path = f"/tmp/up_{msg.chat.id}_{os.getpid()}_{safe_name}"
    try:
        f = await bot.get_file(msg.document.file_id)
        await bot.download_file(f.file_path, path)
        r = await pc.upload_gcode(path, safe_name)
        if not r.ok:
            eid = await errorlog.register_async(cfg, "moonraker", r.error or "upload failed", str(r.raw or ""), filename=safe_name)
            await bot.send_message(msg.chat.id, f"❌ Ошибка загрузки #{eid}: {r.error}")
            return
        item = r.result.get("item", {}) if isinstance(r.result, dict) else {}
        sz = fmt_size(item.get("size",0)) if isinstance(item, dict) else ""
        log.info("gcode uploaded: %s (%s)", safe_name, sz)
        PENDING[msg.chat.id] = {"kind":"gcode", "filename":safe_name}
        await bot.send_message(
            msg.chat.id,
            f"📦 Загружено: {safe_name} · {sz}",
            reply_markup=kb([[("▶ Напечатать","g:start"),("⬅️ Меню","m:main")]])
        )
    except Exception as e:
        eid = await errorlog.register_async(cfg, "upload", str(e), repr(e), filename=safe_name)
        await bot.send_message(msg.chat.id, f"❌ Ошибка загрузки #{eid}: {e}")
    finally:
        clear_temp_file(path)

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
    if cq.from_user.id not in cfg.allowed_user_ids:
        await cq.answer()
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
            SESSIONS.pop(chat, None)
            clear_temp_file(s.get("src"))
            if s.get("msg_id"):
                try: await bot.delete_message(chat, s["msg_id"])
                except Exception: pass
            await cq.answer("Отменено")
            return
        if a == "apply":
            await cq.answer()
            async with chat_lock(chat):
                ok = await anims.apply_session(bot, cfg, chat, s, pc, SAFETY)
            SESSIONS.pop(chat, None)
            clear_temp_file(s.get("src"))
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
        await cq.answer("Анимация по умолчанию обновлена")
        await show(cq, bot, "🎬 Анимации:", anim_kb(idx))
        return

    if data.startswith("ren:"):
        RENAMING[chat] = data.split(":",1)[1]
        await cq.answer()
        await bot.send_message(chat, "Пришлите новое имя анимации.")
        return

    if data.startswith("del:"):
        p = data.split(":",1)[1]
        await cq.answer()
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
        await cq.answer(); return
    if data == "m:main":
        await cq.answer(); await show(cq, bot, "🖨 Пульт принтера:", await main_kb(pc)); return
    if data == "m:status":
        await cq.answer(); await show(cq, bot, await status_text(pc), await main_kb(pc)); return
    if data == "m:history":
        await cq.answer(); await show(cq, bot, await history_text(pc), await main_kb(pc)); return
    if data == "m:cam":
        await cq.answer()
        if not await ws.send_cam():
            await bot.send_message(chat, "❌ Не удалось получить кадр камеры.")
        return
    if data == "m:anims":
        await cq.answer()
        if idx["items"]: await show(cq, bot, "🎬 Анимации:", anim_kb(idx))
        else: await show(cq, bot, "Анимаций нет. Пришлите GIF/фото.", await main_kb(pc))
        return
    if data == "m:macros" or data.startswith("m:macros:"):
        await cq.answer()
        p = int(data.split(":")[2]) if data.startswith("m:macros:") else 0
        kbc, p, pages = await macro_kb(pc, p)
        await show(cq, bot, f"🧩 Макросы (стр. {p+1}/{pages}):", kbc)
        return

    if data.startswith("mc:run:"):
        try:
            _, _, idx_s, digest = data.split(":", 3)
            i = int(idx_s)
        except Exception:
            await cq.answer("Некорректная команда"); return
        pending = PENDING.pop(chat, None)
        if not pending or pending.get("kind") != "macro":
            await cq.answer("Подтверждение устарело"); return
        macros = await pc.macros()
        if i >= len(macros) or macro_digest(macros[i]) != digest:
            await cq.answer("Макрос изменился — откройте меню заново"); return
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_idle(pc)
            if not ok:
                await cq.answer("Операция запрещена")
                await bot.send_message(chat, f"❌ Макрос запрещён: {reason}")
                return
            r = await pc.gcode(macros[i])
        eid = None if r.ok else await errorlog.register_async(cfg, "macro", r.error or "macro failed", str(r.raw or ""), filename=macros[i])
        await cq.answer("Выполнено" if r.ok else f"Ошибка #{eid}")
        await bot.send_message(chat, f"{'🧩 Выполнено' if r.ok else '⚠️ Ошибка'}: {macros[i]}"
                               + (f"\n{r.error}" if not r.ok else ""))
        return

    if data.startswith("mc:"):
        i, digest = _macro_from_data(data)
        if i is None:
            await cq.answer("Некорректный макрос"); return
        macros = await pc.macros()
        if i >= len(macros) or macro_digest(macros[i]) != digest:
            await cq.answer("Макрос изменился — откройте меню заново"); return
        await cq.answer()
        name = macros[i]
        if cfg.dangerous_macros_require_confirmation and macro_is_dangerous(name):
            PENDING[chat] = {"kind":"macro","index":i,"digest":digest,"name":name}
            await show(cq, bot, f"⚠️ Потенциально опасный макрос: {name}",
                       kb([[("✅ Выполнить",f"mc:run:{i}:{digest}"),("❌ Отмена","m:macros")]]))
            return
        ok, reason = await SAFETY.require_idle(pc)
        if not ok:
            await bot.send_message(chat, f"❌ Макрос запрещён: {reason}")
            return
        r = await pc.gcode(name)
        eid = None if r.ok else await errorlog.register_async(cfg, "macro", r.error or "macro failed", str(r.raw or ""), filename=name)
        await bot.send_message(chat, f"{'🧩 Выполнено' if r.ok else '⚠️ Ошибка'}: {name}"
                               + (f"\n{r.error} · #{eid}" if not r.ok else ""))
        return

    if data.startswith("m:files:"):
        await cq.answer()
        try:
            kbc, p, pages = await files_kb(pc, int(data.split(":")[2]))
            await show(cq, bot, f"📂 Файлы (стр. {p+1}/{pages}):", kbc)
        except Exception as e:
            eid = await errorlog.register_async(cfg, "ui", str(e), repr(e))
            await bot.send_message(chat, f"❌ Ошибка списка файлов #{eid}")
        return

    if data.startswith("f:conf:"):
        k = int(data.split(":")[2])
        items = file_items(await pc.files())
        if k >= len(items):
            await cq.answer("Список устарел"); return
        filename = items[k][1]
        PENDING[chat] = {"kind":"file","filename":filename}
        await cq.answer()
        await show(cq, bot, f"Запустить «{filename}»?",
                   kb([[("✅ Старт","f:start"),("⬅️ Назад","m:files:0")]]))
        return

    if data == "f:start" or data == "g:start":
        p = PENDING.pop(chat, None)
        expected = "file" if data == "f:start" else "gcode"
        if not p or p.get("kind") != expected:
            await cq.answer("Список устарел"); return
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_idle(pc)
            if not ok:
                await cq.answer("Печать активна")
                await bot.send_message(chat, f"❌ Запуск запрещён: {reason}")
                return
            r = await pc.print_start(p["filename"])
        if r.ok:
            await cq.answer("Запускаю…")
            await show(cq, bot, f"▶ Запускаю {p['filename']}…", await main_kb(pc))
        else:
            eid = await errorlog.register_async(cfg, "moonraker", r.error or "print start failed", str(r.raw or ""), filename=p["filename"])
            await cq.answer(f"Ошибка #{eid}")
            await show(cq, bot, f"❌ Не удалось запустить #{eid}: {r.error}", await main_kb(pc))
        return

    if data == "m:pause":
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_printing(pc)
            r = await pc.pause() if ok else MRResult(False, error=reason)
        await cq.answer("Пауза" if r.ok else f"Ошибка: {r.error}")
        if not r.ok:
            await errorlog.register_async(cfg, "moonraker", r.error or "pause failed", str(r.raw or ""))
        return

    if data == "m:resume":
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_paused(pc)
            r = await pc.resume() if ok else MRResult(False, error=reason)
        await cq.answer("Продолжаю" if r.ok else f"Ошибка: {r.error}")
        if not r.ok:
            await errorlog.register_async(cfg, "moonraker", r.error or "resume failed", str(r.raw or ""))
        return

    if data == "m:cancel":
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
            await cq.answer("Отмена отправлена")
            await bot.send_message(chat, "⏹ Команда отмены печати отправлена. Ожидаю подтверждение от Klipper…")
        else:
            eid = await errorlog.register_async(cfg, "moonraker", r.error or "cancel failed", str(r.raw or ""))
            await cq.answer(f"Ошибка #{eid}")
            await bot.send_message(chat, f"❌ Отмена не выполнена #{eid}: {r.error}")
        return

    if data == "m:temp":
        await cq.answer(); await show(cq, bot, "🌡 Температуры:", await temp_kb(pc)); return

    if data.startswith("t:hot:"):
        target = int(data.split(":")[2])
        r = await pc.gcode(f"SET_HEATER_TEMPERATURE HEATER=extruder TARGET={target}")
        await cq.answer("ОК" if r.ok else f"Ошибка: {r.error}")
        if not r.ok:
            await errorlog.register_async(cfg, "gcode", r.error or "heater failed", str(r.raw or ""))
        await asyncio.sleep(0.2)
        await show(cq, bot, "🌡 Температуры:", await temp_kb(pc))
        return

    if data.startswith("t:bed:"):
        target = int(data.split(":")[2])
        r = await pc.gcode(f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={target}")
        await cq.answer("ОК" if r.ok else f"Ошибка: {r.error}")
        if not r.ok:
            await errorlog.register_async(cfg, "gcode", r.error or "heater failed", str(r.raw or ""))
        await asyncio.sleep(0.2)
        await show(cq, bot, "🌡 Температуры:", await temp_kb(pc))
        return

    if data == "m:move":
        await cq.answer(); await show(cq, bot, "🕹 Движение:", move_kb(move_state(cfg, chat))); return

    if data == "v:xydist":
        mv = move_state(cfg, chat); mv["xy"] = cycle(cfg.xy_steps, mv["xy"])
        await cq.answer(f"XY шаг: {mv['xy']:g} мм")
        await show(cq, bot, "🕹 Движение:", move_kb(mv)); return

    if data == "v:zdist":
        mv = move_state(cfg, chat); mv["z"] = cycle(cfg.z_steps, mv["z"])
        await cq.answer(f"Z шаг: {mv['z']:g} мм")
        await show(cq, bot, "🕹 Движение:", move_kb(mv)); return

    if data == "v:home":
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_motion(pc)
            r = await pc.gcode("G28") if ok else MRResult(False, error=reason)
        await cq.answer("Home" if r.ok else f"Ошибка: {r.error}")
        if not r.ok:
            await errorlog.register_async(cfg, "motion", r.error or "home failed", str(r.raw or ""))
        return

    if data.startswith("v:"):
        a = data[2:]
        if len(a) < 2:
            await cq.answer("Некорректное движение"); return
        axis = a[0]
        sign = 1 if a[1] == "+" else -1
        async with chat_lock(chat):
            ok, reason = await SAFETY.require_motion(pc)
            r = await jog(pc, axis, sign, move_state(cfg, chat)) if ok else MRResult(False, error=reason)
        await cq.answer(
            f"{axis.upper()} {'+' if sign > 0 else '−'}"
            f"{move_state(cfg,chat)['z'] if axis=='z' else move_state(cfg,chat)['xy']:g} мм"
            if r.ok else f"Ошибка: {r.error}"
        )
        if not r.ok:
            await errorlog.register_async(cfg, "motion", r.error or "jog failed", str(r.raw or ""))
        return

    if data in ("m:tune","u:tune"):
        await cq.answer(); await show(cq, bot, "⚙️ Тюнинг:", await tune_kb(pc)); return

    if data in ("u:speed-","u:speed+","u:flow-","u:flow+"):
        st = await pc.status()
        th = st.get("toolhead", {})
        if data.startswith("u:speed"):
            cur = max(10, min(200, round((th.get("speed_factor") or 1)*100) + (10 if data.endswith("+") else -10)))
            r = await pc.gcode(f"M220 S{cur}")
        else:
            cur = max(10, min(200, round((th.get("extrude_factor") or 1)*100) + (10 if data.endswith("+") else -10)))
            r = await pc.gcode(f"M221 S{cur}")
        await cq.answer("ОК" if r.ok else f"Ошибка: {r.error}")
        if not r.ok:
            await errorlog.register_async(cfg, "gcode", r.error or "tune failed", str(r.raw or ""))
        await show(cq, bot, "⚙️ Тюнинг:", await tune_kb(pc))
        return

    if data in ("u:fan:on","u:fan:off"):
        r = await pc.gcode("M106 S255" if data.endswith("on") else "M106 S0")
        await cq.answer("ОК" if r.ok else f"Ошибка: {r.error}")
        return

    if data in ("u:z+","u:z-"):
        adj = 0.05 if data == "u:z+" else -0.05
        r = await pc.gcode(f"SET_GCODE_OFFSET Z_ADJUST={adj} MOVE=1")
        await cq.answer(f"Z {adj:+.2f}" if r.ok else f"Ошибка: {r.error}")
        if not r.ok:
            await errorlog.register_async(cfg, "gcode", r.error or "z offset failed", str(r.raw or ""))
        await show(cq, bot, "⚙️ Тюнинг:", await tune_kb(pc))
        return

    if data == "m:power":
        await cq.answer(); await show(cq, bot, "⚡ Питание:", power_kb()); return

    if data == "p:recover":
        await cq.answer()
        await show(cq, bot, "🔄 Восстановление Klipper",
                   kb([[("🔄 Restart Klipper","p:conf:restart"),("🔁 Firmware restart","p:conf:fw")],
                       [("⬅️ Меню","m:main")]]))
        return

    if data.startswith("p:conf:"):
        a = data.split(":")[2]
        names = {"restart":"Restart Klipper","fw":"Firmware restart","reboot":"Reboot хоста","shutdown":"Shutdown хоста"}
        if a not in names:
            await cq.answer("Неизвестная операция"); return

        if not cfg.power_actions_require_confirmation:
            await cq.answer()
            await _execute_power(cq, bot, pc, cfg, chat, a)
        else:
            await cq.answer()
            await show(cq, bot, f"Точно: {names[a]}?",
                       kb([[("✅ Да",f"p:do:{a}"),("⬅️ Назад","m:power")]]))
        return

    if data.startswith("p:do:"):
        a = data.split(":")[2]
        await cq.answer()
        await _execute_power(cq, bot, pc, cfg, chat, a)
        return

    if data == "m:estop":
        async with chat_lock(chat):
            r = await pc.emergency_stop()
        eid = None
        if not r.ok:
            eid = await errorlog.register_async(cfg, "moonraker", r.error or "emergency stop failed", str(r.raw or ""))
        log.critical("EMERGENCY STOP by %s ok=%s", cq.from_user.id, r.ok)
        await cq.answer("АВАРИЙНЫЙ СТОП" if r.ok else f"Ошибка #{eid}")
        await bot.send_message(
            chat,
            "🚨 Выполнен аварийный стоп. Klipper теперь можно восстановить через кнопку в меню."
            if r.ok else f"🚨 Не удалось выполнить аварийный стоп #{eid}: {r.error}"
        )
        return

    await cq.answer()

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
