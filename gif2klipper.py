#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GIF/video -> Klipper ST7920 128x64 animation.

The converter is intentionally standalone so it can be used both by the
Telegram bot and directly from the shell.  It keeps memory bounded by the
number/size of normalized display frames, validates generated Klipper config,
and never calls sys.exit() from library code.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

try:
    from PIL import Image, ImageFile, ImageFilter, ImageOps, UnidentifiedImageError
except ImportError as exc:  # pragma: no cover - handled by CLI
    raise RuntimeError("Нужен Pillow. Установите зависимости бота из requirements.lock") from exc

ImageFile.LOAD_TRUNCATED_IMAGES = False

GLYPH = 16
COLS, ROWS = 8, 4
W, H = COLS * GLYPH, ROWS * GLYPH
DEFAULT_GROUP = "_default_16x4"
DEFAULT_MAX_FRAMES = 120
DEFAULT_MAX_INPUT_FRAMES = 500
DEFAULT_MAX_INPUT_PIXELS = 12_000_000
DEFAULT_MAX_DURATION_MS = 120_000
DEFAULT_FFMPEG_TIMEOUT = 45
DEFAULT_VIDEO_SECONDS = 5
DEFAULT_VIDEO_FPS = 10

PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
RESERVED_PREFIXES = {"anims", "display", "printer", "gcode", "delayed", "default"}


class ConverterError(Exception):
    """Base converter exception safe to present to the Telegram user."""


class InputFormatError(ConverterError):
    pass


class ResourceLimitError(ConverterError):
    pass


class FFmpegError(ConverterError):
    pass


class GlyphLimitError(ConverterError):
    pass


class ConfigValidationError(ConverterError):
    pass


class ArgumentError(ConverterError):
    pass


@dataclass(slots=True)
class ConversionResult:
    output_path: Path
    frames: int
    unique_glyphs: int
    source_duration_ms: int
    output_duration_ms: int
    deduplicated_frames: int
    binarizer: str
    preview_path: Path | None

    def as_dict(self) -> dict[str, object]:
        return {
            "output_path": str(self.output_path),
            "frames": self.frames,
            "unique_glyphs": self.unique_glyphs,
            "source_duration_ms": self.source_duration_ms,
            "output_duration_ms": self.output_duration_ms,
            "deduplicated_frames": self.deduplicated_frames,
            "binarizer": self.binarizer,
            "preview_path": str(self.preview_path) if self.preview_path else None,
        }


@dataclass(slots=True)
class ConverterLimits:
    max_frames: int | None = None
    max_input_frames: int = DEFAULT_MAX_INPUT_FRAMES
    max_input_pixels: int = DEFAULT_MAX_INPUT_PIXELS
    max_duration_ms: int = DEFAULT_MAX_DURATION_MS
    ffmpeg_timeout: int = DEFAULT_FFMPEG_TIMEOUT


@dataclass(slots=True)
class FrameSample:
    image: Image.Image
    duration_ms: int


@dataclass(slots=True)
class FrameResult:
    names: tuple[str, ...]
    bw: Image.Image
    duration_ms: int


def validate_prefix(prefix: str) -> str:
    p = str(prefix).strip().lower()
    if not PREFIX_RE.fullmatch(p):
        raise ArgumentError(
            "Некорректный prefix: разрешены 1–32 символа, первый — a-z, затем только a-z, 0-9, _."
        )
    if p in RESERVED_PREFIXES or p.startswith("anims_") or p == "_default_16x4":
        raise ArgumentError(f"Prefix '{p}' зарезервирован.")
    return p


def _open_checked(path: Path):
    try:
        im = Image.open(path)
        im.load()
        return im
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InputFormatError(f"Файл не является корректным изображением: {path.name}") from exc


def _image_meta(im: Image.Image) -> tuple[int, int, int]:
    w, h = im.size
    frames = int(getattr(im, "n_frames", 1) or 1)
    pixels = w * h
    return w, h, frames if frames > 0 else 1


def _is_probable_video(path: Path) -> bool:
    return path.suffix.lower() in {
        ".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".mpeg", ".mpg", ".3gp"
    }


