"""Mirror agents/*.md as Claude Code subagents in .claude/agents/, so Claude Code can act as the orchestrator.

agents/*.md stays the single source of truth. Edit those, then regenerate:

  python -m clipforge.claude_agents          # write .claude/agents/*.md
  python -m clipforge.claude_agents --check  # exit 1 if the mirrors are stale (run by the smoke test)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from .llm import AGENTS_DIR, load_agent
from .schemas import SCHEMAS

ROOT = AGENTS_DIR.parent
OUT_DIR = ROOT / ".claude" / "agents"

# When the orchestrator (Claude Code) should delegate to each specialist.
DESCRIPTIONS = {
    "campaign_scout": "ClipForge Campaign Scout. Use to rank clipping campaign briefs (Whop/Vyro/Kick) by expected "
                      "money per clip and decide pursue/test/skip. Input: operator_profile + campaigns.",
    "moment_scout": "ClipForge Moment Scout. Use after signal peaks are computed to pick standalone short-clip "
                    "candidates with exact hook start / payoff end. Input: campaign, clip_length, peaks.",
    "critic": "ClipForge Critic (blind judge). Use after Moment Scout to approve/retrim/reject candidates. Pass ONLY "
              "campaign + candidates (id, start, end, transcript), never the scout's reasons or scores.",
    "transform": "ClipForge Transform (originality layer). Use for every approved clip before render to write the "
                 "hook overlay and context overlays. Mandatory: captions alone are not original on any platform.",
    "packager": "ClipForge Packager. Use after render to write a unique title/caption/hashtags per platform for one "
                "clip. Input: id, hook_overlay, transcript, campaign, platforms.",
    "compliance": "ClipForge Compliance (final gate). Use on every packaged clip before publish: #ad first, campaign "
                  "tags, bans, length, and manual in-app steps. Nothing posts without passing this.",
    "analyst": "ClipForge Analyst (feedback loop). Use weekly once views settle to re-weight signals and pick "
               "exemplar clips from real views/payouts.",
}

HEADER = "<!-- GENERATED from agents/{name}.md by `python -m clipforge.claude_agents`. Do not edit here. -->\n\n"


def _role_line(system: str) -> str:
    """Fallback description for an agent not yet listed in DESCRIPTIONS: its '# Role: ...' heading."""
    first = next((ln for ln in system.splitlines() if ln.startswith("# ")), "# ClipForge specialist")
    return "ClipForge " + first.lstrip("# ").replace("Role: ", "")


def render(name: str) -> str:
    text = (AGENTS_DIR / f"{name}.md").read_text()
    meta = yaml.safe_load(text.split("---\n", 2)[1]) or {}
    spec = load_agent(name)
    front = {
        "name": name.replace("_", "-"),
        "description": DESCRIPTIONS.get(name) or _role_line(spec.system),
        "tools": "Read, Glob, Grep",
        "model": meta.get("model", "sonnet"),
    }
    body = spec.system
    if name in ("moment_scout", "critic"):
        body += (f"\n\n## Calibration examples from your own past results\n"
                 f"If `agents/exemplars/{name}.md` exists, read it first and calibrate against it.")
    if name == "critic":
        body += ("\n\nStay blind: do not read anything under `runs/` (agent logs, clips.json). Those files hold "
                 "the scout's reasoning.")
    body += ("\n\n## Output contract\nReturn ONLY one JSON object matching this JSON Schema. No prose. The "
             "orchestrator validates it with `clipforge/schemas.py` and sends it back if it fails.\n"
             + json.dumps(SCHEMAS[name].model_json_schema()))
    return "---\n" + yaml.safe_dump(front, sort_keys=False, width=1000) + "---\n" + HEADER.format(name=name) + body + "\n"


def sync(check: bool = False) -> list[str]:
    """Write mirrors (or, with check=True, only report). Returns the paths that were/are stale.

    Generated files whose source agent no longer exists count as stale and are removed; hand-written
    subagents in .claude/agents/ (no GENERATED header) are left alone.
    """
    stale = []
    names = sorted(p.stem for p in AGENTS_DIR.glob("*.md"))
    expected = {OUT_DIR / f"{n.replace('_', '-')}.md": render(n) for n in names}
    for path, content in expected.items():
        if not path.exists() or path.read_text() != content:
            stale.append(str(path.relative_to(ROOT)))
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
    for path in sorted(OUT_DIR.glob("*.md")) if OUT_DIR.exists() else []:
        if path not in expected and "<!-- GENERATED from agents/" in path.read_text():
            stale.append(str(path.relative_to(ROOT)))
            if not check:
                path.unlink()
    return stale


if __name__ == "__main__":
    check = "--check" in sys.argv
    stale = sync(check=check)
    if check and stale:
        sys.exit(f"stale Claude Code subagents: {stale}\nrun: python -m clipforge.claude_agents")
    print(("stale: " if check else "synced: ") + (", ".join(stale) or "nothing, all in sync"))
