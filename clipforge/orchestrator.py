"""Orchestrator — the master node. Routes work between code layers and specialist agents; never judges content itself.

  python -m clipforge.orchestrator campaigns                      # rank campaign briefs
  python -m clipforge.orchestrator run <url|file> --campaign ID   # full pipeline for one VOD
  python -m clipforge.orchestrator feedback --perf data/performance.csv
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

from . import feedback, ingest, publish, render, signals, transcribe
from .llm import AgentRunner

ROOT = Path(__file__).resolve().parent.parent


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def load_campaigns() -> dict:
    return {c["id"]: c for c in (yaml.safe_load(p.read_text()) for p in (ROOT / "campaigns").glob("*.yaml"))}


# ----------------------------------------------------------------------------------------------
def cmd_campaigns(runner: AgentRunner, cfg: dict) -> dict:
    out = runner.run("campaign_scout", {"operator_profile": cfg["operator"], "campaigns": list(load_campaigns().values())})
    return out.model_dump()


def cmd_run(runner: AgentRunner, cfg: dict, url: str, campaign_id: str, chat_file: str | None = None,
            work_root: Path | None = None) -> dict:
    campaign = load_campaigns()[campaign_id]
    slug = hashlib.sha1(url.encode()).hexdigest()[:10]
    runs = work_root or ROOT / "runs"
    work = runs / slug
    work.mkdir(parents=True, exist_ok=True)
    log = lambda m: print(f"[orchestrator] {m}")  # noqa: E731

    # 1-2 ingest + transcribe (code)
    src = ingest.ingest(url, work, chat_file)
    segs = transcribe.load(transcribe.transcribe(src["video"], work, cfg["transcribe"]))
    log(f"ingested {src['source']} · {len(segs)} segments · chat={'yes' if src['chat'] else 'no'}")

    # 3 signals (code)
    peaks = signals.detect(src["video"], src["chat"], segs, cfg["signals"], work)
    log(f"{len(peaks)} signal peaks")
    if not peaks:
        return {"clips": [], "note": "no peaks above threshold; lower signals.z_threshold"}

    # 4 moment scout (agent) — sees only peak windows + text, returns candidates
    scout_in = {"campaign": _brief(campaign),
                "clip_length": cfg["clips"]["length_seconds"],
                "peaks": [{**p, "transcript": transcribe.window_text(segs, *p["window"])} for p in peaks]}
    cands = runner.run("moment_scout", scout_in).candidates
    cands = [c for c in cands if c.end - c.start >= cfg["clips"]["length_seconds"][0] * 0.7]
    log(f"scout proposed {len(cands)}")

    # 5 critic (agent, stronger model, blind to scout's reasoning)
    critic_in = {"campaign": _brief(campaign),
                 "candidates": [{"id": c.id, "start": c.start, "end": c.end,
                                 "transcript": transcribe.window_text(segs, c.start, c.end)} for c in cands]}
    verdicts = {v.id: v for v in runner.run("critic", critic_in).verdicts}
    kept = []
    for c in cands:
        v = verdicts.get(c.id)
        if not v or v.verdict == "reject" or not v.brand_safe:
            continue
        if v.verdict == "retrim" and v.new_start is not None and v.new_end is not None:
            c.start, c.end = v.new_start, v.new_end
        kept.append((c, v, c.score * 0.4 + (v.standalone + v.hook + v.payoff) * 2))
    kept = sorted(kept, key=lambda x: -x[2])[: cfg["clips"]["max_per_vod"]]
    log(f"critic kept {len(kept)}")

    cuts = signals.scene_cuts(src["video"])
    finder = render.FaceFinder(cfg["render"].get("mediapipe_model"))
    seen = _seen(runs / "posted_moments.json")
    clips = []
    for c, v, rank in kept:
        key = f"{slug}:{int(c.start // 10)}"
        if key in seen:
            continue
        text = transcribe.window_text(segs, c.start, c.end)
        # 6 transform (agent) — originality layer
        tf = runner.run("transform", {"id": c.id, "campaign": _brief(campaign), "hook_line": c.hook_line,
                                      "payoff": c.payoff, "transcript": text, "duration": c.end - c.start})
        # 7 render (code)
        r = render.render_clip(src["video"], segs, c.start, c.end, tf.model_dump(), work / "clips" / f"{c.id}.mp4",
                               cfg["render"], finder, cuts)
        # 8 packager (agent, cheap model)
        pk = runner.run("packager", {"id": c.id, "campaign": _brief(campaign), "hook_overlay": tf.hook_overlay,
                                     "transcript": text, "platforms": cfg["publish"]["platforms"]})
        # 9 compliance (agent) — last gate before anything leaves the machine
        cp = runner.run("compliance", {"id": c.id, "campaign_rules": campaign.get("rules", {}),
                                       "disclosure": cfg["operator"]["disclosure"],
                                       "clip_duration": r["duration"], "overlays": tf.model_dump(),
                                       "packages": {k: p.model_dump() for k, p in pk.packages.items()}})
        packages = {k: p.model_dump() for k, p in pk.packages.items()}
        packages.update({k: p.model_dump() for k, p in cp.fixed_packages.items()})
        blocked = [i for i in cp.issues if i.severity == "block"]
        receipts = [] if blocked else publish.publish(r["path"], packages, cfg["publish"], work, c.id)
        if not blocked:
            seen.add(key)
        peak = next((p for p in peaks if p["id"] in c.signal_peak_ids), None)
        clips.append({"id": c.id, "campaign": campaign_id, "start": c.start, "end": c.end, "rank": round(rank, 1),
                      "hook_line": c.hook_line, "reason": c.reason, "hook_overlay": tf.hook_overlay,
                      "components": peak["components"] if peak else None, **r, "packages": packages,
                      "compliance": cp.model_dump(), "blocked": bool(blocked), "receipts": receipts})
    _save_seen(runs / "posted_moments.json", seen)
    (work / "clips.json").write_text(json.dumps(clips, indent=1, default=str))
    (work / "REVIEW.md").write_text(_review(clips))
    log(f"done · {len(clips)} clips · review {work / 'REVIEW.md'}")
    return {"work_dir": str(work), "clips": clips}


# ----------------------------------------------------------------------------------------------
def _brief(c: dict) -> dict:
    return {k: c.get(k) for k in ("id", "name", "creator", "niche", "cpm_usd", "rules", "notes")}


def _seen(p: Path) -> set:
    return set(json.loads(p.read_text())) if p.exists() else set()


def _save_seen(p: Path, s: set) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(s)))


def _review(clips: list[dict]) -> str:
    out = ["# Review queue\n"]
    for c in clips:
        out.append(f"## {c['id']} — {'BLOCKED' if c['blocked'] else 'ready'} (rank {c['rank']})\n"
                   f"- file: `{c['path']}` · {c['duration']}s · reframe: {c['reframe_mode']}\n"
                   f"- hook: {c['hook_overlay']}\n- why: {c['reason']}\n")
        for i in c["compliance"]["issues"]:
            out.append(f"- [{i['severity']}] {i['issue']} → {i['fix']}")
        for s in c["compliance"]["manual_steps"]:
            out.append(f"- [ ] MANUAL: {s}")
        out.append("")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(prog="clipforge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("campaigns")
    r = sub.add_parser("run")
    r.add_argument("source")
    r.add_argument("--campaign", required=True)
    r.add_argument("--chat", help="pre-downloaded chat.jsonl")
    f = sub.add_parser("feedback")
    f.add_argument("--perf", default=str(ROOT / "data" / "performance.csv"))
    a = ap.parse_args()

    cfg = load_cfg()
    runner = AgentRunner(exemplars=feedback.load_exemplars(), log_dir=ROOT / "runs" / "agent_logs")
    if a.cmd == "campaigns":
        print(json.dumps(cmd_campaigns(runner, cfg), indent=1))
    elif a.cmd == "run":
        cmd_run(runner, cfg, a.source, a.campaign, a.chat)
    else:
        print(json.dumps(feedback.run_feedback(runner, ROOT / "runs", Path(a.perf), ROOT / "config.yaml"), indent=1))


if __name__ == "__main__":
    main()
