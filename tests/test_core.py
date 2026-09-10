from pathlib import Path
from types import SimpleNamespace
import importlib.util
import sys
import tempfile

# Unit-test the dependency-light modules without requiring aiogram.
ROOT = Path("/root/tgbot/tgbot")

from tgbot.config import CURRENT_CONFIG_VERSION, _migrate, as_bool
from tgbot.printer import file_items, fmt_size, fmt_dur, upload_ok, MRResult
from tgbot.safety import Safety
from tgbot.errorlog import extract_klippy_error
from tgbot.anims import num_to_prefix

def test_config_migration():
    old = {"backup_dir": "/tmp/anims_backup"}
    new = _migrate(old)
    assert new["config_version"] == CURRENT_CONFIG_VERSION
    assert new["backup_dir"] == "/root/tgbot/backups/animations"
    assert new["python"] == "/opt/tgbot/bin/python"
    assert new["error_db"] == "/root/tgbot/errors.db"
    assert new["quiet_hours"] == {"start": 23, "end": 7}

def test_bool_parser():
    assert as_bool("false") is False
    assert as_bool("true") is True
    assert as_bool("off") is False
    assert as_bool("on") is True

def test_file_items():
    f=[{"filename":"a.gcode","size":10},{"nope":1},"str",{"path":"b.gcode"}]
    assert [x[1] for x in file_items(f)] == ["a.gcode","b.gcode"]

def test_formatters():
    assert fmt_size(500)=="500 B"
    assert fmt_size(2048)=="2.0 KB"
    assert fmt_dur(3725)=="1:02:05"

def test_upload_models():
    assert upload_ok(MRResult(True, result={"item":{"path":"x.gcode"}}))
    assert not upload_ok(MRResult(False, error="boom"))
    assert upload_ok({"action":"create_file","item":{"path":"x.gcode"}})

def test_prefix():
    assert num_to_prefix(1)=="a"
    assert num_to_prefix(27)=="aa"

def test_log_extract():
    text="info\n!! Move exceeds maximum extrusion (0.752mm^2 vs 0.640mm^2)\n"
    assert "Move exceeds maximum extrusion" in extract_klippy_error(text)

def test_backup_cleanup_path_exists(tmp_path):
    assert isinstance(tmp_path, Path)

def test_animation_apply_rolls_back(tmp_path, monkeypatch):
    import asyncio
    import tgbot.anims as anims

    class PC:
        async def info(self): return {"state": "ready"}
        async def status(self): return {"print_stats": {"state": "standby"}}

    class Bot:
        def __init__(self): self.messages=[]
        async def send_message(self, chat, text, **kwargs): self.messages.append(text)

    cfg = SimpleNamespace(
        config_dir=str(tmp_path / "config"),
        backup_dir=str(tmp_path / "backups"),
        python="python3",
        converter="/bin/true",
        max_glyphs=250,
        preview_base=str(tmp_path / "preview"),
        error_db=str(tmp_path / "errors.db"),
        animation_backup_keep=5,
        animation_max_input_frames=500,
        animation_max_input_pixels=12000000,
        animation_max_duration_ms=120000,
        animation_ffmpeg_timeout=45,
    )
    Path(cfg.config_dir, "anims").mkdir(parents=True)
    old = Path(cfg.config_dir, "anims", "anim_001.cfg")
    old.write_text("OLD", encoding="utf-8")
    anims.save_index(cfg, {"next_id":2, "items":[{"prefix":"a","name":"old","file":"anim_001.cfg"}], "default":None})

    def fake_convert(cfg, src, out_cfg, prefix, s, preview_base=None):
        Path(out_cfg).write_text("NEW", encoding="utf-8")
        return 0, ""
    async def fake_restart(pc, timeout=40):
        return False, "forced test failure"

    monkeypatch.setattr(anims, "convert_params", fake_convert)
    monkeypatch.setattr(anims, "restart_and_wait", fake_restart)

    s = {"src": str(tmp_path / "src.gif"), "name":"new", "fill":0.3,
         "invert":False, "fit":"contain", "mode":"threshold", "frames":10}

    class Safety:
        async def require_idle(self, pc): return True, None

    ok = asyncio.run(anims.apply_session(Bot(), cfg, 1, s, PC(), Safety()))
    assert ok is False
    assert old.read_text(encoding="utf-8") == "OLD"

def test_runtime_exports():
    from tgbot.ws import MoonrakerWS, health_loop
    from tgbot import main as main_module
    assert MoonrakerWS is not None
    assert callable(health_loop)
    assert callable(main_module.main)

def test_safety_class_exists():
    assert hasattr(Safety(), "require_idle")
    assert hasattr(Safety(), "require_motion")
    assert hasattr(Safety(), "require_power_action")
