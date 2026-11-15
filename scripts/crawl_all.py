"""Resumable download of every ImpactU product from the dev API (/search/works).

Usage:
    python crawl_all.py [OUT_DIR] [--pause 3] [--only le100|gt100] [--years FIRST-LAST] [--plan]

OUT_DIR (default ./colombia, kept apart from the UdeA data in data/) gets one sub-folder per part:

    le100/          works with at most 100 authors, one publication year at a time
                    (y2015_page_0001.json.gz, ...), 250 per page
    gt100/          works with more than 100 authors, same slices, 100 per page (5 in runs
                    started before 2026-11-03; the size in use is kept in state.json)
    le100_noyear/   works with no year (mostly Minciencias-only records), paged from
    gt100_noyear/   the whole list sorted year_asc, where they come first
    state.json      what is finished; slice counts; totals

Why slices: on the full list every request costs 30-85 s of database time; a single
year costs 2-15 s. Works without a year can't be selected by the years filter, so
they are paged from the year_asc list until a page holds works with a year.

Interrupt it at any time (Ctrl-C, reboot, server outage): run the same command
again and it continues from the first page not on disk. Pages are written
atomically, so a half-written page is never kept. Network or server errors are
retried with growing waits (up to 15 min) forever, so a dev-server outage only
pauses the run. Only one copy can run per OUT_DIR (lock file).

Lists return only the first 100 authors of each work (authors_count keeps the
total), so after the gt100 part run fetch_authors.py on gt100/ and gt100_noyear/
to get the full author lists before building.

--plan prints the slices and their counts (cheap requests) without downloading.
Load each part afterwards with build.py, e.g.
    python build.py colombia/le100 --dataset colombia_le100
"""
import argparse
import datetime as dt
import fcntl
import gzip
import json
import os
import sys
import time

import requests

BASE = os.environ.get("IMPACTU_API_BASE", "https://api.dev.impactu.colav.co").rstrip("/")
PATH = "/search/works"
# lists cut each work to its first 100 authors, so gt100 pages are light now (5 per page before 2026-11-03)
PARTS = {"le100": {"authors_count": "-100", "size": 250}, "gt100": {"authors_count": "101-", "size": 100}}


def get(params, page, size, pause):
    wait = 30
    while True:
        try:
            r = requests.get(BASE + PATH, params={**params, "max": size, "page": page}, timeout=900)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                return r.json()
            if r.status_code == 400:
                sys.exit(f"bad request {params} page {page}: {r.text[:300]}")
            msg = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            msg = f"{type(e).__name__}: {e}"
        print(f"  {params} page {page}: {msg}; retrying in {wait}s", flush=True)
        time.sleep(wait)
        wait = min(wait * 2, 900)


def save(path, data):
    with gzip.open(path + ".tmp", "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def year_slices(only=None):
    if only:
        a, b = (int(x) for x in only.split("-"))
        return [(f"y{y}", str(y)) for y in range(a, b + 1)]
    last = dt.date.today().year + 1
    s = [("y0000", "0"), ("y0001-1899", "1,1899")]
    s += [(f"y{y}", str(y)) for y in range(1900, last + 1)]
    s.append((f"y{last + 1}-9999", f"{last + 1},9999"))
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?", default="colombia")
    ap.add_argument("--pause", type=float, default=3, help="seconds between requests")
    ap.add_argument("--only", choices=list(PARTS), help="download only one part")
    ap.add_argument("--plan", action="store_true", help="count slices only, download nothing")
    ap.add_argument("--years", metavar="FIRST-LAST", help="only these publication years (no 'no year' step); for tests or partial runs")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    lock = open(os.path.join(args.out, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f"another crawl_all.py is already running on {args.out}")
    spath = os.path.join(args.out, "state.json")
    state = json.load(open(spath)) if os.path.exists(spath) else {}

    def commit():
        json.dump(state, open(spath + ".tmp", "w"), indent=1)
        os.replace(spath + ".tmp", spath)

    for part, cfg in PARTS.items():
        if args.only and part != args.only:
            continue
        st = state.setdefault(part, {"slices": {}})
        base = {"authors_count": cfg["authors_count"], "sort": "citations_desc"}
        folder = os.path.join(args.out, part)
        if "size" not in st:  # a part begun by an older version used 5 per page for gt100
            begun = os.path.isdir(folder) and any(f.endswith(".json.gz") for f in os.listdir(folder))
            st["size"] = (5 if part == "gt100" else cfg["size"]) if begun else cfg["size"]
        size = st["size"]
        if "total" not in st:
            st["total"] = get(base, 1, 1, args.pause)["meta"]["count"]
            commit()
        print(f"[{part}] {st['total']} works in total, {size} per page", flush=True)
        os.makedirs(folder, exist_ok=True)

        # 1) one slice per year
        for prefix, years in year_slices(args.years):
            sl = st["slices"].setdefault(prefix, {})
            if sl.get("complete"):
                continue
            params = {**base, "years": years}
            if "count" not in sl:
                sl["count"] = get(params, 1, 1, args.pause)["meta"]["count"]
                commit()
                time.sleep(args.pause)
            if args.plan:
                print(f"[{part}] {prefix}: {sl['count']}", flush=True)
                continue
            pages = -(-sl["count"] // size)
            if pages:
                print(f"[{part}] {prefix}: {sl['count']} works, {pages} pages", flush=True)
            for page in range(1, pages + 1):
                f = os.path.join(folder, f"{prefix}_page_{page:04d}.json.gz")
                if os.path.exists(f):
                    continue
                t = time.time()
                save(f, get(params, page, size, args.pause))
                print(f"[{part}] {prefix} page {page}/{pages} {time.time() - t:.0f}s", flush=True)
                time.sleep(args.pause)
            sl["complete"] = True
            commit()

        if args.plan or args.years:
            continue
        # 2) works with no year: first pages of the whole list sorted by year ascending
        with_year = sum(s["count"] for s in st["slices"].values())
        st["noyear_expected"] = st["total"] - with_year
        commit()
        nst = st.setdefault("noyear", {})
        if not nst.get("complete") and st["noyear_expected"] > 0:
            nfolder = os.path.join(args.out, f"{part}_noyear")
            os.makedirs(nfolder, exist_ok=True)
            params = {"authors_count": cfg["authors_count"], "sort": "year_asc"}
            pages = -(-st["noyear_expected"] // size)
            print(f"[{part}] no year: ~{st['noyear_expected']} works, ~{pages} pages of the year_asc list", flush=True)
            page = 1
            while True:
                f = os.path.join(nfolder, f"noyear_page_{page:04d}.json.gz")
                if os.path.exists(f):
                    with gzip.open(f, "rt", encoding="utf-8") as h:
                        d = json.load(h)
                else:
                    t = time.time()
                    d = get(params, page, size, args.pause)
                    save(f, d)
                    print(f"[{part}] no year page {page}/~{pages} {time.time() - t:.0f}s", flush=True)
                    time.sleep(args.pause)
                items = d.get("data") or []
                if not items or any(isinstance(w.get("year_published"), int) for w in items):
                    break  # reached works that have a year: those are in the year slices
                page += 1
            nst["complete"] = True
            nst["pages"] = page
            commit()

    if args.plan:
        for part, s in state.items():
            n = sum(x.get("count", 0) for x in s["slices"].values())
            print(f"{part}: total {s['total']}, in year slices {n}, no year ~{s['total'] - n}")
        return
    print("done; run fetch_authors.py on gt100 and gt100_noyear, load each part with build.py, "
          "then compare unique works with state.json totals", flush=True)


if __name__ == "__main__":
    main()
