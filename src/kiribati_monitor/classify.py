from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from .models import Article, Classification

LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL = "gpt-5.4-mini"

CLASSIFICATION_SCHEMA: dict[str, Any] = Classification.model_json_schema()


class OpenAIConfigurationError(RuntimeError):
    """Raised when OpenAI classification is requested without configuration."""


def get_model() -> str:
    return os.getenv("MODEL", DEFAULT_MODEL)


def load_classifier_prompt(path: str | Path = "prompts/daily_brief_system.md") -> str:
    prompt_path = Path(path)
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return (
        "Classify Kiribati public-source articles for macroeconomic relevance. "
        "Return only the requested structured output and do not invent facts."
    )


def classify_article(
    article: Article,
    *,
    model: str | None = None,
    prompt_path: str | Path = "prompts/daily_brief_system.md",
) -> Classification:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        raise OpenAIConfigurationError("OPENAI_API_KEY is not set")

    client = OpenAI()
    response = client.responses.parse(
        model=model or get_model(),
        input=[
            {
                "role": "system",
                "content": build_system_prompt(load_classifier_prompt(prompt_path)),
            },
            {
                "role": "user",
                "content": json.dumps(article_payload(article), ensure_ascii=False, indent=2),
            },
        ],
        text_format=Classification,
    )
    parsed = extract_parsed_response(response)
    if parsed.source_url != article.url:
        LOGGER.warning("Classifier returned a different source_url for %s; correcting it", article.url)
        parsed = parsed.model_copy(update={"source_url": article.url})
    return parsed


def classify_articles(
    articles: list[Article],
    *,
    model: str | None = None,
    prompt_path: str | Path = "prompts/daily_brief_system.md",
    allow_heuristic_fallback: bool = True,
) -> list[Classification]:
    classifications: list[Classification] = []
    for article in articles:
        try:
            classifications.append(classify_article(article, model=model, prompt_path=prompt_path))
        except OpenAIConfigurationError:
            if not allow_heuristic_fallback:
                raise
            LOGGER.warning("OPENAI_API_KEY is missing; using low-confidence heuristic classification")
            classifications.append(heuristic_classification(article))
        except Exception:
            LOGGER.exception("Classification failed for %s; using low-confidence fallback", article.url)
            classifications.append(heuristic_classification(article))
    return classifications


def build_system_prompt(base_prompt: str) -> str:
    return f"""{base_prompt}

Classification rules:
- Use official, statistical, multilateral, donor, and regional institutional source metadata as evidence of source authority.
- Treat media reporting as useful but less authoritative unless it quotes or links an official decision or data release.
- Set macro_relevance to 3 only for direct macro/fiscal/external-sector/fisheries-revenue/inflation/financial-sector/disaster-budget implications.
- Set urgency to 3 only when there is a time-sensitive policy, financing, disaster, market, or data-release implication.
- If the supplied text is too thin to support a claim, say so in why_it_matters and keep confidence low.
- Preserve source_url exactly as provided in the input article.
- Return concise text. Do not add facts not present in the input.
"""


def article_payload(article: Article) -> dict[str, Any]:
    return {
        "title": article.title,
        "source_name": article.source_name,
        "source_type": article.source_type,
        "source_url": article.source_url,
        "source_tags": article.source_tags,
        "source_importance": article.source_importance,
        "url": article.url,
        "published_date": article.published_date.isoformat() if article.published_date else None,
        "fetched_at": article.fetched_at.isoformat(),
        "snippet": article.snippet[:2500],
        "text": article.text[:5000],
    }


def extract_parsed_response(response: Any) -> Classification:
    direct = getattr(response, "output_parsed", None)
    if isinstance(direct, Classification):
        return direct

    for output in getattr(response, "output", []) or []:
        if getattr(output, "type", None) != "message":
            continue
        for item in getattr(output, "content", []) or []:
            if getattr(item, "type", None) == "refusal":
                raise RuntimeError(f"OpenAI refusal: {getattr(item, 'refusal', '')}")
            parsed = getattr(item, "parsed", None)
            if isinstance(parsed, Classification):
                return parsed
            if isinstance(parsed, dict):
                return Classification.model_validate(parsed)

    text = getattr(response, "output_text", None)
    if text:
        return Classification.model_validate_json(text)
    raise RuntimeError("Could not parse structured classification response")


def heuristic_classification(article: Article) -> Classification:
    text = f"{article.title} {article.snippet} {' '.join(article.source_tags)}".lower()
    topic = "other"
    if any(word in text for word in ["budget", "tax", "revenue", "debt", "fiscal", "finance"]):
        topic = "fiscal"
    elif any(word in text for word in ["fish", "tuna", "wcpfc", "pna", "vessel"]):
        topic = "fisheries"
    elif any(word in text for word in ["climate", "disaster", "cyclone", "drought", "king tide", "resilience"]):
        topic = "climate_disaster"
    elif any(word in text for word in ["inflation", "price", "import", "food", "fuel"]):
        topic = "inflation_imports"
    elif any(word in text for word in ["grant", "project", "adb", "world bank", "dfat", "mfat", "aid"]):
        topic = "donor_project"
    elif any(word in text for word in ["election", "parliament", "cabinet", "minister", "president"]):
        topic = "politics_governance"

    institutional_bonus = 1 if article.source_type in {"official", "statistics", "multilateral", "donor"} else 0
    relevance = min(3, institutional_bonus + (2 if topic != "other" else 0))
    urgency = 2 if topic in {"climate_disaster", "fiscal", "fisheries"} and relevance >= 2 else 1 if relevance >= 2 else 0
    include = relevance >= 2 or urgency >= 2 or article.source_importance >= 5

    return Classification(
        source_url=article.url,
        topic=topic,  # type: ignore[arg-type]
        macro_relevance=relevance,
        urgency=urgency,
        confidence="low",
        one_sentence_summary=article.title,
        why_it_matters="OpenAI classification was unavailable; this low-confidence fallback is based on source tags and keywords only.",
        suggested_follow_up="Review the linked source directly before using this item in mission work." if include else "",
        include_in_daily_brief=include,
    )
