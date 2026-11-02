"""The EQ engine stays cautious, correctable and ephemeral."""

from datetime import UTC, datetime

from app.services.emotion import EmotionCue, Intent, assess_conversation, response_guidance


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def test_explicit_confusion_requests_step_by_step_help() -> None:
    result = assess_conversation(["I'm confused about what to do next."], now=NOW)

    assert result.emotion_cue == EmotionCue.CONFUSION
    assert result.intent == Intent.EXPLAIN
    assert result.user_confirmed is True
    assert result.confidence == 0.9
    assert result.timestamp == NOW


def test_frustrated_code_request_is_a_cue_not_a_claim() -> None:
    result = assess_conversation(["Why is this stupid code failing again?"], now=NOW)
    guidance = response_guidance(result)

    assert result.emotion_cue == EmotionCue.FRUSTRATION
    assert result.source == "textual"
    assert "Possible conversational cue" in guidance
    assert "Treat this as uncertain" in guidance
    assert "Do not diagnose" in guidance


def test_celebration_matches_positive_energy() -> None:
    result = assess_conversation(["JARVIS! I finally finished my application!"], now=NOW)

    assert result.emotion_cue == EmotionCue.EXCITEMENT
    assert result.response_style == "celebratory"


def test_user_correction_resets_inference_immediately() -> None:
    result = assess_conversation(
        ["This keeps failing and it is frustrating.", "I'm not frustrated, just give me the command."],
        now=NOW,
    )

    assert result.emotion_cue == EmotionCue.NEUTRAL
    assert result.user_confirmed is True
    assert result.confidence == 1.0


def test_insufficient_evidence_stays_neutral() -> None:
    result = assess_conversation(["List the open tasks."], now=NOW)

    assert result.emotion_cue == EmotionCue.NEUTRAL
    assert result.source == "insufficient"
    assert result.confidence == 0


def test_context_never_becomes_high_confidence() -> None:
    result = assess_conversation(["I'm worried this migration is unsafe.", "What is the next check?"], now=NOW)

    assert result.emotion_cue == EmotionCue.WORRY
    assert result.source == "context"
    assert result.confidence == 0.45
