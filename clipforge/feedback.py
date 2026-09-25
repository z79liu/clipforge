"""Layer 8 — Feedback loop. Joins real performance back onto clips, lets the Analyst agent re-tune.

Input: data/performance.csv with columns clip_id,platform,views,avg_view_pct,payout_usd
(export from campaign dashboards / upload-post status; fill manually at first).
Output: updated signal weights in config.yaml (bounded changes) + agents/exemplars/*.md used as
calibration examples by the Moment Scout and Critic next run.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_clip_index(runs_dir: Path) -> dict:
    idx = {}
    for f in runs_dir.glob("*/clips.json"):
        for c in json.loads(f.read_text()):
            idx[c["id"]] = c
    return idx


def correlations(rows: list[dict], idx: dict) -> dict:
    by = {}
    for r in rows:
        c = idx.get(r["clip_id"])
        if not c or not c.get("components"):
            continue
        for k, v in c["components"].items():
            by.setdefault(k, []).append((v, np.log1p(float(r["views"]))))
    return {k: round(float(np.corrcoef(*zip(*v))[0, 1]), 3) if len(v) > 4 else None for k, v in by.items()}


def run_feedback(runner, runs_dir: Path, perf_csv: Path, cfg_path: Path) -> dict:
    rows = list(csv.DictReader(open(perf_csv)))
    idx = load_clip_index(runs_dir)
    cfg = yaml.safe_load(cfg_path.read_text())
    payload = {
        "current_weights": cfg["signals"]["weights"],
        "signal_view_correlations": correlations(rows, idx),
        "clips": [{**{k: idx[r["clip_id"]].get(k) for k in ("id", "hook_line", "reason", "duration", "components", "hook_overlay")},
                   **r} for r in rows if r["clip_id"] in idx],
    }
    out = runner.run("analyst", payload)

    # bounded update: move each weight at most 30% toward the analyst's suggestion
    old = cfg["signals"]["weights"]
    for k, v in out.signal_weights.items():
        if k in old:
            old[k] = round(old[k] + 0.3 * (v - old[k]), 3)
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False))

    ex_dir = ROOT / "agents" / "exemplars"
    ex_dir.mkdir(exist_ok=True)
    def fmt(ids):
        return "\n".join(f"- hook: {idx[i].get('hook_line')!r} | {idx[i].get('duration')}s | why: {idx[i].get('reason')}"
                         for i in ids if i in idx)
    text = (f"### Clips that performed (imitate)\n{fmt(out.exemplars_good)}\n\n"
            f"### Clips that flopped (avoid)\n{fmt(out.exemplars_bad)}\n\n"
            f"### Patterns\nWin: {'; '.join(out.winning_patterns)}\nLose: {'; '.join(out.losing_patterns)}\n")
    for agent in ("moment_scout", "critic"):
        (ex_dir / f"{agent}.md").write_text(text)
    return out.model_dump()


def load_exemplars() -> dict:
    d = ROOT / "agents" / "exemplars"
    return {p.stem: p.read_text() for p in d.glob("*.md")} if d.exists() else {}
