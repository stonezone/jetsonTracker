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

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel

from .auth import CONFIG, PTZ, SAFETY, require
from .control_api import register_control_api
from .ptz_owner import AUTONOMOUS


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>WAVECAM live tuning</title><style>
:root{color-scheme:dark;--bg:#090c0f;--panel:#10161c;--panel2:#151d24;--line:#273541;--text:#dce6ee;--muted:#7f909c;--cyan:#45d8f0;--amber:#ffb02e;--red:#ff5d5d;--green:#38e08a}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#15202a 0,#090c0f 34rem);color:var(--text);font:13px ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"SF Pro Text",Arial,sans-serif}
main{display:grid;grid-template-columns:minmax(340px,1.45fr) minmax(360px,.9fr);gap:12px;padding:12px;min-height:100vh}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
h1{font-size:17px;letter-spacing:.08em;margin:0;font-weight:750}.sub{color:var(--muted);font-size:12px}.video{min-width:0}.feed{position:relative;background:#050708;border:1px solid var(--line);border-radius:8px;overflow:hidden;box-shadow:0 18px 60px rgba(0,0,0,.35)}
img{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;background:#000}.hud{position:absolute;left:10px;right:10px;bottom:10px;display:flex;flex-wrap:wrap;gap:6px}.chip{background:rgba(9,12,15,.82);border:1px solid rgba(69,216,240,.25);border-radius:999px;padding:5px 8px;color:var(--cyan);font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
.side{display:flex;flex-direction:column;gap:10px}.bar{display:flex;gap:8px;flex-wrap:wrap}.card{background:linear-gradient(180deg,var(--panel),#0d1217);border:1px solid var(--line);border-radius:8px;padding:10px}
.title{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-weight:700;letter-spacing:.04em}.tag{font:11px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}
button{background:var(--panel2);border:1px solid var(--line);border-radius:7px;color:var(--text);padding:8px 10px;font:700 12px ui-sans-serif,system-ui;cursor:pointer}
button:hover{border-color:var(--cyan)}button.active{border-color:var(--amber);color:var(--amber);box-shadow:0 0 0 1px rgba(255,176,46,.18) inset}.kill{border-color:#77252b;color:#ff8585}.kill.active{background:#441014;color:#ffb1b1}.go{border-color:#1d744d;color:#7af0af}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.control{background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.055);border-radius:7px;padding:8px;min-width:0}
label{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:11px;margin-bottom:6px}.value{color:var(--text);font:11px ui-monospace,SFMono-Regular,Menlo,monospace}
input,select{width:100%;background:#0b1116;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:7px}input[type=range]{padding:0;accent-color:var(--cyan)}input[type=checkbox]{width:auto}.check{display:flex;align-items:center;justify-content:space-between;gap:8px;height:100%}
.control.pending{border-color:var(--amber)}.control.saved{border-color:var(--green)}.control.failed{border-color:var(--red)}#msg{min-height:18px;color:var(--muted);font:12px ui-monospace,SFMono-Regular,Menlo,monospace}.foot{color:var(--muted);font-size:11px;line-height:1.35}
@media(max-width:980px){main{grid-template-columns:1fr}.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){main{padding:8px}.grid{grid-template-columns:1fr}.bar button{flex:1 1 auto}}
</style></head><body><main>
<section class=video>
  <header><div><h1>WAVECAM LIVE</h1><div class=sub>camera video, PTZ state, detector tuning</div></div><div id=msg>loading config</div></header>
  <div class=feed><img id=v src="/stream.mjpg"><div class=hud id=hud></div></div>
</section>
<section class=side>
  <div class=card><div class=title>PTZ COMMANDS <span class=tag id=ownerTag>owner -</span></div>
    <div class=bar>
      <button class=kill id=killBtn onclick="sendCommand('kill')">KILL</button>
      <button class=go id=resumeBtn onclick="sendCommand('resume')">RESUME</button>
      <button id=autoBtn onclick="sendCommand('auto')">START AUTO</button>
      <button id=stopBtn onclick="sendCommand('stop')">STOP PTZ</button>
      <button onclick="sendCommand('zin')">ZOOM IN</button>
      <button onclick="sendCommand('zout')">ZOOM OUT</button>
      <button onclick="sendCommand('zstop')">ZOOM STOP</button>
    </div>
  </div>
  <div class=card><div class=title>TRACKING TRIGGER <span class=tag>hot</span></div><div class=grid>
    <div class=control data-key=color.preset><label>Color preset <span class=value id=colorPreset_v></span></label><select id=colorPreset></select></div>
    <div class=control data-key=detector.person_class><label>YOLO class <span class=value id=yoloClass_v></span></label><select id=yoloClass></select></div>
    <div class=control data-key=detector.conf><label>YOLO conf <span class=value id=detectorConf_v></span></label><input id=detectorConf type=range min=.05 max=.95 step=.05></div>
    <div class=control data-key=detector.every_n><label>YOLO every N frames <span class=value id=everyN_v></span></label><input id=everyN type=range min=1 max=30 step=1></div>
    <div class=control data-key=fusion.require_person><div class=check><label>Require YOLO person <span class=value id=requirePerson_v></span></label><input id=requirePerson type=checkbox></div></div>
    <div class=control data-key=fusion.person_aim_y><label>Person aim Y <span class=value id=aimY_v></span></label><input id=aimY type=range min=.2 max=.75 step=.05></div>
    <div class=control data-key=fusion.lock_threshold><label>Lock threshold <span class=value id=lock_v></span></label><input id=lock type=range min=.05 max=.95 step=.05></div>
    <div class=control data-key=fusion.unlock_threshold><label>Unlock threshold <span class=value id=unlock_v></span></label><input id=unlock type=range min=.05 max=.95 step=.05></div>
    <div class=control data-key=fusion.match_dist><label>Color/YOLO match px <span class=value id=matchDist_v></span></label><input id=matchDist type=range min=20 max=500 step=10></div>
    <div class=control data-key=color.min_area><label>Min color area <span class=value id=minArea_v></span></label><input id=minArea type=number min=1 max=500000 step=20></div>
    <div class=control data-key=color.max_area><label>Max color area <span class=value id=maxArea_v></span></label><input id=maxArea type=number min=100 max=1000000 step=1000></div>
    <div class=control data-key=color.morph_kernel><label>Mask cleanup <span class=value id=morphKernel_v></span></label><input id=morphKernel type=range min=1 max=31 step=2></div>
  </div></div>
  <div class=card><div class=title>PTZ TUNING <span class=tag>hot</span></div><div class=grid>
    <div class=control data-key=ptz.deadzone><label>Deadband <span class=value id=deadzone_v></span></label><input id=deadzone type=range min=.02 max=.30 step=.01></div>
    <div class=control data-key=ptz.ff_gain><label>Feed-forward gain <span class=value id=ffGain_v></span></label><input id=ffGain type=range min=0 max=1 step=.05></div>
    <div class=control data-key=ptz.ff_deadzone_mult><label>FF deadband mult <span class=value id=ffDeadzone_v></span></label><input id=ffDeadzone type=range min=1 max=4 step=.1></div>
    <div class=control data-key=ptz.max_pan_speed><label>Max pan speed <span class=value id=maxPan_v></span></label><input id=maxPan type=range min=1 max=24 step=1></div>
    <div class=control data-key=ptz.max_tilt_speed><label>Max tilt speed <span class=value id=maxTilt_v></span></label><input id=maxTilt type=range min=1 max=20 step=1></div>
    <div class=control data-key=ptz.min_speed><label>Min speed <span class=value id=minSpeed_v></span></label><input id=minSpeed type=range min=1 max=8 step=1></div>
    <div class=control data-key=ptz.command_min_interval><label>Command interval <span class=value id=cmdInterval_v></span></label><input id=cmdInterval type=range min=.01 max=.5 step=.01></div>
    <div class=control data-key=ptz.invert_tilt><div class=check><label>Invert tilt <span class=value id=invertTilt_v></span></label><input id=invertTilt type=checkbox></div></div>
    <div class=control data-key=ptz.invert_pan><div class=check><label>Invert pan <span class=value id=invertPan_v></span></label><input id=invertPan type=checkbox></div></div>
    <div class=control data-key=web.show_mask><div class=check><label>Show mask <span class=value id=showMask_v></span></label><input id=showMask type=checkbox></div></div>
    <div class=control data-key=web.jpeg_quality><label>JPEG quality <span class=value id=jpegQuality_v></span></label><input id=jpegQuality type=range min=30 max=95 step=5></div>
  </div></div>
  <div class=card><div class=title>RESTART ONLY <span class=tag id=restartCount></span></div><div class=foot id=restartKeys></div></div>
</section></main>
<script>
const $=id=>document.getElementById(id);
const defs=[
 {id:'colorPreset',key:'color.preset',path:['color','preset'],type:'select'},
 {id:'yoloClass',key:'detector.person_class',path:['detector','person_class'],type:'int'},
 {id:'detectorConf',key:'detector.conf',path:['detector','conf'],type:'float',digits:2},
 {id:'everyN',key:'detector.every_n',path:['detector','every_n'],type:'int'},
 {id:'requirePerson',key:'fusion.require_person',path:['fusion','require_person'],type:'bool'},
 {id:'aimY',key:'fusion.person_aim_y',path:['fusion','person_aim_y'],type:'float',digits:2},
 {id:'lock',key:'fusion.lock_threshold',path:['fusion','lock_threshold'],type:'float',digits:2},
 {id:'unlock',key:'fusion.unlock_threshold',path:['fusion','unlock_threshold'],type:'float',digits:2},
 {id:'matchDist',key:'fusion.match_dist',path:['fusion','match_dist'],type:'float',digits:0},
 {id:'minArea',key:'color.min_area',path:['color','min_area'],type:'int'},
 {id:'maxArea',key:'color.max_area',path:['color','max_area'],type:'int'},
 {id:'morphKernel',key:'color.morph_kernel',path:['color','morph_kernel'],type:'int'},
 {id:'deadzone',key:'ptz.deadzone',path:['ptz','deadzone'],type:'float',digits:2},
 {id:'ffGain',key:'ptz.ff_gain',path:['ptz','ff_gain'],type:'float',digits:2},
 {id:'ffDeadzone',key:'ptz.ff_deadzone_mult',path:['ptz','ff_deadzone_mult'],type:'float',digits:1},
 {id:'maxPan',key:'ptz.max_pan_speed',path:['ptz','max_pan_speed'],type:'int'},
 {id:'maxTilt',key:'ptz.max_tilt_speed',path:['ptz','max_tilt_speed'],type:'int'},
 {id:'minSpeed',key:'ptz.min_speed',path:['ptz','min_speed'],type:'int'},
 {id:'cmdInterval',key:'ptz.command_min_interval',path:['ptz','command_min_interval'],type:'float',digits:2},
 {id:'invertTilt',key:'ptz.invert_tilt',path:['ptz','invert_tilt'],type:'bool'},
 {id:'invertPan',key:'ptz.invert_pan',path:['ptz','invert_pan'],type:'bool'},
 {id:'showMask',key:'web.show_mask',path:['web','show_mask'],type:'bool'},
 {id:'jpegQuality',key:'web.jpeg_quality',path:['web','jpeg_quality'],type:'int'}
];
let timers={};
function currentValue(def){
 const el=$(def.id);
 if(def.type==='bool')return !!el.checked;
 if(def.type==='int')return parseInt(el.value,10);
 if(def.type==='float')return parseFloat(el.value);
 return el.value;
}
function format(def,value){if(def.type==='bool')return value?'on':'off';if(def.type==='float')return Number(value).toFixed(def.digits??2);return value}
function setValue(def,value){
 const el=$(def.id); if(!el)return;
 if(def.type==='bool')el.checked=!!value; else el.value=value;
 const out=$(def.id+'_v'); if(out)out.textContent=format(def,value);
}
function mark(key,state){
 const el=document.querySelector(`.control[data-key="${key}"]`); if(!el)return;
 el.classList.remove('pending','saved','failed'); if(state)el.classList.add(state);
 if(state==='saved')setTimeout(()=>el.classList.remove('saved'),550);
}
async function patch(def){
 const value=currentValue(def); setValue(def,value); mark(def.key,'pending');
 try{
  const res=await fetch('/api/v1/config/hot',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({patch:{[def.key]:value}})});
  const body=await res.json();
  if(!res.ok||body.ok===false){throw new Error(body.message||body.code||'rejected')}
  mark(def.key,'saved'); $('msg').textContent=`applied ${def.key}`;
 }catch(e){mark(def.key,'failed'); $('msg').textContent=`${def.key}: ${e.message}`}
}
function bind(def){
 const el=$(def.id); if(!el)return;
 const ev=(el.type==='range')?'input':'change';
 el.addEventListener(ev,()=>{clearTimeout(timers[def.key]);timers[def.key]=setTimeout(()=>patch(def),ev==='input'?140:0)});
}
async function loadConfig(){
 const cfg=await (await fetch('/api/v1/config')).json();
 $('colorPreset').innerHTML=cfg.supported.color_presets.map(x=>`<option value="${x}">${x}</option>`).join('');
 $('yoloClass').innerHTML=cfg.supported.yolo_classes.map(x=>`<option value="${x.id}">${x.id} ${x.label}</option>`).join('');
 defs.forEach(def=>{let v=cfg.current; def.path.forEach(p=>v=v[p]); setValue(def,v); bind(def)});
 $('restartKeys').textContent=cfg.restart_required_keys.join(' | ');
 $('restartCount').textContent=`${cfg.restart_required_keys.length} keys`;
 $('msg').textContent='ready';
}
async function sendCommand(kind){
 const opts={method:'POST',headers:{'Content-Type':'application/json'}};
 const body=o=>Object.assign({},opts,{body:JSON.stringify(o)});
 let req;
 if(kind==='kill')req=fetch('/api/v1/safety/kill',body({reason:'operator_ui'}));
 if(kind==='resume')req=fetch('/api/v1/safety/resume',body({source:'operator_ui'}));
 if(kind==='auto')req=fetch('/api/v1/ptz/auto',opts);
 if(kind==='stop')req=fetch('/api/v1/ptz/stop',body({hold:true,source:'operator_ui'}));
 if(kind==='zin')req=fetch('/api/v1/ptz/zoom',body({requested_owner:'manual',takeover:true,value:.5,source:'operator_ui'}));
 if(kind==='zout')req=fetch('/api/v1/ptz/zoom',body({requested_owner:'manual',takeover:true,value:-.5,source:'operator_ui'}));
 if(kind==='zstop')req=fetch('/api/v1/ptz/zoom',body({requested_owner:'manual',takeover:true,value:0,source:'operator_ui'}));
 try{const r=await req; const j=await r.json(); $('msg').textContent=j.ok===false?(j.message||j.code):`${kind} accepted`; poll()}catch(e){$('msg').textContent=e.message}
}
function chip(txt){return `<span class=chip>${txt}</span>`}
async function poll(){
 try{
  const s=await (await fetch('/status')).json();
  $('hud').innerHTML=[
   chip(`${s.state||'UNKNOWN'} conf ${Number(s.conf||0).toFixed(2)}`),
   chip(`C${s.has_color?'Y':'-'} P${s.has_person?'Y':'-'} M${s.matched?'Y':'-'}`),
   chip(`fps ${Number(s.fps||0).toFixed(1)}`),
   chip(`owner ${s.owner||'-'}`),
   chip(`cmd ${s.cmd||'-'}`),
   chip(`camera ${s.connected?'up':'down'}`),
   s.killed?chip('KILL'):null
  ].filter(Boolean).join('');
  $('ownerTag').textContent=`owner ${s.owner||'-'}`;
  $('killBtn').classList.toggle('active',!!s.killed);
  $('autoBtn').classList.toggle('active',s.owner==='testbed');
  $('stopBtn').classList.toggle('active',s.owner==='manual');
 }catch(e){}
 setTimeout(poll,500);
}
loadConfig().catch(e=>$('msg').textContent=e.message); poll();
</script></body></html>"""


class Tune(BaseModel):
    lock_threshold: float | None = None
    unlock_threshold: float | None = None
    require_person: bool | None = None
    match_dist: float | None = None
    person_aim_x: float | None = None
    person_aim_y: float | None = None
    color_preset: str | None = None
    min_area: int | None = None
    max_area: int | None = None
    morph_kernel: int | None = None
    deadzone: float | None = None
    max_pan_speed: int | None = None
    max_tilt_speed: int | None = None
    min_speed: int | None = None
    command_min_interval: float | None = None
    ff_gain: float | None = None
    ff_deadzone_mult: float | None = None
    invert_pan: bool | None = None
    invert_tilt: bool | None = None
    detector_conf: float | None = None
    detector_imgsz: int | None = None
    detector_person_class: int | None = None
    detector_every_n: int | None = None
    detector_box_ttl_sec: float | None = None
    show_mask: bool | None = None
    jpeg_quality: int | None = None


TUNE_FIELDS = {
    "lock_threshold": "fusion.lock_threshold",
    "unlock_threshold": "fusion.unlock_threshold",
    "require_person": "fusion.require_person",
    "match_dist": "fusion.match_dist",
    "person_aim_x": "fusion.person_aim_x",
    "person_aim_y": "fusion.person_aim_y",
    "color_preset": "color.preset",
    "min_area": "color.min_area",
    "max_area": "color.max_area",
    "morph_kernel": "color.morph_kernel",
    "deadzone": "ptz.deadzone",
    "max_pan_speed": "ptz.max_pan_speed",
    "max_tilt_speed": "ptz.max_tilt_speed",
    "min_speed": "ptz.min_speed",
    "command_min_interval": "ptz.command_min_interval",
    "ff_gain": "ptz.ff_gain",
    "ff_deadzone_mult": "ptz.ff_deadzone_mult",
    "invert_pan": "ptz.invert_pan",
    "invert_tilt": "ptz.invert_tilt",
    "detector_conf": "detector.conf",
    "detector_imgsz": "detector.imgsz",
    "detector_person_class": "detector.person_class",
    "detector_every_n": "detector.every_n",
    "detector_box_ttl_sec": "detector.box_ttl_sec",
    "show_mask": "web.show_mask",
    "jpeg_quality": "web.jpeg_quality",
}


def tune_patch(t: Tune) -> dict:
    return {key: value for attr, key in TUNE_FIELDS.items() if (value := getattr(t, attr)) is not None}


def build_app(pipeline) -> FastAPI:
    app = FastAPI(title="WAVECAM testbed")
    app.state.pipeline = pipeline

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

    @app.post("/kill", dependencies=[Depends(require(SAFETY))])
    def kill():
        pipeline.kill(True)
        return {"killed": True}

    @app.post("/resume", dependencies=[Depends(require(SAFETY))])
    def resume():
        app.state.control_api.resume_without_autostart()
        app.state.control_api.bump_revision()
        return {"killed": False}

    @app.post("/ptz/stop", dependencies=[Depends(require(PTZ))])
    def ptz_stop():
        # STOP: halt pan/tilt AND zoom, and release the current owner (pause
        # autonomous) WITHOUT clearing the kill latch.
        pipeline.ptz.stop()
        pipeline.ptz.zoom("stop")
        pipeline.owner.release(pipeline.owner.owner)
        return {"ok": True, "owner": pipeline.owner.owner}

    @app.post("/ptz/zin", dependencies=[Depends(require(PTZ))])
    def ptz_zin():
        return legacy_zoom(0.5)

    @app.post("/ptz/zout", dependencies=[Depends(require(PTZ))])
    def ptz_zout():
        return legacy_zoom(-0.5)

    @app.post("/ptz/zstop", dependencies=[Depends(require(PTZ))])
    def ptz_zstop():
        return legacy_zoom(0.0)

    def legacy_zoom(value: float):
        api = app.state.control_api
        if pipeline.owner.killed:
            return JSONResponse({"ok": False, "blocked": "killed"}, status_code=409)
        if pipeline.owner.owner in AUTONOMOUS:
            api.send_manual_zoom_velocity(value)
            if value == 0:
                api.cancel_zoom_deadman()
            else:
                api.schedule_zoom_deadman(800)
            api.bump_revision()
            return {"ok": True, "owner": pipeline.owner.owner}
        if not api.claim_manual(takeover=False):
            return JSONResponse({"ok": False, "blocked": "owner_busy"}, status_code=409)
        api.send_manual_zoom_velocity(value)
        if value == 0:
            if not api.manual_pan_tilt_active:
                api.cancel_manual_deadman()
                api.release_manual_owner()
        else:
            api.schedule_manual_deadman(800)
        api.bump_revision()
        return {"ok": True, "owner": pipeline.owner.owner}

    @app.post("/tune", dependencies=[Depends(require(CONFIG))])
    def tune(t: Tune):
        patch = tune_patch(t)
        if not patch:
            return {"ok": True}
        refusal = app.state.control_api.apply_hot_config(patch)
        if refusal is not None:
            return refusal
        app.state.control_api.bump_revision()
        return {"ok": True, "patch": patch}

    register_control_api(app, pipeline, _frames)

    return app
