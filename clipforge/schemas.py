"""Contracts between layers. Every agent returns JSON that must validate against one of these."""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field, conlist

Platform = Literal["tiktok", "youtube_shorts", "instagram_reels", "facebook_reels", "x"]


# ---------- Campaign Scout ----------
class CampaignRank(BaseModel):
    campaign_id: str
    expected_views_per_clip: int = Field(ge=0)
    effective_cpm_usd: float = Field(ge=0, description="CPM after fees, verification haircut and agency cut")
    expected_usd_per_clip: float = Field(ge=0)
    competition: Literal["low", "medium", "high"]
    fit_score: int = Field(ge=0, le=100)
    risks: list[str] = []
    decision: Literal["pursue", "test", "skip"]
    reason: str


class CampaignScoutOut(BaseModel):
    ranked: list[CampaignRank]


# ---------- Moment Scout ----------
class Candidate(BaseModel):
    id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    hook_line: str = Field(description="Exact words said in the first seconds of the clip")
    payoff: str
    score: int = Field(ge=0, le=100)
    reason: str
    signal_peak_ids: list[str] = []


class MomentScoutOut(BaseModel):
    candidates: list[Candidate]


# ---------- Critic ----------
class Verdict(BaseModel):
    id: str
    verdict: Literal["approve", "retrim", "reject"]
    standalone: int = Field(ge=0, le=10)
    hook: int = Field(ge=0, le=10)
    payoff: int = Field(ge=0, le=10)
    brand_safe: bool
    new_start: Optional[float] = None
    new_end: Optional[float] = None
    why: str


class CriticOut(BaseModel):
    verdicts: list[Verdict]


# ---------- Transform (originality layer) ----------
class Overlay(BaseModel):
    t: float = Field(ge=0, description="seconds from clip start")
    duration: float = Field(gt=0)
    text: str = Field(max_length=90)


class TransformOut(BaseModel):
    id: str
    hook_overlay: str = Field(max_length=70, description="Big text for first ~2.5s")
    context_overlays: list[Overlay] = Field(default_factory=list, description="Context the viewer needs")
    commentary: Optional[str] = Field(None, description="Optional voiceover/intro line in your voice")
    trim_dead_air_start: float = Field(0, ge=0, description="seconds to cut before the hook")
    end_on: Literal["payoff", "loop"] = "payoff"


# ---------- Packager ----------
class PlatformPackage(BaseModel):
    title: Optional[str] = Field(None, max_length=100)
    caption: str = Field(max_length=2200)
    hashtags: list[str] = []


class PackagerOut(BaseModel):
    id: str
    packages: dict[Platform, PlatformPackage]


# ---------- Compliance ----------
class ComplianceIssue(BaseModel):
    platform: Optional[Platform] = None
    severity: Literal["block", "fix", "warn"]
    issue: str
    fix: str


class ComplianceOut(BaseModel):
    id: str
    passed: bool
    issues: list[ComplianceIssue] = []
    fixed_packages: dict[Platform, PlatformPackage] = Field(
        default_factory=dict, description="Packages rewritten to pass (disclosure, required tags)")
    manual_steps: list[str] = Field(default_factory=list, description="Things a human must do in-app")


# ---------- Analyst (feedback loop) ----------
class AnalystOut(BaseModel):
    signal_weights: dict[str, float]
    winning_patterns: list[str]
    losing_patterns: list[str]
    exemplars_good: list[str] = Field(description="clip ids to use as positive examples")
    exemplars_bad: list[str]
    campaign_notes: list[str] = []


SCHEMAS = {
    "campaign_scout": CampaignScoutOut,
    "moment_scout": MomentScoutOut,
    "critic": CriticOut,
    "transform": TransformOut,
    "packager": PackagerOut,
    "compliance": ComplianceOut,
    "analyst": AnalystOut,
}
