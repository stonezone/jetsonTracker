"""
Web layer (FastAPI): the operator console for solo bring-up.
  GET  /            -> HTML page: live MJPEG + HUD + kill + live sliders
  GET  /stream.mjpg -> annotated MJPEG
  GET  /status      -> JSON
  POST /kill /resume
  POST /tune        -> live-update tunables (no restart)
  POST /ptz/{stop,zin,zout,zstop}
"""
from __future__ import annotations
import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

from .control_api import register_control_api


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>WAVECAM testbed</title><style>
body{background:#0b0e11;color:#cdd6dc;font:13px ui-monospace,Menlo,monospace;margin:0;padding:10px}
h1{font-size:15px;color:#ffb02e;margin:4px 0 10px;letter-spacing:1px}
img{width:100%;max-width:760px;border:1px solid #273641;border-radius:4px;display:block}
.row{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0;align-items:center}
button{background:#141b22;color:#cdd6dc;border:1px solid #273641;border-radius:4px;padding:7px 12px;font:13px monospace;cursor:pointer}
button:hover{border-color:#ffb02e}
.kill{background:#3a0f10;border-color:#7d2020;color:#ff6b6b;font-weight:bold}
.go{border-color:#1f7d4f;color:#3fe08f}
label{display:flex;flex-direction:column;gap:2px;font-size:11px;color:#6b7d86;min-width:120px}
input[type=range]{width:130px}
#hud{font-size:12px;color:#3fd0e0;margin:6px 0}
.box{border:1px solid #1e2a33;border-radius:4px;padding:8px;margin:6px 0}
</style></head><body>
<h1>WAVECAM // VISION TESTBED</h1>
<img id=v src="/stream.mjpg">
<div id=hud>connecting…</div>
<div class=row>
  <button class=kill onclick="post('/kill')">KILL</button>
  <button class=go onclick="post('/resume')">RESUME</button>
  <button onclick="post('/ptz/stop')">PTZ stop</button>
  <button onclick="post('/ptz/zin')">Zoom+</button>
  <button onclick="post('/ptz/zout')">Zoom-</button>
  <button onclick="post('/ptz/zstop')">Zoom stop</button>
  <button onclick="toggleMask()">Mask</button>
</div>
<div class=box>
<div class=row>
  <label>conf threshold<input type=range min=0.05 max=0.95 step=0.05 id=lock oninput="tune()"></label>
  <label>min blob area<input type=range min=20 max=4000 step=20 id=area oninput="tune()"></label>
  <label>deadzone<input type=range min=0.02 max=0.30 step=0.01 id=dz oninput="tune()"></label>
  <label>max pan spd<input type=range min=1 max=24 step=1 id=mps oninput="tune()"></label>
  <label>max tilt spd<input type=range min=1 max=20 step=1 id=mts oninput="tune()"></label>
</div></div>
<script>
let mask=true;
function post(u){fetch(u,{method:'POST'})}
function toggleMask(){mask=!mask;fetch('/tune',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({show_mask:mask})})}
function tune(){
 const b={lock_threshold:+lock.value,min_area:+area.value,deadzone:+dz.value,max_pan_speed:+mps.value,max_tilt_speed:+mts.value};
 fetch('/tune',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
}
async function poll(){
 try{const s=await (await fetch('/status')).json();
  document.getElementById('hud').textContent=
   `${s.state} | conf ${(s.conf||0).toFixed(2)} | C${s.has_color?'Y':'-'} P${s.has_person?'Y':'-'} M${s.matched?'Y':'-'} | fps ${(s.fps||0).toFixed(1)} | PTZ ${s.ptz_enabled?'ON':'off'} | cmd ${s.cmd||'-'} | owner ${s.owner||'-'}${s.killed?' [KILL]':''} | conn ${s.connected}`;
 }catch(e){}
 setTimeout(poll,500);
}
poll();
</script></body></html>"""


class Tune(BaseModel):
    lock_threshold: float | None = None
    unlock_threshold: float | None = None
    min_area: int | None = None
    deadzone: float | None = None
    max_pan_speed: int | None = None
    max_tilt_speed: int | None = None
    invert_pan: bool | None = None
    invert_tilt: bool | None = None
    show_mask: bool | None = None


def build_app(pipeline) -> FastAPI:
    app = FastAPI(title="WAVECAM testbed")
    app.state.pipeline = pipeline
    cfg = pipeline.cfg

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    def _frames():
        boundary = b"--frame\r\n"
        while True:
            jpg = pipeline.state.get_jpeg()
            if jpg is None:
                time.sleep(0.05)
                continue
            yield boundary + b"Content-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.03)

    @app.get("/stream.mjpg")
    def stream():
        return StreamingResponse(_frames(),
                                 media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/status")
    def status():
        s = pipeline.state.get_status()
        s.update(pipeline.owner.state())      # expose owner + sticky kill latch
        return JSONResponse(s)

    @app.post("/kill")
    def kill():
        pipeline.kill(True)
        return {"killed": True}

    @app.post("/resume")
    def resume():
        pipeline.kill(False)
        return {"killed": False}

    @app.post("/ptz/stop")
    def ptz_stop():
        # STOP: halt pan/tilt AND zoom, and release the current owner (pause
        # autonomous) WITHOUT clearing the kill latch.
        pipeline.ptz.stop()
        pipeline.ptz.zoom("stop")
        pipeline.owner.release(pipeline.owner.owner)
        return {"ok": True, "owner": pipeline.owner.owner}

    @app.post("/ptz/zin")
    def ptz_zin():
        if pipeline.owner.killed:
            return {"ok": False, "blocked": "killed"}
        pipeline.ptz.zoom("tele")
        return {"ok": True}

    @app.post("/ptz/zout")
    def ptz_zout():
        if pipeline.owner.killed:
            return {"ok": False, "blocked": "killed"}
        pipeline.ptz.zoom("wide")
        return {"ok": True}

    @app.post("/ptz/zstop")
    def ptz_zstop():
        pipeline.ptz.zoom("stop")
        return {"ok": True}

    @app.post("/tune")
    def tune(t: Tune):
        if t.show_mask is not None:
            pipeline.state.show_mask = t.show_mask
        # fusion knobs
        if t.lock_threshold is not None:
            cfg.fusion.lock_threshold = t.lock_threshold
        if t.unlock_threshold is not None:
            cfg.fusion.unlock_threshold = t.unlock_threshold
        # color knobs
        if t.min_area is not None and pipeline.color is not None:
            cfg.color.min_area = t.min_area
        # servo knobs (live)
        if t.deadzone is not None:
            cfg.ptz.deadzone = t.deadzone
        if t.max_pan_speed is not None:
            cfg.ptz.max_pan_speed = t.max_pan_speed
        if t.max_tilt_speed is not None:
            cfg.ptz.max_tilt_speed = t.max_tilt_speed
        if t.invert_pan is not None:
            cfg.ptz.invert_pan = t.invert_pan
        if t.invert_tilt is not None:
            cfg.ptz.invert_tilt = t.invert_tilt
        return {"ok": True}

    register_control_api(app, pipeline, _frames)

    return app
