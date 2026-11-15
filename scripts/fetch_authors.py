"""Fetch the full author list of every work whose list record was cut to the first 100 authors.

Usage:
    python fetch_authors.py PAGES_DIR [--out DIR] [--pause 2] [--dry-run]

Since late October 2026 the dev API's product lists (/search/works and the
research/products of an affiliation, person, source or geo entity) return only
the first 100 authors of each work, with the true total in authors_count
(https://help.impactu.colav.co/guides/authorship). For every work in PAGES_DIR
with authors_count > len(authors), this calls GET /work/{id}/authors (all
authors, same order, a superset of the list fields) and saves the response as
OUT/<work_id>.json.gz (percent-encoded, since ids such as DOIs contain slashes). OUT defaults to PAGES_DIR + "_authors"; build.py reads it
from there and puts the full list back before flattening.

Resumable: works already in OUT are skipped. Errors are retried with growing
waits (up to 15 min); a 404 is recorded in OUT/missing.json and skipped.
--dry-run only counts the cut works.
"""
import argparse
import gzip
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build import authors_file, page_files, read_json  # noqa: E402

BASE = os.environ.get("IMPACTU_API_BASE", "https://api.dev.impactu.colav.co").rstrip("/")


def get(wid):
    wait = 30
    while True:
        try:
            r = requests.get(f"{BASE}/work/{wid}/authors", timeout=900)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                return r.json()
            if r.status_code in (400, 404):
                return None
            msg = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            msg = f"{type(e).__name__}: {e}"
        print(f"  {wid}: {msg}; retrying in {wait}s", flush=True)
        time.sleep(wait)
        wait = min(wait * 2, 900)


def cut_works(pages):
    """(work_id, authors_count, authors in the list) for every work whose list was cut."""
    seen = set()
    for f in page_files(pages):
        d = read_json(f)
        for w in (d.get("data") if isinstance(d, dict) else d) or []:
            wid, n = w.get("id"), w.get("authors_count") or 0
            if wid not in seen and n > len(w.get("authors") or []):
                seen.add(wid)
                yield wid, n, len(w.get("authors") or [])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pages", help="directory with page_*.json[.gz] files")
    ap.add_argument("--out", help="where to save the author lists (default PAGES_DIR_authors)")
    ap.add_argument("--pause", type=float, default=2, help="seconds between requests")
    ap.add_argument("--dry-run", action="store_true", help="count the cut works, fetch nothing")
    args = ap.parse_args()

    out = args.out or args.pages.rstrip("/") + "_authors"
    todo = list(cut_works(args.pages))
    print(f"{len(todo)} works with a cut author list in {args.pages}", flush=True)
    if args.dry_run or not todo:
        return
    os.makedirs(out, exist_ok=True)
    mpath = os.path.join(out, "missing.json")
    missing = json.load(open(mpath)) if os.path.exists(mpath) else []
    for i, (wid, n, have) in enumerate(todo, 1):
        f = authors_file(out, wid)
        if os.path.exists(f) or wid in missing:
            continue
        t = time.time()
        d = get(wid)
        if d is None:
            missing.append(wid)
            json.dump(missing, open(mpath, "w"))
            print(f"{i}/{len(todo)} {wid}: not found", flush=True)
            continue
        with gzip.open(f + ".tmp", "wt", encoding="utf-8") as h:
            json.dump(d, h, ensure_ascii=False)
        os.replace(f + ".tmp", f)
        print(f"{i}/{len(todo)} {wid}: {len(d.get('data') or [])} of {n} authors (list had {have}), "
              f"{time.time() - t:.0f}s", flush=True)
        time.sleep(args.pause)
    print(f"done; {len(missing)} not found", flush=True)


if __name__ == "__main__":
    main()
