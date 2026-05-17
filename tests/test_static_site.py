from pathlib import Path

from kiribati_monitor.static_site import publish_static_site


def test_publish_static_site_generates_index_and_brief_page(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    site_dir = tmp_path / "site"
    output_dir.mkdir()
    (output_dir / "daily_brief_2026-05-17.html").write_text("<html><body>Brief</body></html>", encoding="utf-8")

    index_path = publish_static_site(output_dir=output_dir, site_dir=site_dir)

    index_text = index_path.read_text(encoding="utf-8")
    assert index_path == site_dir / "index.html"
    assert (site_dir / "daily_brief_2026-05-17.html").exists()
    assert "Public-source-only" in index_text
    assert "daily_brief_2026-05-17.html" in index_text
