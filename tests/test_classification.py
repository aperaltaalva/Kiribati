from pydantic import ValidationError
import pytest

from kiribati_monitor.classify import CLASSIFICATION_SCHEMA
from kiribati_monitor.models import Classification


def test_classification_schema_validates_sample_json() -> None:
    sample = {
        "source_url": "https://example.org/news",
        "topic": "fiscal",
        "macro_relevance": 3,
        "urgency": 2,
        "confidence": "high",
        "one_sentence_summary": "The budget was tabled with new revenue measures.",
        "why_it_matters": "Budget measures can change the fiscal path and financing needs.",
        "suggested_follow_up": "Check the budget tables when published.",
        "include_in_daily_brief": True,
    }
    parsed = Classification.model_validate(sample)
    assert parsed.topic == "fiscal"
    assert CLASSIFICATION_SCHEMA["type"] == "object"


def test_classification_rejects_invalid_topic() -> None:
    with pytest.raises(ValidationError):
        Classification.model_validate(
            {
                "source_url": "https://example.org/news",
                "topic": "not_a_topic",
                "macro_relevance": 1,
                "urgency": 1,
                "confidence": "medium",
                "one_sentence_summary": "Summary.",
                "why_it_matters": "Reason.",
                "suggested_follow_up": "",
                "include_in_daily_brief": False,
            }
        )
