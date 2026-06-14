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
.gps-strip{display:flex;justify-content:center;flex-wrap:wrap;gap:6px;min-height:27px;margin-top:8px}.gps-strip:empty{display:none}
.joystick-wrap{display:flex;justify-content:center;padding:14px 0 2px}.joystick{--joy-x:0px;--joy-y:0px;position:relative;width:clamp(132px,21vw,190px);aspect-ratio:1;border-radius:50%;background:radial-gradient(circle at 50% 45%,#1a2730,#0e161d 72%);border:1px solid rgba(255,255,255,.22);box-shadow:0 18px 46px rgba(0,0,0,.34);touch-action:none;user-select:none}
.joystick.active{border-color:rgba(255,176,46,.78);box-shadow:0 0 0 1px rgba(255,176,46,.22) inset,0 18px 46px rgba(0,0,0,.34)}.joystick:before,.joystick:after{content:"";position:absolute;left:14%;right:14%;top:50%;height:1px;background:rgba(255,255,255,.18)}.joystick:after{left:50%;right:auto;top:14%;bottom:14%;width:1px;height:auto}.joy-ring{position:absolute;inset:13%;border:1px dashed rgba(255,255,255,.2);border-radius:50%}
.joy-label{position:absolute;color:rgba(220,230,238,.48);font:700 9px ui-sans-serif,system-ui;letter-spacing:.12em;white-space:nowrap;pointer-events:none}.joy-top{left:50%;top:5%;transform:translateX(-50%)}.joy-bottom{left:50%;bottom:5%;transform:translateX(-50%)}.joy-left{left:5%;top:50%;transform:translateY(-50%)}.joy-right{right:5%;top:50%;transform:translateY(-50%)}
.joy-nub{position:absolute;left:50%;top:50%;width:32%;aspect-ratio:1;border-radius:50%;transform:translate(-50%,-50%) translate(var(--joy-x),var(--joy-y));background:radial-gradient(circle at 50% 22%,#ff8a4d,#e2540f 78%);border:1px solid rgba(255,255,255,.18);box-shadow:0 16px 30px rgba(69,216,240,.22);pointer-events:none}.joy-nub:after{content:"";position:absolute;inset:39%;border:1px solid rgba(255,255,255,.38);border-radius:50%}
.side{display:flex;flex-direction:column;gap:10px}.bar{display:flex;gap:8px;flex-wrap:wrap}.card{background:linear-gradient(180deg,var(--panel),#0d1217);border:1px solid var(--line);border-radius:8px;padding:10px}
.title{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-weight:700;letter-spacing:.04em}.tag{font:11px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted)}
button{background:var(--panel2);border:1px solid var(--line);border-radius:7px;color:var(--text);padding:8px 10px;font:700 12px ui-sans-serif,system-ui;cursor:pointer}
button:hover{border-color:var(--cyan)}button.active{border-color:var(--amber);color:var(--amber);box-shadow:0 0 0 1px rgba(255,176,46,.18) inset}.kill{border-color:#77252b;color:#ff8585}.kill.active{background:#441014;color:#ffb1b1}.go{border-color:#1d744d;color:#7af0af}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.control{background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.055);border-radius:7px;padding:8px;min-width:0}
label{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:11px;margin-bottom:6px}.value{color:var(--text);font:11px ui-monospace,SFMono-Regular,Menlo,monospace}
input,select{width:100%;background:#0b1116;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:7px}input[type=range]{padding:0;accent-color:var(--cyan)}input[type=checkbox]{width:auto}.check{display:flex;align-items:center;justify-content:space-between;gap:8px;height:100%}
.control.pending{border-color:var(--amber)}.control.saved{border-color:var(--green)}.control.failed{border-color:var(--red)}.feature.hidden,.control.hidden{display:none}.control.dimmed{opacity:.5}.caption{color:var(--muted);font-size:11px;line-height:1.35;margin-top:6px}.full{grid-column:1/-1}#msg{min-height:18px;color:var(--muted);font:12px ui-monospace,SFMono-Regular,Menlo,monospace}.foot{color:var(--muted);font-size:11px;line-height:1.35}
@media(max-width:980px){main{grid-template-columns:1fr}.grid{grid-template-columns:1fr 1fr}}@media(max-width:560px){main{padding:8px}.grid{grid-template-columns:1fr}.bar button{flex:1 1 auto}}
</style></head><body><main>
<section class=video>
  <header><div><h1>WAVECAM LIVE</h1><div class=sub>camera video, PTZ state, detector tuning</div></div><div id=msg>loading config</div></header>
  <div class=feed><img id=v src="/stream.mjpg"><div class=hud id=hud></div></div>
  <div class=joystick-wrap>
    <div class=joystick id=ptzJoystick role=application aria-label="PTZ joystick">
      <div class=joy-ring></div>
      <span class="joy-label joy-top">TILT +</span>
      <span class="joy-label joy-bottom">TILT -</span>
      <span class="joy-label joy-left">PAN -</span>
      <span class="joy-label joy-right">PAN +</span>
      <div class=joy-nub id=ptzNub></div>
    </div>
  </div>
  <div class=gps-strip id=gpsStrip></div>
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
    <div class=control data-key=web.show_hud><div class=check><label>Show debug HUD <span class=value id=showHud_v></span></label><input id=showHud type=checkbox></div></div>
    <div class=control data-key=web.jpeg_quality><label>JPEG quality <span class=value id=jpegQuality_v></span></label><input id=jpegQuality type=range min=30 max=95 step=5></div>
  </div></div>
  <div class="card feature" id=cinematicCard><div class=title>CINEMATIC ZOOM <span class=tag>hot</span></div><div class=grid>
    <div class=control data-key=ptz.cinematic_zoom_enabled><div class=check><label>Cinematic zoom <span class=value id=cinematicEnabled_v></span></label><input id=cinematicEnabled type=checkbox></div></div>
    <div class=control id=subjectSizeControl data-key=ptz.zoom_target_frac><label>Subject size <span class=value id=subjectSize_v></span></label><input id=subjectSize type=range min=.2 max=.8 step=.05></div>
  </div></div>
  <div class="card feature" id=trackingModeCard><div class=title>TRACKING MODE <span class=tag>hot</span></div><div class=grid>
    <div class="control full" data-key=tracking.mode><label>Source <span class=value id=trackingMode_v></span></label><select id=trackingMode>
      <option value=auto>Auto (vision + GPS)</option>
      <option value=gps_only>GPS-only</option>
      <option value=vision_only>Vision-only</option>
    </select><div class=caption>GPS-only ignores vision lock and points from GPS geometry.</div></div>
  </div></div>
  <div class="card feature" id=gpsTuneCard><div class=title>GPS TRACKING <span class=tag>hot</span></div><div class=grid>
    <div class=control data-key=fusion.gps_boost><label>GPS lock boost <span class=value id=gpsBoost_v></span></label><input id=gpsBoost type=range min=0 max=.4 step=.05></div>
    <div class=control data-key=gps.stale_threshold_sec><label>GPS stale after <span class=value id=gpsStale_v></span></label><input id=gpsStale type=range min=2 max=60 step=1></div>
    <div class=control data-key=gps.grace_sec><label>Vision-loss grace <span class=value id=gpsGrace_v></span></label><input id=gpsGrace type=range min=.5 max=5 step=.5></div>
    <div class=control data-key=gps.drive_zoom><div class=check><label>GPS drives zoom <span class=value id=gpsDriveZoom_v></span></label><input id=gpsDriveZoom type=checkbox></div></div>
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
 {id:'showHud',key:'web.show_hud',path:['web','show_hud'],type:'bool'},
 {id:'jpegQuality',key:'web.jpeg_quality',path:['web','jpeg_quality'],type:'int'},
 {id:'cinematicEnabled',key:'ptz.cinematic_zoom_enabled',path:['ptz','cinematic_zoom_enabled'],type:'bool',feature:'cinematicCard'},
 {id:'subjectSize',key:'ptz.zoom_target_frac',path:['ptz','zoom_target_frac'],type:'float',digits:2,feature:'cinematicCard'},
 {id:'trackingMode',key:'tracking.mode',path:['tracking','mode'],type:'select',feature:'trackingModeCard'},
 {id:'gpsBoost',key:'fusion.gps_boost',path:['fusion','gps_boost'],type:'float',digits:2,feature:'gpsTuneCard'},
 {id:'gpsStale',key:'gps.stale_threshold_sec',path:['gps','stale_threshold_sec'],type:'int',suffix:'s',feature:'gpsTuneCard'},
 {id:'gpsGrace',key:'gps.grace_sec',path:['gps','grace_sec'],type:'float',digits:1,suffix:'s',feature:'gpsTuneCard'},
 {id:'gpsDriveZoom',key:'gps.drive_zoom',path:['gps','drive_zoom'],type:'bool',feature:'gpsTuneCard'}
];
let timers={};
function currentValue(def){
 const el=$(def.id);
 if(def.type==='bool')return !!el.checked;
 if(def.type==='int')return parseInt(el.value,10);
 if(def.type==='float')return parseFloat(el.value);
 return el.value;
}
function format(def,value){
 if(def.type==='bool')return value?'on':'off';
 const text=def.type==='float'?Number(value).toFixed(def.digits??2):value;
 return `${text}${def.suffix||''}`;
}
function setValue(def,value){
 const el=$(def.id); if(!el)return;
 if(def.type==='bool')el.checked=!!value; else el.value=value;
 const out=$(def.id+'_v'); if(out)out.textContent=format(def,value);
}
function getPath(obj,path){
 let value=obj;
 for(const part of path){if(value==null||!(part in value))return undefined; value=value[part]}
 return value;
}
function setHidden(id,hidden){const el=$(id); if(el)el.classList.toggle('hidden',!!hidden)}
function setControlVisible(def,visible){
 const el=document.querySelector(`.control[data-key="${def.key}"]`);
 if(el)el.classList.toggle('hidden',!visible);
}
function syncConditionalControls(){
 const enabled=!!$('cinematicEnabled')?.checked;
 const subject=$('subjectSizeControl');
 if(subject)subject.classList.toggle('dimmed',!enabled);
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
 el.addEventListener(ev,()=>{
  syncConditionalControls();
  clearTimeout(timers[def.key]);
  timers[def.key]=setTimeout(()=>patch(def),ev==='input'?140:0);
 });
}
async function loadConfig(){
 const cfg=await (await fetch('/api/v1/config')).json();
 const supported=cfg.supported||{};
 setHidden('cinematicCard',supported.cinematic_zoom!==true);
 setHidden('trackingModeCard',supported.tracking_mode!==true);
 const hasGps=!!cfg.current?.gps||supported.gps===true;
 setHidden('gpsTuneCard',!hasGps);
 $('colorPreset').innerHTML=(supported.color_presets||[]).map(x=>`<option value="${x}">${x}</option>`).join('');
 $('yoloClass').innerHTML=(supported.yolo_classes||[]).map(x=>`<option value="${x.id}">${x.id} ${x.label}</option>`).join('');
 defs.forEach(def=>{
  const v=getPath(cfg.current,def.path);
  const visible=v!==undefined && (!def.feature || !$(def.feature)?.classList.contains('hidden'));
  setControlVisible(def,visible);
  if(!visible)return;
  setValue(def,v);
  bind(def);
 });
 syncConditionalControls();
 const restartKeys=cfg.restart_required_keys||[];
 $('restartKeys').textContent=restartKeys.join(' | ');
 $('restartCount').textContent=`${restartKeys.length} keys`;
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
function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
async function postJson(path,payload){
 const res=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 let body={}; try{body=await res.json()}catch(e){}
 if(!res.ok||body.ok===false)throw new Error(body.message||body.code||`${path} rejected`);
 return body;
}
const joyState={active:false,pointerId:null,pan:0,tilt:0,lastSent:0,pending:null,stopToken:0};
function zeroDeadzone(value){return Math.abs(value)<0.05?0:value}
function clampVector(x,y,radius){
 const distance=Math.hypot(x,y);
 if(distance<=radius||distance===0)return {x,y};
 const scale=radius/distance; return {x:x*scale,y:y*scale};
}
function setJoystickOffset(x,y){
 const nub=$('ptzNub'); if(!nub)return;
 nub.style.setProperty('--joy-x',`${x}px`);
 nub.style.setProperty('--joy-y',`${y}px`);
}
async function postJoystickVelocity(pan,tilt){
 joyState.lastSent=performance.now(); joyState.stopToken++;
 try{
  await postJson('/api/v1/ptz/velocity',{requested_owner:'manual',takeover:true,pan,tilt,zoom:0,deadman_ms:800,source:'operator_ui'});
 }catch(e){$('msg').textContent=e.message}
}
function sendJoystickVelocity(pan,tilt){
 joyState.pan=pan; joyState.tilt=tilt;
 const elapsed=performance.now()-joyState.lastSent;
 if(elapsed>=110){postJoystickVelocity(pan,tilt);return}
 clearTimeout(joyState.pending);
 joyState.pending=setTimeout(()=>postJoystickVelocity(joyState.pan,joyState.tilt),110-elapsed);
}
function updateJoystick(ev){
 const joy=$('ptzJoystick'); if(!joy)return;
 const rect=joy.getBoundingClientRect();
 const diameter=Math.min(rect.width,rect.height);
 const center=diameter/2;
 const radius=diameter*.3565;
 const next=clampVector(ev.clientX-rect.left-center,ev.clientY-rect.top-center,radius);
 setJoystickOffset(next.x,next.y);
 sendJoystickVelocity(zeroDeadzone(next.x/radius),zeroDeadzone(-next.y/radius));
}
async function stopJoystick(){
 const token=++joyState.stopToken;
 for(let attempt=0;attempt<3;attempt++){
  if(joyState.active||token!==joyState.stopToken)return;
  try{await postJson('/api/v1/ptz/stop',{hold:false,source:'operator_ui'});poll();return}
  catch(e){if(attempt===2)$('msg').textContent=e.message}
  if(attempt<2)await delay(120);
 }
}
function resetJoystick(){
 const joy=$('ptzJoystick'); if(!joy)return;
 if(!joyState.active&&joyState.pan===0&&joyState.tilt===0)return;
 joyState.active=false; joyState.pointerId=null; joyState.pan=0; joyState.tilt=0;
 clearTimeout(joyState.pending); joyState.pending=null;
 setJoystickOffset(0,0); joy.classList.remove('active'); stopJoystick();
}
function initJoystick(){
 const joy=$('ptzJoystick'); if(!joy)return;
 joy.addEventListener('pointerdown',ev=>{
  if(ev.button!==undefined&&ev.button!==0)return;
  ev.preventDefault(); joyState.active=true; joyState.pointerId=ev.pointerId; joy.classList.add('active');
  joy.setPointerCapture?.(ev.pointerId); updateJoystick(ev);
 });
 joy.addEventListener('pointermove',ev=>{
  if(!joyState.active||ev.pointerId!==joyState.pointerId)return;
  ev.preventDefault(); updateJoystick(ev);
 });
 joy.addEventListener('pointerup',ev=>{if(ev.pointerId===joyState.pointerId)resetJoystick()});
 joy.addEventListener('pointercancel',ev=>{if(ev.pointerId===joyState.pointerId)resetJoystick()});
 joy.addEventListener('lostpointercapture',resetJoystick);
}
function escapeHtml(value){
 return String(value).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function chip(txt){return `<span class=chip>${escapeHtml(txt)}</span>`}
function ageText(value){
 if(value===null||value===undefined||Number.isNaN(Number(value)))return '-';
 const seconds=Number(value);
 if(seconds<10)return `${seconds.toFixed(1)}s`;
 return `${Math.round(seconds)}s`;
}
function normalizeStatus(raw){
 if(raw&&raw.tracking&&raw.ptz){
  const gps=raw.gps||null;
  return {
   state:raw.tracking.state||raw.session?.state||'UNKNOWN',
   conf:raw.tracking.confidence,
   has_color:raw.tracking.has_color,
   has_person:raw.tracking.has_person,
   matched:raw.tracking.matched,
   fps:raw.tracking.fps,
   owner:raw.ptz.owner,
   cmd:raw.ptz.pan_tilt_cmd,
   connected:raw.network?.camera_lan??raw.ptz.enabled,
   killed:raw.safety?.killed,
   gps
  };
 }
 return raw||{};
}
async function fetchStatus(){
 try{
  const res=await fetch('/api/v1/status');
  if(!res.ok)throw new Error(`status ${res.status}`);
  return normalizeStatus(await res.json());
 }catch(e){
  return normalizeStatus(await (await fetch('/status')).json());
 }
}
function gpsChips(gps){
 if(!gps||gps.source==null)return [];
 const chips=[chip(`gps ${gps.source}`)];
 if(gps.distance_m!==null&&gps.distance_m!==undefined)chips.push(chip(`range ${Math.round(Number(gps.distance_m))}m`));
 else chips.push(chip('gps no fix'));
 if(gps.bearing_deg!==null&&gps.bearing_deg!==undefined)chips.push(chip(`bearing ${Math.round(Number(gps.bearing_deg))}deg`));
 if(gps.target_age_sec!==null&&gps.target_age_sec!==undefined)chips.push(chip(`${gps.stale?'target stale':'target live'} ${ageText(gps.target_age_sec)}`));
 if(gps.base_age_sec!==null&&gps.base_age_sec!==undefined)chips.push(chip(`base ${ageText(gps.base_age_sec)}`));
 else chips.push(chip('base no fix'));
 if(gps.target_sats!==null&&gps.target_sats!==undefined)chips.push(chip(`sats ${gps.target_sats}`));
 if(gps.target_battery_mv!==null&&gps.target_battery_mv!==undefined)chips.push(chip(`batt ${(Number(gps.target_battery_mv)/1000).toFixed(2)}V`));
 if(gps.reader_alive===false)chips.push(chip('gps reader down'));
 return chips;
}
async function poll(){
 try{
  const s=await fetchStatus();
  const gps=gpsChips(s.gps);
  $('hud').innerHTML=[
   chip(`${s.state||'UNKNOWN'} conf ${Number(s.conf||0).toFixed(2)}`),
   chip(`C${s.has_color?'Y':'-'} P${s.has_person?'Y':'-'} M${s.matched?'Y':'-'}`),
   chip(`fps ${Number(s.fps||0).toFixed(1)}`),
   chip(`owner ${s.owner||'-'}`),
   chip(`cmd ${s.cmd||'-'}`),
   chip(`camera ${s.connected?'up':'down'}`),
   ...gps.slice(0,4),
   s.killed?chip('KILL'):null
  ].filter(Boolean).join('');
  $('gpsStrip').innerHTML=gps.join('');
  $('ownerTag').textContent=`owner ${s.owner||'-'}`;
  $('killBtn').classList.toggle('active',!!s.killed);
  $('autoBtn').classList.toggle('active',s.owner==='testbed'||s.owner==='vision_follow'||s.owner==='gps_tracker');
  $('stopBtn').classList.toggle('active',s.owner==='manual');
 }catch(e){}
 setTimeout(poll,500);
}
initJoystick(); loadConfig().catch(e=>$('msg').textContent=e.message); poll();
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
