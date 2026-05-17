from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


SourceType = Literal[
    "official",
    "statistics",
    "multilateral",
    "donor",
    "regional",
    "media",
    "data",
]
FetchMethod = Literal["rss", "html", "gdelt", "manual"]
Topic = Literal[
    "fiscal",
    "fisheries",
    "climate_disaster",
    "inflation_imports",
    "external_sector",
    "banking_payments",
    "donor_project",
    "politics_governance",
    "geopolitics",
    "social",
    "other",
]
Confidence = Literal["low", "medium", "high"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    url: HttpUrl
    source_type: SourceType
    fetch_method: FetchMethod
    tags: list[str] = Field(default_factory=list)
    importance: int = Field(ge=1, le=5)
    enabled: bool = True

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, tags: list[str]) -> list[str]:
        return [tag.strip().lower() for tag in tags if tag.strip()]


class Article(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    source_type: SourceType
    source_url: str
    source_tags: list[str] = Field(default_factory=list)
    source_importance: int = Field(ge=1, le=5)
    url: str = Field(min_length=1)
    published_date: datetime | None = None
    fetched_at: datetime = Field(default_factory=utc_now)
    snippet: str = ""
    text: str = ""
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "source_name", "source_url", "url", mode="before")
    @classmethod
    def strip_string_fields(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("published_date", "fetched_at")
    @classmethod
    def ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(description="The article source URL, copied exactly from the input.")
    topic: Topic
    macro_relevance: int = Field(ge=0, le=3)
    urgency: int = Field(ge=0, le=3)
    confidence: Confidence
    one_sentence_summary: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    suggested_follow_up: str = ""
    include_in_daily_brief: bool


class StoredArticle(BaseModel):
    id: int
    article: Article
    classification: Classification | None = None
