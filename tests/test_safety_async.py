import pytest
from types import SimpleNamespace
from tgbot.safety import Safety

class FakePC:
    def __init__(self, klippy="ready", pstate="standby"):
        self.klippy = klippy
        self.pstate = pstate
    async def info(self):
        return {"state": self.klippy}
    async def status(self):
        return {"print_stats": {"state": self.pstate}}

@pytest.mark.asyncio
async def test_motion_blocked_while_printing():
    ok, reason = await Safety().require_motion(FakePC("ready","printing"))
    assert not ok
    assert "печать" in reason

@pytest.mark.asyncio
async def test_recovery_restart_allowed_after_shutdown():
    ok, reason = await Safety().require_power_action(FakePC("shutdown","paused"), "restart")
    assert ok

@pytest.mark.asyncio
async def test_reboot_blocked_during_normal_print():
    ok, reason = await Safety().require_power_action(FakePC("ready","printing"), "reboot")
    assert not ok

@pytest.mark.asyncio
async def test_idle_allowed():
    ok, reason = await Safety().require_idle(FakePC("ready","standby"))
    assert ok
