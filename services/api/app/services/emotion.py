"""Conservative, non-persistent conversational style cues.

This module does not diagnose emotions.  It derives a short-lived style hint from
the user's own words and punctuation.  The result is used for the current model
request only and is deliberately not written to conversation metadata or memory.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.core.time import utc_now


class EmotionCue(StrEnum):
    FRUSTRATION = "frustration"
    EXCITEMENT = "excitement"
    SADNESS = "sadness"
    CONFUSION = "confusion"
    WORRY = "worry"
    HAPPINESS = "happiness"
    URGENCY = "urgency"
    UNCERTAINTY = "uncertainty"
    NEUTRAL = "neutral"


class Intent(StrEnum):
    TROUBLESHOOT = "troubleshoot"
    CELEBRATE = "celebrate"
    EXPLAIN = "explain"
    REASSURE = "reassure"
    ACT_QUICKLY = "act_quickly"
    GENERAL = "general"


class EmotionAssessment(BaseModel):
    """Ephemeral assessment used only to shape the next response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    emotion_cue: EmotionCue
    confidence: float = Field(ge=0, le=1)
    source: Literal["explicit", "textual", "context", "insufficient"]
    response_style: Literal["direct", "celebratory", "gentle", "step_by_step", "calm", "urgent", "neutral"]
    user_confirmed: bool = False
    timestamp: datetime


_CORRECTION = re.compile(
    r"\b(?:i(?:'m| am) not|i wasn't|do not assume i(?:'m| am)|you(?:'re| are) wrong,? i(?:'m| am) not)\s+"
    r"(?:frustrated|angry|sad|worried|confused|excited|happy)\b",
    re.IGNORECASE,
)

_SIGNALS: tuple[tuple[EmotionCue, Intent, str, tuple[re.Pattern[str], ...]], ...] = (
    (
        EmotionCue.FRUSTRATION,
        Intent.TROUBLESHOOT,
        "direct",
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
            r"\b(?:i(?:'m| am) frustrated|this is frustrating|fed up)\b",
            r"\bwhy (?:is|does|did) .{0,80}(?:fail|break|crash)(?:ing)?\b",
            r"\b(?:stupid|damn) (?:code|error|bug|thing)\b",
        )),
    ),
    (
        EmotionCue.EXCITEMENT,
        Intent.CELEBRATE,
        "celebratory",
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
            r"\b(?:i finally (?:finished|did it|shipped)|i(?:'m| am) (?:so )?excited)\b",
            r"\b(?:great news|we did it|it works!)\b",
        )),
    ),
    (
        EmotionCue.CONFUSION,
        Intent.EXPLAIN,
        "step_by_step",
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
            r"\b(?:i(?:'m| am) confused|i don(?:'t|’t) understand|lost me)\b",
            r"\bwhat (?:should|do) i do next\b",
        )),
    ),
    (
        EmotionCue.WORRY,
        Intent.REASSURE,
        "calm",
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
            r"\b(?:i(?:'m| am) worried|i(?:'m| am) anxious|this worries me|i(?:'m| am) concerned)\b",
        )),
    ),
    (
        EmotionCue.SADNESS,
        Intent.REASSURE,
        "gentle",
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
            r"\b(?:i(?:'m| am) sad|feeling down|this is upsetting)\b",
        )),
    ),
    (
        EmotionCue.HAPPINESS,
        Intent.CELEBRATE,
        "celebratory",
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
            r"\b(?:i(?:'m| am) happy|this made my day|fantastic news)\b",
        )),
    ),
    (
        EmotionCue.URGENCY,
        Intent.ACT_QUICKLY,
        "urgent",
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
            r"\b(?:urgent|asap|right now|immediately|deadline (?:is )?(?:today|in))\b",
        )),
    ),
    (
        EmotionCue.UNCERTAINTY,
        Intent.EXPLAIN,
        "step_by_step",
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
            r"\b(?:i(?:'m| am) not sure|maybe|i think,? but|could be wrong)\b",
        )),
    ),
)


def assess_conversation(messages: Sequence[str], *, now: datetime | None = None) -> EmotionAssessment:
    """Return a cautious style cue from recent user messages.

    The newest message has priority.  Older messages can provide context, but
    never raise confidence above the conservative contextual level.
    """

    timestamp = now or utc_now()
    cleaned = [message.strip() for message in messages if message.strip()]
    if not cleaned:
        return _neutral(timestamp)
    latest = cleaned[-1]
    if _CORRECTION.search(latest):
        return EmotionAssessment(
            intent=Intent.GENERAL,
            emotion_cue=EmotionCue.NEUTRAL,
            confidence=1.0,
            source="explicit",
            response_style="neutral",
            user_confirmed=True,
            timestamp=timestamp,
        )

    for cue, intent, style, patterns in _SIGNALS:
        if any(pattern.search(latest) for pattern in patterns):
            explicit = re.search(r"\bi(?:'m| am| feel| was)\b", latest, re.IGNORECASE) is not None
            return EmotionAssessment(
                intent=intent,
                emotion_cue=cue,
                confidence=0.9 if explicit else 0.72,
                source="explicit" if explicit else "textual",
                response_style=style,  # type: ignore[arg-type]
                user_confirmed=explicit,
                timestamp=timestamp,
            )

    for previous in reversed(cleaned[-4:-1]):
        for cue, intent, style, patterns in _SIGNALS:
            if any(pattern.search(previous) for pattern in patterns):
                return EmotionAssessment(
                    intent=intent,
                    emotion_cue=cue,
                    confidence=0.45,
                    source="context",
                    response_style=style,  # type: ignore[arg-type]
                    user_confirmed=False,
                    timestamp=timestamp,
                )
    return _neutral(timestamp)


def response_guidance(assessment: EmotionAssessment) -> str:
    """Build model guidance without asserting that an inferred cue is a fact."""

    if assessment.emotion_cue == EmotionCue.NEUTRAL:
        return "Use a clear, natural tone. Do not invent an emotional interpretation."
    style_rules = {
        "direct": "Acknowledge the blocker once, then move directly to diagnosis and a tested next step.",
        "celebratory": "Match the positive energy briefly, then offer a useful next action.",
        "gentle": "Use gentle wording and manageable steps without pretending to share the feeling.",
        "step_by_step": "Explain one concrete step at a time and check ambiguous assumptions.",
        "calm": "Use calm, precise wording and state uncertainty and safeguards clearly.",
        "urgent": "Lead with the most time-sensitive safe action and keep the response concise.",
        "neutral": "Use a clear, natural tone.",
    }
    return (
        f"Possible conversational cue: {assessment.emotion_cue.value} "
        f"(confidence {assessment.confidence:.2f}, source {assessment.source}). "
        "Treat this as uncertain unless the user stated it explicitly. "
        f"{style_rules[assessment.response_style]} "
        "Do not diagnose, claim to know the user's feelings, manipulate vulnerability, or repeat artificial empathy. "
        "Accept a correction immediately."
    )


def _neutral(timestamp: datetime) -> EmotionAssessment:
    return EmotionAssessment(
        intent=Intent.GENERAL,
        emotion_cue=EmotionCue.NEUTRAL,
        confidence=0.0,
        source="insufficient",
        response_style="neutral",
        user_confirmed=False,
        timestamp=timestamp,
    )