def _ffmpeg_to_gif(path: Path, timeout: int) -> Path:
    if not shutil.which("ffmpeg"):
        raise FFmpegError(
            "Файл не распознан как картинка. Для видео нужен ffmpeg: apt install ffmpeg"
        )
    tmpdir = Path(tempfile.mkdtemp(prefix="gif2klipper_"))
    dst = tmpdir / "converted.gif"
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(path), "-t", str(DEFAULT_VIDEO_SECONDS),
            "-vf", f"fps={DEFAULT_VIDEO_FPS},scale=128:-2",
            str(dst),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise FFmpegError(f"ffmpeg превысил таймаут {timeout} с") from exc
        if proc.returncode != 0 or not dst.exists():
            detail = (proc.stderr or proc.stdout or "неизвестная ошибка").strip()
            raise FFmpegError("ffmpeg не смог обработать файл:\n" + detail[-1500:])
        return dst
    except Exception:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def _compose_gif_frames(
    im: Image.Image,
    limits: ConverterLimits,
) -> Iterator[FrameSample]:
    """Yield complete logical-screen frames.

    Pillow's GIF decoder applies GIF frame disposal while seeking/loading the
    next frame. We deliberately use that decoded logical screen instead of
    trying to reimplement disposal ourselves; doing both would dispose frames
    twice and corrupt partial-frame animations.
    """
    width, height, n_frames = _image_meta(im)
    if width * height > limits.max_input_pixels:
        raise ResourceLimitError(
            f"Изображение слишком большое: {width}x{height} > {limits.max_input_pixels:,} пикселей"
        )
    if n_frames > limits.max_input_frames:
        raise ResourceLimitError(
            f"Слишком много кадров в исходнике: {n_frames} > {limits.max_input_frames}"
        )

    emitted = 0
    elapsed = 0
    for i in range(n_frames):
        im.seek(i)
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        rgba.close()

        duration = max(int(im.info.get("duration", 100) or 100), 1)
        if elapsed + duration > limits.max_duration_ms:
            duration = max(1, limits.max_duration_ms - elapsed)
        if duration <= 0:
            break

        emitted += 1
        elapsed += duration
        yield FrameSample(bg.convert("L"), duration)
        bg.close()
        if limits.max_frames and emitted >= limits.max_frames:
            break
        if elapsed >= limits.max_duration_ms:
            break

def load_frames(
    path: str | Path,
    max_frames: int | None = None,
    limits: ConverterLimits | None = None,
) -> tuple[list[Image.Image], list[int]]:
    """Compatibility wrapper used by older callers/tests."""
    lim = limits or ConverterLimits(max_frames=max_frames)
    if max_frames is not None:
        lim.max_frames = max_frames
    source = Path(path)
    generated: Path | None = None
    try:
        try:
            im = _open_checked(source)
        except InputFormatError:
            if not _is_probable_video(source):
                raise
            generated = _ffmpeg_to_gif(source, lim.ffmpeg_timeout)
            im = _open_checked(generated)
        try:
            samples = list(_compose_gif_frames(im, lim))
        finally:
            im.close()
        return [s.image for s in samples], [s.duration_ms for s in samples]
    finally:
        if generated is not None:
            shutil.rmtree(generated.parent, ignore_errors=True)


def iter_frames(path: Path, limits: ConverterLimits) -> Iterator[FrameSample]:
    generated: Path | None = None
    im: Image.Image | None = None
    try:
        try:
            im = _open_checked(path)
        except InputFormatError:
            if not _is_probable_video(path):
                raise
            generated = _ffmpeg_to_gif(path, limits.ffmpeg_timeout)
            im = _open_checked(generated)
        yield from _compose_gif_frames(im, limits)
    finally:
        if im is not None:
            im.close()
        if generated is not None:
            shutil.rmtree(generated.parent, ignore_errors=True)


# ============================================================
# ГЕОМЕТРИЯ
# ============================================================

