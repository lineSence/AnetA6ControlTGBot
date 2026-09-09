from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import aiohttp

@dataclass(slots=True)
class MRResult:
    ok: bool
    result: Any = None
    error: str | None = None
    status: int | None = None
    raw: Any = None

def _error_text(payload: Any) -> str | None:
    if isinstance(payload, dict) and payload.get("error"):
        err = payload["error"]
        if isinstance(err, dict):
            return str(err.get("message") or err)
        return str(err)
    return None

def fmt_size(b):
    b = int(b or 0)
    if b < 1024: return f"{b} B"
    if b < 1024**2: return f"{b/1024:.1f} KB"
    if b < 1024**3: return f"{b/1024**2:.1f} MB"
    return f"{b/1024**3:.2f} GB"

def fmt_dur(s):
    s = int(s or 0)
    return f"{s//3600:d}:{(s%3600)//60:02d}:{s%60:02d}"

def file_items(files):
    out = []
    for i, f in enumerate(files):
        if isinstance(f, dict):
            name = f.get("filename") or f.get("path") or f.get("name")
            if name:
                out.append((i, name, f.get("size", 0)))
    return out

def upload_ok(r: Any):
    if isinstance(r, MRResult):
        return r.ok
    if not isinstance(r, dict) or "error" in r:
        return False
    res = r.get("result", r)
    return isinstance(res, dict) and (
        "item" in res or res.get("action") in ("create_file", "modify_file")
    )

class PrinterClient:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self._s: aiohttp.ClientSession | None = None

    async def session(self):
        if self._s is None or self._s.closed:
            self._s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._s

    async def close(self):
        if self._s and not self._s.closed:
            await self._s.close()

    async def _request(self, method: str, path: str, **kwargs) -> MRResult:
        try:
            s = await self.session()
            async with s.request(method, self.base + path, **kwargs) as r:
                text = await r.text()
                try:
                    payload = await r.json(content_type=None)
                except Exception:
                    payload = {"raw": text[:1000]}
                err = _error_text(payload)
                if r.status < 200 or r.status >= 300:
                    return MRResult(False, error=err or f"HTTP {r.status}", status=r.status, raw=payload)
                if err:
                    return MRResult(False, error=err, status=r.status, raw=payload)
                result = payload.get("result") if isinstance(payload, dict) and "result" in payload else payload
                return MRResult(True, result=result, status=r.status, raw=payload)
        except Exception as e:
            return MRResult(False, error=f"{type(e).__name__}: {e}")

    async def get(self, p): return await self._request("GET", p)
    async def post(self, p, payload=None): return await self._request("POST", p, json=payload or {})
    async def gcode(self, script): return await self.post("/printer/gcode/script", {"script": script})

    async def status(self):
        r = await self.get("/printer/objects/query?print_stats&display_status&extruder&heater_bed&toolhead&gcode_move&webhooks")
        return (r.result.get("status") or {}) if r.ok and isinstance(r.result, dict) else {}

    async def info(self):
        r = await self.get("/printer/info")
        return r.result if r.ok and isinstance(r.result, dict) else {}

    async def files(self):
        r = await self.get("/server/files/list")
        return r.result if r.ok and isinstance(r.result, list) else []

    async def history(self, limit=10):
        r = await self.get(f"/server/history/list?limit={int(limit)}")
        if not r.ok or not isinstance(r.result, dict): return []
        return r.result.get("jobs", []) or []

    async def macros(self):
        r = await self.get("/printer/objects/list")
        objs = (r.result or {}).get("objects", []) if r.ok and isinstance(r.result, dict) else []
        out = []
        for o in objs:
            if not isinstance(o, str) or not o.startswith("gcode_macro "):
                continue
            n = o.split(" ", 1)[1]
            if n.startswith("_") or n == "ANIMS_STOP_ALL" or n.endswith("_START") or n.endswith("_STOP"):
                continue
            out.append(n)
        return sorted(out)

    async def upload_gcode(self, path, filename):
        try:
            s = await self.session()
            d = aiohttp.FormData()
            with open(path, "rb") as fh:
                d.add_field("file", fh, filename=filename)
                async with s.post(self.base + "/server/files/upload", data=d) as r:
                    payload = await r.json(content_type=None)
                    err = _error_text(payload)
                    if r.status < 200 or r.status >= 300 or err:
                        return MRResult(False, error=err or f"HTTP {r.status}", status=r.status, raw=payload)
                    return MRResult(True, result=payload.get("result", payload), status=r.status, raw=payload)
        except Exception as e:
            return MRResult(False, error=f"{type(e).__name__}: {e}")

    async def print_start(self, filename): return await self.post("/printer/print/start", {"filename": filename})
    async def pause(self): return await self.post("/printer/print/pause")
    async def resume(self): return await self.post("/printer/print/resume")
    async def cancel(self): return await self.post("/printer/print/cancel")
    async def emergency_stop(self): return await self.post("/printer/emergency_stop")
    async def restart(self): return await self.post("/printer/restart")
    async def firmware_restart(self): return await self.post("/printer/firmware_restart")
    async def reboot_host(self): return await self.post("/machine/reboot")
    async def shutdown_host(self): return await self.post("/machine/shutdown")
