from pathlib import Path

from kiribati_monitor.ingest import load_sources


def test_sources_yaml_loads_and_has_required_fields() -> None:
    sources = load_sources(Path("sources.yaml"))
    assert len(sources) >= 19
    names = {source.name for source in sources}
    assert "IMF Kiribati Country Page" in names
    assert "Reuters/GDELT Kiribati Query" in names
    for source in sources:
        assert source.name
        assert source.url
        assert source.source_type
        assert source.fetch_method
        assert 1 <= source.importance <= 5
        assert isinstance(source.enabled, bool)
