"""End-to-end smoke test with a synthetic stream and a mock LLM (no API keys, no GPU).

  python tests/smoke_test.py
"""
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipforge import claude_agents, orchestrator  # noqa: E402
from clipforge.llm import AgentRunner, load_agent  # noqa: E402

WORK = ROOT / "runs" / "_smoke"
DUR = 180
SPIKES = [62, 141]


def make_fixture():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    vid = WORK / "stream.mp4"
    vol = "+".join(f"between(t,{s - 2},{s + 3})*0.9" for s in SPIKES)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=1280x720:d={DUR}:r=30",
                    "-f", "lavfi", "-i", f"sine=f=220:d={DUR}", "-af", f"volume='0.05+{vol}':eval=frame",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(vid)], check=True)
    rnd = random.Random(1)
    with open(WORK / "chat.jsonl", "w") as f:
        for t in range(DUR):
            n = 25 if any(0 <= t - s - 8 < 6 for s in SPIKES) else rnd.randint(0, 3)
            for _ in range(n):
                f.write(json.dumps({"t": t + rnd.random(), "text": "KEKW" if n > 5 else "hi",
                                    "emotes": ["KEKW"] if n > 5 else []}) + "\n")
    lines = ["so welcome back chat", "wait did you just see that", "no way he actually said that on stream",
             "okay okay listen to this", "that is the craziest thing I have ever heard", "anyway moving on"]
    segs, t = [], 0.0
    while t < DUR - 4:
        text = lines[int(t / 4) % len(lines)]
        ws = text.split()
        step = 3.5 / len(ws)
        segs.append({"start": t, "end": t + 3.5, "text": text, "speaker": "SPEAKER_00",
                     "words": [{"w": w, "start": t + i * step, "end": t + (i + 1) * step} for i, w in enumerate(ws)]})
        t += 4
    (WORK / "transcript.json").write_text(json.dumps({"segments": segs}))
    return vid


def mock(name, p):
    if name == "moment_scout":
        return {"candidates": [{"id": f"c{i}", "start": pk["window"][0], "end": min(pk["window"][1], pk["window"][0] + 45),
                                "hook_line": "wait did you just see that", "payoff": "reaction", "score": 80 - i,
                                "reason": "chat spike + reaction", "signal_peak_ids": [pk["id"]]}
                               for i, pk in enumerate(p["peaks"][:3])]}
    if name == "critic":
        return {"verdicts": [{"id": c["id"], "verdict": "approve" if i < 2 else "reject", "standalone": 7, "hook": 8,
                              "payoff": 7, "brand_safe": True, "why": "clear hook"} for i, c in enumerate(p["candidates"])]}
    if name == "transform":
        return {"id": p["id"], "hook_overlay": "He did NOT expect this",
                "context_overlays": [{"t": 3, "duration": 2.5, "text": "Streamer reacting live"}],
                "trim_dead_air_start": 0.5, "end_on": "payoff"}
    if name == "packager":
        return {"id": p["id"], "packages": {pl: {"title": "Streamer reacts", "caption": "wait for it",
                                                 "hashtags": ["#stream"]} for pl in p["platforms"]}}
    if name == "compliance":
        fixed = {k: {**v, "caption": "#ad " + v["caption"] + " @examplestreamer #examplestreamer Live on Kick"}
                 for k, v in p["packages"].items()}
        return {"id": p["id"], "passed": True,
                "issues": [{"severity": "fix", "issue": "missing #ad + required tags", "fix": "added"}],
                "fixed_packages": fixed, "manual_steps": ["Add Paid Partnership label in-app"]}
    raise KeyError(name)


def main():
    for a in ("campaign_scout", "moment_scout", "critic", "transform", "packager", "compliance", "analyst"):
        load_agent(a)  # every spec parses
    stale = claude_agents.sync(check=True)
    assert not stale, f"stale .claude/agents mirrors {stale}; run: python -m clipforge.claude_agents"
    vid = make_fixture()
    cfg = orchestrator.load_cfg()
    cfg["render"]["preset"] = "ultrafast"
    runner = AgentRunner(mock=mock)
    import hashlib
    out_root = ROOT / "runs" / "_smoke_out"
    if out_root.exists():
        shutil.rmtree(out_root)
    wd = out_root / hashlib.sha1(str(vid).encode()).hexdigest()[:10]
    wd.mkdir(parents=True)
    shutil.copy(WORK / "transcript.json", wd / "transcript.json")
    res = orchestrator.cmd_run(runner, cfg, str(vid), "example-streamer", str(WORK / "chat.jsonl"),
                               work_root=ROOT / "runs" / "_smoke_out")
    clips = res["clips"]
    assert len(clips) == 2, clips
    peaks = json.loads((Path(res["work_dir"]) / "peaks.json").read_text())
    hits = [pk["t"] for pk in peaks[:4]]
    assert any(abs(h - s) < 15 for h in hits for s in SPIKES), f"peaks {hits} missed spikes {SPIKES}"
    for c in clips:
        info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=width,height", "-of", "csv=p=0", c["path"]], capture_output=True, text=True).stdout
        assert info.strip() == "1080,1920", info
        assert c["packages"]["tiktok"]["caption"].startswith("#ad")
        assert c["receipts"][0]["fields"]["post_mode"] == "MEDIA_UPLOAD"
    print("SMOKE OK", [(c["id"], c["duration"], c["reframe_mode"]) for c in clips], "peaks:", hits)


if __name__ == "__main__":
    main()
