"""Layer 3 — Signal detection (no LLM). Fuses chat, emotes, loudness, laughter and scene cuts into a peak curve.

Research basis: chat message rate + emote bursts predict stream highlights; fusing chat+audio(+video)
beats any single source (F1 0.722 in multimodal work). Chat lags the moment, so we shift it back.
Weights live in config.yaml and are re-tuned by the Analyst agent from real view data.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

HYPE_EMOTES = {"kekw", "omegalul", "lul", "lulw", "pog", "pogchamp", "poggers", "pogu", "monkas", "pepelaugh",
               "icant", "aware", "sadge", "wtf", "lmao", "lol", "💀", "😂", "🤣", "😭", "w", "l", "clip it", "clipit"}


def duration(video: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def loudness_curve(video: str, n: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    per = 8000
    a = a[: (len(a) // per) * per].reshape(-1, per) if len(a) >= per else np.zeros((1, per))
    rms = np.sqrt((a ** 2).mean(axis=1) + 1e-9)
    db = 20 * np.log10(rms + 1e-9)
    return _fit(db, n)


def chat_curves(chat_path: Optional[str], n: int, lag: float) -> tuple[np.ndarray, np.ndarray]:
    vel, emo = np.zeros(n), np.zeros(n)
    if not chat_path or not Path(chat_path).exists():
        return vel, emo
    for line in open(chat_path):
        m = json.loads(line)
        i = int(m["t"] - lag)
        if 0 <= i < n:
            vel[i] += 1
            toks = {t.lower() for t in m.get("emotes", [])} | set(re.findall(r"[\w💀😂🤣😭]+", m.get("text", "").lower()))
            emo[i] += len(toks & HYPE_EMOTES) > 0
    ratio = np.where(vel > 0, emo / np.maximum(vel, 1), 0)
    return vel, ratio * np.sqrt(vel)  # burst = share of hype msgs weighted by volume


def laughter_curve(video: str, n: int, enabled: bool) -> np.ndarray:
    """Hook for LaughterSegmentation / SenseVoice. Returns zeros if no model installed."""
    if not enabled:
        return np.zeros(n)
    try:
        from laughter_segmentation import segment  # type: ignore  # user-installed wrapper
    except ImportError:
        return np.zeros(n)
    curve = np.zeros(n)
    for s, e, p in segment(video):
        curve[int(s): int(e) + 1] = np.maximum(curve[int(s): int(e) + 1], p)
    return curve


def scene_cuts(video: str, threshold: float = 0.35) -> list[float]:
    proc = subprocess.run(["ffmpeg", "-i", video, "-vf", f"select='gt(scene,{threshold})',showinfo", "-an",
                           "-f", "null", "-"], capture_output=True, text=True)
    return [float(x) for x in re.findall(r"pts_time:([\d.]+)", proc.stderr)]


def _fit(x: np.ndarray, n: int) -> np.ndarray:
    return x[:n] if len(x) >= n else np.pad(x, (0, n - len(x)), mode="edge")


def _z(x: np.ndarray) -> np.ndarray:
    sd = x.std()
    return (x - x.mean()) / sd if sd > 1e-6 else np.zeros_like(x)


def _smooth(x: np.ndarray, k: int) -> np.ndarray:
    k = max(1, k)
    return np.convolve(x, np.ones(k) / k, mode="same")


def snap(t: float, bounds: list[float], direction: str, max_shift: float = 4.0) -> float:
    cands = [b for b in bounds if (b <= t if direction == "back" else b >= t) and abs(b - t) <= max_shift]
    if not cands:
        return t
    return max(cands) if direction == "back" else min(cands)


def detect(video: str, chat: Optional[str], segments: list[dict], cfg: dict, work_dir: Path) -> list[dict]:
    n = int(duration(video)) + 1
    w = cfg["weights"]
    vel, burst = chat_curves(chat, n, cfg.get("chat_lag_seconds", 8))
    comps = {
        "chat_velocity": _z(_smooth(vel, 5)),
        "emote_burst": _z(_smooth(burst, 5)),
        "loudness": _z(_smooth(loudness_curve(video, n), 3)),
        "laughter": _z(_smooth(laughter_curve(video, n, cfg.get("laughter_model", False)), 3)),
    }
    fused = sum(w.get(k, 0) * v for k, v in comps.items())
    fused = _smooth(fused, 3)

    # peak picking: local maxima above threshold, separated by min_gap
    thr, gap = cfg.get("z_threshold", 1.0), cfg.get("min_gap_seconds", 45)
    order = np.argsort(-fused)
    picked: list[int] = []
    for i in order:
        if fused[i] < thr or len(picked) >= cfg.get("max_peaks", 40):
            break
        if all(abs(i - p) >= gap for p in picked):
            picked.append(int(i))

    seg_starts = [s["start"] for s in segments]
    seg_ends = [s["end"] for s in segments]
    cuts = scene_cuts(video)
    pre, post = cfg.get("window_pre", 35), cfg.get("window_post", 20)
    peaks = []
    for j, i in enumerate(sorted(picked)):
        s = snap(max(0, i - pre), seg_starts + cuts, "back", 6)
        e = snap(min(n - 1, i + post), seg_ends, "forward", 6)
        peaks.append({"id": f"p{j}", "t": i, "score": round(float(fused[i]), 2),
                      "components": {k: round(float(v[i]), 2) for k, v in comps.items()},
                      "window": [round(s, 2), round(e, 2)]})
    (work_dir / "peaks.json").write_text(json.dumps(peaks, indent=1))
    return peaks
