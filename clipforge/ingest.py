"""Layer 1 — Ingest (no LLM). Downloads VOD + chat replay, or accepts local files."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def detect_source(url: str) -> str:
    if "twitch.tv" in url:
        return "twitch"
    if "kick.com" in url:
        return "kick"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    return "local" if Path(url).exists() else "other"


def download_video(url: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    if Path(url).exists():
        dst = out_dir / "source.mp4"
        if Path(url).resolve() != dst.resolve():
            shutil.copy(url, dst)
        return dst
    _run(["yt-dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]", "--merge-output-format", "mp4",
          "-o", str(out_dir / "source.%(ext)s"), url])
    return out_dir / "source.mp4"


def download_chat(url: str, out_dir: Path) -> Optional[Path]:
    """Returns path to normalized chat.jsonl: {"t": seconds, "text": str, "emotes": [str]} per line."""
    src = detect_source(url)
    raw = out_dir / "chat_raw.json"
    try:
        if src == "twitch" and shutil.which("TwitchDownloaderCLI"):
            vid = re.search(r"videos/(\d+)", url).group(1)
            _run(["TwitchDownloaderCLI", "chatdownload", "--id", vid, "-o", str(raw), "-E"])
            return _normalize_twitch(raw, out_dir / "chat.jsonl")
        if src in ("youtube", "kick", "twitch"):
            # pinned chat-downloader (or the maintained fork for Kick) — see README
            _run(["chat_downloader", url, "--output", str(raw.with_suffix(".jsonl")), "--quiet"])
            return _normalize_chat_downloader(raw.with_suffix(".jsonl"), out_dir / "chat.jsonl")
    except (subprocess.CalledProcessError, FileNotFoundError, AttributeError) as e:
        print(f"[ingest] chat unavailable ({e}); continuing with audio/transcript signals only")
    return None


def _normalize_twitch(raw: Path, dst: Path) -> Path:
    data = json.loads(raw.read_text())
    with open(dst, "w") as f:
        for c in data.get("comments", []):
            frags = c.get("message", {}).get("fragments", [])
            f.write(json.dumps({
                "t": c.get("content_offset_seconds", 0),
                "text": c.get("message", {}).get("body", ""),
                "emotes": [x["text"] for x in frags if x.get("emoticon")],
            }) + "\n")
    return dst


def _normalize_chat_downloader(raw: Path, dst: Path) -> Path:
    with open(raw) as src, open(dst, "w") as f:
        for line in src:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = m.get("time_in_seconds")
            if t is None:
                continue
            f.write(json.dumps({"t": t, "text": m.get("message", ""),
                                "emotes": [e.get("name", "") for e in m.get("emotes", []) or []]}) + "\n")
    return dst


def ingest(url: str, work_dir: Path, chat_file: Optional[str] = None) -> dict:
    video = download_video(url, work_dir)
    chat = Path(chat_file) if chat_file else download_chat(url, work_dir)
    return {"video": str(video), "chat": str(chat) if chat else None, "source": detect_source(url), "url": url}
