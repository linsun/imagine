"""The viewfinder: the camera preview, owned by the BROWSER.

WHY THE BROWSER, NOT OPENCV
---------------------------
OpenCV grabbing the camera server-side could not reliably use macOS Continuity
Camera (the iPhone): it enumerated the device but the picture never made it to
the screen. Browsers, on the other hand, access cameras through getUserMedia,
which is exactly what handles Continuity Camera, virtual webcams and device
selection natively -- the same thing Streamlit's st.camera_input relies on.

So the browser now owns the camera. The page shows a live <video>, a dropdown
of real camera NAMES (iPhone included), and pushes JPEG frames to this server a
few times a second. This process just holds the latest frame, so the
agent-driven capture -- "take the photo", 3-2-1 countdown and all -- keeps
working unchanged: camera_mcp asks for /frame.jpg and gets the last frame the
browser pushed.

No OpenCV, no device probing here -- it is pure stdlib.

  GET  /                       the camera page (put this on the projector)
  POST /push                   browser posts a JPEG frame; returns the countdown
  GET  /frame.jpg?countdown=3  show 3-2-1 on the page, then return the frame
  GET  /release                ask the page to drop the camera (green light off)
  GET  /resume                 ask the page to pick it back up
  GET  /healthz                {"ok":true,"has_frame":true,"streaming":true,...}

  python -m servers.viewfinder
"""

import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PREVIEW_PORT", "8888"))

_lock = threading.Lock()
_frame = b""             # latest JPEG bytes the browser pushed
_last_push = 0.0
_countdown_until = 0.0
_paused = False          # advisory: the page drops the camera when this is set


