"""Layer 7 — Publish. Multi-platform via upload-post (22 platforms, API on every tier).

Research-driven defaults:
- TikTok: unaudited API posts are SELF_ONLY -> we upload as draft (MEDIA_UPLOAD); you add the
  commercial-content toggle + publish in-app.
- Instagram: the Graph API can't apply the Paid Partnership label -> flagged as a manual step.
- Staggered schedule per platform; never post identical files across multiple accounts.
- dry_run writes a manifest instead of posting (default until you've reviewed output).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

API = "https://api.upload-post.com/api/upload"

PLATFORM_KEY = {"tiktok": "tiktok", "youtube_shorts": "youtube", "instagram_reels": "instagram",
                "facebook_reels": "facebook", "x": "x"}


def build_fields(pkg: dict, platforms: list[str], cfg: dict, when: dt.datetime | None) -> dict:
    fields: dict = {"user": cfg["upload_post_user"], "platform[]": [PLATFORM_KEY[p] for p in platforms]}
    first = pkg[platforms[0]]
    fields["title"] = first.get("title") or first["caption"][:100]
    for p in platforms:
        k = PLATFORM_KEY[p]
        body = pkg[p]["caption"] + ("\n\n" + " ".join(pkg[p].get("hashtags", [])) if pkg[p].get("hashtags") else "")
        fields[f"{k}_title"] = (pkg[p].get("title") + "\n\n" + body) if (k == "tiktok" and pkg[p].get("title")) else (pkg[p].get("title") or body)
        if k in ("youtube", "facebook"):
            fields[f"{k}_description"] = body
    if "tiktok" in platforms:
        fields["post_mode"] = "MEDIA_UPLOAD" if cfg.get("tiktok_draft_mode", True) else "DIRECT_POST"
    if when:
        fields["scheduled_date"] = when.isoformat()
        fields["timezone"] = cfg.get("timezone", "America/Toronto")
    fields.update(cfg.get("extra_fields", {}))
    return fields


def publish(clip_path: str, packages: dict, cfg: dict, out_dir: Path, clip_id: str) -> list[dict]:
    """Posts each platform at its own staggered time. Returns one receipt per platform."""
    receipts = []
    base = dt.datetime.now().astimezone() + dt.timedelta(minutes=cfg.get("first_post_delay_minutes", 30))
    for i, p in enumerate([p for p in cfg["platforms"] if p in packages]):
        when = base + dt.timedelta(minutes=i * cfg.get("stagger_minutes", 90))
        fields = build_fields(packages, [p], cfg, when)
        rec = {"clip_id": clip_id, "platform": p, "scheduled": when.isoformat(), "fields": fields}
        if cfg.get("dry_run", True):
            rec["status"] = "dry_run"
        else:
            import requests  # noqa: WPS433
            with open(clip_path, "rb") as f:
                r = requests.post(API, headers={"Authorization": f"Apikey {os.environ['UPLOAD_POST_API_KEY']}"},
                                  data={k: v for k, v in fields.items() if k != "platform[]"} | {"platform[]": fields["platform[]"]},
                                  files={"video": f}, timeout=300)
            rec["status"] = r.status_code
            rec["response"] = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        receipts.append(rec)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "publish_log.jsonl", "a") as f:
        for r in receipts:
            f.write(json.dumps(r, default=str) + "\n")
    return receipts
