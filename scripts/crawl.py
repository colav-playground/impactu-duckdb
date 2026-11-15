"""Resumable download of an ImpactU dev-API products list, one gzipped page per file.

Usage:
    python crawl.py API_PATH OUT_DIR [--authors-count -100] [--size 100] [--pause 3] [--param k=v ...]

Example (all UdeA works with at most 100 authors):
    python crawl.py /affiliation/institution/03bp5hc83/research/products data/udea_authors_le100

Writes OUT_DIR/page_NNNN.json.gz (raw API responses) and OUT_DIR/manifest.json
(path, params, total count). Re-running skips pages already on disk; pass the
same parameters, since a different filter or page size would mix datasets.
Lists return only the first 100 authors of each work: for works with more than 100
authors run fetch_authors.py OUT_DIR afterwards to get the full lists.
"""
import argparse
import gzip
import json
import os
import sys
import time

import requests

BASE = os.environ.get("IMPACTU_API_BASE", "https://api.dev.impactu.colav.co").rstrip("/")


def fetch(path, params, page, size):
    for attempt in range(8):
        try:
            r = requests.get(BASE + path, params={**params, "max": size, "page": page}, timeout=600)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                return r.json()
            print(f"page {page}: HTTP {r.status_code} {r.text[:200]!r}", flush=True)
            if r.status_code == 400:
                sys.exit("bad request: check the path and parameters")
        except (requests.RequestException, ValueError) as e:
            print(f"page {page}: {type(e).__name__}: {e}", flush=True)
        time.sleep(min(30 * 2**attempt, 900))
    sys.exit(f"page {page}: giving up")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="dev API products route, e.g. /affiliation/institution/03bp5hc83/research/products")
    ap.add_argument("out")
    ap.add_argument("--authors-count", default="-100", help="authors_count filter; '' to disable (dangerous for big lists)")
    ap.add_argument("--size", type=int, default=100, help="works per page (max 250)")
    ap.add_argument("--pause", type=float, default=3, help="seconds between pages")
    ap.add_argument("--sort", default="citations_desc")
    ap.add_argument("--param", action="append", default=[], help="extra filter k=v (repeatable)")
    ap.add_argument("--split-years", metavar="FIRST-LAST",
                    help="crawl one publication year at a time (e.g. 1900-2027), plus years 1..FIRST-1 as one slice. "
                         "Much faster on huge lists such as /search/works, where every request costs ~1 min. "
                         "Works with no year cannot be selected by the years filter and are reported as a gap.")
    args = ap.parse_args()

    params = {"sort": args.sort, **dict(p.split("=", 1) for p in args.param)}
    if args.authors_count:
        params["authors_count"] = args.authors_count
    os.makedirs(args.out, exist_ok=True)
    mpath = os.path.join(args.out, "manifest.json")
    if os.path.exists(mpath):
        old = json.load(open(mpath))
        if (old.get("params") != params or old.get("size") != args.size or old.get("api_path") != args.path
                or old.get("split_years") != args.split_years):
            sys.exit(f"{mpath} was made with other settings: {old}")
    count = fetch(args.path, params, 1, 1)["meta"]["count"]
    if args.split_years:
        first, last = (int(x) for x in args.split_years.split("-"))
        slices = [(f"y0001-{first - 1}_", {"years": f"1,{first - 1}"})] + [(f"y{y}_", {"years": str(y)}) for y in range(first, last + 1)]
    else:
        slices = [("", {})]
    json.dump({"base": BASE, "api_path": args.path, "params": params, "size": args.size, "count": count,
               "split_years": args.split_years, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, open(mpath, "w"), indent=1)
    print(f"{count} works in total, {len(slices)} slice(s) of up to {args.size} per page", flush=True)
    spath = os.path.join(args.out, "slices.json")
    done = json.load(open(spath)) if os.path.exists(spath) else {}
    for prefix, extra in slices:
        if done.get(prefix, {}).get("complete"):
            continue
        sp = {**params, **extra}
        n = fetch(args.path, sp, 1, 1)["meta"]["count"] if extra else count
        pages = -(-n // args.size)
        print(f"slice {prefix or 'all'}: {n} works, {pages} pages", flush=True)
        for page in range(1, pages + 1):
            path = os.path.join(args.out, f"{prefix}page_{page:04d}.json.gz")
            if os.path.exists(path):
                continue
            t = time.time()
            d = fetch(args.path, sp, page, args.size)
            with gzip.open(path + ".tmp", "wt", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(path + ".tmp", path)
            print(f"{prefix}page {page}/{pages}: {len(d['data'])} works, {time.time() - t:.0f}s", flush=True)
            time.sleep(args.pause)
        done[prefix] = {"params": extra, "count": n, "pages": pages, "complete": True}
        json.dump(done, open(spath, "w"), indent=1)
    covered = sum(s["count"] for s in done.values())
    if args.split_years and covered < count:
        print(f"WARNING: slices cover {covered} of {count} works; {count - covered} have no usable year", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
