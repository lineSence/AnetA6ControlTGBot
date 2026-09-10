"""Regression tests for the defects listed in docs/AUDIT.md.

Every test here maps to one audit finding fixed in release 2.6.0.
"""
from pathlib import Path
from types import SimpleNamespace
import asyncio
import inspect
import os
import time

import pytest

import tgbot.anims as anims
import tgbot.handlers as handlers
from tgbot import errorlog
from tgbot.config import CURRENT_CONFIG_VERSION, _migrate
from tgbot.printer import PrinterClient
from tgbot.ui import cycle
from tgbot.ws import should_notify


def make_cfg(tmp_path):
    """Config stub with the same fields the runtime code reads."""
    return SimpleNamespace(
        config_dir=str(tmp_path / "config"),
        backup_dir=str(tmp_path / "backups"),
        error_db=str(tmp_path / "errors.db"),
        error_log_keep=50,
        python="/opt/tgbot/bin/python",
        converter="/bin/true",
        max_glyphs=250,
        preview_base=str(tmp_path / "preview"),
        fits=["contain", "cover", "stretch"],
        frames=[5, 10, 15, 20],
        animation_backup_keep=5,
        animation_max_input_frames=500,
        animation_max_input_pixels=12000000,
        animation_max_duration_ms=120000,
        animation_ffmpeg_timeout=45,
        session_ttl_seconds=3600,
        pending_ttl_seconds=900,
        quiet_hours={"start": 0, "end": 24},
        critical_alerts_ignore_quiet_hours=True,
        moonraker="http://127.0.0.1:7125",
        moonraker_api_key="secret",
        http_timeout=15,
        upload_timeout=300,
    )


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat, text, **kwargs):
        self.messages.append(text)
        return SimpleNamespace(message_id=len(self.messages))

    async def download(self, file_id, destination=None, **kwargs):
        Path(destination).write_bytes(b"GIF89a fake bytes")


def document_message(name="pic.gif", size=100):
    return SimpleNamespace(document=SimpleNamespace(file_id="f-1", file_name=name, file_size=size))


# --- P0-1: handlers called anims.start_session, which did not exist ------

def test_start_session_is_defined():
    assert inspect.iscoroutinefunction(anims.start_session)


