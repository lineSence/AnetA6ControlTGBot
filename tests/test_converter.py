from pathlib import Path
import importlib.util
import sys
from PIL import Image, ImageDraw

SPEC = importlib.util.spec_from_file_location("gif2klipper", "/root/gif2klipper.py")
mod = importlib.util.module_from_spec(SPEC)
sys.modules["gif2klipper"] = mod
assert SPEC.loader is not None
SPEC.loader.exec_module(mod)

def test_prefix_validation():
    assert mod.validate_prefix("abc_12") == "abc_12"
    for value in ("", "1abc", "bad-name", "a" * 33, "anims"):
        try:
            mod.validate_prefix(value)
        except mod.ConverterError:
            pass
        else:
            raise AssertionError(value)

def test_glyph_limit_is_enforced_early():
    bank = mod.GlyphBank("x", max_glyphs=2)
    blank = next(iter(bank.by_data))
    assert bank.get(blank) == "x_blank"
    first = "*" * 16 + "\n" + ("." * 16 + "\n") * 15 + "*" * 16
    assert bank.get(first) == "x_g001"
    try:
        bank.get("." * 16 + "\n" + ("*" * 16 + "\n") * 15)
    except mod.GlyphLimitError:
        pass
    else:
        raise AssertionError("glyph limit not enforced")

def test_duplicate_durations_are_accumulated():
    a = Image.new("L", (128, 64), 255)
    b = Image.new("L", (128, 64), 255)
    x = mod.FrameResult(("a",), a, 70)
    y = mod.FrameResult(("a",), b, 80)
    out, removed = mod._deduplicate([x, y])
    assert removed == 1
    assert len(out) == 1
    assert out[0].duration_ms == 150
    out[0].bw.close()

def test_config_validator_catches_missing_glyph():
    try:
        mod.validate_config_text('[display_data x_f0 r0]\ntext: ~missing~\n', 'x')
    except mod.ConfigValidationError:
        pass
    else:
        raise AssertionError("missing glyph was not detected")

def test_real_conversion_and_json_result(tmp_path):
    src = tmp_path / "in.png"
    out = tmp_path / "out.cfg"
    im = Image.new("RGB", (64, 64), "white")
    ImageDraw.Draw(im).rectangle((10, 10, 30, 30), fill="black")
    im.save(src)
    result = mod.convert_file(src, out, prefix="smoke", max_frames=2, max_glyphs=250, preview_out=tmp_path / "preview")
    assert result.frames == 1
    assert result.unique_glyphs >= 1
    assert out.exists()
    assert result.preview_path and result.preview_path.exists()
