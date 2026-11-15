"""Compare a built database with the totals the API reported during the crawl.

Usage:
    python check.py [OUT_DIR]        # default ./colombia; reads OUT_DIR/state.json and OUT_DIR/<name>.duckdb

Prints each dataset and the number of distinct works against the sum of the API totals of the
le100 and gt100 parts. Exits with status 1 when works are missing.
"""
import argparse
import json
import os
import sys

import duckdb


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?", default="colombia")
    ap.add_argument("--db", help="default OUT_DIR/<name of OUT_DIR>.duckdb")
    args = ap.parse_args()

    state = json.load(open(os.path.join(args.out, "state.json")))
    con = duckdb.connect(args.db or os.path.join(args.out, os.path.basename(os.path.abspath(args.out)) + ".duckdb"), read_only=True)
    con.sql("SELECT dataset, n_files, n_works, n_duplicates, api_count, built_at FROM datasets ORDER BY dataset").show()
    total = sum(p.get("total", 0) for p in state.values() if isinstance(p, dict))
    got = con.execute("SELECT count(DISTINCT work_id) FROM works").fetchone()[0]
    print(f"distinct works {got:,} of {total:,} reported by the API")
    if got < total:
        sys.exit(f"{total - got:,} works missing: rerun the crawl and the build")


if __name__ == "__main__":
    main()
