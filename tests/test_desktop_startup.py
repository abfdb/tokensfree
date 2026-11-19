"""User-level autostart: write/remove the Startup folder launcher."""
from tokensfree.desktop import startup


def test_enable_creates_launcher(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    path = startup.set_autostart(True, repo_root=repo)
    assert path.exists()
    assert startup.is_autostart_enabled()
    content = path.read_text(encoding="utf-8")
    assert "cd /d" in content
    assert str(repo) in content
    assert "tokensfree.desktop" in content
    assert path.parent.name == "Startup"


def test_disable_removes_launcher(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    startup.set_autostart(True, repo_root=tmp_path)
    removed = startup.set_autostart(False, repo_root=tmp_path)
    assert removed is not None
    assert not startup.is_autostart_enabled()
    assert startup.remove_autostart() is None
