"""Layer 2 — Transcribe (no LLM). Word-level timestamps + optional speakers.

Priority: WhisperX (word alignment + pyannote diarization) > faster-whisper (large-v3-turbo) > existing transcript.json.
For English-heavy streams, NVIDIA Parakeet-TDT-0.6B-v3 is ~49x faster; plug it in via `engine: parakeet` if installed.
Output schema: {"segments":[{"start","end","text","speaker","words":[{"w","start","end"}]}]}
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def transcribe(video: str, work_dir: Path, cfg: dict) -> Path:
    out = work_dir / "transcript.json"
    if out.exists():
        return out
    engine = cfg.get("engine", "auto")
    segs = None
    if engine in ("auto", "whisperx"):
        segs = _whisperx(video, cfg)
    if segs is None and engine in ("auto", "faster_whisper"):
        segs = _faster_whisper(video, cfg)
    if segs is None:
        raise RuntimeError("No transcription engine installed. `pip install whisperx` or faster-whisper, "
                           "or drop a transcript.json into the work dir.")
    out.write_text(json.dumps({"segments": segs}))
    return out


def _whisperx(video: str, cfg: dict):
    try:
        import whisperx  # type: ignore
    except ImportError:
        return None
    device = cfg.get("device", "cuda")
    model = whisperx.load_model(cfg.get("model", "large-v3-turbo"), device,
                                compute_type="float16" if device == "cuda" else "int8")
    audio = whisperx.load_audio(video)
    res = model.transcribe(audio, batch_size=16)
    align_model, meta = whisperx.load_align_model(language_code=res["language"], device=device)
    res = whisperx.align(res["segments"], align_model, meta, audio, device)
    if cfg.get("diarize") and os.environ.get("HF_TOKEN"):
        from whisperx.diarize import DiarizationPipeline  # type: ignore
        dia = DiarizationPipeline(model_name="pyannote/speaker-diarization-community-1",
                                  use_auth_token=os.environ["HF_TOKEN"], device=device)
        res = whisperx.assign_word_speakers(dia(audio), res)
    return [{"start": s["start"], "end": s["end"], "text": s["text"].strip(), "speaker": s.get("speaker"),
             "words": [{"w": w["word"], "start": w.get("start", s["start"]), "end": w.get("end", s["end"])}
                       for w in s.get("words", [])]} for s in res["segments"]]


def _faster_whisper(video: str, cfg: dict):
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return None
    device = cfg.get("device", "cuda")
    model = WhisperModel(cfg.get("model", "large-v3-turbo"), device=device,
                         compute_type="float16" if device == "cuda" else "int8")
    segments, _ = model.transcribe(video, word_timestamps=True, vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text.strip(), "speaker": None,
             "words": [{"w": w.word, "start": w.start, "end": w.end} for w in (s.words or [])]}
            for s in segments]


def load(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text())["segments"]


def window_text(segments: list[dict], start: float, end: float) -> str:
    return " ".join(f"[{s['start']:.1f}]{' ' + s['speaker'] + ':' if s.get('speaker') else ''} {s['text']}"
                    for s in segments if s["end"] > start and s["start"] < end)
