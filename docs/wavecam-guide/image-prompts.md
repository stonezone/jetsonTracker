# WaveCam Operator Guide — Image Prompts for ChatGPT

Four images for the operator guide. All should feel like authentic photography, not illustrations or renders — real equipment, real ocean, real people. Dark/cinematic mood consistent with the guide's color palette (`#090d13` background, `#36D1C4` teal).

---

## Image 1: Hero — The Rig in Action
**File**: `img/hero-shot.jpg`  
**Used in**: Introduction hero banner  
**Aspect**: 16:9 (widescreen), landscape  

**Prompt:**
> A Prisual NDI PTZ camera on a heavy-duty carbon fiber tripod, positioned on a rocky beach or concrete seawall, tracking a foil surfer far offshore. The camera is in sharp focus in the foreground — black and silver housing, visible lens, professional appearance. The distant ocean background shows a foil surfer silhouetted against the late-afternoon water, maybe 150 meters out, just a small orange figure above the water on a hydrofoil board. The shot has a cinematic, slightly anamorphic feel. Golden hour or late afternoon light. Shallow depth of field. No text or UI overlays. Photorealistic DSLR style, not CGI.

---

## Image 2: Tracking Visualization — YOLO + Color Detection
**File**: `img/tracking-overlay.jpg`  
**Used in**: Introduction → Capabilities section  
**Aspect**: 16:9 or 4:3, landscape  

**Prompt:**
> A computer vision monitoring screen showing a foil surfer offshore being tracked by an AI system. The video frame shows a foil surfer in an orange rashguard above the water on a hydrofoil board. Overlaid on the video: a tight teal/cyan bounding box around the surfer labeled "PERSON 0.78" in a monospaced font, and a semi-transparent orange color mask highlighting the rashguard area. In the corner: a small HUD showing "LOCKED" in green, "FPS 31" in teal, and a small confidence bar. The overall image has a dark, cinematic operator-terminal feel. Photorealistic screen/monitor shot, not a cartoon. The UI elements look like a real OpenCV annotation overlay.

---

## Image 3: Hardware Setup — Orin + System
**File**: `img/hardware-setup.jpg`  
**Used in**: Getting Started section  
**Aspect**: 4:3 or 3:2, landscape  

**Prompt:**
> A close-up, well-lit photograph of an NVIDIA Jetson Orin Nano developer kit in a compact weatherproof enclosure or case, placed on a table next to the Prisual NDI PTZ camera. Power cables, an Ethernet cable, and a USB cable are neatly organized. The Jetson has its green circuit board visible. The background is dark, slightly blurred — maybe a garage or outdoor gear setup area. Technical, professional photography style. Clean and purposeful, not cluttered. Warm side light to show hardware details.

---

## Image 4: iOS App on Device
**File**: `img/ios-app.jpg`  
**Used in**: Operation section  
**Aspect**: 9:19.5 portrait (iPhone screen ratio) or slightly wider  

**Prompt:**
> An iPhone 15 Pro in Space Black being held against a coastal/beach background (blurred ocean in the distance). The screen shows a custom dark iOS app with a live video feed of ocean water, and a floating glass control rail at the bottom with visible buttons: a red KILL stop button, a joystick circle, a teal "AUTO TRACK" button showing LOCKED status. The UI is dark-themed with teal (#36D1C4) accent color. The status HUD at the top of the video shows "LOCKED" in green and "31 FPS". The phone is held at an angle, not flat-on. Cinematic, authentic feel — like a real product shot. No watermarks.

---

## Notes for image generation
- All images should use a consistent color temperature: slightly desaturated, cinematic, not oversaturated
- No stock-photo clichés (no smiling people looking at screens, no clipart)
- The ocean/beach setting should feel like a real surf break, not a resort
- Teal (`#36D1C4`) is a signature brand color — it should appear in UI overlays, LED indicators, or cable color if possible
