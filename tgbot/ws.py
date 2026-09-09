from __future__ import annotations
import asyncio, json, logging, time
from datetime import datetime
import aiohttp
from . import errorlog
from .printer import fmt_dur
from .ui import progress_bar

log = logging.getLogger("tgbot")

def is_quiet(cfg):
    q = cfg.quiet_hours or {}
    s, e = int(q.get("start", 23)), int(q.get("end", 7))
    if s == e: return False
    h = datetime.now().hour
    return (h >= s or h < e) if s > e else (s <= h < e)

class MoonrakerWS:
    def __init__(self, cfg, bot):
        self.cfg = cfg
        self.bot = bot
        self.ws_url = cfg.moonraker.replace("http://","ws://").replace("https://","wss://") + "/websocket"
        self.connected = False
        self.state = None
        self.filename = ""
        self.frac = 0.0
        self.duration = 0
        self.ext = (0, 0)
        self.bed = (0, 0)
        self.live = None
        self.last_text = None
        self.last_edit = 0
        self.snap_bucket = 0
        self.reconnects = 0
        self.last_event = 0.0
        self.last_error = None

    async def run(self):
        while True:
            try:
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
                async with aiohttp.ClientSession(timeout=timeout) as s:
                    async with s.ws_connect(self.ws_url, heartbeat=30) as ws:
                        self.connected = True
                        self.last_error = None
                        await ws.send_json({
                            "jsonrpc": "2.0",
                            "method": "printer.objects.subscribe",
                            "params": {"objects": {
                                "print_stats": None, "display_status": None, "extruder": None,
                                "heater_bed": None, "webhooks": None
                            }},
                            "id": 1,
                        })
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                self.last_event = time.time()
                                await self.handle(json.loads(msg.data))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = str(e)
                self.reconnects += 1
                log.warning("ws error: %s", e)
            finally:
                self.connected = False
            await asyncio.sleep(3)

    def live_text(self):
        rem = ""
        if self.frac > 0.001:
            rem = f" · осталось ~{fmt_dur(max(0, self.duration / self.frac - self.duration))}"
        return (
            f"🖨 {self.filename}\n{progress_bar(self.frac)} {self.frac*100:.1f}%\n"
            f"⏱ {fmt_dur(self.duration)}{rem}\n🌡 {self.ext[0]:.0f}/{self.ext[1]:.0f} · "
            f"стол {self.bed[0]:.0f}/{self.bed[1]:.0f}"
        )

    async def edit_live(self, text):
        if text == self.last_text: return
        try:
            if self.live:
                await self.bot.edit_message_text(text, chat_id=self.cfg.notify_chat, message_id=self.live)
            else:
                m = await self.bot.send_message(self.cfg.notify_chat, text)
                self.live = m.message_id
            self.last_text = text
            self.last_edit = time.time()
        except Exception:
            try:
                m = await self.bot.send_message(self.cfg.notify_chat, text)
                self.live = m.message_id
                self.last_text = text
            except Exception:
                pass

    async def live_update(self, force=False):
        if not force and (time.time() - self.last_edit) < 5: return
        await self.edit_live(self.live_text())

    async def send_cam(self, caption=None):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.get(self.cfg.camera_url) as r:
                    if r.status != 200: return False
                    data = await r.read()
            from aiogram.types import BufferedInputFile
            await self.bot.send_photo(
                self.cfg.notify_chat,
                BufferedInputFile(data, filename="cam.jpg"),
                caption=caption,
            )
            return True
        except Exception:
            return False

    async def handle(self, data):
        if isinstance(data.get("result"), dict) and "status" in data["result"]:
            old = self.state
            self._apply_status(data["result"]["status"])
            if self.state != old:
                await self.on_state(old, self.state, self.filename)
            elif self.state == "printing":
                await self.live_update(force=True)
            return

        if data.get("method") == "notify_status_update":
            params = data.get("params") or []
            updates = params[0] if params and isinstance(params[0], dict) else {}
            old = self.state
            self._apply_status(updates)
            if self.state != old:
                await self.on_state(old, self.state, self.filename)
            elif self.state == "printing":
                await self.live_update()

            if self.state == "printing":
                b = int(self.frac * 100) // 25
                if b > self.snap_bucket and b < 4 and not is_quiet(self.cfg):
                    self.snap_bucket = b
                    await self.send_cam(f"📷 {b*25}%")

        elif data.get("method") == "notify_klippy_shutdown":
            await self._register_klippy_event("shutdown", "Klippy shutdown")

        elif data.get("method") == "notify_klippy_disconnected":
            if not is_quiet(self.cfg):
                await self.bot.send_message(self.cfg.notify_chat, "🔌 Klipper отключился")
            await self._register_klippy_event("disconnected", "Klipper disconnected")

        elif data.get("method") == "notify_klippy_ready":
            if not is_quiet(self.cfg):
                await self.bot.send_message(self.cfg.notify_chat, "✅ Klipper готов")

    def _apply_status(self, status):
        ps = status.get("print_stats", {}) or {}
        ds = status.get("display_status", {}) or {}
        ex = status.get("extruder", {}) or {}
        bd = status.get("heater_bed", {}) or {}
        self.filename = ps.get("filename") or self.filename
        if "total_duration" in ps: self.duration = ps["total_duration"]
        if "progress" in ds: self.frac = ds["progress"]
        self.state = ps.get("state")
        if ex: self.ext = (ex.get("temperature", 0), ex.get("target", 0))
        if bd: self.bed = (bd.get("temperature", 0), bd.get("target", 0))

    async def _register_klippy_event(self, state, message):
        details = errorlog.read_klippy_tail("/tmp/klippy.log", self.cfg.max_error_log_lines)
        reason = errorlog.extract_klippy_error(details) or message
        await errorlog.register_async(
            self.cfg, "klipper", reason, details, state=state, filename=self.filename or None
        )
        if state == "shutdown" and not is_quiet(self.cfg):
            await self.bot.send_message(self.cfg.notify_chat, f"🛑 Klipper остановлен: {reason}")

    async def on_state(self, prev, state, fname):
        self.last_edit = 0
        self.last_text = None

        if state == "printing" and prev in (None, "standby", "complete", "cancelled", "error"):
            self.frac = 0.0
            self.duration = 0
            self.live = None
            self.snap_bucket = 0
            await self.live_update(force=True)

        elif state == "paused":
            await self.edit_live(f"⏸ Пауза: {fname}\n{self.live_text()}")

        elif state == "complete":
            await self.edit_live(f"✅ Завершено: {fname} за {fmt_dur(self.duration)}")
            self.live = None

        elif state in ("error", "cancelled"):
            if state == "error":
                details = errorlog.read_klippy_tail("/tmp/klippy.log", self.cfg.max_error_log_lines)
                reason = errorlog.extract_klippy_error(details) or "Klipper сообщил state=error"
                eid = await errorlog.register_async(
                    self.cfg, "klipper", reason, details, state=state, filename=fname or None
                )
                await self.edit_live(f"🚨 Ошибка печати #{eid}\n📄 {fname or '—'}\n❌ {reason}")
            else:
                await self.edit_live(f"⛔ Печать отменена: {fname}")
            self.live = None
async def health_loop(pc, bot, cfg, ws):
    """Background Moonraker health monitor used by main.py."""
    fails = 0
    alerted = False
    while True:
        try:
            info = await pc.info()
            ok = bool(info)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ok = False
            log.debug("health check failed: %s", exc)

        if ok:
            if alerted and not is_quiet(cfg):
                try:
                    await bot.send_message(cfg.notify_chat, "✅ Moonraker снова на связи")
                except Exception:
                    pass
            alerted = False
            fails = 0
        else:
            fails += 1
            if fails >= 3 and not alerted and not is_quiet(cfg):
                try:
                    await bot.send_message(cfg.notify_chat, "🚫 Moonraker/Klipper недоступен")
                except Exception:
                    pass
                alerted = True

        await asyncio.sleep(10)

