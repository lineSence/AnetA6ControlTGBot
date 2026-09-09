from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml

CURRENT_CONFIG_VERSION = 6

DEFAULTS: dict[str, Any] = {
    "config_version": CURRENT_CONFIG_VERSION,
    "bot_token": "",
    "allowed_user_ids": [],
    "notify_chat": None,
    "moonraker": "http://127.0.0.1:7125",
    "camera_url": "http://127.0.0.1:8080/?action=snapshot",
    "config_dir": "/root/printer_data/config",
    "converter": "/root/gif2klipper.py",
    "python": "/opt/tgbot/bin/python",
    "max_glyphs": 250,
    "preview_base": "/tmp/tg_preview",
    "backup_dir": "/root/tgbot/backups/animations",
    "error_db": "/root/tgbot/errors.db",
    "xy_steps": [1, 5, 10, 50],
    "z_steps": [0.1, 0.5, 1, 5],
    "move_default": {"xy": 10, "z": 1},
    "fits": ["contain", "cover", "stretch"],
    "frames": [5, 10, 15, 20],
    "quiet_hours": {"start": 23, "end": 7},
    "log_file": "/var/log/tg2printer.log",
    "log_level": "INFO",
    "power_actions_require_confirmation": True,
    "dangerous_macros_require_confirmation": True,
    "animation_backup_keep": 5,
    "max_error_log_lines": 180,
    "animation_max_input_frames": 500,
    "animation_max_input_pixels": 12000000,
    "animation_max_duration_ms": 120000,
    "animation_ffmpeg_timeout": 45,
    "moonraker_api_key": "",
    "http_timeout": 15,
    "upload_timeout": 300,
    "error_log_keep": 500,
    "critical_alerts_ignore_quiet_hours": True,
    "pending_ttl_seconds": 900,
    "session_ttl_seconds": 3600,
}

@dataclass(slots=True)
class Config:
    config_version: int = CURRENT_CONFIG_VERSION
    bot_token: str = ""
    allowed_user_ids: set[int] = field(default_factory=set)
    notify_chat: int | None = None
    moonraker: str = DEFAULTS["moonraker"]
    camera_url: str = DEFAULTS["camera_url"]
    config_dir: str = DEFAULTS["config_dir"]
    converter: str = DEFAULTS["converter"]
    python: str = DEFAULTS["python"]
    max_glyphs: int = 250
    preview_base: str = DEFAULTS["preview_base"]
    backup_dir: str = DEFAULTS["backup_dir"]
    error_db: str = DEFAULTS["error_db"]
    xy_steps: list[float] = field(default_factory=lambda: [1, 5, 10, 50])
    z_steps: list[float] = field(default_factory=lambda: [0.1, 0.5, 1, 5])
    move_default: dict[str, float] = field(default_factory=lambda: {"xy": 10, "z": 1})
    fits: list[str] = field(default_factory=lambda: ["contain", "cover", "stretch"])
    frames: list[int] = field(default_factory=lambda: [5, 10, 15, 20])
    quiet_hours: dict[str, int] = field(default_factory=lambda: {"start": 23, "end": 7})
    log_file: str = "/var/log/tg2printer.log"
    log_level: str = "INFO"
    power_actions_require_confirmation: bool = True
    dangerous_macros_require_confirmation: bool = True
    animation_backup_keep: int = 5
    max_error_log_lines: int = 180
    animation_max_input_frames: int = 500
    animation_max_input_pixels: int = 12000000
    animation_max_duration_ms: int = 120000
    animation_ffmpeg_timeout: int = 45
    moonraker_api_key: str = ""
    http_timeout: int = 15
    upload_timeout: int = 300
    error_log_keep: int = 500
    critical_alerts_ignore_quiet_hours: bool = True
    pending_ttl_seconds: int = 900
    session_ttl_seconds: int = 3600

def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"1", "true", "yes", "on"}: return True
        if s in {"0", "false", "no", "off"}: return False
    return default

