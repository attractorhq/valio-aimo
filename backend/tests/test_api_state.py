"""Recorded-capture discovery used by the HTTP layer (architect integration fix for #52)."""

from app.api.state import latest_capture


def test_latest_capture_accepts_a_capture_dir_or_picks_the_newest_subdir(tmp_path):
    assert latest_capture(tmp_path) is None
    (tmp_path / "20261113T210000Z").mkdir()
    (tmp_path / "20261113T210000Z" / "manifest.json").write_text("{}")
    (tmp_path / "20261113T220000Z").mkdir()
    (tmp_path / "20261113T220000Z" / "manifest.json").write_text("{}")
    assert latest_capture(tmp_path) == tmp_path / "20261113T220000Z"
    (tmp_path / "manifest.json").write_text("{}")
    assert latest_capture(tmp_path) == tmp_path
