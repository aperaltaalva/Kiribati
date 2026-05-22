from agent import clean_html_response, extract_follow_up_tasks, render_fallback_html_page


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


def test_clean_html_response_removes_markdown_fence() -> None:
    html = clean_html_response("```html\n<!DOCTYPE html>\n<html></html>\n```")

    assert html == "<!DOCTYPE html>\n<html></html>"


def test_fallback_dashboard_escapes_task_text() -> None:
    html = render_fallback_html_page("- <script>alert('x')</script>")

    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