def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data or {})
    version = int(data.get("config_version", 1) or 1)

    if version < 2:
        if data.get("backup_dir") in (None, "", "/tmp/anims_backup"):
            data["backup_dir"] = "/root/tgbot/backups/animations"
        data.setdefault("power_actions_require_confirmation", True)
        data.setdefault("dangerous_macros_require_confirmation", True)
        version = 2

    if version < 3:
        data.setdefault("error_db", "/root/tgbot/errors.db")
        version = 3

    if version < 4:
        data.setdefault("animation_backup_keep", 5)
        data.setdefault("max_error_log_lines", 180)
        q = data.get("quiet_hours")
        if not isinstance(q, dict):
            data["quiet_hours"] = {"start": 23, "end": 7}
        else:
            q.setdefault("start", 23)
            q.setdefault("end", 7)
        version = 4

    if version < 5:
        data.setdefault("animation_max_input_frames", 500)
        data.setdefault("animation_max_input_pixels", 12000000)
        data.setdefault("animation_max_duration_ms", 120000)
        data.setdefault("animation_ffmpeg_timeout", 45)
        version = 5

    if version < 6:
        if str(data.get("python") or "").strip() in {"", "python", "python3", "/usr/bin/python", "/usr/bin/python3"}:
            data["python"] = "/opt/tgbot/bin/python"
        data.setdefault("moonraker_api_key", "")
        data.setdefault("http_timeout", 15)
        data.setdefault("upload_timeout", 300)
        data.setdefault("error_log_keep", 500)
        data.setdefault("critical_alerts_ignore_quiet_hours", True)
        data.setdefault("pending_ttl_seconds", 900)
        data.setdefault("session_ttl_seconds", 3600)
        version = 6

    data["config_version"] = CURRENT_CONFIG_VERSION
    return data

def _save(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)
    os.chmod(path, 0o600)

def load(path: str | None = None) -> Config:
    path = Path(path or os.environ.get("TG_CONFIG", "/root/tgbot/config.yaml"))
    raw: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError("config.yaml должен содержать YAML-объект")
        raw = loaded

    migrated = _migrate(raw)
    if path.exists() and migrated != raw:
        _save(path, migrated)

    merged = {**DEFAULTS, **migrated}
    token = os.environ.get("TG_BOT_TOKEN")
    if token:
        merged["bot_token"] = token
    api_key = os.environ.get("MOONRAKER_API_KEY")
    if api_key:
        merged["moonraker_api_key"] = api_key

    c = Config(
        config_version=int(merged.get("config_version", CURRENT_CONFIG_VERSION)),
        bot_token=str(merged.get("bot_token") or ""),
        allowed_user_ids={int(x) for x in (merged.get("allowed_user_ids") or [])},
        notify_chat=int(merged["notify_chat"]) if merged.get("notify_chat") is not None else None,
        moonraker=str(merged["moonraker"]),
        camera_url=str(merged["camera_url"]),
        config_dir=str(merged["config_dir"]),
        converter=str(merged["converter"]),
        python=str(merged["python"]),
        max_glyphs=int(merged["max_glyphs"]),
        preview_base=str(merged["preview_base"]),
        backup_dir=str(merged["backup_dir"]),
        error_db=str(merged["error_db"]),
        xy_steps=[float(x) for x in merged["xy_steps"]],
        z_steps=[float(x) for x in merged["z_steps"]],
        move_default={k: float(v) for k, v in (merged["move_default"] or {}).items()},
        fits=list(merged["fits"]),
        frames=[int(x) for x in merged["frames"]],
        quiet_hours={k: int(v) for k, v in (merged["quiet_hours"] or {}).items()},
        log_file=str(merged["log_file"]),
        log_level=str(merged["log_level"]),
        power_actions_require_confirmation=as_bool(merged["power_actions_require_confirmation"], True),
        dangerous_macros_require_confirmation=as_bool(merged["dangerous_macros_require_confirmation"], True),
        animation_backup_keep=max(1, int(merged["animation_backup_keep"])),
        max_error_log_lines=max(20, int(merged["max_error_log_lines"])),
        animation_max_input_frames=max(1, int(merged["animation_max_input_frames"])),
        animation_max_input_pixels=max(1, int(merged["animation_max_input_pixels"])),
        animation_max_duration_ms=max(1000, int(merged["animation_max_duration_ms"])),
        animation_ffmpeg_timeout=max(5, int(merged["animation_ffmpeg_timeout"])),
        moonraker_api_key=str(merged.get("moonraker_api_key") or ""),
        http_timeout=max(5, int(merged["http_timeout"])),
        upload_timeout=max(30, int(merged["upload_timeout"])),
        error_log_keep=max(50, int(merged["error_log_keep"])),
        critical_alerts_ignore_quiet_hours=as_bool(merged["critical_alerts_ignore_quiet_hours"], True),
        pending_ttl_seconds=max(60, int(merged["pending_ttl_seconds"])),
        session_ttl_seconds=max(300, int(merged["session_ttl_seconds"])),
    )
    if c.notify_chat is None and c.allowed_user_ids:
        c.notify_chat = sorted(c.allowed_user_ids)[0]
    return c
