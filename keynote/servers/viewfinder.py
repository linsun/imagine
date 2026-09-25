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

PHOTO INBOX -- a photo taken ELSEWHERE (phone, or drone -> phone)
  POST /upload                 raw image body (or multipart); newest upload wins
  GET  /latest.jpg             the newest uploaded photo, normalised to JPEG
  GET  /latest                 {"has_photo":..,"received_at":..,"age_s":..}
  GET  /inbox                  a phone-friendly "send a photo" page

STAGE -- the projector, as a web page (replaces ffplay/say when in a cluster)
  GET  /stage                  fullscreen page: shows the photo, plays the film
  POST /stage/show             body = image or video; ?caption=
  POST /stage/say              body = text, spoken by the browser
  GET  /stage/state            {"seq":..,"kind":..,"caption":..,"say":..}
  GET  /stage/media            the bytes last shown

  python -m servers.viewfinder
"""

import email
import email.policy
import io
import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PREVIEW_PORT", "8888"))
# 127.0.0.1 on the laptop; 0.0.0.0 in a pod so the Service can reach it.
HOST = os.environ.get("VIEWFINDER_HOST", "127.0.0.1")
INBOX_DIR = os.environ.get("INBOX_DIR", "./inbox")
# Phone and drone photos are 12-48MP. Everything downstream (base64 over MCP,
# the gateway's 32MB buffer, Nano Banana) wants far less, so shrink on arrival.
INBOX_MAX_EDGE = int(os.environ.get("INBOX_MAX_EDGE", "2560"))

_lock = threading.Lock()
_frame = b""             # latest JPEG bytes the browser pushed
_last_push = 0.0
_countdown_until = 0.0
_paused = False          # advisory: the page drops the camera when this is set

_photo = b""             # newest uploaded photo (JPEG)
_photo_at = 0.0

_stage = {"seq": 0, "kind": "", "caption": "", "say": "", "say_seq": 0,
          "mime": ""}
_stage_media = b""


def _normalise(data: bytes) -> bytes:
    """Any phone photo -> an upright JPEG no bigger than INBOX_MAX_EDGE.

    iPhones default to HEIC and store rotation in EXIF; both break naive
    consumers. Pillow (+ pillow-heif when present) fixes both. Without Pillow
    the bytes pass through untouched, which is fine for JPEGs.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((INBOX_MAX_EDGE, INBOX_MAX_EDGE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=90)
        return out.getvalue()


def _first_file(body: bytes, ctype: str) -> bytes:
    """Pull the first file part out of a multipart/form-data body."""
    msg = email.message_from_bytes(
        b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body,
        policy=email.policy.HTTP)
    for part in msg.iter_parts():
        payload = part.get_payload(decode=True)
        if payload and (part.get_filename() or
                        part.get_content_maintype() == "image"):
            return payload
    return b""


def _load_newest() -> None:
    """Pick up the newest photo already in INBOX_DIR, so a restart keeps it."""
    global _photo, _photo_at
    try:
        names = sorted(n for n in os.listdir(INBOX_DIR) if n.endswith(".jpg"))
    except FileNotFoundError:
        return
    if names:
        p = os.path.join(INBOX_DIR, names[-1])
        with open(p, "rb") as f:
            _photo = f.read()
        _photo_at = os.path.getmtime(p)


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


# The phone's page. Behind agentgateway the key rides in the URL fragment
# (#key=...) -- fragments never leave the phone -- and goes out as a Bearer
# header, which is what the gateway's apiKey policy checks.
_INBOX_PAGE = b"""<!doctype html><html><head><title>Send a photo</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:18px;background:#0b0c0e;color:#e8e8ea;
  font:600 17px -apple-system,system-ui,sans-serif;padding:16px;box-sizing:border-box}
label{background:#f5b642;color:#111;padding:18px 28px;border-radius:16px;font-size:20px}
input{display:none}
img{max-width:92vw;max-height:55vh;border-radius:12px;display:none}
#s{opacity:.75;text-align:center}
</style></head><body>
<label>Send a photo<input id="f" type="file" accept="image/*"></label>
<img id="p"><div id="s">the newest photo you send is the one the Director uses</div>
<script>
var key=(location.hash.match(/key=([^&]+)/)||[])[1]||'';
var H=key?{'Authorization':'Bearer '+decodeURIComponent(key)}:{};
var s=document.getElementById('s'), p=document.getElementById('p');
function latest(){
  fetch('latest.jpg?'+Date.now(),{headers:H}).then(function(r){
    if(!r.ok) return; return r.blob().then(function(b){
      p.src=URL.createObjectURL(b); p.style.display='block';});
  }).catch(function(){});
}
document.getElementById('f').onchange=function(e){
  var f=e.target.files[0]; if(!f) return;
  s.textContent='sending '+Math.round(f.size/1024)+' KB ...';
  var h=Object.assign({'Content-Type':f.type||'application/octet-stream'},H);
  fetch('upload',{method:'POST',headers:h,body:f}).then(function(r){
    return r.json().then(function(d){
      s.textContent=r.ok?'sent. ask the Director to use your latest photo.'
                        :'failed: '+(d.error||r.status);
      if(r.ok) latest();
    });
  }).catch(function(err){ s.textContent='failed: '+err; });
};
latest();
</script></body></html>"""


# The projector. Replaces ffplay + `say` when the agents live in a cluster:
# the stage MCP server posts media here, this page shows it. Browsers block
# sound until someone clicks once, so the page starts with one click to arm.
_STAGE_PAGE = b"""<!doctype html><html><head><title>Stage</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{margin:0;height:100%;background:#000;overflow:hidden;color:#fff;
  font:600 18px -apple-system,system-ui,sans-serif}
img,video{position:fixed;inset:0;width:100%;height:100%;object-fit:contain;display:none}
#arm{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;
  background:#000;cursor:pointer;z-index:9;opacity:.9}
#cap{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);opacity:.75}
</style></head><body>
<div id="arm">click once to arm the stage (sound)</div>
<img id="i"><video id="v" playsinline></video><div id="cap"></div>
<script>
var seq=-1, sseq=-1, i=document.getElementById('i'), v=document.getElementById('v'),
    cap=document.getElementById('cap'), arm=document.getElementById('arm');
arm.onclick=function(){ arm.style.display='none';
  try{ speechSynthesis.speak(new SpeechSynthesisUtterance('')); }catch(e){}
  document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen().catch(function(){});
};
function tick(){
  fetch('/stage/state').then(function(r){return r.json();}).then(function(d){
    if(d.seq!==seq && d.seq>0){
      seq=d.seq; var src='/stage/media?'+d.seq; cap.textContent=d.caption||'';
      if(d.kind==='video'){ i.style.display='none'; v.src=src; v.style.display='block';
        v.play().catch(function(){ v.muted=true; v.play(); }); }
      else { v.pause(); v.style.display='none'; i.src=src; i.style.display='block'; }
    }
    if(d.say_seq!==sseq){ var first=sseq<0; sseq=d.say_seq;
      if(!first && d.say){ try{ speechSynthesis.speak(new SpeechSynthesisUtterance(d.say)); }catch(e){} } }
  }).catch(function(){}).finally(function(){ setTimeout(tick,700); });
}
tick();
</script></body></html>"""


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

    def _body(self) -> bytes:
        # iOS Shortcuts may send chunked bodies, which BaseHTTPRequestHandler
        # does not decode for you.
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            out = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    return bytes(out)
                out += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(n) if n else b""

    def _bytes(self, data: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        global _photo, _photo_at, _stage_media, _frame, _last_push
        u = urlparse(self.path)
        if u.path == "/upload":
            body = self._body()
            ctype = self.headers.get("Content-Type", "")
            if ctype.startswith("multipart/"):
                body = _first_file(body, ctype)
            if not body:
                return self._json({"error": "empty upload -- send the photo as "
                                            "the request body"}, 400)
            try:
                jpg = _normalise(body)
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": f"not an image I can read: {exc}"}, 415)
            os.makedirs(INBOX_DIR, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}.jpg"
            with open(os.path.join(INBOX_DIR, name), "wb") as f:
                f.write(jpg)
            with _lock:
                _photo, _photo_at = jpg, time.time()
            print(f"viewfinder: photo received ({len(body)//1024} KB -> "
                  f"{len(jpg)//1024} KB jpeg) {name}", flush=True)
            return self._json({"ok": True, "name": name, "bytes": len(jpg)})

        if u.path == "/stage/show":
            body = self._body()
            ctype = self.headers.get("Content-Type", "application/octet-stream")
            q = parse_qs(u.query)
            with _lock:
                _stage_media = body
                _stage.update(seq=_stage["seq"] + 1, mime=ctype,
                              kind="video" if ctype.startswith("video") else "image",
                              caption=(q.get("caption") or [""])[0])
            return self._json({"ok": True, "seq": _stage["seq"]})

        if u.path == "/stage/say":
            text = self._body().decode("utf-8", "replace").strip()
            with _lock:
                _stage.update(say=text, say_seq=_stage["say_seq"] + 1)
            return self._json({"ok": True})

        if u.path == "/push":
            data = self._body()
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

        if u.path == "/inbox":
            return self._bytes(_INBOX_PAGE, "text/html")

        if u.path == "/latest.jpg":
            with _lock:
                data = _photo
            if not data:
                return self._json({"error": "no photo yet -- send one from the "
                                            "phone first"}, 404)
            return self._bytes(data, "image/jpeg")

        if u.path == "/latest":
            with _lock:
                has, at = bool(_photo), _photo_at
            return self._json({"has_photo": has, "received_at": at,
                               "age_s": round(time.time() - at) if has else None})

        if u.path == "/stage":
            return self._bytes(_STAGE_PAGE, "text/html")

        if u.path == "/stage/state":
            with _lock:
                return self._json(dict(_stage))

        if u.path == "/stage/media":
            with _lock:
                data, mime = _stage_media, _stage["mime"]
            if not data:
                return self._json({"error": "nothing on stage yet"}, 404)
            return self._bytes(data, mime)

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
    _load_newest()
    srv = ThreadingHTTPServer((HOST, PORT), H)
    srv.daemon_threads = True
    print(f"viewfinder: listening on http://localhost:{PORT}/  "
          f"(browser owns the camera; open the page and allow access)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
