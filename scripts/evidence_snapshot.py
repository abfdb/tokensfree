"""Record free-tier pricing evidence for a provider.

Fetches the official pricing document, hashes it, extracts the model's row and
fails closed if the row no longer says the model is free. The snapshot is written
under .superpowers/ (not committed) together with a suggested expiry timestamp
for TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT.

  python scripts/evidence_snapshot.py --provider zhipu --model glm-4.7-flash
  python scripts/evidence_snapshot.py --file local-pricing.md   # offline / tests

Stdout JSON uses ASCII escapes for compatibility with redirected Windows output;
snapshot files remain UTF-8 with readable Unicode. Parsed JSON values are identical.

Exit codes: 0 = evidence recorded, 2 = fetch/read failed, 3 = evidence no longer free.
"""
import argparse
import hashlib
import json
import pathlib
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://docs.bigmodel.cn/cn/guide/start/pricing.md"
DEFAULT_OUT = ROOT / ".superpowers" / "evidence"
FREE_MARKERS = ("免费", "free", "0", "0.0")


def fetch(url, timeout=20.0):
    request = urllib.request.Request(url, headers={"User-Agent": "tokensfree-evidence/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL by default
        raw = response.read()
    return raw.decode("utf-8", errors="replace")


def read_content(source_file, url):
    if source_file is not None:
        return pathlib.Path(source_file).read_text(encoding="utf-8")
    return fetch(url)


def _cells(line):
    candidate = line.lstrip()
    while candidate.startswith(">"):
        candidate = candidate[1:].lstrip()
    if not candidate.startswith("|"):
        return None
    return [cell.strip() for cell in candidate.strip().strip("|").split("|")]


def model_rows(text, model):
    """Exact model-cell matches only: GLM-4.7-Flash must not match GLM-4.7-FlashX."""
    rows = []
    for line in text.splitlines():
        cells = _cells(line)
        if cells and cells[0].strip("* ").lower() == model.lower():
            rows.append(cells)
    return rows


def row_is_free(cells):
    """Only the input and output price columns count; the cache column can say 限时免费."""
    if len(cells) < 4:
        return False
    for cell in (cells[2], cells[3]):
        lowered = cell.lower()
        if not any(lowered == marker or marker in lowered for marker in FREE_MARKERS):
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Record free-tier pricing evidence")
    parser.add_argument("--provider", default="zhipu")
    parser.add_argument("--model", default="glm-4.7-flash")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--file", default=None, help="read pricing content from a local file instead of the URL")
    parser.add_argument("--ttl-days", type=int, default=30)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    if args.ttl_days < 1 or args.ttl_days > 365:
        print(json.dumps({"verified": False, "reason": "invalid_ttl_days"}))
        return 2
    try:
        text = read_content(args.file, args.url)
    except Exception as error:  # network, file, permission
        print(json.dumps({"verified": False, "reason": "fetch_failed", "error": str(error)}, ensure_ascii=True))
        return 2

    rows = model_rows(text, args.model)
    if not rows:
        print(json.dumps({"verified": False, "reason": "model_not_found", "model": args.model}, ensure_ascii=True))
        return 3
    free_rows = [cells for cells in rows if row_is_free(cells)]
    if not free_rows:
        print(
            json.dumps(
                {"verified": False, "reason": "model_not_marked_free", "rows": [" | ".join(cells) for cells in rows]},
                ensure_ascii=True,
            )
        )
        return 3

    now = time.time()
    expires_at = int(now + args.ttl_days * 86400)
    snapshot = {
        "version": 1,
        "provider": args.provider,
        "model": args.model,
        "source": args.file or args.url,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "fetched_at_unix": int(now),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "matched_rows": [" | ".join(cells) for cells in rows],
        "free_rows": [" | ".join(cells) for cells in free_rows],
        "ttl_days": args.ttl_days,
        "evidence_expires_at": expires_at,
        "suggested_env": {
            "TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT": str(expires_at),
            "TOKENSFREE_REAL_PROVIDER": args.provider,
            "TOKENSFREE_REAL_MODEL": args.model,
        },
    }
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("%s-%d.json" % (args.provider, int(now)))
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "verified": True,
        "provider": args.provider,
        "model": args.model,
        "source": snapshot["source"],
        "sha256": snapshot["sha256"],
        "evidence_expires_at": expires_at,
        "snapshot_path": str(path),
        "matched_rows": [" | ".join(cells) for cells in rows],
        "free_rows": [" | ".join(cells) for cells in free_rows],
        "note": "pricing evidence only; keep the snapshot outside git and refresh before expiry",
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
