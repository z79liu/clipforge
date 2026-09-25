"""Layer 5 — Render (no LLM). Cut, speaker-aware 9:16 reframe, karaoke captions + hook/context overlays.

Originality note (research): burned captions alone are NOT "original" on YouTube/TikTok/Meta. The Transform
agent's hook + context overlays (and optional commentary) are rendered here so every clip carries added value.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

W, H = 1080, 1920


# ---------------- face tracking ----------------
class FaceFinder:
    def __init__(self, mp_model: Optional[str] = None):
        self.mp = None
        if mp_model and Path(mp_model).exists():
            try:
                from mediapipe.tasks.python import vision, BaseOptions  # type: ignore
                self.mp = vision.FaceDetector.create_from_options(
                    vision.FaceDetectorOptions(base_options=BaseOptions(model_asset_path=mp_model)))
            except Exception as e:  # pragma: no cover
                print(f"[render] mediapipe unavailable ({e}); using Haar")
        self.haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    def center_x(self, frame: np.ndarray) -> Optional[float]:
        if self.mp:
            import mediapipe as mp  # type: ignore
            res = self.mp.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
            if res.detections:
                b = max(res.detections, key=lambda d: d.bounding_box.width).bounding_box
                return b.origin_x + b.width / 2
            return None
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.haar.detectMultiScale(g, 1.2, 5, minSize=(g.shape[0] // 12,) * 2)
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        return x + w / 2


def crop_path(video: str, start: float, end: float, finder: FaceFinder, cuts: list[float],
              sample_fps: float = 4.0) -> tuple[list[float], float, str]:
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    src_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    ts, xs = [], []
    t = start
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, fr = cap.read()
        if not ok:
            break
        ts.append(t)
        xs.append(finder.center_x(fr))
        t += 1 / sample_fps
    cap.release()
    hit = sum(x is not None for x in xs) / max(1, len(xs))
    if hit < 0.4:
        return [], fps, "fit_blur"
    # fill gaps, then one-euro-ish smoothing that resets on scene cuts
    last = next((x for x in xs if x is not None), src_w / 2)
    filled = []
    for x in xs:
        last = x if x is not None else last
        filled.append(last)
    sm, prev = [], filled[0]
    for tt, x in zip(ts, filled):
        if any(abs(tt - c) < 1 / sample_fps for c in cuts):
            prev = x  # snap on cut
        alpha = 0.15 if abs(x - prev) < src_w * 0.08 else 0.5  # ignore jitter, follow real moves
        prev = prev + alpha * (x - prev)
        sm.append(prev)
    n = int((end - start) * fps)
    per_frame = np.interp(np.arange(n) / fps + start, ts, sm).tolist()
    return per_frame, fps, "track"


def reframe(video: str, start: float, end: float, out: Path, finder: FaceFinder, cuts: list[float]) -> str:
    path, fps, mode = crop_path(video, start, end, finder, cuts)
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    sw, sh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    vw = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    n = int((end - start) * fps)
    cw = min(sw, int(sh * 9 / 16))
    for i in range(n):
        ok, fr = cap.read()
        if not ok:
            break
        if mode == "track":
            cx = path[min(i, len(path) - 1)]
            x0 = int(np.clip(cx - cw / 2, 0, sw - cw))
            frame = cv2.resize(fr[:, x0:x0 + cw], (W, H), interpolation=cv2.INTER_LINEAR)
        else:  # podcast/gameplay: full frame over blurred fill
            bg = cv2.GaussianBlur(cv2.resize(fr[:, max(0, sw // 2 - cw // 2): sw // 2 + cw // 2], (W, H)), (0, 0), 25)
            fg_h = int(W * sh / sw)
            fg = cv2.resize(fr, (W, fg_h))
            y0 = (H - fg_h) // 2
            bg[y0:y0 + fg_h] = fg
            frame = bg
        vw.write(frame)
    cap.release()
    vw.release()
    return mode


# ---------------- captions ----------------
def _ts(t: float) -> str:
    t = max(0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[dict], clip_start: float, clip_end: float, transform: dict, style: dict) -> str:
    font = style.get("font", "DejaVu Sans")
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{style.get('caption_size', 78)},&H00FFFFFF,&H0000E5FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,2,2,90,160,{style.get('caption_margin_v', 560)},1
Style: Hook,{font},{style.get('hook_size', 84)},&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4000000,1,0,0,0,100,100,0,0,3,18,0,8,90,90,{style.get('hook_margin_v', 300)},1
Style: Ctx,{font},54,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,1,0,0,0,100,100,0,0,3,12,0,8,90,90,{style.get('hook_margin_v', 300) + 40},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    hook = transform.get("hook_overlay")
    if hook:
        ev.append(f"Dialogue: 2,{_ts(0)},{_ts(2.6)},Hook,,0,0,0,,{{\\fad(80,200)}}{_esc(hook).upper()}")
    for o in transform.get("context_overlays", []):
        ev.append(f"Dialogue: 1,{_ts(o['t'])},{_ts(o['t'] + o['duration'])},Ctx,,0,0,0,,{{\\fad(120,120)}}{_esc(o['text'])}")
    # karaoke captions: 3-word chunks, active word highlighted
    ws = [w for w in words if w["end"] > clip_start and w["start"] < clip_end]
    for i in range(0, len(ws), style.get("words_per_line", 3)):
        chunk = ws[i:i + style.get("words_per_line", 3)]
        s, e = chunk[0]["start"] - clip_start, chunk[-1]["end"] - clip_start
        txt = "".join(f"{{\\k{max(1, int((w['end'] - w['start']) * 100))}}}{_esc(w['w'].strip()).upper()} " for w in chunk)
        ev.append(f"Dialogue: 0,{_ts(s)},{_ts(e + 0.05)},Cap,,0,0,0,,{txt.strip()}")
    return hdr + "\n".join(ev) + "\n"


def render_clip(video: str, segments: list[dict], start: float, end: float, transform: dict, out_path: Path,
                style: dict, finder: FaceFinder, cuts: list[float]) -> dict:
    start = start + float(transform.get("trim_dead_air_start", 0) or 0)
    words = [w for s in segments for w in s.get("words", [])]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        silent = Path(td) / "v.mp4"
        mode = reframe(video, start, end, silent, finder, [c for c in cuts if start <= c <= end])
        ass = Path(td) / "c.ass"
        ass.write_text(build_ass(words, start, end, transform, style))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
                        "-i", video, "-map", "0:v", "-map", "1:a?", "-vf", f"ass={ass}",
                        "-c:v", "libx264", "-preset", style.get("preset", "medium"), "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5",
                        "-movflags", "+faststart", "-shortest", str(out_path)], check=True)
    return {"path": str(out_path), "reframe_mode": mode, "duration": round(end - start, 2)}
