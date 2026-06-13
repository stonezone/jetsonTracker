#!/usr/bin/env python3
"""
WAVECAM vision-only testbed entrypoint.

  python run.py                 # uses ./config.yaml
  python run.py my.yaml         # custom config

Bring-up order (see README):
  1) ptz.enabled=false -> open the web UI, confirm it boxes your orange jersey + person
  2) confirm onboard AI-track is OFF
  3) ptz.enabled=true with conservative speeds -> stand in front, verify it follows
     (if it moves the wrong way, flip invert_pan/invert_tilt via the UI or config)
"""
from __future__ import annotations
import os
import signal
import sys


def shutdown_pipeline(pipe, ptz, join_timeout: float = 3.0, force_exit: bool = False) -> None:
    try:
        pipe.stop()
        join = getattr(pipe, "join", None)
        if callable(join):
            join(timeout=join_timeout)
    finally:
        try:
            ptz.stop()
        finally:
            ptz.close()

    if force_exit:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


def handle_shutdown_signal(signum, pipe, ptz, state: dict, force_exit: bool) -> None:
    if state.get("handled"):
        if force_exit:
            os._exit(128 + int(signum))
        raise SystemExit(128 + int(signum))
    state["handled"] = True
    shutdown_pipeline(pipe, ptz, force_exit=force_exit)
    raise SystemExit(0)


def install_shutdown_handlers(pipe, ptz, force_exit: bool) -> dict:
    state = {"handled": False}

    def _handler(signum, _frame):
        handle_shutdown_signal(signum, pipe, ptz, state, force_exit)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    return state


def main():
    import uvicorn

    from wavecam.camera_http import disable_onboard_ai
    from wavecam.config import load_config
    from wavecam.pipeline import Pipeline
    from wavecam.ptz_visca import NullPtz, ViscaIP
    from wavecam.recorder import Recorder, RecorderConfig, main_stream_from_detection_source
    from wavecam.web import build_app

    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(cfg_path)

    # PTZ backend
    if cfg.ptz.enabled:
        ptz = ViscaIP(cfg.ptz.ip, cfg.ptz.port, cfg.ptz.address)
        if cfg.ptz.reset_sequence:
            ptz.reset_sequence()
        ptz.stop()  # ensure stationary at startup
        print(f"[run] PTZ ENABLED -> VISCA {cfg.ptz.ip}:{cfg.ptz.port}")
    else:
        ptz = NullPtz()
        print("[run] PTZ disabled (detection-only). Set ptz.enabled=true when ready.")

    def detector_factory():
        from wavecam.detector import PersonDetector
        print(f"[run] loading YOLO model: {cfg.detector.model}")
        return PersonDetector(cfg.detector)

    pipe = Pipeline(cfg, ptz, detector_factory)
    pipe.recorder = Recorder(
        RecorderConfig(rtsp_main=main_stream_from_detection_source(cfg.camera.source))
    )

    # onboard AI off (best effort); pipe.events captures outcome for /events
    disable_onboard_ai(cfg.camera_ai, events=pipe.events)

    # LoRa GPS cue (Meshtastic): exposes the remote fix in /api/v1/status; now also
    # drives PTZ coarse-pointing via the arbiter (P1). Failsafe — never blocks vision.
    # The reader thread auto-connects and auto-reconnects; connect() starts it even if
    # the device isn't ready yet (USB enumeration race after reboot).
    if getattr(cfg.gps, "enabled", False):
        try:
            from wavecam.gps_meshtastic import MeshtasticGps
            _gps = MeshtasticGps(dev_path=cfg.gps.dev_path, remote_id=cfg.gps.remote_id or None)
            _gps.connect()  # starts reader thread (retries until device appears)
            pipe.gps = _gps
            print(f"[run] GPS: Meshtastic ingest started on {cfg.gps.dev_path}")
        except Exception as exc:  # never let GPS break the vision pipeline
            print(f"[run] GPS init skipped (non-fatal): {exc}")
    pipe.start()

    app = build_app(pipe)
    print(f"[run] open the console:  http://<this-host>:{cfg.web.port}/")
    force_exit = bool(os.environ.get("WAVECAM_FORCE_EXIT_AFTER_CLEANUP"))
    shutdown_state = install_shutdown_handlers(pipe, ptz, force_exit=force_exit)
    try:
        uvicorn.run(app, host=cfg.web.host, port=cfg.web.port, log_level="warning")
    finally:
        if not shutdown_state["handled"]:
            shutdown_pipeline(pipe, ptz, force_exit=force_exit)


if __name__ == "__main__":
    main()
