"""UX/UI tests for release 2.7.0.

They lock the design system: screen zones, button limits, exits from every
screen, empty states, middlewares and the command list.
"""
from pathlib import Path
from types import SimpleNamespace

from aiogram import Dispatcher
from aiogram.types import CallbackQuery, Message

from tgbot import ui, uxkit
from tgbot.config import CURRENT_CONFIG_VERSION, _migrate, load
from tgbot.middlewares import AccessMiddleware, AntiFloodMiddleware, setup
from tgbot.uxkit import (
    BAR_EMPTY,
    BAR_FULL,
    BOT_COMMANDS,
    MAX_LABEL,
    Debounce,
    clamp_label,
    crumbs,
    deny_text,
    esc,
    eta_seconds,
    field,
    is_allowed,
    pct,
    progress,
    screen,
)

STATUS = {
    "print_stats": {"state": "standby", "filename": "", "print_duration": 0},
    "display_status": {"progress": 0.0},
    "extruder": {"temperature": 24.0, "target": 0.0},
    "heater_bed": {"temperature": 23.0, "target": 0.0},
    "toolhead": {"speed_factor": 1.0, "extrude_factor": 1.0},
    "gcode_move": {"homing_origin": [0, 0, 0.0]},
    "fan": {"speed": 0.0},
}


class FakePC:
    """Printer client with fixed answers. No network in tests."""

    def __init__(self, status=None, info=None, files=None, macros=None):
        self._status = status if status is not None else dict(STATUS)
        self._info = info if info is not None else {"state": "ready"}
        self._files = files if files is not None else []
        self._macros = macros if macros is not None else []

    async def status(self):
        return self._status

    async def info(self):
        return self._info

    async def files(self):
        return self._files

    async def macros(self):
        return self._macros


def gcode_files(count):
    return [
        {"path": f"part{i}.gcode", "filename": f"part{i}.gcode", "size": 1024 * (i + 1)}
        for i in range(count)
    ]


class FakeCQ(CallbackQuery):
    def __init__(self, user_id=1, data="m:main"):
        super().__init__()
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, bool(show_alert)))


class FakeMsg(Message):
    def __init__(self, user_id=1, text="/menu"):
        super().__init__()
        self.from_user = SimpleNamespace(id=user_id)
        self.text = text
        self.replies = []

    async def answer(self, text, **kwargs):
        self.replies.append(text)


