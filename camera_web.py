"""Live YOLO camera on the AUTHORITY web page - drop-in add-on for dashboard_server.py.

main_linked.py writes camera_frame.jpg (the same picture as its OpenCV window) and
camera_stats.json. This module streams them to the browser and adds the panel to the page.

Add 2 lines, nothing else changes:
  dashboard_server.py   at the top:                 import camera_web
                        first line inside do_GET:   if camera_web.handle(self): return
  web/operator.html     just before </body>:        <script src="/camera_panel.js"></script>

Routes:  /api/camera/stream  (MJPEG live video)   /api/camera/stats   /api/camera/snapshot.jpg
         /camera_panel.js    (the panel for operator.html)   /camera  (a page with only the camera)
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FRAME = os.path.join(HERE, "camera_frame.jpg")
STATS = os.path.join(HERE, "camera_stats.json")
IDLE_END_S = 30                  # end a stream after 30 s without new pictures (the page reconnects)
GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)


def _read(path, mode="rb"):
    for _ in range(3):                                    # Windows: file may be replaced right now
        try:
            with open(path, mode) as f:
                return f.read()
        except FileNotFoundError:
            return None
        except OSError:
            time.sleep(0.01)
    return None


def _send(h, body, ctype, code=200):
    h.send_response(code)
    h.send_header("Content-Type", ctype)
    h.send_header("Cache-Control", "no-store")
    h.send_header("Access-Control-Allow-Origin", "*")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def stats():
    raw = _read(STATS, "r")
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    age = time.time() - d.get("time", 0) if d else None
    d["age_s"] = round(age, 1) if age is not None else None
    d["online"] = bool(d) and d.get("live", False) and age is not None and age < 5
    d["live"] = d["online"]                               # true only while YOLO is really running
    return d


def stream(h):
    h.send_response(200)
    h.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
    h.send_header("Cache-Control", "no-cache, no-store")
    h.send_header("Access-Control-Allow-Origin", "*")
    h.send_header("Connection", "close")
    h.end_headers()
    last, idle_since = None, time.time()
    try:
        while True:
            try:
                mt = os.path.getmtime(FRAME)
            except OSError:
                mt = None
            if mt and mt != last:
                jpg = _read(FRAME)
                if jpg and jpg[:2] == b"\xff\xd8":               # a complete JPEG
                    last, idle_since = mt, time.time()
                    h.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                  + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                    h.wfile.flush()
            elif time.time() - idle_since > IDLE_END_S:
                return
            time.sleep(0.04)
    except GONE:
        return                                            # browser closed the page


def handle(h):
    """Call first in do_GET. Returns True when this module answered the request."""
    path = h.path.split("?")[0].rstrip("/")
    if path == "/api/camera/stream":
        stream(h)
    elif path == "/api/camera/stats":
        _send(h, json.dumps(stats()).encode(), "application/json")
    elif path == "/api/camera/snapshot.jpg":
        jpg = _read(FRAME)
        if jpg:
            _send(h, jpg, "image/jpeg")
        else:
            _send(h, b'{"error": "no camera picture yet"}', "application/json", 404)
    elif path == "/camera_panel.js":
        _send(h, PANEL_JS.encode(), "text/javascript; charset=utf-8")
    elif path == "/camera":                               # stand-alone page: only the live camera
        _send(h, CAMERA_PAGE.encode(), "text/html; charset=utf-8")
    else:
        return False
    return True


CAMERA_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Live camera</title>
<link rel="stylesheet" href="/common.css"><style>body{background:var(--paper,#eef2f5);margin:0}</style></head>
<body><main></main><script src="/camera_panel.js"></script></body></html>"""


