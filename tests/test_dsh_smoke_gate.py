"""DSH smoke as a repeatable gate.

The fast tests always run. The real-client test is opt-in because it needs an
unpacked DSH install and node on PATH:

    TOKENSFREE_DSH_SMOKE=1 python -m pytest tests/test_dsh_smoke_gate.py -q
"""
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXTRACTED_DSH = pathlib.Path(r"D:\IFan\dsh-extracted\node_modules\@deepseek-ai\dsh\lib\bin.js")


def load_smoke_module():
    spec = importlib.util.spec_from_file_location("dsh_smoke", ROOT / "scripts" / "dsh_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixture_asks_for_a_read_then_answers(tmp_path):
    smoke = load_smoke_module()
    fixture = smoke.Fixture(tmp_path / "fixture.txt")
    first = fixture.respond({"messages": [{"role": "system"}, {"role": "user"}], "tools": [{}] * 23, "stream": True})
    assert "tool_calls" in first
    assert smoke.CALL_ID in first
    second = fixture.respond({"messages": [{"role": "system"}, {"role": "user"}, {"role": "tool"}], "tools": [{}] * 23, "stream": True})
    assert smoke.FINAL_TEXT in second
    assert fixture.requests[0]["tools"] == 23
    assert "tool" not in fixture.requests[0]["roles"]


@pytest.mark.skipif(os.environ.get("TOKENSFREE_DSH_SMOKE") != "1", reason="set TOKENSFREE_DSH_SMOKE=1 to run the real DSH client")
def test_real_dsh_client_roundtrip():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    if not EXTRACTED_DSH.exists():
        pytest.skip("unpacked DSH install not found; run scripts/dsh_smoke.py once with an extracted copy")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "dsh_smoke.py"), node, str(EXTRACTED_DSH)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, (result.stdout[-2000:] + result.stderr[-2000:])
    summary = json.loads(result.stdout[result.stdout.index("{"):])
    assert summary["verified"] is True
    assert summary["checks"]["one_tool_pair"] is True
    assert summary["checks"]["first_turn_had_tools"] is True
