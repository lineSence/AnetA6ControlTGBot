from __future__ import annotations

class Safety:
    async def snapshot(self, pc):
        info = await pc.info()
        status = await pc.status()
        return info, status

    async def _connected(self, pc):
        info = await pc.info()
        if not info:
            return False, "Moonraker/Klipper недоступен"
        return True, None

    async def require_idle(self, pc):
        ok, reason = await self._connected(pc)
        if not ok:
            return ok, reason
        st = await pc.status()
        if not st:
            return False, "нет состояния принтера"
        pstate = (st.get("print_stats") or {}).get("state")
        if pstate in ("printing", "paused"):
            return False, f"идёт печать ({pstate})"
        return True, None

    async def require_printing(self, pc):
        ok, reason = await self._connected(pc)
        if not ok:
            return ok, reason
        state = (await pc.status()).get("print_stats", {}).get("state")
        return (True, None) if state == "printing" else (False, "принтер не печатает")

    async def require_paused(self, pc):
        ok, reason = await self._connected(pc)
        if not ok:
            return ok, reason
        state = (await pc.status()).get("print_stats", {}).get("state")
        return (True, None) if state == "paused" else (False, "печать не на паузе")

    async def require_motion(self, pc):
        ok, reason = await self.require_idle(pc)
        if not ok:
            return ok, reason
        info = await pc.info()
        if info.get("state") != "ready":
            return False, f"Klipper не ready: {info.get('state', 'offline')}"
        return True, None

    async def require_power_action(self, pc, action):
        info = await pc.info()
        if not info:
            return False, "Moonraker/Klipper недоступен"
        pstate = (await pc.status()).get("print_stats", {}).get("state")
        klippy = info.get("state")
        recovery = klippy in ("shutdown", "error")

        if action in ("restart", "fw") and recovery:
            return True, None
        if pstate in ("printing", "paused"):
            return False, f"идёт печать ({pstate}); сначала остановите печать"
        if klippy not in ("ready", "shutdown", "error"):
            return False, f"Klipper не готов: {klippy or 'unknown'}"
        return True, None

    async def require_recovery(self, pc):
        info = await pc.info()
        if not info:
            return False, "Moonraker/Klipper недоступен"
        if info.get("state") in ("shutdown", "error"):
            return True, None
        return False, f"Klipper не находится в recovery-состоянии: {info.get('state', 'unknown')}"
