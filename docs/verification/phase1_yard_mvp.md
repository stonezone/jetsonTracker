# Phase 1 — Yard Test MVP (vision-only, no GPS): build verification

Built + deployed to `/data/projects/gimbal`. Vision-only PTZ follow — orange
rashguard (color) as the primary cue, YOLO confirming it's a person — controllable
from the WaveCam web UI on `:8088`. No GPS (LoRa comes later).

## What was built

| Component | File | Verified |
|---|---|---|
| HSV color detector (orange + red hue-wraparound + blob filter) | `vision/color_detector.py` | `scripts/test_color_detector.py` **14/14** offline (local + Orin) |
| Vision-follow service (YOLO + color → PTZ center + zoom, fused) | `vision/vision_follow.py` | `scripts/test_vision_follow_logic.py` **9/9** offline; live Orin smokes |
| WaveCam web UI / API controls (start/stop + readout + session state) | `wavecam/web.py`, `wavecam/control_api.py` | endpoint integration test (below) |

## Target fusion (the design we agreed)

Priority: **color-confirmed person** (orange inside a YOLO person box) > largest YOLO
person > largest color blob > none. Orange is the constant cue (present in tow-boogie
mode all the time); YOLO validates. Temporal **continuity** (prefer the candidate
nearest last frame's target) prevents flipping between objects.

## Live verification on Orin (camera, garage scene)

- Vision-follow smoke: connects camera, fuses YOLO+color, drives pan/tilt + zoom,
  restores camera home on stop. Ran end-to-end.
- **Fix found + applied from the smoke:** zoom was oscillating because it was driven by
  person-box height on YOLO frames and orange-patch height on color-only frames. Now
  zoom is driven **only off a person box**; on color-only frames it holds. Verified the
  oscillation is gone (`zoom=+0.00` held on color-only frames).
- Legacy dashboard endpoint integration: `start` → `running=True`, readout
  `src=both off=(-0.00,-0.01) size=0.98 ...` (centered a person+orange target), session
  banner = **`following`** → `stop` → camera restored, `running=False`.

## Ready for the live yard test (Zack)

Open `http://192.168.1.155:8088` on your phone → WaveCam web controls →
**START AUTO**. Wear the orange rashguard, walk/jog around the yard. The camera should
center + frame you; watch the readout (`src`, `off`, `size`). **stop** returns it home.

Tunables if needed (via `vision_follow.py` args, later exposed as sliders): `--target-frac`
(how big you sit in frame), `--kp-pan/--kp-tilt` (responsiveness), `--no-yolo` (color-only).

## Known follow-ups (not blocking the yard test)

- Multi-object scenes can still flip targets across cue tiers on YOLO dropouts; a
  persistent track ID (ByteTrack/BoT-SORT, per spec) is the durable fix.
- Retired legacy `:8080` dashboard references may still exist in archived specs; live control is `:8088`.
