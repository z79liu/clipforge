# ClipForge — AI clipping pipeline for paid campaigns

A master orchestrator routes each VOD through code layers and seven specialist agents. Code does the deterministic work: downloading, transcription, signal math, and rendering. The agents only make judgment calls, and each returns JSON that is checked against a strict schema (`clipforge/schemas.py`). A reply that fails the check is sent back to the agent automatically.

```
                   ┌──────────────── Campaign Scout (Sonnet) ── which briefs are worth your clips
                   │
VOD/stream URL → [1 Ingest] → [2 Transcribe] → [3 Signals] ─ chat velocity · emote bursts · loudness · laughter
   (+ chat replay)  yt-dlp       WhisperX        fused z-score peaks, snapped to sentences/scene cuts
                                                     │
                              Moment Scout (Sonnet) ◄┘  picks clips + exact hook start / payoff end
                                     │
                              Critic (Opus, blind) ─── approve / retrim / reject (30–50% pass)
                                     │
                              Transform (Sonnet) ───── hook overlay + context overlays (originality layer)
                                     │
                              [5 Render] ───────────── 9:16 face-tracked or blur-fill, karaoke captions, -14 LUFS
                                     │
                              Packager (Haiku) ─────── unique title/caption/tags per platform
                                     │
                              Compliance (Sonnet) ──── #ad placement, campaign tags, bans, length; manual steps
                                     │
                              [7 Publish] ──────────── upload-post, staggered; TikTok as draft; dry-run default
                                     │
                              Analyst (Opus) ◄──────── real views/payouts → re-weights signals + writes exemplars
```

## You control every agent
Each specialist is a file in `agents/`. Its frontmatter sets `model` (haiku, sonnet or opus), `temperature` and `max_tokens`, and the body is the agent's full instructions. You can edit any file directly. `agents/exemplars/` is written by the Analyst after each feedback run and gets appended to the Scout's and Critic's prompts, so their picks track what actually earned money.

## Setup
```bash
pip install -r requirements.txt          # plus whisperx on a GPU machine
export ANTHROPIC_API_KEY=...  UPLOAD_POST_API_KEY=...  HF_TOKEN=...
```
Edit `config.yaml`. The most important fields are `publish.upload_post_user`, `platforms`, and `operator`. Then add one YAML file per campaign in `campaigns/`, copying the fields from the example.

## Daily loop
```bash
python -m clipforge.orchestrator campaigns                                   # 1. rank briefs → pursue/test/skip
python -m clipforge.orchestrator run https://www.twitch.tv/videos/123 --campaign example-streamer
#   → runs/<id>/clips/*.mp4 + REVIEW.md (dry run: nothing posts)
#   review, then set publish.dry_run: false and re-run, or post manually
#   do the MANUAL steps (IG Paid Partnership, TikTok commercial toggle, link accounts first)
python -m clipforge.orchestrator feedback --perf data/performance.csv        # 3. weekly, once views settle
```
Test without API keys or a GPU: `python tests/smoke_test.py`.

## Cost
Estimated at about $0.50–0.80 per hour of source video: roughly $0.16 for the Scout, $0.45 for the Critic, and $0.05 of GPU time. Transcripts are prompt-cached. Moving non-urgent runs to the Batch API roughly halves the cost.

## Research-driven defaults (Sept 2026)
- **Captions alone are not "original".** YouTube's inauthentic-content policy, TikTok's For You eligibility rules and Meta's March 2026 rules all suppress reposts, even ones the creator permitted. That is why the Transform layer exists and why it is never skipped.
- **Clips are 30–45s**, with the hook in the first 1–3s and the clip ending on the payoff or looping.
- **Disclosure:** `#ad` goes first in the caption (Ad Standards Canada, Oct 2025; FTC), and the native paid-partnership labels must be turned on. Instagram's label cannot be set through the API.
- **TikTok:** posts from an unaudited API app stay private, so the pipeline uploads drafts (`MEDIA_UPLOAD`) and you publish them in the app.
- **Deduplication:** each moment is posted only once (`runs/posted_moments.json`). Never post identical files across multiple accounts. Shared files, captions and posting rhythm get account networks flagged together.
- **Chat lag:** chat reacts about 5–20s after a moment happens. `signals.chat_lag_seconds` controls the shift and should be tuned for each streamer.
- **Gambling campaigns are skipped by default.** Ontario's AGCO restricts gambling advertising.

## Things to plug in on a GPU machine
- WhisperX plus pyannote `community-1` for speaker-aware captions and better face tracking in multi-person podcasts.
- `blaze_face_short_range.tflite` in `models/` for MediaPipe face detection. Without it the pipeline falls back to OpenCV.
- An optional laughter model: LaughterSegmentation or SenseVoice, wrapped as `laughter_segmentation.segment()`.
- For Kick chat, use the maintained chat-downloader fork and pin its version.
