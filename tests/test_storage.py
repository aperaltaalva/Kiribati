from kiribati_monitor.storage import normalize_url, url_hash


def test_normalize_url_removes_tracking_and_fragments() -> None:
    first = normalize_url("HTTPS://Example.ORG:443/path/?b=2&utm_source=x&a=1#section")
    second = normalize_url("https://example.org/path?a=1&b=2")
    assert first == second


def test_url_hash_uses_normalized_url() -> None:
    assert url_hash("https://example.org/story?utm_medium=email") == url_hash("https://example.org/story")