def serve(port=8601):
    """Small web server for the camera (started by main_linked.py in the background)."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.split("?")[0] in ("", "/"):
                self.path = "/camera"
            if not handle(self):
                _send(self, b"not found", "text/plain", 404)

    class S(ThreadingHTTPServer):
        allow_reuse_address = False                       # Windows: never share the port with an old copy

    try:
        srv = S(("0.0.0.0", port), H)
    except OSError:
        print(f"Camera web page: port {port} is busy (another YOLO running?) - web view off")
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"Camera web page: http://localhost:{port}   (also shown on the authority page)")
    return srv

# ------------------------------------------------------------------ the panel for operator.html
PANEL_JS = r"""
(function () {
  // Where the camera lives: the server this script was loaded from (YOLO's own camera server, port 8601)
  const me = document.currentScript && document.currentScript.src;
  const API = me ? new URL(me).origin : (window.API_BASE || "").replace(/\/$/, "");
  if (document.getElementById("ycamPanel")) return;                 // already on the page
  const css = `
  .ycamWrap{max-width:1800px;margin:0 auto;padding:18px 24px 0}
  @media (max-width:700px){.ycamWrap{padding:12px 16px 0}}
  .ycamP{background:var(--panel,#fff);border:1px solid var(--rule,#dde3e8);border-radius:14px;padding:16px;
         font-family:inherit;color:var(--ink,#16202a)}
  .ycamP h2{display:flex;align-items:center;gap:10px;margin:0 0 12px;font-size:17px;font-weight:600}
  .ycamP h2 small{font-weight:400;color:var(--ink-2,#5b6773);font-size:13px}
  .ycamBox{position:relative;width:100%;aspect-ratio:16/9;max-height:72vh;background:#121a22;border-radius:12px;overflow:hidden}
  .ycamBox img{width:100%;height:100%;object-fit:contain;display:block}
  .ycamOffline .ycamBox img{visibility:hidden}
  .ycamOff{position:absolute;inset:0;display:none;flex-direction:column;align-items:center;justify-content:center;gap:10px;
           color:#c9d6e2;font-size:15px;text-align:center;padding:16px}
  .ycamOff b{color:#fff;font-size:16px}
  .ycamOff code{color:#fff;font-size:15px;font-family:ui-monospace,Consolas,monospace}
  .ycamBadge{position:absolute;top:12px;left:12px;display:flex;gap:6px}
  .ycamBadge span{background:rgba(0,0,0,.6);color:#fff;font-size:12.5px;padding:5px 9px;border-radius:7px;display:flex;align-items:center;gap:6px}
  .ycamDot{width:9px;height:9px;border-radius:50%;background:#e53935;animation:ycamPulse 1.2s infinite}
  .ycamOffline .ycamDot{background:#8a96a3;animation:none}
  @keyframes ycamPulse{50%{opacity:.25}}
  .ycamBtns{display:flex;justify-content:flex-end;gap:10px;margin:12px 0}
  .ycamBtns button{border:1px solid var(--rule,#cfd8df);background:var(--panel,#fff);border-radius:8px;padding:9px 16px;
                   font:inherit;font-size:15px;cursor:pointer;color:inherit}
  .ycamBtns button:hover{background:var(--paper,#f3f6f8)}
  .ycamGrid{display:grid;gap:10px;margin-top:10px}
  .ycamG5{grid-template-columns:repeat(5,1fr)} .ycamG4{grid-template-columns:repeat(4,1fr)}
  .ycamTile{border:1px solid var(--rule,#dde3e8);border-radius:8px;padding:12px 14px;min-width:0}
  .ycamTile .k{display:block;font-size:13px;font-weight:600}
  .ycamTile .v{display:block;font-size:26px;font-weight:700;line-height:1.3;font-variant-numeric:tabular-nums}
  .ycamTile .c{display:block;font-size:13px;color:var(--ink-2,#5b6773);margin-top:4px}
  .ycamTile .v.ok{color:#1b7a3a} .ycamTile .v.bad{color:#c62828}
  @media (max-width:800px){.ycamG5{grid-template-columns:repeat(3,1fr)}.ycamG4{grid-template-columns:repeat(2,1fr)}
    .ycamBtns{justify-content:stretch}.ycamBtns button{flex:1}}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

  const KINDS = [["car", "Cars"], ["bike", "Bikes"], ["auto", "Autos"], ["bus", "Buses"], ["truck", "Trucks"]];
  const p = document.createElement("section");
  p.className = "ycamP ycamOffline"; p.id = "ycamPanel";
  p.innerHTML = `
    <h2>Live camera - AI detection <small id="ycamSub">waiting for YOLO</small></h2>
    <div class="ycamBox" id="ycamBox">
      <img id="ycamImg" alt="Live YOLO camera with detected vehicles">
      <div class="ycamOff" id="ycamOff"><b>Camera offline</b><code>python main_linked.py --source usb</code></div>
      <div class="ycamBadge"><span><i class="ycamDot"></i><b id="ycamLiveTxt">OFFLINE</b></span><span id="ycamFps" style="display:none"></span></div>
    </div>
    <div class="ycamBtns">
      <button id="ycamFull">Fullscreen</button><button id="ycamSnap">Snapshot</button><button id="ycamPause">Pause view</button>
    </div>
    <div class="ycamGrid ycamG5">${KINDS.map(([k, n]) =>
      `<div class="ycamTile"><span class="k">${n}</span><span class="v" id="ycamV_${k}">0</span><span class="c" id="ycamC_${k}">Counted: 0</span></div>`).join("")}</div>
    <div class="ycamGrid ycamG4">
      <div class="ycamTile"><span class="k">Waiting</span><span class="v" id="ycamWait">&mdash;</span></div>
      <div class="ycamTile"><span class="k">Traffic status</span><span class="v ok" id="ycamAlert">Normal</span></div>
      <div class="ycamTile"><span class="k">Inference</span><span class="v" id="ycamLat">&mdash; ms</span></div>
      <div class="ycamTile"><span class="k">Last sent to SUMO</span><span class="v" id="ycamSent">&mdash;</span></div>
    </div>`;
  const wrap = document.createElement("div");
  wrap.className = "ycamWrap"; wrap.appendChild(p);
  const main = document.querySelector("main");
  if (main && main.parentNode) main.parentNode.insertBefore(wrap, main);
  else document.body.prepend(wrap);

  const $ = id => document.getElementById(id);
  let online = false, paused = false, streaming = false;
  function startStream() { if (paused) return; streaming = true; $("ycamImg").src = API + "/api/camera/stream?t=" + Date.now(); }
  function stopStream() { streaming = false; $("ycamImg").removeAttribute("src"); }
  $("ycamImg").onerror = () => { streaming = false; };
  function setOnline(on) {
    online = on;
    p.classList.toggle("ycamOffline", !on);
    $("ycamOff").style.display = on ? "none" : "flex";
    $("ycamLiveTxt").textContent = on ? "LIVE" : "OFFLINE";
    if (on && !streaming) startStream();
    if (!on && streaming) stopStream();
  }
  async function poll() {
    let s = {};
    try { s = await (await fetch(API + "/api/camera/stats", { cache: "no-store" })).json(); } catch (e) {}
    setOnline(!!s.online);
    $("ycamSub").textContent = s.online ? `road ${s.road} - ${s.source}` : (s.age_s != null ? "stopped" : "waiting for YOLO");
    $("ycamFps").style.display = s.online ? "" : "none"; $("ycamFps").textContent = `${s.fps || 0} fps`;
    const iv = s.in_view || {}, ct = s.counted || {};
    for (const [k] of KINDS) { $("ycamV_" + k).textContent = s.online ? (iv[k] || 0) : 0; $("ycamC_" + k).textContent = `Counted: ${ct[k] || 0}`; }
    $("ycamWait").innerHTML = s.online ? (s.waiting || 0) : "&mdash;";
    $("ycamAlert").className = "v " + (s.online && s.alert ? "bad" : "ok");
    $("ycamAlert").textContent = s.online && s.alert ? "TRAFFIC ALERT" : "Normal";
    $("ycamLat").innerHTML = s.online ? `${s.latency_ms} ms` : "&mdash; ms";
    $("ycamSent").innerHTML = s.last_sent ? `${Math.max(0, Math.round(Date.now() / 1000 - s.last_sent))} s ago` : "&mdash;";
    if (s.online && !streaming && !paused) startStream();
  }
  $("ycamFull").onclick = () => { const b = $("ycamBox"); (b.requestFullscreen || b.webkitRequestFullscreen).call(b); };
  $("ycamSnap").onclick = () => {
    const a = document.createElement("a");
    a.href = API + "/api/camera/snapshot.jpg?t=" + Date.now();
    a.download = "camera_" + new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") + ".jpg";
    document.body.appendChild(a); a.click(); a.remove();
  };
  $("ycamPause").onclick = () => {
    paused = !paused; $("ycamPause").textContent = paused ? "Resume view" : "Pause view";
    if (paused) stopStream(); else if (online) startStream();
  };
  // Hide any OTHER camera panel already on the page (e.g. an older one that never connects).
  function hideOldPanels() {
    const marks = ["main_linked.py", "Live camera", "Live Camera", "LIVE CAMERA", "Camera feed", "Camera Feed"];
    const hit = el => !wrap.contains(el) && !el.contains(wrap) && marks.some(m => (el.textContent || "").includes(m));
    const all = [...document.querySelectorAll("body *")].filter(hit);
    const leaves = all.filter(el => !all.some(o => o !== el && el.contains(o)));   // the exact element with the text
    for (const el of leaves) {
      const panel = el.closest("section, .panel, .card");                          // its own panel only
      if (!panel || panel.contains(wrap) || panel.dataset.ycamHidden) continue;
      if ((panel.textContent || "").length > 3000) continue;                     // never hide a big area
      panel.style.display = "none"; panel.dataset.ycamHidden = "1";
    }
  }
  hideOldPanels(); setInterval(hideOldPanels, 2000);                // the page may draw it again later
  setOnline(false); poll(); setInterval(poll, 1000);
})();
"""
