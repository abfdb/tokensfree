"""One-command P0 gate: lint, tests, DSH contract matrix, secret scan.

Run from the repository root:  python scripts/verify_p0.py
Exit code 0 means every P0 acceptance check passed.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", ".superpowers", "__pycache__"}
TEXT_SUFFIXES = {".py", ".json", ".js", ".yml", ".yaml", ".toml", ".md", ".txt", ".html", ".css"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-]{32,}"),
)


def run(args):
    proc = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def scan_secrets():
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            hits.append(str(path.relative_to(ROOT)))
    return sorted(hits)


def main():
    failed = []
    checks = (
        ("ruff", ["-m", "ruff", "check", "."]),
        ("pytest", ["-m", "pytest", "-q"]),
        ("contract", ["check_dsh_contract.py"]),
    )
    for name, args in checks:
        code, output = run(args)
        print(("ok   " if code == 0 else "FAIL ") + name)
        if code != 0:
            failed.append(name)
            print(output[-3000:])
    hits = scan_secrets()
    print(("ok   " if not hits else "FAIL ") + "secret-scan" + ("" if not hits else ": " + ", ".join(hits)))
    if hits:
        failed.append("secret-scan")
    print("P0 OK" if not failed else "P0 FAILED: " + ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