def fit(img: Image.Image, mode: str) -> Image.Image:
    if img.width <= 0 or img.height <= 0:
        raise InputFormatError("У кадра некорректный размер")
    if mode == "stretch":
        return img.resize((W, H), Image.Resampling.LANCZOS)
    scale = min(W / img.width, H / img.height) if mode == "contain" else max(W / img.width, H / img.height)
    nw, nh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    if mode == "cover":
        left, top = (nw - W) // 2, (nh - H) // 2
        return resized.crop((left, top, left + W, top + H))
    canvas = Image.new("L", (W, H), 255)
    canvas.paste(resized, ((W - nw) // 2, (H - nh) // 2))
    return canvas


# ============================================================
# БИНАРИЗАЦИЯ
# ============================================================

def otsu_threshold(gray: Image.Image) -> int:
    hist = gray.histogram()
    total = gray.width * gray.height
    if total <= 0:
        return 127
    total_sum = sum(i * hist[i] for i in range(256))
    sum_bg = 0.0
    weight_bg = 0
    max_var = -1.0
    best = 127
    for i in range(256):
        weight_bg += hist[i]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += i * hist[i]
        mean_bg = sum_bg / weight_bg
        mean_fg = (total_sum - sum_bg) / weight_fg
        var = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var > max_var:
            max_var = var
            best = i
    return best


def make_bw_global(gray: Image.Image) -> Image.Image:
    gray = ImageOps.autocontrast(gray, cutoff=1)
    threshold = otsu_threshold(gray)
    return gray.point(lambda p: 0 if p <= threshold else 255)


def make_bw_adaptive(gray: Image.Image, radius: int = 7, c: int = 5) -> Image.Image:
    gray = ImageOps.autocontrast(gray, cutoff=1)
    smooth = gray.filter(ImageFilter.GaussianBlur(radius=0.6))
    mean = smooth.filter(ImageFilter.BoxBlur(radius))
    gp, mp = smooth.load(), mean.load()
    out = Image.new("L", smooth.size, 255)
    op = out.load()
    for y in range(smooth.height):
        for x in range(smooth.width):
            op[x, y] = 0 if gp[x, y] <= mp[x, y] - c else 255
    return out


def make_bw_edges(gray: Image.Image) -> Image.Image:
    gray = ImageOps.autocontrast(gray, cutoff=1).filter(ImageFilter.GaussianBlur(radius=0.5))
    edges = ImageOps.autocontrast(gray.filter(ImageFilter.FIND_EDGES), cutoff=1)
    threshold = otsu_threshold(edges)
    return edges.point(lambda p: 0 if p >= threshold else 255)


def black_ratio(bw: Image.Image) -> float:
    hist = bw.histogram()
    total = sum(hist)
    return hist[0] / total if total else 0.0


def border_black_ratio(bw: Image.Image) -> float:
    px = bw.load()
    total = black = 0
    for x in range(bw.width):
        for y in (0, bw.height - 1):
            total += 1
            black += px[x, y] == 0
    for y in range(1, bw.height - 1):
        for x in (0, bw.width - 1):
            total += 1
            black += px[x, y] == 0
    return black / total if total else 0.0


def transitions_score(bw: Image.Image) -> float:
    px = bw.load()
    tr = 0
    for y in range(bw.height):
        prev = px[0, y]
        for x in range(1, bw.width):
            cur = px[x, y]
            tr += cur != prev
            prev = cur
    for x in range(bw.width):
        prev = px[x, 0]
        for y in range(1, bw.height):
            cur = px[x, y]
            tr += cur != prev
            prev = cur
    return min(1.0, tr / 1200.0)


def candidate_score(bw: Image.Image, target_fill: float = 0.30) -> float:
    ratio = black_ratio(bw)
    border = border_black_ratio(bw)
    detail = transitions_score(bw)
    score = abs(ratio - target_fill) * 2.5 + border * 1.2 - detail * 0.45
    if ratio < 0.015 or ratio > 0.90:
        score += 4.0
    return score


def _candidate_map(gray: Image.Image) -> list[tuple[str, Image.Image]]:
    candidates = [("global", make_bw_global(gray))]
    candidates.append(("global_inv", ImageOps.invert(candidates[0][1])))
    for radius, c in ((5, 3), (7, 5), (9, 7), (11, 9)):
        a = make_bw_adaptive(gray, radius=radius, c=c)
        candidates.append((f"adaptive_{radius}_{c}", a))
        candidates.append((f"adaptive_{radius}_{c}_inv", ImageOps.invert(a)))
    e = make_bw_edges(gray)
    candidates.append(("edges", e))
    candidates.append(("edges_inv", ImageOps.invert(e)))
    return candidates


def select_auto_method(gray: Image.Image, target_fill: float = 0.30) -> str:
    candidates = _candidate_map(gray)
    return min(candidates, key=lambda x: candidate_score(x[1], target_fill))[0]


def robust_auto_binarize(gray: Image.Image, target_fill: float = 0.30, method: str | None = None) -> Image.Image:
    candidates = _candidate_map(gray)
    if method is None:
        method = min(candidates, key=lambda x: candidate_score(x[1], target_fill))[0]
    for name, img in candidates:
        if name == method:
            return img
    raise ArgumentError(f"Неизвестный auto-метод: {method}")


def binarize(
    gray: Image.Image,
    mode: str,
    threshold: str | int | float,
    target_fill: float = 0.30,
    auto_method: str | None = None,
) -> Image.Image:
    auto = str(threshold).lower() == "auto"
    if mode == "dither":
        gray = ImageOps.autocontrast(gray, cutoff=1)
        bw = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
        if auto and auto_method == "dither_inv":
            return ImageOps.invert(bw)
        return bw
    if auto:
        return robust_auto_binarize(gray, target_fill, auto_method)
    try:
        threshold_int = int(float(threshold))
    except (TypeError, ValueError) as exc:
        raise ArgumentError("threshold должен быть 'auto' или целым числом 0–255") from exc
    if not 0 <= threshold_int <= 255:
        raise ArgumentError("threshold должен быть в диапазоне 0–255")
    return gray.point(lambda p: 0 if p <= threshold_int else 255)


# ============================================================
# GLYPH / CFG
# ============================================================

class GlyphBank:
    def __init__(self, prefix: str, max_glyphs: int = 0):
        self.prefix = validate_prefix(prefix)
        self.max_glyphs = int(max_glyphs or 0)
        if self.max_glyphs and self.max_glyphs < 1:
            raise ArgumentError("max_glyphs должен быть 0 или положительным")
        self.definitions: list[tuple[str, str]] = []
        self.by_data: dict[str, str] = {}
        blank = "\n".join("." * GLYPH for _ in range(GLYPH))
        self._add(f"{self.prefix}_blank", blank)

    def _add(self, name: str, data: str) -> str:
        self.by_data[data] = name
        self.definitions.append((name, data))
        return name

    def get(self, tile: str) -> str:
        name = self.by_data.get(tile)
        if name is not None:
            return name
        if self.max_glyphs and len(self.definitions) >= self.max_glyphs:
            raise GlyphLimitError(
                f"Слишком много уникальных глифов: лимит {self.max_glyphs}. "
                "Упростите изображение или увеличьте лимит."
            )
        return self._add(f"{self.prefix}_g{len(self.definitions):03d}", tile)


def frame_to_names(bw: Image.Image, bank: GlyphBank, invert: bool) -> list[str]:
    if bw.size != (W, H):
        raise ArgumentError(f"Кадр должен быть {W}x{H}, получено {bw.size}")
    px = bw.load()
    names: list[str] = []
    for r in range(ROWS):
        for c in range(COLS):
            lines = []
            for y in range(r * GLYPH, (r + 1) * GLYPH):
                line = []
                for x in range(c * GLYPH, (c + 1) * GLYPH):
                    draw = px[x, y] == 0
                    if invert:
                        draw = not draw
                    line.append("*" if draw else ".")
                lines.append("".join(line))
            names.append(bank.get("\n".join(lines)))
    return names


def _validate_frame_names(frames: Sequence[Sequence[str]]) -> None:
    if not frames:
        raise ConfigValidationError("Анимация не содержит кадров")
    expected = ROWS * COLS
    for i, names in enumerate(frames):
        if len(names) != expected:
            raise ConfigValidationError(f"Кадр {i}: {len(names)} тайлов вместо {expected}")


def build_config(prefix: str, bank: GlyphBank, frames: Sequence[Sequence[str]], durs_s: Sequence[float], autostart: bool) -> str:
    prefix = validate_prefix(prefix)
    _validate_frame_names(frames)
    if len(frames) != len(durs_s):
        raise ConfigValidationError("Количество кадров и длительностей не совпадает")
    if any(d <= 0 or not math.isfinite(float(d)) for d in durs_s):
        raise ConfigValidationError("Некорректные длительности кадров")

    m = prefix.upper()
    out: list[str] = []
    ap = out.append
    ap("# " + "=" * 60)
    ap(f"# АНИМАЦИЯ '{prefix}': кадров {len(frames)}, глифов {len(bank.definitions)}")
    ap("# Сгенерировано gif2klipper.py")
    ap("# " + "=" * 60)
    ap("")

    for name, data in bank.definitions:
        rows = data.split("\n")
        if len(rows) != GLYPH or any(len(row) != GLYPH or any(ch not in ".*" for ch in row) for row in rows):
            raise ConfigValidationError(f"Повреждён глиф {name}")
        ap(f"[display_glyph {name}]")
        ap("data:")
        out.extend("  " + row for row in rows)
        ap("")

    for i, names in enumerate(frames):
        for r in range(ROWS):
            ap(f"[display_data {prefix}_f{i} r{r}]")
            ap(f"position: {r}, 0")
            ap("text: " + "".join(f"~{x}~" for x in names[r * COLS:(r + 1) * COLS]))
            ap("")

    ap(f"[gcode_macro {m}_START]")
    ap("gcode:")
    ap("    ANIMS_STOP_ALL")
    if len(frames) > 1:
        for i in range(len(frames)):
            ap(f"    UPDATE_DELAYED_GCODE ID={prefix}_t{i} DURATION=0")
    ap(f"    SET_DISPLAY_GROUP GROUP={prefix}_f0")
    if len(frames) > 1:
        ap(f"    UPDATE_DELAYED_GCODE ID={prefix}_t0 DURATION={float(durs_s[0]):.3f}")
    ap("")

    ap(f"[gcode_macro {m}_STOP]")
    ap("gcode:")
    if len(frames) > 1:
        for i in range(len(frames)):
            ap(f"    UPDATE_DELAYED_GCODE ID={prefix}_t{i} DURATION=0")
    ap(f"    SET_DISPLAY_GROUP GROUP={DEFAULT_GROUP}")
    ap("")

    if len(frames) > 1:
        for i in range(len(frames)):
            nxt = (i + 1) % len(frames)
            ap(f"[delayed_gcode {prefix}_t{i}]")
            ap("gcode:")
            ap(f"    SET_DISPLAY_GROUP GROUP={prefix}_f{nxt}")
            ap(f"    UPDATE_DELAYED_GCODE ID={prefix}_t{nxt} DURATION={float(durs_s[nxt]):.3f}")
            ap("")

    if autostart:
        ap(f"[delayed_gcode {prefix}_boot]")
        ap("initial_duration: 2.0")
        ap("gcode:")
        ap(f"    {m}_START")
        ap("")
    return "\n".join(out)


def validate_config_text(text: str, prefix: str) -> None:
    prefix = validate_prefix(prefix)
    sections: list[str] = []
    seen: set[str] = set()
    glyphs: set[str] = set()
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1]
            if sec in seen:
                raise ConfigValidationError(f"Дублированная секция: [{sec}]")
            seen.add(sec)
            if sec.startswith("display_glyph "):
                glyphs.add(sec.split(" ", 1)[1])
            sections.append(sec)
    if not any(s.startswith(f"gcode_macro {prefix.upper()}_START") for s in sections):
        raise ConfigValidationError("Не найден START macro")
    if not any(s.startswith(f"gcode_macro {prefix.upper()}_STOP") for s in sections):
        raise ConfigValidationError("Не найден STOP macro")
    refs = re.findall(r"~([a-z][a-z0-9_]*)~", text)
    missing = sorted({r for r in refs if r not in glyphs})
    if missing:
        raise ConfigValidationError("CFG содержит ссылки на отсутствующие glyph: " + ", ".join(missing[:8]))


# ============================================================
# PREVIEW / RESAMPLING
# ============================================================

def make_preview(bw_frames: Sequence[Image.Image], durs_ms: Sequence[float], base: str | Path, scale: int = 3) -> Path | None:
    if not bw_frames:
        return None
    if scale < 1 or scale > 8:
        raise ArgumentError("preview-scale должен быть 1–8")
    base = Path(base)
    base.parent.mkdir(parents=True, exist_ok=True)
    blue, white = (43, 74, 220), (255, 255, 255)
    rgb: list[Image.Image] = []
    for bw in bw_frames:
        resized = bw.resize((bw.width * scale, bw.height * scale), Image.Resampling.NEAREST)
        mask = resized.point(lambda p: 255 if p == 0 else 0)
        rgb.append(Image.composite(Image.new("RGB", resized.size, blue), Image.new("RGB", resized.size, white), mask))
    if len(rgb) == 1:
        out = base.with_suffix(".png")
        rgb[0].save(out)
    else:
        out = base.with_suffix(".gif")
        rgb[0].save(out, save_all=True, append_images=rgb[1:], duration=[max(int(d), 20) for d in durs_ms], loop=0, optimize=True)
    return out


def _resample_to_fps(items: Sequence[FrameResult], fps: float) -> list[FrameResult]:
    if fps <= 0 or not math.isfinite(fps):
        raise ArgumentError("fps должен быть положительным")
    if not items:
        return []
    step = 1000.0 / fps
    total = float(sum(x.duration_ms for x in items))
    if total <= 0:
        return list(items)

    # Sample the source timeline at the requested cadence, but keep the exact
    # original total duration by shortening the final output frame as needed.
    sample_times = []
    t = 0.0
    while t < total:
        sample_times.append(t)
        t += step

    out: list[FrameResult] = []
    source_i = 0
    source_end = float(items[0].duration_ms)
    for idx, tick in enumerate(sample_times):
        while source_i < len(items) - 1 and tick >= source_end:
            source_i += 1
            source_end += items[source_i].duration_ms
        next_tick = sample_times[idx + 1] if idx + 1 < len(sample_times) else total
        duration = max(1, int(round(next_tick - tick)))
        src = items[source_i]
        out.append(FrameResult(src.names, src.bw.copy(), duration))
    return out


def _deduplicate(items: Iterable[FrameResult]) -> tuple[list[FrameResult], int]:
    out: list[FrameResult] = []
    removed = 0
    for item in items:
        if out and out[-1].names == item.names:
            out[-1].duration_ms += item.duration_ms
            item.bw.close()
            removed += 1
            continue
        out.append(item)
    return out, removed


# ============================================================
# CONVERSION
# ============================================================

def convert_file(
    source: str | Path,
    output: str | Path,
    *,
    prefix: str = "anim",
    fit_mode: str = "contain",
    mode: str = "threshold",
    threshold: str | int = "auto",
    auto_fill: float = 0.30,
    invert: bool = False,
    fps: float | None = None,
    min_frame_ms: int = 60,
    max_frames: int | None = DEFAULT_MAX_FRAMES,
    max_glyphs: int = 0,
    autostart: bool = False,
    preview_dir: str | Path | None = None,
    preview_out: str | Path | None = None,
    preview_scale: int = 3,
    limits: ConverterLimits | None = None,
) -> ConversionResult:
    source = Path(source)
    if not source.exists():
        raise InputFormatError(f"Файл не найден: {source}")
    if source.stat().st_size <= 0:
        raise InputFormatError("Пустой входной файл")
    prefix = validate_prefix(prefix)
    if fit_mode not in {"contain", "cover", "stretch"}:
        raise ArgumentError("fit должен быть contain, cover или stretch")
    if mode not in {"threshold", "dither"}:
        raise ArgumentError("mode должен быть threshold или dither")
    if min_frame_ms < 1:
        raise ArgumentError("min-frame-ms должен быть >= 1")
    if not 0.0 < auto_fill < 1.0:
        raise ArgumentError("auto-fill должен быть в диапазоне 0..1")

    lim = limits or ConverterLimits(max_frames=max_frames)
    if max_frames is not None:
        lim.max_frames = max_frames
    items: list[FrameResult] = []
    bank = GlyphBank(prefix, max_glyphs=max_glyphs)
    auto_method: str | None = None
    dither_auto_method: str | None = None
    source_duration = 0

    preview_debug = Path(preview_dir) if preview_dir else None
    if preview_debug:
        preview_debug.mkdir(parents=True, exist_ok=True)

    for i, sample in enumerate(iter_frames(source, lim)):
        source_duration += sample.duration_ms
        gray = fit(sample.image, fit_mode)
        sample.image.close()
        if str(threshold).lower() == "auto" and mode == "threshold" and auto_method is None:
            auto_method = select_auto_method(gray, auto_fill)
        if str(threshold).lower() == "auto" and mode == "dither" and dither_auto_method is None:
            probe = ImageOps.autocontrast(gray, cutoff=1).convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
            inv = ImageOps.invert(probe)
            dither_auto_method = "dither_inv" if candidate_score(inv, auto_fill) < candidate_score(probe, auto_fill) else "dither"
            probe.close(); inv.close()
        method = auto_method if mode == "threshold" else dither_auto_method
        bw = binarize(gray, mode, threshold, target_fill=auto_fill, auto_method=method)
        gray.close()
        if preview_debug:
            bw.resize((W * 4, H * 4), Image.Resampling.NEAREST).save(preview_debug / f"frame_{i:03d}.png")
        names = tuple(frame_to_names(bw, bank, invert))
        items.append(FrameResult(names, bw, max(sample.duration_ms, min_frame_ms)))

    if not items:
        raise InputFormatError("Во входном файле нет отображаемых кадров")

    if fps is not None:
        resampled = _resample_to_fps(items, fps)
        for old in items:
            old.bw.close()
        items = resampled

    deduped, removed = _deduplicate(items)
    durs_ms = [max(int(x.duration_ms), min_frame_ms) for x in deduped]
    frames = [x.names for x in deduped]
    bw_frames = [x.bw for x in deduped]

    preview_path = make_preview(bw_frames, durs_ms, preview_out, preview_scale) if preview_out else None
    durs_s = [d / 1000.0 for d in durs_ms]
    cfg = build_config(prefix, bank, frames, durs_s, autostart)
    validate_config_text(cfg, prefix)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    try:
        tmp.write_text(cfg, encoding="utf-8")
        tmp.replace(output)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        for item in deduped:
            item.bw.close()

    return ConversionResult(
        output_path=output,
        frames=len(frames),
        unique_glyphs=len(bank.definitions),
        source_duration_ms=source_duration,
        output_duration_ms=sum(durs_ms),
        deduplicated_frames=removed,
        binarizer=auto_method or dither_auto_method or ("dither" if mode == "dither" else "fixed"),
        preview_path=preview_path,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GIF/видео -> анимация дисплея Klipper 128x64")
    p.add_argument("gif", help="путь к GIF/PNG/JPG или видео")
    p.add_argument("-o", "--output")
    p.add_argument("--prefix", default="anim")
    p.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain")
    p.add_argument("--mode", choices=["threshold", "dither"], default="threshold")
    p.add_argument("--threshold", default="auto")
    p.add_argument("--auto-fill", type=float, default=0.30)
    p.add_argument("--invert", action="store_true")
    p.add_argument("--fps", type=float, default=None, help="реально пересэмплировать таймлайн под эту частоту")
    p.add_argument("--min-frame-ms", type=int, default=60)
    p.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES)
    p.add_argument("--max-input-frames", type=int, default=DEFAULT_MAX_INPUT_FRAMES)
    p.add_argument("--max-input-pixels", type=int, default=DEFAULT_MAX_INPUT_PIXELS)
    p.add_argument("--max-duration-ms", type=int, default=DEFAULT_MAX_DURATION_MS)
    p.add_argument("--ffmpeg-timeout", type=int, default=DEFAULT_FFMPEG_TIMEOUT)
    p.add_argument("--max-glyphs", type=int, default=0)
    p.add_argument("--autostart", action="store_true")
    p.add_argument("--preview-dir")
    p.add_argument("--preview-out")
    p.add_argument("--preview-scale", type=int, default=3)
    p.add_argument("--json-result", action="store_true")
    return p.parse_args()


def main() -> int:
    try:
        a = parse_args()
        src = Path(a.gif)
        out = Path(a.output) if a.output else src.with_suffix(".cfg")
        limits = ConverterLimits(
            max_frames=a.max_frames,
            max_input_frames=a.max_input_frames,
            max_input_pixels=a.max_input_pixels,
            max_duration_ms=a.max_duration_ms,
            ffmpeg_timeout=a.ffmpeg_timeout,
        )
        result = convert_file(
            src, out,
            prefix=a.prefix,
            fit_mode=a.fit,
            mode=a.mode,
            threshold=a.threshold,
            auto_fill=a.auto_fill,
            invert=a.invert,
            fps=a.fps,
            min_frame_ms=a.min_frame_ms,
            max_frames=a.max_frames,
            max_glyphs=a.max_glyphs,
            autostart=a.autostart,
            preview_dir=a.preview_dir,
            preview_out=a.preview_out,
            preview_scale=a.preview_scale,
            limits=limits,
        )
        if a.json_result:
            print(json.dumps(result.as_dict(), ensure_ascii=False))
        else:
            print(f"Кадров: {result.frames}, уникальных глифов: {result.unique_glyphs}")
            print(f"Объединено подряд дубликатов: {result.deduplicated_frames}")
            print(f"Auto/binarizer: {result.binarizer}")
            print(f"Макросы: {a.prefix.upper()}_START / {a.prefix.upper()}_STOP")
            print(f"Файл: {result.output_path}")
            if result.preview_path:
                print(f"Preview: {result.preview_path}")
        return 0
    except ConverterError as exc:
        print(f"Ошибка конвертера: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Операция прервана", file=sys.stderr)
        return 130
    except Exception as exc:  # defensive CLI boundary
        print(f"Непредвиденная ошибка конвертера: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
