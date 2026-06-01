#!/usr/bin/env python3
"""Orin operator dashboard (MVP): health, GPS, PTZ read-back, logs.

Stdlib http.server only (zero new deps). Reuses GPSClient (live GPS) and
ViscaBackend (PTZ read-back). Serves a mobile-first page + a small JSON API on
0.0.0.0:8080. LAN/tunnel only for now — add Cloudflare Access + auth before
exposing state-changing endpoints remotely (none yet; this MVP is read-only).

  python3 dashboard/dashboard.py        # then open http://<orin>:8080
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_fusion.gps_client import GPSClient  # noqa: E402
from gps_fusion.geo_calc import haversine_distance  # noqa: E402
from camera_control.visca_backend import ViscaBackend  # noqa: E402
from calibration import CalibrationManager  # noqa: E402
from media.recorder import Recorder  # noqa: E402
from tracker_runner import TrackerRunner  # noqa: E402
from media.streamer import Streamer  # noqa: E402
from follow_runner import FollowRunner  # noqa: E402

PORT = int(os.environ.get("DASH_PORT", "8080"))
CAMERA_HOST = os.environ.get("CAMERA_HOST", "192.168.100.88")
SERVICES = ["gps-server", "cloudflared", "tracker", "recorder", "streamer", "dashboard"]

_gps = GPSClient(uri=os.environ.get("GPS_URI", "ws://localhost:8765"))
_gps.start()
_cam = None
_cam_lock = threading.Lock()


def cam():
    global _cam
    with _cam_lock:
        if _cam is None:
            c = ViscaBackend(CAMERA_HOST)
            try:
                if c.connect():
                    _cam = c
            except Exception:
                _cam = None
        return _cam


_calib = CalibrationManager(_gps, cam)
_rec = Recorder()
_tracker = TrackerRunner()
_stream = Streamer()
_follow = FollowRunner()


def svc_active(name):
    try:
        r = subprocess.run(["systemctl", "is-active", name],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def port_open(host, port, t=1.0):
    try:
        with socket.create_connection((host, port), timeout=t):
            return True
    except Exception:
        return False


def system_metrics():
    m = {}
    try:
        m["loadavg"] = open("/proc/loadavg").read().split()[:3]
    except Exception:
        pass
    try:
        mem = {}
        for line in open("/proc/meminfo"):
            parts = line.split()
            mem[parts[0].rstrip(":")] = int(parts[1])
        m["mem_used_pct"] = round(100 * (1 - mem.get("MemAvailable", 0) / mem.get("MemTotal", 1)), 1)
    except Exception:
        pass
    try:
        du = shutil.disk_usage("/data")
        m["disk_free_gb"] = round(du.free / 1e9, 1)
        m["disk_used_pct"] = round(100 * du.used / du.total, 1)
    except Exception:
        pass
    for z in ("thermal_zone0", "thermal_zone1", "thermal_zone2"):
        p = f"/sys/class/thermal/{z}/temp"
        try:
            if os.path.exists(p):
                m["temp_c"] = round(int(open(p).read()) / 1000, 1)
                break
        except Exception:
            pass
    try:
        m["uptime_s"] = int(float(open("/proc/uptime").read().split()[0]))
    except Exception:
        pass
    return m


def health():
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "services": {s: svc_active(s) for s in SERVICES},
        "system": system_metrics(),
        "camera": {
            "http80": port_open(CAMERA_HOST, 80),
            "onvif81": port_open(CAMERA_HOST, 81),
            "rtsp554": port_open(CAMERA_HOST, 554),
        },
    }


def _ip_for_subnet(prefix):
    """(iface, ip) of the local IPv4 address on the given subnet prefix."""
    try:
        r = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                           capture_output=True, text=True, timeout=3)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "inet" and parts[3].startswith(prefix):
                return parts[1], parts[3].split("/")[0]
    except Exception:
        pass
    return None, None


def _default_route():
    """(iface, gateway) of the default route — the internet uplink."""
    try:
        r = subprocess.run(["ip", "-o", "-4", "route", "show", "default"],
                           capture_output=True, text=True, timeout=3)
        for line in r.stdout.splitlines():
            parts = line.split()
            if "via" in parts and "dev" in parts:
                return parts[parts.index("dev") + 1], parts[parts.index("via") + 1]
    except Exception:
        pass
    return None, None


def network_status():
    """Three links the operator cares about: the wired camera LAN, the internet
    uplink (WiFi/tether), and the Cloudflare tunnel carrying watch GPS."""
    cam_if, cam_ip = _ip_for_subnet("192.168.100.")
    up_if, up_gw = _default_route()
    return {
        "camera_lan": {
            "iface": cam_if, "ip": cam_ip, "camera_host": CAMERA_HOST,
            "reachable": port_open(CAMERA_HOST, 554),   # RTSP port = liveness
        },
        "uplink": {
            "iface": up_if, "gateway": up_gw,
            "internet": port_open("1.1.1.1", 443, 1.5),
        },
        "cloudflare": {
            "service": svc_active("cloudflared"),
            "gps_ws_local": port_open("127.0.0.1", 8765),
            "endpoint": "wss://ws.stonezone.net",
        },
    }


def session_state():
    """Single overall state for the operator banner, by priority. Lightweight
    (2 systemctl + 1 socket) so it can poll often without the full health() cost.
    fault = a critical service down or the camera unreachable."""
    reasons = []
    for crit in ("gps-server", "dashboard"):
        s = svc_active(crit)
        if s != "active":
            reasons.append(f"{crit} {s}")
    cam_ok = port_open(CAMERA_HOST, 554)
    if not cam_ok:
        reasons.append("camera unreachable")
    try:
        recording = bool(_rec.status().get("recording"))
    except Exception:
        recording = False
    streaming = _stream.is_running()
    tracking = _tracker.is_running()
    following = _follow.is_running()
    cs = _calib.state()
    calibrating = bool((cs["base"]["set"] or cs["heading_points"] > 0) and not cs["saved"])
    if reasons:
        state = "fault"
    elif following:
        state = "following"
    elif tracking:
        state = "tracking"
    elif recording:
        state = "recording"
    elif streaming:
        state = "streaming"
    elif calibrating:
        state = "calibrating"
    else:
        state = "idle"
    return {
        "state": state,
        "fault_reasons": reasons,
        "components": {
            "following": following, "tracking": tracking, "recording": recording,
            "streaming": streaming, "calibrating": calibrating, "camera_ok": cam_ok,
        },
    }


def autonomous_ptz_owner():
    """Return the active autonomous camera writer, if any.

    Manual PTZ controls are useful for setup, but they should not compete with
    the tracking loops. Recording and streaming do not own PTZ.
    """
    if _follow.is_running():
        return "follow"
    if _tracker.is_running():
        return "tracker"
    return None


def _reject_autonomous_owner(action):
    owner = autonomous_ptz_owner()
    if owner is None:
        return None
    return {
        "ok": False,
        "error": f"ptz is owned by {owner}; stop {owner} before {action}",
        "owner": owner,
    }


def _fix(p, updated, now):
    if p is None:
        return None
    return {
        "lat": round(p.lat, 6), "lon": round(p.lon, 6),
        "speed": getattr(p, "speed", None), "course": getattr(p, "course", None),
        "acc": getattr(p, "accuracy", None),
        "age_s": round(now - updated, 1) if updated else None,
    }


def gps_status():
    st = _gps.get_state()
    now = time.time()
    out = {
        "connected": st.connected,
        "watch": _fix(st.target, st.target_updated, now),
        "base": _fix(st.gimbal, st.gimbal_updated, now),
        "fixes": st.fixes_received,
        "dropped": st.dropped,
        "last_error": st.last_error,
    }
    if st.target and st.gimbal:
        out["distance_m"] = round(haversine_distance(st.gimbal, st.target), 1)
    return out


def ptz_status():
    c = cam()
    if c is None:
        return {"available": False}
    try:
        p = c.get_position()
        if p is None:
            return {"available": False}
        return {"available": True, "pan": p.pan, "tilt": p.tilt, "zoom": p.zoom}
    except Exception as e:
        return {"available": False, "error": str(e)}


# --- PTZ manual control (operator override), with a deadman auto-stop ---
_last_cmd_t = 0.0
_last_vel = [0.0, 0.0, 0.0]   # pan, tilt, zoom
_last_vel_t = 0.0


def _rate_ok():
    global _last_cmd_t
    now = time.time()
    if now - _last_cmd_t < 0.04:
        return False
    _last_cmd_t = now
    return True


def _deadman():
    """Stop the camera if a velocity move was commanded but no fresh command
    arrived (e.g. the operator's connection dropped mid-hold)."""
    while True:
        time.sleep(0.3)
        if (_last_vel[0] or _last_vel[1] or _last_vel[2]) and time.time() - _last_vel_t > 0.8:
            c = cam()
            if c:
                try:
                    c.stop()
                except Exception:
                    pass
            _last_vel[0] = _last_vel[1] = _last_vel[2] = 0.0


threading.Thread(target=_deadman, daemon=True).start()


def ptz_velocity(pan, tilt):
    global _last_vel_t
    owned = _reject_autonomous_owner("manual pan/tilt")
    if owned:
        return owned
    c = cam()
    if c is None:
        return {"ok": False, "error": "no camera"}
    pan = max(-1.0, min(1.0, float(pan)))
    tilt = max(-1.0, min(1.0, float(tilt)))
    _last_vel[0], _last_vel[1], _last_vel_t = pan, tilt, time.time()
    if not _rate_ok():
        return {"ok": True, "throttled": True}
    c.pan_tilt_velocity(pan, tilt)
    return {"ok": True}


def ptz_zoom(vel):
    global _last_vel_t
    owned = _reject_autonomous_owner("manual zoom")
    if owned:
        return owned
    c = cam()
    if c is None:
        return {"ok": False}
    vel = max(-1.0, min(1.0, float(vel)))
    _last_vel[2], _last_vel_t = vel, time.time()
    c.zoom_velocity(vel)
    return {"ok": True}


def ptz_stop():
    c = cam()
    if c is None:
        return {"ok": False}
    _last_vel[0] = _last_vel[1] = _last_vel[2] = 0.0
    c.stop()
    owner = autonomous_ptz_owner()
    return {
        "ok": True,
        "warning": (
            f"{owner} is still running and may resume camera commands; stop it for manual control"
            if owner else ""
        ),
    }


def ptz_home():
    owned = _reject_autonomous_owner("home")
    if owned:
        return owned
    c = cam()
    if c is None:
        return {"ok": False}
    _last_vel[0] = _last_vel[1] = _last_vel[2] = 0.0
    c.home()
    return {"ok": True}


def ptz_nudge(dpan, dtilt):
    owned = _reject_autonomous_owner("nudge")
    if owned:
        return owned
    c = cam()
    if c is None:
        return {"ok": False}
    p = c.get_position()
    if p is None:
        return {"ok": False}
    c.move_absolute(p.pan + float(dpan), p.tilt + float(dtilt), 0.5, 0.5)
    return {"ok": True}


def preflight():
    h = health()
    g = gps_status()
    return {
        "gps_server": h["services"].get("gps-server") == "active",
        "cloudflared": h["services"].get("cloudflared") == "active",
        "camera": bool(h["camera"]["http80"] and h["camera"]["rtsp554"]),
        "ptz_readback": ptz_status().get("available", False),
        "watch_fresh": bool(g.get("watch") and (g["watch"].get("age_s") or 999) < 12),
        "base_fresh": bool(g.get("base") and (g["base"].get("age_s") or 999) < 15),
        "disk_free_gb": h["system"].get("disk_free_gb"),
    }


def logs(service, n=60):
    if service not in SERVICES:
        return {"error": "unknown service"}
    try:
        r = subprocess.run(["journalctl", "-u", service, "-n", str(n), "--no-pager", "-o", "short"],
                           capture_output=True, text=True, timeout=6)
        return {"service": service, "lines": r.stdout.splitlines()[-n:]}
    except Exception as e:
        return {"error": str(e)}


# --- live preview from RTSP /2 (lazy; released when no viewers, to protect tracking CPU) ---
RTSP_SUB = os.environ.get("RTSP_SUB", f"rtsp://{CAMERA_HOST}:554/2")
_pv_src = None
_pv_lock = threading.Lock()
_pv_last_req = 0.0


def preview_src():
    global _pv_src, _pv_last_req
    _pv_last_req = time.time()
    with _pv_lock:
        if _pv_src is None:
            try:
                from vision.frame_source import RtspFrameSource
                _pv_src = RtspFrameSource(RTSP_SUB)
            except Exception:
                _pv_src = None
        return _pv_src


def _pv_reaper():
    global _pv_src
    while True:
        time.sleep(5)
        with _pv_lock:
            if _pv_src is not None and time.time() - _pv_last_req > 15:
                try:
                    _pv_src.release()
                except Exception:
                    pass
                _pv_src = None


threading.Thread(target=_pv_reaper, daemon=True).start()


def jpeg_frame(quality=70):
    src = preview_src()
    if src is None:
        return None
    ok, frame = src.read()
    if frame is None:
        return None
    try:
        import cv2
        ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ret else None
    except Exception:
        return None


HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>JetsonTracker</title><style>
*{box-sizing:border-box}body{margin:0;font:14px/1.4 -apple-system,system-ui,sans-serif;background:#0d1117;color:#e6edf3}
h1{font-size:18px;margin:10px 12px}.row{display:flex;flex-wrap:wrap;gap:10px;padding:0 10px 10px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;flex:1 1 320px;min-width:280px}
.card h2{font-size:13px;margin:0 0 8px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}
.kv{display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #21262d}
.kv b{font-weight:600}.ok{color:#3fb950}.bad{color:#f85149}.warn{color:#d29922}.dim{color:#8b949e}
pre{background:#010409;border:1px solid #30363d;border-radius:8px;padding:8px;max-height:300px;overflow:auto;font-size:11px;white-space:pre-wrap}
select{background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:4px}
.big{font-size:20px;font-weight:700}
.b{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:11px 15px;margin:2px;font-size:17px;cursor:pointer;user-select:none;-webkit-user-select:none;touch-action:none}.b:active{background:#388bfd}
.b2{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:5px 9px;margin:2px;cursor:pointer}.b2:active{background:#388bfd}
#calbox input{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:5px;padding:4px;width:90px;margin:2px}#calout{font-size:11px}
</style></head><body>
<h1>🎥 JetsonTracker <span id=clock class=dim></span></h1>
<div id=sess style="margin:0 10px 10px;padding:10px 14px;border-radius:10px;font-weight:700;font-size:16px;border:1px solid #30363d;color:#8b949e">&mdash;</div>
<div class=row><div class=card style="flex:1 1 100%;text-align:center">
  <h2>Live preview /2 <label style="float:right;font-weight:400;text-transform:none"><input type=checkbox id=pvon checked> on</label></h2>
  <img id=pv style="max-width:100%;max-height:50vh;border-radius:8px;background:#000;min-height:120px" alt="(preview)"></div></div>
<div class=row><div class=card style="flex:1 1 100%"><h2>Tracking <span id=trkmode class=dim></span></h2>
  <button class=b2 onclick="trk('start')">&#9654; start tracking</button>
  <button class=b2 onclick="trk('stop')">&#9632; stop</button>
  <pre id=trkout style="max-height:120px">&mdash;</pre></div></div>
<div class=row><div class=card style="flex:1 1 100%"><h2>Vision Follow &mdash; yard, no GPS <span id=folmode class=dim></span></h2>
  <button class=b2 onclick="fol('start')">&#9654; start follow</button>
  <button class=b2 onclick="fol('stop')">&#9632; stop</button>
  <span id=follast class=dim style="margin-left:8px">&mdash;</span>
  <pre id=folout style="max-height:120px">&mdash;</pre></div></div>
<div class=row>
  <div class=card><h2>Health</h2><div id=services></div><div id=system></div><div id=camera></div></div>
  <div class=card><h2>GPS</h2><div id=gps></div></div>
  <div class=card><h2>Network</h2><div id=net></div></div>
  <div class=card><h2>Camera PTZ</h2><div id=ptz></div>
    <div style="text-align:center;margin-top:8px">
      <div class=dim>speed <input id=spd type=range min=0.1 max=1 step=0.1 value=0.5 style="vertical-align:middle"></div>
      <div style="margin:6px 0">
        <button class=b data-p=0 data-t=1>&uarr;</button><br>
        <button class=b data-p=-1 data-t=0>&larr;</button>
        <button class=b onclick="ptzAction('/api/ptz/home',{})">&#8962;</button>
        <button class=b data-p=1 data-t=0>&rarr;</button><br>
        <button class=b data-p=0 data-t=-1>&darr;</button>
      </div>
      <div>zoom <button class=b data-z=1>&#65291;</button>
        <button class=b onclick="ptzAction('/api/ptz/stop',{})">&#9632;</button>
        <button class=b data-z=-1>&#65293;</button></div>
    </div></div>
</div>
<div class=row><div class=card style="flex:1 1 100%" id=calbox><h2>Calibration wizard</h2>
  <div id=calst class=dim style="margin-bottom:6px">&mdash;</div>
  <div><b>1. Base</b> <input id=blat placeholder=lat><input id=blon placeholder=lon>
    <button class=b2 onclick="cal('/api/calibration/base/manual',{lat:+blat.value,lon:+blon.value})">set manual</button>
    <button class=b2 onclick="cal('/api/calibration/base/lock',{})">lock from iPhone</button></div>
  <div style="margin-top:6px"><b>2. Heading</b> (aim camera at a known point, enter its lat/lon, Add &times;2)
    <input id=hlat placeholder=lat><input id=hlon placeholder=lon>
    <button class=b2 onclick="cal('/api/calibration/heading/point',{lat:+hlat.value,lon:+hlon.value})">add pt</button>
    <button class=b2 onclick="cal('/api/calibration/heading/commit',{})">commit</button>
    <button class=b2 onclick="cal('/api/calibration/heading/reset',{})">reset</button></div>
  <div style="margin-top:6px"><b>3.</b> <button class=b2 onclick="cal('/api/calibration/save',{})">save camera_pose.json</button></div>
  <pre id=calout>&mdash;</pre></div></div>
<div class=row><div class=card style="flex:1 1 100%"><h2>Recording / media</h2>
  <div id=medst class=dim style="margin-bottom:6px">&mdash;</div>
  <button class=b2 onclick="medRec(true)">&#9679; record</button>
  <button class=b2 onclick="medRec(false)">&#9632; stop record</button>
  <div id=strmst class=dim style="margin:6px 0">&mdash;</div>
  <button class=b2 onclick="strm(true)">&#128225; stream</button>
  <button class=b2 onclick="strm(false)">&#9632; stop stream</button></div></div>
<div class=row><div class=card style="flex:1 1 100%"><h2>Logs
  <select id=svc onchange=loadLogs()>
   <option>gps-server</option><option>cloudflared</option><option>tracker</option>
   <option>recorder</option><option>streamer</option><option>dashboard</option></select></h2>
  <pre id=logs>...</pre></div></div>
<script>
const $=id=>document.getElementById(id);
function kv(k,v,cls){return `<div class=kv><span class=dim>${k}</span><b class="${cls||''}">${v}</b></div>`}
function sCls(s){return s=='active'?'ok':(s=='unknown'||s=='inactive'?'dim':'bad')}
async function tick(){
 try{
  const r=await fetch('/api/status'); const d=await r.json();
  $('clock').textContent=d.health.time;
  $('services').innerHTML=Object.entries(d.health.services).map(([k,v])=>kv(k,v,sCls(v))).join('');
  const sy=d.health.system||{};
  $('system').innerHTML=kv('cpu load',(sy.loadavg||['?'])[0])+kv('mem used',(sy.mem_used_pct??'?')+'%')
    +kv('disk free',(sy.disk_free_gb??'?')+' GB')+kv('temp',(sy.temp_c??'?')+'°C')
    +kv('uptime',Math.floor((sy.uptime_s||0)/3600)+'h');
  const c=d.health.camera;
  $('camera').innerHTML=kv('cam http',c.http80?'up':'down',c.http80?'ok':'bad')
    +kv('cam onvif',c.onvif81?'up':'down',c.onvif81?'ok':'bad')+kv('cam rtsp',c.rtsp554?'up':'down',c.rtsp554?'ok':'bad');
  const g=d.gps;
  const w=g.watch, b=g.base;
  let h=kv('link',g.connected?'connected':'down',g.connected?'ok':'bad');
  h+=w?kv('watch',`${w.lat}, ${w.lon}`)+kv('watch age',w.age_s+'s',w.age_s<5?'ok':'warn')
       +kv('watch acc',w.acc+'m')+kv('watch spd',(w.speed??0).toFixed?w.speed.toFixed(1):w.speed+' m/s')
     :kv('watch','no fix','bad');
  h+=b?kv('base',`${b.lat}, ${b.lon}`)+kv('base age',b.age_s+'s',b.age_s<10?'ok':'warn')
     :kv('base','no fix (stale-dropped)','warn');
  if(g.distance_m!=null)h+=kv('distance',g.distance_m+' m','big');
  h+=kv('fixes',`w:${g.fixes.watchOS||0} i:${g.fixes.iOS||0}`)+kv('dropped',g.dropped);
  $('gps').innerHTML=h;
  const p=d.ptz;
  $('ptz').innerHTML=p.available?kv('pan',Math.round(p.pan))+kv('tilt',Math.round(p.tilt))+kv('zoom',Math.round(p.zoom))
    :kv('camera',p.error||'unavailable','bad');
 }catch(e){$('clock').textContent='(backend unreachable)'}
}
async function loadLogs(){
 try{const r=await fetch('/api/logs?service='+$('svc').value+'&n=80');const d=await r.json();
   $('logs').textContent=(d.lines||[d.error]).join('\\n')}catch(e){$('logs').textContent='(err)'}
}
function postJSON(url,body){
 return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})})
  .then(async r=>{let d={};try{d=await r.json()}catch(e){};d.http_status=r.status;return d})
}
function showErr(id,d){
 if(d&&d.error){$(id).innerHTML='<b class=bad>'+d.error+'</b>'+(d.warning?'<br><span class=warn>'+d.warning+'</span>':'')}
 else if(d&&d.warning){$(id).innerHTML='<span class=warn>'+d.warning+'</span>'}
}
function ptzAction(url,body){postJSON(url,body||{}).then(d=>showErr('ptz',d)).catch(e=>{})}
function spd(){return parseFloat($('spd').value)}
function bindHold(btn){
 const p=+btn.dataset.p||0,t=+btn.dataset.t||0,z=+btn.dataset.z||0;
 if(!p&&!t&&!z)return;
 const start=e=>{e.preventDefault();if(z)ptzAction('/api/ptz/zoom',{vel:z*spd()});else ptzAction('/api/ptz/velocity',{pan:p*spd(),tilt:t*spd()})};
 const stop=e=>{if(e)e.preventDefault();if(z)ptzAction('/api/ptz/zoom',{vel:0});else ptzAction('/api/ptz/stop',{})};
 btn.addEventListener('mousedown',start);btn.addEventListener('touchstart',start,{passive:false});
 btn.addEventListener('mouseup',stop);btn.addEventListener('mouseleave',stop);btn.addEventListener('touchend',stop);
}
document.querySelectorAll('.b').forEach(bindHold);
function pvTick(){if($('pvon')&&$('pvon').checked){const i=new Image();i.onload=()=>{$('pv').src=i.src};i.src='/api/preview.jpg?'+Date.now()}}
setInterval(pvTick,250);
function cal(url,body){postJSON(url,body).then(d=>{$('calout').textContent=JSON.stringify(d)}).catch(e=>{$('calout').textContent='err'})}
function calState(){fetch('/api/calibration/state').then(r=>r.json()).then(d=>{
 var dr=d.drift||{},drtxt='';
 if(dr.locked&&dr.live){drtxt=dr.warn?' | <b class=bad>BASE DRIFT '+dr.drift_m+'m</b>':' | base drift '+dr.drift_m+'m';}
 else if(dr.locked&&dr.live===false){drtxt=' | <span class=dim>base drift: no live fix</span>';}
 $('calst').innerHTML='base '+(d.base.set?d.base.lat.toFixed(5)+', '+d.base.lon.toFixed(5):'UNSET')+' | pan '+(d.pan_calibrated?'CAL '+d.pan_enc_per_deg+' enc/deg':'uncal')+' | heading pts '+d.heading_points+' | saved '+d.saved+drtxt}).catch(e=>{})}
setInterval(calState,3000);calState();
function medState(){fetch('/api/media/status').then(r=>r.json()).then(d=>{$('medst').innerHTML='<b class="'+(d.recording?'ok':'dim')+'">'+(d.recording?'RECORDING':'idle')+'</b> | segments '+d.segments+' | '+d.total_mb+' MB | disk '+d.disk_free_gb+' GB free';}).catch(e=>{})}
function medRec(start){fetch('/api/media/record/'+(start?'start':'stop'),{method:'POST'}).then(()=>setTimeout(medState,600))}
setInterval(medState,4000);medState();
function strm(start){fetch('/api/media/stream/'+(start?'start':'stop'),{method:'POST'}).then(r=>r.json()).then(d=>{if(d&&d.error)$('strmst').innerHTML='<b class=warn>'+d.error+'</b>';setTimeout(strmState,600)})}
function strmState(){fetch('/api/media/stream/status').then(r=>r.json()).then(d=>{$('strmst').innerHTML='stream: <b class="'+(d.streaming?'ok':'dim')+'">'+(d.streaming?'LIVE '+d.uptime_s+'s':(d.configured?'idle':'no stream key configured'))+'</b>';}).catch(e=>{})}
setInterval(strmState,4000);strmState();
function trk(a){postJSON('/api/tracking/'+a,{}).then(d=>{if(d.error)$('trkout').textContent=d.error;setTimeout(trkState,500)}).catch(e=>{})}
function trkState(){fetch('/api/tracking/status').then(r=>r.json()).then(d=>{$('trkmode').textContent=d.running?'RUNNING':'(stopped)';$('trkmode').className=d.running?'ok':'dim';$('trkout').textContent=(d.lines||[]).join('\\n')||(d.error||'')||(d.pose_exists?'idle':'no camera_pose.json - calibrate first')}).catch(e=>{})}
setInterval(trkState,2000);trkState();
function fol(a){postJSON('/api/follow/'+a,{}).then(d=>{if(d.error)$('folout').textContent=d.error;setTimeout(folState,500)}).catch(e=>{})}
function folState(){fetch('/api/follow/status').then(r=>r.json()).then(d=>{$('folmode').textContent=d.running?'RUNNING':'(stopped)';$('folmode').className=d.running?'ok':'dim';$('follast').textContent=d.last||'';$('folout').textContent=(d.lines||[]).join('\\n')||(d.error||'')||'idle'}).catch(e=>{})}
setInterval(folState,1500);folState();
function netState(){fetch('/api/network').then(r=>r.json()).then(d=>{
 const c=d.camera_lan,u=d.uplink,f=d.cloudflare;
 $('net').innerHTML=
  kv('camera LAN',(c.iface||'?')+' '+(c.ip||'-'),c.ip?'ok':'bad')+
  kv('camera '+c.camera_host,c.reachable?'reachable':'unreachable',c.reachable?'ok':'bad')+
  kv('uplink',(u.iface||'?')+' &rarr; '+(u.gateway||'-'),u.iface?'ok':'warn')+
  kv('internet',u.internet?'up':'down',u.internet?'ok':'bad')+
  kv('cloudflared',f.service,sCls(f.service))+
  kv('gps ws :8765',f.gps_ws_local?'listening':'down',f.gps_ws_local?'ok':'bad');
 }).catch(e=>{})}
setInterval(netState,5000);netState();
function sessState(){fetch('/api/session').then(r=>r.json()).then(d=>{
 const m={idle:['#161b22','idle','#8b949e'],calibrating:['#3d2e00','CALIBRATING','#d29922'],
  following:['#08260f','FOLLOWING','#3fe08f'],
  tracking:['#08260f','TRACKING','#3fb950'],recording:['#3d0a0a','RECORDING','#f85149'],
  streaming:['#0a1f3d','STREAMING','#58a6ff'],fault:['#3d0a0a','FAULT','#f85149']};
 const s=m[d.state]||m.idle,c=d.components||{},b=[];
 if(c.following)b.push('follow');if(c.tracking)b.push('track');if(c.recording)b.push('rec');if(c.streaming)b.push('stream');
 if(c.calibrating)b.push('calibrating');if(!c.camera_ok)b.push('no-cam');
 const extra=(d.fault_reasons&&d.fault_reasons.length)?' — '+d.fault_reasons.join(', '):(b.length?' · '+b.join(' · '):'');
 const e=$('sess');e.style.background=s[0];e.style.color=s[2];e.style.borderColor=s[2];e.textContent=s[1]+extra;
}).catch(e=>{})}
setInterval(sessState,3000);sessState();
tick();loadLogs();setInterval(tick,2000);setInterval(loadLogs,5000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="application/json", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        try:
            if path in ("/", "/index.html"):
                return self._send(HTML, "text/html")
            if path == "/api/health":
                return self._send(health())
            if path == "/api/gps":
                return self._send(gps_status())
            if path == "/api/ptz":
                return self._send(ptz_status())
            if path == "/api/status":
                return self._send({"health": health(), "gps": gps_status(), "ptz": ptz_status()})
            if path == "/api/logs":
                q = parse_qs(u.query)
                return self._send(logs(q.get("service", ["gps-server"])[0], int(q.get("n", ["60"])[0])))
            if path == "/api/calibration/state":
                return self._send(_calib.state())
            if path == "/api/calibration/preflight":
                return self._send(preflight())
            if path == "/api/media/status":
                return self._send(_rec.status())
            if path == "/api/media/stream/status":
                return self._send(_stream.status())
            if path == "/api/tracking/status":
                return self._send(_tracker.status())
            if path == "/api/follow/status":
                return self._send(_follow.status())
            if path == "/api/network":
                return self._send(network_status())
            if path == "/api/session":
                return self._send(session_state())
            if path == "/api/preview.jpg":
                j = jpeg_frame()
                if j is None:
                    return self._send({"error": "no frame"}, code=503)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(j)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(j)
                return
            self._send({"error": "not found"}, code=404)
        except Exception as e:
            self._send({"error": str(e)}, code=500)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n)) if n else {}
        except Exception:
            return {}

    def do_POST(self):
        path = urlparse(self.path).path
        b = self._body()
        try:
            if path == "/api/ptz/velocity":
                return self._send(ptz_velocity(b.get("pan", 0), b.get("tilt", 0)))
            if path == "/api/ptz/zoom":
                return self._send(ptz_zoom(b.get("vel", 0)))
            if path == "/api/ptz/stop":
                return self._send(ptz_stop())
            if path == "/api/ptz/home":
                return self._send(ptz_home())
            if path == "/api/ptz/nudge":
                return self._send(ptz_nudge(b.get("dpan", 0), b.get("dtilt", 0)))
            if path == "/api/calibration/base/manual":
                return self._send(_calib.set_base_manual(b.get("lat", 0), b.get("lon", 0), b.get("alt", 0)))
            if path == "/api/calibration/base/lock":
                return self._send(_calib.base_lock(float(b.get("seconds", 8))))
            if path == "/api/calibration/heading/point":
                return self._send(_calib.heading_point(b.get("lat", 0), b.get("lon", 0)))
            if path == "/api/calibration/heading/commit":
                return self._send(_calib.heading_commit())
            if path == "/api/calibration/heading/reset":
                return self._send(_calib.reset_heading())
            if path == "/api/calibration/save":
                return self._send(_calib.save())
            if path == "/api/media/record/start":
                return self._send(_rec.start())
            if path == "/api/media/record/stop":
                return self._send(_rec.stop())
            if path == "/api/media/stream/start":
                return self._send(_stream.start())
            if path == "/api/media/stream/stop":
                return self._send(_stream.stop())
            if path == "/api/tracking/start":
                if _follow.is_running():
                    return self._send({
                        "ok": False,
                        "error": "vision follow is running; stop follow before starting GPS tracker",
                        "owner": "follow",
                    }, code=409)
                return self._send(_tracker.start(bool(b.get("mock_camera", False))))
            if path == "/api/tracking/stop":
                return self._send(_tracker.stop())
            if path == "/api/follow/start":
                if _tracker.is_running():
                    return self._send({
                        "ok": False,
                        "error": "GPS tracker is running; stop tracking before starting vision follow",
                        "owner": "tracker",
                    }, code=409)
                return self._send(_follow.start(bool(b.get("no_yolo", False)),
                                                bool(b.get("no_color", False)),
                                                b.get("target_frac")))
            if path == "/api/follow/stop":
                return self._send(_follow.stop())
            self._send({"error": "not found"}, code=404)
        except Exception as e:
            self._send({"error": str(e)}, code=500)

    def log_message(self, *a):
        pass


def main():
    print(f"[dashboard] serving http://0.0.0.0:{PORT}  (camera={CAMERA_HOST})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
