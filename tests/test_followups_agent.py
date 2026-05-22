from agent import (
    EvidenceLink,
    FollowUpResearchReport,
    InvestigatedFollowUp,
    clean_html_response,
    extract_follow_up_items,
    extract_follow_up_tasks,
    render_fallback_html_page,
    render_research_html_page,
)


def test_extract_follow_up_tasks_stops_at_next_section() -> None:
    html = """
    <html>
      <body>
        <h2>H. Items requiring follow-up</h2>
        <ul>
          <li>Check official budget note.</li>
          <li>Confirm project financing.</li>
        </ul>
        <h2>I. Source health report</h2>
        <p>This should not be included.</p>
      </body>
    </html>
    """

    tasks = extract_follow_up_tasks(html)

    assert tasks == ["Check official budget note.", "Confirm project financing."]


def test_extract_follow_up_items_keeps_original_link_context() -> None:
    html = """
    <html>
      <body>
        <h2>H. Items requiring follow-up</h2>
        <ul>
          <li><strong><a href="https://example.org/item">Budget delay</a></strong> - Check official budget note.</li>
        </ul>
      </body>
    </html>
    """

    tasks = extract_follow_up_items(html)

    assert len(tasks) == 1
    assert tasks[0].title == "Budget delay"
    assert tasks[0].action == "Check official budget note."
    assert tasks[0].source_url == "https://example.org/item"


def test_clean_html_response_removes_markdown_fence() -> None:
    html = clean_html_response("```html\n<!DOCTYPE html>\n<html></html>\n```")

    assert html == "<!DOCTYPE html>\n<html></html>"


def test_fallback_dashboard_escapes_task_text() -> None:
    html = render_fallback_html_page("- <script>alert('x')</script>")

    assert "&lt;script&gt;alert('x')&lt;/script&gt;" in html


def test_research_page_renders_results_not_next_steps() -> None:
    report = FollowUpResearchReport(
        executive_summary="One item was answered.",
        items=[
            InvestigatedFollowUp(
                original_title="Budget delay",
                original_action="Check official budget note.",
                status="answered",
                result="The official note confirms the delay.",
                macro_policy_implication="No near-term fiscal impact was identified.",
                evidence=[
                    EvidenceLink(
                        title="Official note",
                        url="https://example.org/note",
                        publisher="Ministry",
                        date="2026-05-22",
                        finding="The note confirms the delay.",
                    )
                ],
                searched_queries=["Budget delay official note"],
                remaining_gap="None identified.",
            )
        ],
    )

    html = render_research_html_page(report, model="test-model")

    assert "Investigation Result" in html
    assert "Official note" in html
    assert "Actionable Next Steps" not in html