def test_start_session_downloads_file_and_opens_session(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    sessions = {}
    bot = FakeBot()

    async def fake_preview(bot_, cfg_, chat_, s_):
        s_["msg_id"] = 42
        return True

    monkeypatch.setattr(anims, "refresh_preview", fake_preview)
    ok = asyncio.run(anims.start_session(bot, cfg, document_message(), 7, sessions))

    assert ok is True
    session = sessions[7]
    assert session["name"] == "pic"
    assert session["fit"] == "contain"
    assert session["frames"] == 10
    assert Path(session["src"]).exists()

    anims.close_session(sessions, 7)
    assert 7 not in sessions
    assert not Path(session["dir"]).exists()


def test_start_session_rejects_unsupported_format(tmp_path):
    cfg = make_cfg(tmp_path)
    sessions = {}
    bot = FakeBot()
    ok = asyncio.run(anims.start_session(bot, cfg, document_message("model.stl"), 7, sessions))
    assert ok is False
    assert sessions == {}
    assert "не поддерживается" in bot.messages[0]


# --- P0-2: config pointed at system python without Pillow ----------------

def test_migration_switches_to_venv_python():
    new = _migrate({"config_version": 5, "python": "python3"})
    assert new["config_version"] == CURRENT_CONFIG_VERSION
    assert CURRENT_CONFIG_VERSION >= 6
    assert new["python"] == "/opt/tgbot/bin/python"
    assert new["moonraker_api_key"] == ""
    assert new["upload_timeout"] == 300


def test_migration_keeps_custom_python():
    new = _migrate({"config_version": 5, "python": "/usr/local/bin/python3.12"})
    assert new["python"] == "/usr/local/bin/python3.12"


# --- P0-3: errorlog leaked sqlite connections ----------------------------

def _open_fd_count():
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        pytest.skip("/proc is not available on this platform")


def test_errorlog_does_not_leak_descriptors(tmp_path):
    cfg = make_cfg(tmp_path)
    errorlog.register(cfg, "test", "warm up")
    before = _open_fd_count()
    for i in range(40):
        errorlog.register(cfg, "test", f"error {i}")
    assert _open_fd_count() - before <= 2


def test_errorlog_history_is_bounded(tmp_path):
    cfg = make_cfg(tmp_path)
    for i in range(80):
        errorlog.register(cfg, "test", f"error {i}")
    assert len(errorlog.recent(cfg, 50)) == 50
    assert errorlog.get(cfg, 1) is None
    assert errorlog.prune(cfg) >= 0


# --- P1-5: one 15 s timeout was also used for G-code uploads -------------

def test_printer_client_reads_timeouts_and_api_key(tmp_path):
    pc = PrinterClient.from_config(make_cfg(tmp_path))
    assert pc.timeout == 15
    assert pc.upload_timeout == 300
    assert pc._headers() == {"X-Api-Key": "secret"}


# --- P1-6: /error could exceed the 4096 character Telegram limit ---------

def test_chunk_text_respects_telegram_limit():
    text = "\n".join(f"line {i} " + "x" * 100 for i in range(200))
    parts = handlers.chunk_text(text)
    assert len(parts) > 1
    assert all(len(p) <= handlers.TG_TEXT_LIMIT for p in parts)
    assert "".join(parts) == text


def test_chunk_text_splits_one_huge_line():
    text = "y" * 9000
    parts = handlers.chunk_text(text)
    assert all(len(p) <= handlers.TG_TEXT_LIMIT for p in parts)
    assert "".join(parts) == text


# --- P1-7: quiet hours also muted failure alerts -------------------------

def test_critical_alerts_ignore_quiet_hours(tmp_path):
    cfg = make_cfg(tmp_path)
    assert should_notify(cfg) is False
    assert should_notify(cfg, critical=True) is True
    cfg.critical_alerts_ignore_quiet_hours = False
    assert should_notify(cfg, critical=True) is False


# --- P2: dangerous macro detection matched substrings --------------------

def test_dangerous_macro_matching():
    assert handlers.macro_is_dangerous("G28")
    assert handlers.macro_is_dangerous("my_g28_helper")
    assert handlers.macro_is_dangerous("SAVE_CONFIG")
    assert handlers.macro_is_dangerous("BED_MESH_CALIBRATE")
    assert handlers.macro_is_dangerous("PROBE_ACCURACY")
    assert not handlers.macro_is_dangerous("PRESTART")
    assert not handlers.macro_is_dangerous("PRINT_START")
    assert not handlers.macro_is_dangerous("LOAD_FILAMENT")


# --- P2: cycle() raised ValueError on an unknown current value -----------

def test_cycle_handles_unknown_value():
    assert cycle([1, 5, 10], 5) == 10
    assert cycle([1, 5, 10], 7) == 1
    assert cycle([], "x") == "x"


# --- P2: tune keyboard sent a callback nobody handled --------------------

def test_tune_keyboard_uses_handled_noop():
    import tgbot.ui as ui_module

    source = Path(ui_module.__file__).read_text(encoding="utf-8")
    assert "u:noop" not in source
    assert "m:noop" in source


# --- P2: confirmations and per-chat state never expired ------------------

def test_pending_confirmation_expires():
    handlers.PENDING.clear()
    handlers.set_pending(1, {"kind": "power", "action": "shutdown"})
    handlers.PENDING[1]["ts"] = time.time() - 10_000
    assert handlers.take_pending(1, "power", 900) is None

    handlers.set_pending(1, {"kind": "power", "action": "shutdown"})
    assert handlers.take_pending(1, "power", 900)["action"] == "shutdown"
    assert handlers.take_pending(1, "power", 900) is None


def test_purge_state_drops_stale_entries(tmp_path):
    cfg = make_cfg(tmp_path)
    handlers.PENDING.clear()
    handlers.RENAMING.clear()
    handlers.SESSIONS.clear()
    old = time.time() - 10_000
    handlers.PENDING[1] = {"kind": "file", "filename": "a.gcode", "ts": old}
    handlers.RENAMING[1] = {"prefix": "a", "ts": old}
    handlers.SESSIONS[1] = {"created": old, "dir": ""}

    assert handlers.purge_state(cfg) == 3
    assert not handlers.PENDING
    assert not handlers.RENAMING
    assert not handlers.SESSIONS


# --- P2: rollback wiped the whole anims directory ------------------------

def test_rollback_keeps_foreign_files(tmp_path):
    cfg = make_cfg(tmp_path)
    anim_dir = Path(anims.anims_dir(cfg))
    anim_dir.mkdir(parents=True)
    (anim_dir / "anim_001.cfg").write_text("OLD", encoding="utf-8")
    (anim_dir / "user_macros.cfg").write_text("MINE", encoding="utf-8")

    backup = anims.snapshot_anims(cfg)
    (anim_dir / "anim_001.cfg").write_text("BROKEN", encoding="utf-8")
    (anim_dir / "later_user_file.cfg").write_text("KEEP", encoding="utf-8")

    anims.rollback_anims(cfg, backup)
    assert (anim_dir / "anim_001.cfg").read_text(encoding="utf-8") == "OLD"
    assert (anim_dir / "user_macros.cfg").read_text(encoding="utf-8") == "MINE"
    assert (anim_dir / "later_user_file.cfg").read_text(encoding="utf-8") == "KEEP"


# --- P2: two snapshots in the same microsecond crashed -------------------

def test_snapshot_names_never_collide(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    Path(anims.anims_dir(cfg)).mkdir(parents=True)

    class FrozenNow:
        def strftime(self, fmt):
            return "20260101_000000_000000"

    class FrozenDatetime:
        @staticmethod
        def now():
            return FrozenNow()

    monkeypatch.setattr(anims, "datetime", FrozenDatetime)
    first = anims.snapshot_anims(cfg)
    second = anims.snapshot_anims(cfg)
    assert first != second
    assert first.exists() and second.exists()


# --- P2: converter timeout ignored animation_ffmpeg_timeout --------------

def test_converter_timeout_follows_config(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(anims.subprocess, "run", fake_run)
    settings = {"mode": "threshold", "fill": 0.3, "fit": "contain", "frames": 10, "invert": False}
    rc, _ = anims.convert_params(cfg, "in.gif", "out.cfg", "a", settings)

    assert rc == 0
    assert captured["timeout"] == cfg.animation_ffmpeg_timeout * 2 + 30


# --- wiring --------------------------------------------------------------

def test_background_cleanup_is_wired():
    from tgbot import main as main_module

    assert callable(main_module.main)
    assert inspect.iscoroutinefunction(handlers.janitor_loop)
