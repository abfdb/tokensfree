"""Pricing evidence must be exact: sibling models and cache columns must not count."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEADER = "| 模型名称 | 上下文 | 输入单价 | 输出单价 | 缓存存储 | 缓存命中 |\n"
FREE_ROW = "> | GLM-4.7-Flash | 200K | 免费 | 免费 | 限时免费 | 免费 |\n"
PAID_ROW = "> | GLM-4.7-FlashX | 200K | 0.5 | 3 | 限时免费 | 0.1 |\n"


def run_snapshot(*args, timeout=120):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "evidence_snapshot.py"), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=dict(os.environ),
    )


def payload_of(result):
    return json.loads(result.stdout[result.stdout.index("{"):])


def write(tmp_path, content, name="pricing.md"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_help_lists_the_flags():
    result = run_snapshot("--help")
    assert result.returncode == 0
    assert "--model" in result.stdout and "--file" in result.stdout


def test_free_row_is_recorded(tmp_path):
    source = write(tmp_path, HEADER + FREE_ROW)
    out = tmp_path / "evidence"
    result = run_snapshot("--file", str(source), "--out-dir", str(out), "--provider", "zhipu", "--model", "glm-4.7-flash")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = payload_of(result)
    assert payload["verified"] is True
    assert payload["free_rows"]
    assert len(payload["sha256"]) == 64
    assert payload["evidence_expires_at"] > 0
    snapshot = json.loads(Path(payload["snapshot_path"]).read_text(encoding="utf-8"))
    assert snapshot["model"] == "glm-4.7-flash"
    assert snapshot["suggested_env"]["TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT"] == str(payload["evidence_expires_at"])


def test_paid_sibling_with_free_cache_column_is_rejected(tmp_path):
    source = write(tmp_path, HEADER + PAID_ROW)
    result = run_snapshot("--file", str(source), "--out-dir", str(tmp_path / "evidence"), "--model", "glm-4.7-flashx")
    assert result.returncode == 3
    payload = payload_of(result)
    assert payload["reason"] == "model_not_marked_free"


def test_sibling_model_does_not_match_the_requested_name(tmp_path):
    source = write(tmp_path, HEADER + PAID_ROW)
    result = run_snapshot("--file", str(source), "--out-dir", str(tmp_path / "evidence"), "--model", "glm-4.7-flash")
    assert result.returncode == 3
    assert payload_of(result)["reason"] == "model_not_found"


def test_missing_source_fails_closed(tmp_path):
    result = run_snapshot("--file", str(tmp_path / "nope.md"), "--out-dir", str(tmp_path / "evidence"))
    assert result.returncode == 2
    assert payload_of(result)["reason"] == "fetch_failed"


def test_invalid_ttl_is_rejected(tmp_path):
    source = write(tmp_path, HEADER + FREE_ROW)
    result = run_snapshot("--file", str(source), "--out-dir", str(tmp_path / "evidence"), "--ttl-days", "0")
    assert result.returncode == 2
    assert payload_of(result)["reason"] == "invalid_ttl_days"


@pytest.mark.parametrize("case, expected_code, reason", [
    ("free", 0, None),
    ("paid", 3, "model_not_marked_free"),
    ("unknown", 3, "model_not_found"),
    ("missing", 2, "fetch_failed"),
])
def test_stdout_json_preserves_unicode_with_cp1252_default(tmp_path, case, expected_code, reason):
    source = tmp_path / "价格.md"
    if case != "missing":
        source.write_text(HEADER + (PAID_ROW if case == "paid" else FREE_ROW), encoding="utf-8")
    model = {"paid": "glm-4.7-flashx", "unknown": "未知模型"}.get(case, "glm-4.7-flash")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "evidence_snapshot.py"),
         "--file", str(source), "--out-dir", str(tmp_path / "证据"), "--model", model],
        cwd=str(ROOT), capture_output=True, timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "cp1252:strict", "PYTHONUTF8": "0"},
    )
    assert result.returncode == expected_code, result.stderr.decode("utf-8", errors="replace")
    # ASCII JSON is valid UTF-8 and must round-trip without lossy replacement.
    payload = json.loads(result.stdout.decode("ascii"))
    if case == "free":
        assert "免费" in payload["free_rows"][0]
        assert payload["source"] == str(source)
        snapshot = json.loads(Path(payload["snapshot_path"]).read_text(encoding="utf-8"))
        assert snapshot["free_rows"] == payload["free_rows"]
    else:
        assert payload["reason"] == reason
        if case == "paid":
            assert "限时免费" in payload["rows"][0]
        elif case == "unknown":
            assert payload["model"] == model
        else:
            assert "价格.md" in payload["error"]