_PAGE = b"""<!doctype html><html><head><title>Viewfinder</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{margin:0;height:100%;background:#000;overflow:hidden;
  font:600 15px -apple-system,system-ui,sans-serif;color:#fff}
video{width:100%;height:100%;object-fit:contain;display:block;background:#000}
#bar{position:fixed;top:16px;left:50%;transform:translateX(-50%);
  display:flex;gap:10px;align-items:center;padding:9px 12px;border-radius:14px;
  background:rgba(0,0,0,.55);-webkit-backdrop-filter:blur(10px);
  backdrop-filter:blur(10px);z-index:10;transition:opacity .35s}
#bar.hide{opacity:0;pointer-events:none}
#bar span{opacity:.7}
select{border:0;border-radius:9px;padding:9px 12px;color:#fff;
  background:rgba(255,255,255,.16);font:inherit;max-width:60vw}
select option{color:#000}
#cd{position:fixed;inset:0;display:none;align-items:center;justify-content:center;
  font-size:26vh;font-weight:800;color:#fff;
  text-shadow:0 6px 40px rgba(0,0,0,.7);z-index:20;pointer-events:none}
#msg{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);
  color:#f5b642;font-weight:600;text-align:center;max-width:80vw}
</style></head>
<body>
<video id="v" autoplay playsinline muted></video>
<div id="bar"><span>Camera</span><select id="sel"></select></div>
<div id="cd"></div>
<div id="msg"></div>
<canvas id="c" style="display:none"></canvas>
<script>
var v=document.getElementById('v'), sel=document.getElementById('sel'),
    cd=document.getElementById('cd'), msg=document.getElementById('msg'),
    c=document.getElementById('c'), bar=document.getElementById('bar'),
    stream=null, paused=false, t;

function sched(){clearTimeout(t);t=setTimeout(function(){bar.classList.add('hide');},4000);}
function show(){bar.classList.remove('hide');sched();}
document.addEventListener('mousemove',show);
document.addEventListener('keydown',function(e){if(e.key==='h')bar.classList.toggle('hide');});

function stop(){ if(stream){stream.getTracks().forEach(function(x){x.stop();});stream=null;} }

async function start(deviceId){
  stop();
  try{
    stream=await navigator.mediaDevices.getUserMedia({
      video: deviceId?{deviceId:{exact:deviceId}}:{width:{ideal:1920},height:{ideal:1080}},
      audio:false});
    v.srcObject=stream; msg.textContent='';
    // Continuity Camera / iPhone often needs an explicit play(), or the
    // <video> sits on a black frame even though the track is live.
    try{ await v.play(); }catch(e){}
    await list();
    if(v.requestVideoFrameCallback){ v.requestVideoFrameCallback(grab); }
  }catch(e){ msg.textContent='camera error: '+(e.message||e.name)+
    ' -- allow camera access for localhost, then reload'; }
}
v.addEventListener('loadedmetadata', function(){ v.play().catch(function(){}); });

async function list(){
  try{
    var devs=await navigator.mediaDevices.enumerateDevices();
    var cams=devs.filter(function(d){return d.kind==='videoinput';});
    var cur=stream&&stream.getVideoTracks()[0]
            ?stream.getVideoTracks()[0].getSettings().deviceId:'';
    sel.innerHTML='';
    cams.forEach(function(d,i){
      var o=document.createElement('option');
      o.value=d.deviceId; o.textContent=d.label||('Camera '+(i+1));
      if(d.deviceId===cur)o.selected=true;
      sel.appendChild(o);
    });
    if(cams.length<=1){bar.classList.add('hide');}
  }catch(e){}
}
sel.onchange=function(){show();start(sel.value);};
navigator.mediaDevices.addEventListener('devicechange', list);

// Capture only REAL, painted frames. requestVideoFrameCallback fires when the
// browser has actually presented a new video frame -- a plain setInterval can
// fire before the first frame is painted and grab a black canvas, which is
// exactly what an iPhone/Continuity Camera does while it wakes.
var lastPush=0;
function send(b){
  fetch('/push',{method:'POST',headers:{'Content-Type':'image/jpeg'},body:b})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.countdown>0){cd.style.display='flex';cd.textContent=d.countdown;}
      else{cd.style.display='none';}
      if(d.paused && !paused){paused=true;stop();msg.textContent='camera released';}
    }).catch(function(){});
}
function grab(){
  if(!paused && v.videoWidth && !v.paused && (performance.now()-lastPush)>230){
    lastPush=performance.now();
    c.width=v.videoWidth; c.height=v.videoHeight;
    c.getContext('2d').drawImage(v,0,0);
    c.toBlob(function(b){ if(b) send(b); },'image/jpeg',0.85);
  }
  if(v.requestVideoFrameCallback){ v.requestVideoFrameCallback(grab); }
}
// While released, keep a slow poll so /resume can wake the camera again.
function pollWhilePaused(){
  fetch('/healthz').then(function(r){return r.json();}).then(function(d){
    if(!d.paused){paused=false;msg.textContent='';start(sel.value||undefined);}
  }).catch(function(){});
}
// Timer drives the paused-poll, and is the frame source on browsers without
// requestVideoFrameCallback.
setInterval(function(){
  if(paused){ pollWhilePaused(); return; }
  if(!v.requestVideoFrameCallback){ grab(); }
}, 250);

start(); sched();
</script>
</body></html>"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        return

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/push":
            global _frame, _last_push
            n = int(self.headers.get("Content-Length", "0") or "0")
            data = self.rfile.read(n) if n else b""
            if data:
                with _lock:
                    _frame = data
                    _last_push = time.time()
            rem = _countdown_until - time.time()
            return self._json({"countdown": max(0, math.ceil(rem)) if rem > 0 else 0,
                               "paused": _paused})
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        global _countdown_until, _paused
        u = urlparse(self.path)

        if u.path == "/release":
            _paused = True
            return self._json({"ok": True, "paused": True})

        if u.path == "/resume":
            _paused = False
            return self._json({"ok": True, "paused": False})

        if u.path == "/healthz":
            with _lock:
                has = bool(_frame)
                fresh = (time.time() - _last_push) < 3
            return self._json({"ok": has and fresh and not _paused,
                               "has_frame": has, "streaming": fresh,
                               "paused": _paused, "port": PORT})

        if u.path == "/frame.jpg":
            q = parse_qs(u.query)
            try:
                cd = int((q.get("countdown") or ["0"])[0])
            except ValueError:
                cd = 0
            if cd > 0:
                _countdown_until = time.time() + cd
                time.sleep(cd + 0.25)     # let the room see 3-2-1, then grab
                _countdown_until = 0.0
            with _lock:
                data = _frame
            if not data:
                return self._json(
                    {"error": "no frame yet -- open the camera page and allow "
                              "camera access (the browser owns the camera now)."},
                    503)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # anything else: the camera page
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)


def main() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    print(f"viewfinder: listening on http://localhost:{PORT}/  "
          f"(browser owns the camera; open the page and allow access)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
