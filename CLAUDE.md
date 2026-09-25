# ClipForge: notes for Claude Code

Read `README.md` first. Code layers (`clipforge/`) do the deterministic work. The 7 specialists in `agents/*.md` make the judgment calls. Every specialist returns JSON that must validate against `clipforge/schemas.py`.

## Acting as the orchestrator
The specialists are mirrored as subagents in `.claude/agents/`. **Never edit those files.** They are generated. Edit `agents/<name>.md` and run `python -m clipforge.claude_agents`.

`clipforge/orchestrator.py::cmd_run` is the reference for stage order and payload shapes. Follow it:
1. `ingest.ingest` → `transcribe.transcribe` + `transcribe.load` → `signals.detect` (code).
2. **moment-scout** gets `{campaign: _brief(c), clip_length, peaks[+transcript]}`.
3. **critic** gets only `{campaign, candidates: [{id, start, end, transcript}]}`. Never pass the scout's reason or score.
4. For each kept clip: **transform** (mandatory, never skipped) → `render.render_clip` → **packager** → **compliance**.
5. `publish.publish` only for clips with no `block` issues. It is a dry run unless `publish.dry_run: false`.

Validate each subagent reply before using it. If validation fails, send the error back to the subagent:
`python -c "import sys; from clipforge.schemas import SCHEMAS; SCHEMAS[sys.argv[1]].model_validate_json(open(sys.argv[2]).read())" critic out.json`

## Hard rules (market research, Sept 2026)
- The Transform layer is mandatory. Captions alone are not "original" on any platform.
- The operator is in Canada. `#ad` goes **first** in every caption.
- TikTok posts as drafts (`MEDIA_UPLOAD`, unaudited API). The Instagram paid-partnership label is a manual step.
- Skip gambling campaigns (Ontario AGCO rules).
- Never post identical clips, captions or files across accounts. Each moment posts once (`runs/posted_moments.json`).
- Never flip `publish.dry_run` or post for real unless the user asks.

## Checks
`python tests/smoke_test.py` needs no API keys or GPU, but needs `ffmpeg` with libass. It also fails if the `.claude/agents` mirrors are stale. CI runs it on every PR (`.github/workflows/ci.yml`).

## PR workflow (standing instruction from the repo owner, the sole contributor)
There are no human reviewers, so Claude owns each PR end to end:
1. After opening a PR, review your own diff adversarially: bugs, edge cases, missing tests, drift from the hard rules above, and anything a reviewer would ask for. Push the fixes. Repeat until a pass finds nothing worth changing.
2. Wait for CI on the latest commit. A red CI is yours to fix. Never skip or disable a test to get green.
3. Once the latest head is green and mergeable and self-review is clean, **squash-merge it yourself**. Don't wait for approval.
4. Post one short PR comment listing what each self-review round changed, so the history explains itself.
