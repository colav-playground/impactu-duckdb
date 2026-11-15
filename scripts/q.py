"""Run SQL against the ImpactU DuckDB database and print or export the result.

Usage:
    python q.py "SELECT ..." [--db data/impactu.duckdb] [--csv out.csv | --parquet out.parquet] [--rows 40]
    python q.py -f query.sql ...
    python q.py --schema            # list tables and columns
"""
import argparse
import sys

import duckdb


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sql", nargs="?")
    ap.add_argument("-f", "--file")
    ap.add_argument("--db", default="data/impactu.duckdb")
    ap.add_argument("--csv")
    ap.add_argument("--parquet")
    ap.add_argument("--rows", type=int, default=40)
    ap.add_argument("--schema", action="store_true")
    args = ap.parse_args()

    con = duckdb.connect(args.db, read_only=True)
    if args.schema:
        for (t,) in con.execute("SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall():
            cols = con.execute("SELECT column_name || ' ' || data_type FROM information_schema.columns "
                               "WHERE table_name = ? ORDER BY ordinal_position", [t]).fetchall()
            n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            print(f"{t} ({n} rows): " + ", ".join(c for (c,) in cols))
        return
    sql = open(args.file).read() if args.file else args.sql
    if not sql:
        sys.exit("give SQL or -f FILE")
    rel = con.sql(sql)
    if args.csv:
        rel.write_csv(args.csv)
        print(f"wrote {args.csv}")
    elif args.parquet:
        rel.write_parquet(args.parquet)
        print(f"wrote {args.parquet}")
    else:
        rel.show(max_rows=args.rows, max_width=250)


if __name__ == "__main__":
    main()
