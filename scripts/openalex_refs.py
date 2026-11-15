"""Add OpenAlex reference lists to a dataset's works that have an OpenAlex id.

Usage:
    python openalex_refs.py --dataset NAME [--db data/impactu.duckdb] [--out DIR] [--api-key KEY] [--pause 1]

Asks the OpenAlex REST API for 100 works per request
(filter=openalex:W1|W2..., select=id,referenced_works,referenced_works_count) and
saves each raw response as OUT/batch_NNNN.json.gz (default
data/<dataset>_openalex_refs/). Re-running skips saved batches. Then (re)loads the
tables work_references (one row per cited work, in OpenAlex order) and
openalex_refs_status (one row per work: found or not, reference count). These
tables are not touched by build.py, but reload them after a rebuild if the
dataset's works changed. Without --api-key requests use the anonymous shared
daily budget (about 1,000 requests per IP and day); a free key gives more.
"""
import argparse
import glob
import gzip
import json
import os
import sys
import time

import duckdb
import requests

API = "https://api.openalex.org/works"


def fetch(ids, key):
    params = {"filter": "openalex:" + "|".join(ids), "select": "id,referenced_works,referenced_works_count",
              "per-page": 100}
    if key:
        params["api_key"] = key
    for attempt in range(6):
        try:
            r = requests.get(API, params=params, timeout=120)
            if r.status_code == 200:
                return r.json()
            print(f"HTTP {r.status_code}: {r.text[:300]!r}", flush=True)
            if r.status_code in (401, 402, 403) or "budget" in r.text.lower():
                sys.exit("OpenAlex budget or key problem; re-run later or pass --api-key (saved batches are kept)")
        except (requests.RequestException, ValueError) as e:
            print(f"{type(e).__name__}: {e}", flush=True)
        time.sleep(min(10 * 2**attempt, 300))
    sys.exit("giving up")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--db", default="data/impactu.duckdb")
    ap.add_argument("--out")
    ap.add_argument("--api-key", default=os.environ.get("OPENALEX_API_KEY"))
    ap.add_argument("--pause", type=float, default=1)
    ap.add_argument("--load-only", action="store_true", help="skip downloading, just reload the tables")
    args = ap.parse_args()
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.db)), f"{args.dataset}_openalex_refs")
    os.makedirs(out, exist_ok=True)

    con = duckdb.connect(args.db, read_only=True)
    rows = con.execute("SELECT work_id, openalex_id FROM works WHERE dataset = ? AND openalex_id IS NOT NULL "
                       "ORDER BY work_id", [args.dataset]).fetchall()
    con.close()
    oa = sorted({o.rsplit("/", 1)[-1] for _, o in rows})
    batches = [oa[i:i + 100] for i in range(0, len(oa), 100)]
    print(f"{len(rows)} works with an OpenAlex id, {len(oa)} distinct ids, {len(batches)} batches", flush=True)

    if not args.load_only:
        for n, ids in enumerate(batches, 1):
            path = os.path.join(out, f"batch_{n:04d}.json.gz")
            if os.path.exists(path):
                continue
            d = fetch(ids, args.api_key)
            d = {"requested": ids, "results": d.get("results", []), "meta": {k: v for k, v in d.get("meta", {}).items() if k != "x_query"}}
            with gzip.open(path + ".tmp", "wt", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(path + ".tmp", path)
            if n % 25 == 0 or n == len(batches):
                print(f"batch {n}/{len(batches)}", flush=True)
            time.sleep(args.pause)

    found = {}
    for path in sorted(glob.glob(os.path.join(out, "batch_*.json.gz"))):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        for r in d["results"]:
            found[r["id"].rsplit("/", 1)[-1]] = r
    tmp = os.path.join(out, "_load.ndjson")
    st = os.path.join(out, "_status.ndjson")
    n_refs = 0
    with open(tmp, "w") as f, open(st, "w") as g:
        for wid, o in rows:
            oid = o.rsplit("/", 1)[-1]
            r = found.get(oid)
            refs = [x.rsplit("/", 1)[-1] for x in (r or {}).get("referenced_works") or []]
            g.write(json.dumps({"dataset": args.dataset, "work_id": wid, "openalex_id": oid, "found": r is not None,
                                "referenced_works_count": (r or {}).get("referenced_works_count")}) + "\n")
            for pos, ref in enumerate(refs, 1):
                f.write(json.dumps({"dataset": args.dataset, "work_id": wid, "position": pos, "ref_openalex_id": ref}) + "\n")
                n_refs += 1
    con = duckdb.connect(args.db)
    con.execute("BEGIN")
    con.execute("CREATE TABLE IF NOT EXISTS work_references (dataset VARCHAR, work_id VARCHAR, position INTEGER, ref_openalex_id VARCHAR)")
    con.execute("CREATE TABLE IF NOT EXISTS openalex_refs_status (dataset VARCHAR, work_id VARCHAR, openalex_id VARCHAR, "
                "found BOOLEAN, referenced_works_count INTEGER, fetched_at TIMESTAMP)")
    con.execute("DELETE FROM work_references WHERE dataset = ?", [args.dataset])
    con.execute("DELETE FROM openalex_refs_status WHERE dataset = ?", [args.dataset])
    if n_refs:
        con.execute(f"INSERT INTO work_references SELECT * FROM read_json('{tmp}', format='newline_delimited', "
                    "columns={'dataset':'VARCHAR','work_id':'VARCHAR','position':'INTEGER','ref_openalex_id':'VARCHAR'})")
    con.execute(f"INSERT INTO openalex_refs_status SELECT *, now() FROM read_json('{st}', format='newline_delimited', "
                "columns={'dataset':'VARCHAR','work_id':'VARCHAR','openalex_id':'VARCHAR','found':'BOOLEAN','referenced_works_count':'INTEGER'})")
    con.execute("COMMIT")
    s = con.execute("SELECT count(*), count(*) FILTER (found), count(*) FILTER (referenced_works_count > 0) "
                    "FROM openalex_refs_status WHERE dataset = ?", [args.dataset]).fetchone()
    con.close()
    os.remove(tmp)
    os.remove(st)
    print(f"{s[0]} works checked, {s[1]} found in OpenAlex, {s[2]} with references, {n_refs} reference rows")
    if s[1] < s[0]:
        print(f"WARNING: {s[0] - s[1]} OpenAlex ids not returned (merged/deleted in OpenAlex, or batches still missing)")


if __name__ == "__main__":
    main()
