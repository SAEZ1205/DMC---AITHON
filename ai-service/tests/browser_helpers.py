"""
Ayudas para probar la pantalla en un Chrome REAL (headless, protocolo CDP). Solo conexiones locales: el servidor estático y el
backend de prueba escuchan en 127.0.0.1 y el navegador no navega a ningún sitio externo. Si no hay Chrome, las pruebas se omiten.
"""
import asyncio
import contextlib
import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from functools import partial
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
_CANDIDATES = (r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
               r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def find_chrome():
    for path in _CANDIDATES:
        if os.path.isfile(path):
            return path
    return shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("msedge")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def static_server(port: int):
    """Sirve frontend/ (solo lectura) en 127.0.0.1:port."""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), partial(Quiet, directory=str(FRONTEND)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


class Cdp:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expression):
        result = await self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            raise RuntimeError(json.dumps(result["exceptionDetails"], ensure_ascii=False)[:500])
        return result["result"].get("value")

    async def wait(self, expression, timeout=20.0, every=0.1):
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                last = await self.js(expression)
            except RuntimeError:
                last = None
            if last:
                return last
            await asyncio.sleep(every)
        raise TimeoutError("timeout esperando: " + expression)


# Cuenta las solicitudes de red que hace la PÁGINA (fetch, XHR, beacon) para demostrar que el validador no hace ninguna.
NET_HOOK = """
window.__net = [];
const _f = window.fetch; window.fetch = function () { window.__net.push(String(arguments[0])); return _f.apply(this, arguments); };
const _o = XMLHttpRequest.prototype.open; XMLHttpRequest.prototype.open = function (m, u) { window.__net.push(String(u)); return _o.apply(this, arguments); };
if (navigator.sendBeacon) { const _b = navigator.sendBeacon.bind(navigator); navigator.sendBeacon = function (u) { window.__net.push(String(u)); return _b.apply(null, arguments); }; }
"""


@contextlib.asynccontextmanager
async def chrome_page(url: str):
    port = free_port()
    profile = tempfile.mkdtemp(prefix="chrome-test-")
    proc = subprocess.Popen([find_chrome(), "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "--window-size=1440,1200",
                             "--no-first-run", "--disable-extensions", "--disable-gpu", "--autoplay-policy=no-user-gesture-required",
                             # el navegador tampoco sale a internet: fuentes, iconos y logos externos fallan al instante (solo 127.0.0.1 resuelve)
                             "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1 , EXCLUDE localhost", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = None
        for _ in range(100):
            try:
                page = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1)) if t["type"] == "page")
                break
            except Exception:
                await asyncio.sleep(0.2)
        if page is None:
            raise RuntimeError("Chrome no arrancó")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
            cdp = Cdp(ws)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await cdp.call("Page.addScriptToEvaluateOnNewDocument", source=NET_HOOK)
            await cdp.call("Page.navigate", url=url)
            await cdp.wait("document.readyState !== 'loading' && !!document.getElementById('quizValidate')", 30)
            yield cdp
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        shutil.rmtree(profile, ignore_errors=True)