def make_cfg(**over):
    base = dict(
        allowed_user_ids={1},
        antiflood_seconds=0.8,
        deny_notice_seconds=300,
        progress_bar_width=12,
        pending_ttl_seconds=900,
        power_actions_require_confirmation=True,
        dangerous_macros_require_confirmation=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def every_keyboard():
    """All keyboards of the bot, so invariants are checked in one place."""
    pc = FakePC(files=gcode_files(3), macros=["HOME_ALL", "LOAD_FILAMENT"])
    files_kb, _, _, _ = await ui.files_kb(pc, 0)
    macro_kb, _, _, _ = await ui.macro_kb(pc, 0)
    return {
        "main": await ui.main_kb(pc),
        "status": await ui.status_kb(pc),
        "history": ui.history_kb(),
        "diag": ui.diag_kb(),
        "errors": ui.errors_kb(),
        "help": ui.help_kb(),
        "files": files_kb,
        "macros": macro_kb,
        "temp": await ui.temp_kb(pc),
        "move": ui.move_kb({"xy": 10, "z": 1}),
        "tune": await ui.tune_kb(pc),
        "power": ui.power_kb(),
        "anims": ui.anim_kb({"items": [{"name": "cat", "prefix": "a1"}], "default": "a1"}),
        "preview": ui.preview_kb(
            {"fill": 0.5, "invert": False, "mode": "dither", "fit": "contain", "frames": 10}
        ),
        "confirm": ui.confirm_kb("f:start"),
    }


def callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# --- design system -------------------------------------------------------

def test_screen_has_four_zones():
    text = screen("Заголовок", ["тело"], path="пульт › файлы", meta="12:00")
    assert text.startswith("<b>Заголовок</b>")
    assert "<i>пульт › файлы</i>" in text
    assert "тело" in text
    assert text.rstrip().endswith("<i>12:00</i>")


def test_screen_without_body_is_valid():
    assert screen("Только заголовок") == "<b>Только заголовок</b>"


def test_esc_protects_html():
    assert esc("<b>&") == "&lt;b&gt;&amp;"
    assert field("📄", "Файл", "a<b>.gcode") == "📄 Файл: a&lt;b&gt;.gcode"


def test_clamp_label_keeps_buttons_short():
    long = "Очень длинная подпись кнопки для проверки"
    out = clamp_label(long)
    assert len(out) <= MAX_LABEL
    assert out.endswith("…")
    assert clamp_label("Коротко") == "Коротко"


def test_crumbs_build_path():
    assert crumbs("пульт", "файлы") == "пульт › файлы"
    assert crumbs("пульт", None, "макросы") == "пульт › макросы"


def test_progress_bar_is_stable():
    bar = progress(0.5, 12)
    assert len(bar) == 12
    assert bar.count(BAR_FULL) == 6
    assert bar.count(BAR_EMPTY) == 6
    assert len(progress(0.5, 999)) == 20
    assert progress(None) == BAR_EMPTY * 12
    assert progress(5.0, 8) == BAR_FULL * 8


def test_pct_clamps_values():
    assert pct(0.4237) == "42.4%"
    assert pct(5) == "100.0%"
    assert pct(None) == "0.0%"


def test_eta_seconds_is_honest():
    assert eta_seconds(100, 0.5) == 100
    assert eta_seconds(100, 0) is None
    assert eta_seconds(0, 0.5) is None
    assert eta_seconds(None, None) is None


def test_debounce_blocks_the_second_tap():
    d = Debounce(1.0)
    assert d.hit("a", now=100.0) is True
    assert d.hit("a", now=100.3) is False
    assert d.hit("a", now=101.5) is True
    assert d.hit("b", now=101.5) is True
    d.purge(now=100000.0)
    assert d.hit("a", now=100001.0) is True


def test_bot_commands_are_short_list():
    assert 3 <= len(BOT_COMMANDS) <= 7
    names = [name for name, _ in BOT_COMMANDS]
    assert len(set(names)) == len(names)
    for name, text in BOT_COMMANDS:
        assert name == name.lower()
        assert not name.startswith("/")
        assert 0 < len(text) <= 60


# --- keyboards -----------------------------------------------------------

async def test_labels_fit_the_limit():
    for name, markup in (await every_keyboard()).items():
        for row in markup.inline_keyboard:
            for b in row:
                assert len(b.text) <= MAX_LABEL, (name, b.text)


async def test_rows_are_not_too_wide():
    for name, markup in (await every_keyboard()).items():
        for row in markup.inline_keyboard:
            assert 1 <= len(row) <= 4, (name, len(row))


async def test_every_screen_has_an_exit():
    exits = {ui.HOME, "m:power", "h:top", "pv:cancel"}
    for name, markup in (await every_keyboard()).items():
        assert set(callbacks(markup)) & exits, name


async def test_no_empty_or_orphan_callbacks():
    for name, markup in (await every_keyboard()).items():
        for data in callbacks(markup):
            assert data, name
            assert data != "u:noop", name


def test_ui_module_has_one_noop():
    src = Path(ui.__file__).read_text(encoding="utf-8")
    assert "u:noop" not in src
    assert ui.NOOP == "m:noop"


def test_state_row_shows_what_is_needed_now():
    printing = dict(ui.state_row("printing", "ready"))
    assert "m:pause" in printing.values()
    assert "m:cancel" in printing.values()

    paused = dict(ui.state_row("paused", "ready"))
    assert "m:resume" in paused.values()

    broken = dict(ui.state_row(None, "shutdown"))
    assert "p:recover" in broken.values()

    idle = dict(ui.state_row("standby", "ready"))
    assert "m:files:0" in idle.values()


async def test_main_keyboard_starts_with_the_main_action():
    markup = await ui.main_kb(FakePC())
    first = callbacks(markup)[0]
    assert first == "m:files:0"
    assert callbacks(markup)[-1] == "m:estop"


async def test_temp_keyboard_has_cooldown():
    markup = await ui.temp_kb(FakePC())
    assert "t:off" in callbacks(markup)


async def test_empty_files_show_a_hint():
    markup, page, pages, total = await ui.files_kb(FakePC(files=[]), 0)
    assert (page, pages, total) == (0, 1, 0)
    assert "h:print" in callbacks(markup)


async def test_empty_macros_show_a_hint():
    markup, page, pages, total = await ui.macro_kb(FakePC(macros=[]), 0)
    assert (page, pages, total) == (0, 1, 0)
    assert "h:macros" in callbacks(markup)


async def test_empty_anims_show_a_hint():
    markup = ui.anim_kb({"items": []})
    assert "h:anims" in callbacks(markup)


async def test_file_pages_have_a_counter():
    pc = FakePC(files=gcode_files(14))
    markup, page, pages, total = await ui.files_kb(pc, 0)
    assert (page, pages, total) == (0, 3, 14)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "1/3" in labels
    assert "m:files:1" in callbacks(markup)

    markup, page, pages, total = await ui.files_kb(pc, 99)
    assert page == 2
    assert "m:files:1" in callbacks(markup)


def test_confirm_keyboard_puts_the_safe_button_first():
    markup = ui.confirm_kb("mc:run:1:abcd1234", "✅ Выполнить", "m:macros")
    row = markup.inline_keyboard[0]
    assert row[0].callback_data == "m:macros"
    assert row[1].callback_data == "mc:run:1:abcd1234"


def test_toggle_shows_state_in_label():
    assert ui.toggle("Вентилятор", True).startswith("✅")
    assert ui.toggle("Вентилятор", False).startswith("⬜")


def test_nav_row_is_never_a_dead_end():
    assert ui.nav_row() == []
    row = ui.nav_row(back="m:status", refresh="m:diag", home=True)
    assert [d for _, d in row] == ["m:status", ui.HOME, "m:diag"]


# --- screens in handlers -------------------------------------------------

def test_handler_screens_are_html():
    from tgbot import handlers as h

    user = SimpleNamespace(id=7, first_name="Аня", full_name="Аня", username="anya")
    assert h.start_text(user).startswith("<b>")
    assert "<b>" in h.menu_screen()
    assert "<b>" in h.help_screen("print")
    assert "<b>" in h.temp_screen()
    assert "<b>" in h.power_screen()


def test_error_screen_offers_retry_and_exit():
    from tgbot import handlers as h

    text, markup = h.error_screen("Ошибка", ["причина"], retry="m:status", where="пульт › статус")
    assert "<b>Ошибка</b>" in text
    cbs = callbacks(markup)
    assert "m:status" in cbs
    assert ui.HOME in cbs


def test_command_aliases_cover_the_command_list():
    from tgbot.handlers import COMMAND_ALIASES

    for name, _ in BOT_COMMANDS:
        assert f"/{name}" in COMMAND_ALIASES, name
    assert COMMAND_ALIASES.get("меню") == COMMAND_ALIASES.get("/menu")


# --- middlewares ---------------------------------------------------------

def test_is_allowed_rejects_updates_without_user():
    cfg = make_cfg()
    assert is_allowed(cfg, SimpleNamespace(id=1)) is True
    assert is_allowed(cfg, SimpleNamespace(id=2)) is False
    assert is_allowed(cfg, None) is False


def test_deny_text_shows_the_user_id():
    text = deny_text(4242)
    assert "4242" in text
    assert "allowed_user_ids" in text


async def test_access_middleware_blocks_a_stranger():
    cfg = make_cfg()
    mw = AccessMiddleware(cfg)
    seen = []

    async def handler(event, data):
        seen.append(event)
        return "ok"

    cq = FakeCQ(user_id=999)
    assert await mw(handler, cq, {"cfg": cfg}) is None
    assert seen == []
    assert cq.answers and cq.answers[0][1] is True

    msg = FakeMsg(user_id=999)
    assert await mw(handler, msg, {"cfg": cfg}) is None
    assert msg.replies and "999" in msg.replies[0]
    assert mw.denied == 2


async def test_access_middleware_lets_the_owner_in():
    cfg = make_cfg()
    mw = AccessMiddleware(cfg)

    async def handler(event, data):
        return "ok"

    assert await mw(handler, FakeCQ(user_id=1), {"cfg": cfg}) == "ok"
    assert await mw(handler, FakeMsg(user_id=1), {"cfg": cfg}) == "ok"
    assert mw.denied == 0


async def test_antiflood_drops_a_double_tap():
    mw = AntiFloodMiddleware(5.0)
    seen = []

    async def handler(event, data):
        seen.append(event)
        return "ok"

    assert await mw(handler, FakeCQ(data="m:status"), {}) == "ok"
    second = FakeCQ(data="m:status")
    assert await mw(handler, second, {}) is None
    assert len(seen) == 1
    assert mw.dropped == 1
    assert second.answers


async def test_antiflood_allows_another_button():
    mw = AntiFloodMiddleware(5.0)

    async def handler(event, data):
        return "ok"

    assert await mw(handler, FakeCQ(data="m:status"), {}) == "ok"
    assert await mw(handler, FakeCQ(data="m:temp"), {}) == "ok"
    assert mw.dropped == 0


def test_setup_attaches_both_middlewares():
    dp = Dispatcher()
    access, flood = setup(dp, make_cfg())
    assert isinstance(access, AccessMiddleware)
    assert isinstance(flood, AntiFloodMiddleware)
    assert len(dp.message.middlewares) == 2
    assert len(dp.callback_query.middlewares) == 2


# --- config --------------------------------------------------------------

def test_migration_adds_ux_keys():
    new = _migrate({"config_version": 6})
    assert CURRENT_CONFIG_VERSION >= 7
    assert new["config_version"] == CURRENT_CONFIG_VERSION
    assert new["antiflood_seconds"] == 0.8
    assert new["deny_notice_seconds"] == 300
    assert new["progress_bar_width"] == 12


def test_load_clamps_ux_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "config_version: 7\n"
        "bot_token: token\n"
        "allowed_user_ids: [1]\n"
        "progress_bar_width: 999\n"
        "antiflood_seconds: -5\n",
        encoding="utf-8",
    )
    cfg = load(str(path))
    assert cfg.progress_bar_width == 20
    assert cfg.antiflood_seconds == 0.0
    assert cfg.deny_notice_seconds == 300
    assert cfg.notify_chat == 1
