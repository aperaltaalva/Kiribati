from datetime import date, datetime, timezone
from pathlib import Path

from kiribati_monitor.brief import generate_daily_brief, generate_email_brief, priority_points
from kiribati_monitor.models import Article, Classification, StoredArticle


def test_brief_generation_works_with_sample_classified_articles(tmp_path: Path) -> None:
    article = Article(
        title="Kiribati announces fisheries revenue update",
        source_name="Ministry of Fisheries and Marine Resources",
        source_type="official",
        source_url="https://www.mfmrd.gov.ki/",
        source_tags=["fisheries"],
        source_importance=5,
        url="https://www.mfmrd.gov.ki/news/fisheries-update",
        published_date=datetime(2026, 5, 17, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        snippet="Fisheries revenue update.",
        text="Fisheries revenue update.",
        raw_metadata={},
    )
    classification = Classification(
        source_url=article.url,
        topic="fisheries",
        macro_relevance=3,
        urgency=2,
        confidence="high",
        one_sentence_summary="Fisheries revenue information was updated.",
        why_it_matters="Fishing license revenue is central to Kiribati fiscal and external-sector monitoring.",
        suggested_follow_up="Compare with budget revenue assumptions.",
        include_in_daily_brief=True,
    )
    md_path, html_path = generate_daily_brief(
        [StoredArticle(id=1, article=article, classification=classification)],
        output_dir=tmp_path,
        run_date=date(2026, 5, 17),
        source_log=[
            {
                "name": "Ministry of Fisheries and Marine Resources",
                "url": "https://www.mfmrd.gov.ki/",
                "source_type": "official",
                "fetch_method": "html",
                "status": "ok",
                "count": 1,
                "error": None,
            }
        ],
    )
    markdown_text = md_path.read_text(encoding="utf-8")
    html_text = html_path.read_text(encoding="utf-8")
    assert "Kiribati Daily Macro and Policy Monitor" in markdown_text
    assert "Confidence and caveats:" in markdown_text
    assert "[Kiribati announces fisheries revenue update]" in markdown_text
    assert "https://www.mfmrd.gov.ki/news/fisheries-update" in markdown_text
    assert "Source health report" in markdown_text
    assert "<html" in html_text


def test_email_brief_is_mobile_friendly_and_has_health_summary(tmp_path: Path) -> None:
    article = Article(
        title="Kiribati budget update",
        source_name="Ministry of Finance and Economic Development",
        source_type="official",
        source_url="https://www.mfed.gov.ki/",
        source_tags=["fiscal", "budget"],
        source_importance=5,
        url="https://www.mfed.gov.ki/news/budget-update",
        published_date=datetime(2026, 5, 17, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        snippet="Budget update.",
        text="Budget update.",
        raw_metadata={},
    )
    classification = Classification(
        source_url=article.url,
        topic="fiscal",
        macro_relevance=3,
        urgency=2,
        confidence="high",
        one_sentence_summary="Budget information was updated.",
        why_it_matters="Budget changes can affect the fiscal path.",
        suggested_follow_up="Check fiscal tables.",
        include_in_daily_brief=True,
    )

    email_path = generate_email_brief(
        [StoredArticle(id=1, article=article, classification=classification)],
        output_dir=tmp_path,
        run_date=date(2026, 5, 17),
        source_log=[
            {
                "name": "Ministry of Finance and Economic Development",
                "url": "https://www.mfed.gov.ki/",
                "source_type": "official",
                "fetch_method": "html",
                "status": "ok",
                "health": "succeeded",
                "health_label": "Succeeded",
                "count": 1,
                "new_count": 1,
                "error": None,
            }
        ],
    )

    html_text = email_path.read_text(encoding="utf-8")
    assert 'name="viewport"' in html_text
    assert "Top signals" in html_text
    assert "Source health summary" in html_text
    assert "https://www.mfed.gov.ki/news/budget-update" in html_text
    assert "No major macro-relevant updates found" not in html_text


def test_priority_points_favor_core_macro_sources() -> None:
    imf_article = Article(
        title="IMF Kiribati Article IV update",
        source_name="IMF Kiribati Country Page",
        source_type="multilateral",
        source_url="https://www.imf.org/en/Countries/KIR",
        source_tags=["imf", "macro", "fiscal"],
        source_importance=5,
        url="https://www.imf.org/en/Countries/KIR/update",
        published_date=datetime(2026, 5, 17, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        snippet="Article IV update.",
        text="Article IV update.",
        raw_metadata={},
    )
    media_article = Article(
        title="General Pacific political commentary",
        source_name="Example Media",
        source_type="media",
        source_url="https://example.org/",
        source_tags=["media"],
        source_importance=2,
        url="https://example.org/story",
        published_date=datetime(2026, 5, 17, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        snippet="Commentary.",
        text="Commentary.",
        raw_metadata={},
    )
    imf_classification = Classification(
        source_url=imf_article.url,
        topic="fiscal",
        macro_relevance=2,
        urgency=1,
        confidence="medium",
        one_sentence_summary="IMF update.",
        why_it_matters="It is macro-relevant.",
        suggested_follow_up="",
        include_in_daily_brief=True,
    )
    media_classification = Classification(
        source_url=media_article.url,
        topic="politics_governance",
        macro_relevance=2,
        urgency=1,
        confidence="medium",
        one_sentence_summary="Media commentary.",
        why_it_matters="It may be relevant.",
        suggested_follow_up="",
        include_in_daily_brief=True,
    )

    assert priority_points(StoredArticle(id=1, article=imf_article, classification=imf_classification)) > priority_points(
        StoredArticle(id=2, article=media_article, classification=media_classification)
    )
