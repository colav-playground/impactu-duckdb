"""Load downloaded ImpactU work pages into a DuckDB database of flat tables.

Usage:
    python build.py PAGES_DIR --dataset NAME [--db data/impactu.duckdb] [--no-abstracts] [--authors-dir DIR]

PAGES_DIR holds API list responses saved by crawl.py (page_NNNN.json[.gz], each
{"data": [...], "meta": {...}}); a single work response (a JSON object with "id"
and "authors") also works. The dataset's rows are replaced on every run, so
re-running after more pages arrive just refreshes it. Works repeated across pages
are kept once (first occurrence).

Product lists now return only the first 100 authors of a work (authors_count keeps
the total). Full lists saved by fetch_authors.py in PAGES_DIR_authors/<quoted id>.json.gz
(or --authors-dir) replace the cut ones; works still cut are counted in a warning.
"""
import argparse
import datetime as dt
import glob
import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from urllib.parse import quote

import duckdb

COT = dt.timezone(dt.timedelta(hours=-5))

# table -> ordered {column: DuckDB type}
SCHEMA = {
    "works": {
        "dataset": "VARCHAR", "work_id": "VARCHAR", "api_id": "VARCHAR", "title": "VARCHAR", "year": "INTEGER",
        "date_published": "DATE", "language": "VARCHAR", "doi": "VARCHAR", "openalex_id": "VARCHAR",
        "authors_count": "INTEGER", "impactu_type": "VARCHAR", "openalex_type": "VARCHAR",
        "scienti_code": "VARCHAR", "scienti_type": "VARCHAR", "minciencias_type": "VARCHAR",
        "citations_openalex": "INTEGER", "citations_scholar": "INTEGER",
        "is_oa": "BOOLEAN", "oa_status": "VARCHAR", "oa_url": "VARCHAR", "apc_paid_usd": "DOUBLE",
        "source_id": "VARCHAR", "source_name": "VARCHAR", "issn_l": "VARCHAR",
        "publisher": "VARCHAR", "publisher_country": "VARCHAR",
        "scimago_quartile": "VARCHAR", "scimago_sjr": "DOUBLE", "publindex_rank": "VARCHAR",
        "volume": "VARCHAR", "issue": "VARCHAR", "start_page": "VARCHAR", "end_page": "VARCHAR",
        "topic": "VARCHAR", "subfield": "VARCHAR", "field": "VARCHAR", "domain": "VARCHAR",
        "n_groups": "INTEGER", "page_file": "VARCHAR",
    },
    "work_types": {
        "dataset": "VARCHAR", "work_id": "VARCHAR", "source": "VARCHAR", "type": "VARCHAR",
        "code": "VARCHAR", "level": "INTEGER",
    },
    "work_external_ids": {"dataset": "VARCHAR", "work_id": "VARCHAR", "source": "VARCHAR", "id": "VARCHAR"},
    "work_citations_by_year": {"dataset": "VARCHAR", "work_id": "VARCHAR", "year": "INTEGER", "count": "INTEGER"},
    "work_topics": {
        "dataset": "VARCHAR", "work_id": "VARCHAR", "topic": "VARCHAR", "subfield": "VARCHAR",
        "field": "VARCHAR", "domain": "VARCHAR", "score": "DOUBLE",
    },
    "work_groups": {
        "dataset": "VARCHAR", "work_id": "VARCHAR", "group_id": "VARCHAR", "group_name": "VARCHAR",
        "minciencias_rank": "VARCHAR",
    },
    "work_abstracts": {"dataset": "VARCHAR", "work_id": "VARCHAR", "source": "VARCHAR", "abstract": "VARCHAR"},
    "authorships": {
        "dataset": "VARCHAR", "work_id": "VARCHAR", "position": "INTEGER", "author_id": "VARCHAR",
        "full_name": "VARCHAR", "sex": "VARCHAR", "orcid": "VARCHAR", "scienti_id": "VARCHAR",
        "openalex_author_id": "VARCHAR",
    },
    "affiliations": {
        "dataset": "VARCHAR", "work_id": "VARCHAR", "position": "INTEGER", "author_id": "VARCHAR",
        "aff_id": "VARCHAR", "aff_name": "VARCHAR", "level": "VARCHAR", "institution_id": "VARCHAR",
        "country_code": "VARCHAR", "country": "VARCHAR", "city": "VARCHAR", "aff_types": "VARCHAR[]",
        "years": "INTEGER[]",
    },
    "authors": {
        "dataset": "VARCHAR", "author_id": "VARCHAR", "full_name": "VARCHAR", "sex": "VARCHAR",
        "orcid": "VARCHAR", "scienti_id": "VARCHAR", "minciencias_category": "VARCHAR",
        "minciencias_date": "DATE", "categoria_laboral": "VARCHAR", "tipo_contrato": "VARCHAR",
        "jornada_laboral": "VARCHAR", "n_works": "INTEGER",
    },
    "journals": {
        "dataset": "VARCHAR", "source_id": "VARCHAR", "name": "VARCHAR", "issn_l": "VARCHAR",
        "issns": "VARCHAR[]", "publisher": "VARCHAR", "publisher_country": "VARCHAR",
        "types": "VARCHAR[]", "apc_charges": "DOUBLE", "apc_currency": "VARCHAR",
    },
    "journal_rankings": {
        "dataset": "VARCHAR", "source_id": "VARCHAR", "source": "VARCHAR", "rank": "VARCHAR",
        "from_date": "DATE", "to_date": "DATE", "order": "INTEGER",
    },
}


def to_date(ts):
    if ts in (None, "", -1) or not isinstance(ts, (int, float)):
        return None
    return dt.datetime.fromtimestamp(ts, COT).date().isoformat()


def by_source(items, source, key):
    for it in items or []:
        if it.get("source") == source:
            return it.get(key)
    return None


def ext_id(items, source):
    v = by_source(items, source, "id")
    return v or None


def rebuild_abstract(inv):
    if isinstance(inv, str):
        return inv or None
    if not isinstance(inv, dict) or not inv:
        return None
    pos = [(p, w) for w, ps in inv.items() for p in (ps or []) if isinstance(p, int)]
    return " ".join(w for _, w in sorted(pos)) or None


def aff_level(types):
    for t in types or []:
        if t.get("source") == "staff" and t.get("type") in ("faculty", "department"):
            return t["type"]
        if t.get("type") == "group" and t.get("source") in ("scienti", "minciencias"):
            return "group"
    return "institution"


def latest(rankings, source):
    rows = [r for r in rankings or [] if r.get("source") == source and r.get("rank")]
    if not rows:
        return None, None
    r = max(rows, key=lambda r: r.get("date") if isinstance(r.get("date"), (int, float)) else -1)
    return str(r["rank"]), to_date(r.get("date"))


def rank_at(rankings, source, ts):
    if not isinstance(ts, (int, float)) or ts < 0:
        return None
    for r in rankings or []:
        if r.get("source") != source or r.get("rank") in (None, ""):
            continue
        f, t = r.get("from_date"), r.get("to_date")
        if isinstance(f, (int, float)) and isinstance(t, (int, float)) and f <= ts <= t:
            return r["rank"]
    return None


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def flatten(w, ds, page_file, out, authors, journals):
    wid = w["id"]
    ts = w.get("date_published")
    src = w.get("source") or {}
    srank = src.get("ranking") or []
    types = w.get("types") or []
    scienti = [t for t in types if t.get("source") == "scienti" and t.get("code")]
    sc = max(scienti, key=lambda t: t.get("level") or 0) if scienti else {}
    minc = [t for t in types if t.get("source") == "minciencias"]
    mc = max(minc, key=lambda t: t.get("level") or 0) if minc else {}
    oa = w.get("open_access") or {}
    paid = (w.get("apc") or {}).get("paid") or {}
    bib = w.get("bibliographic_info") or {}
    pt = w.get("primary_topic") or {}
    pub = src.get("publisher") or {}
    title = w.get("title") or by_source(w.get("titles"), "openalex", "title") or ((w.get("titles") or [{}])[0].get("title"))
    out["works"].append({
        "dataset": ds, "work_id": wid, "api_id": w.get("api_id", wid),
        "title": title, "year": w.get("year_published") if isinstance(w.get("year_published"), int) else None,
        "date_published": to_date(ts), "language": w.get("language") or None, "doi": w.get("doi") or None,
        "openalex_id": ext_id(w.get("external_ids"), "openalex"), "authors_count": w.get("authors_count"),
        "impactu_type": by_source(types, "impactu", "type"), "openalex_type": by_source(types, "openalex", "type"),
        "scienti_code": sc.get("code"), "scienti_type": sc.get("type"), "minciencias_type": mc.get("type"),
        "citations_openalex": by_source(w.get("citations_count"), "openalex", "count"),
        "citations_scholar": by_source(w.get("citations_count"), "scholar", "count"),
        "is_oa": oa.get("is_open_access"), "oa_status": oa.get("open_access_status"), "oa_url": oa.get("url"),
        "apc_paid_usd": num(paid.get("value_usd")),
        "source_id": src.get("id"), "source_name": src.get("name"), "issn_l": ext_id(src.get("external_ids"), "issn_l"),
        "publisher": pub.get("name") if isinstance(pub, dict) else None,
        "publisher_country": pub.get("country_code") if isinstance(pub, dict) else None,
        "scimago_quartile": rank_at(srank, "scimago Best Quartile", ts), "scimago_sjr": num(rank_at(srank, "scimago", ts)),
        "publindex_rank": rank_at(srank, "publindex", ts),
        "volume": bib.get("volume") or None, "issue": bib.get("issue") or None,
        "start_page": bib.get("start_page") or None, "end_page": bib.get("end_page") or None,
        "topic": pt.get("display_name"), "subfield": (pt.get("subfield") or {}).get("display_name"),
        "field": (pt.get("field") or {}).get("display_name"), "domain": (pt.get("domain") or {}).get("display_name"),
        "n_groups": len(w.get("groups") or []), "page_file": page_file,
    })
    for t in types:
        out["work_types"].append({"dataset": ds, "work_id": wid, "source": t.get("source"), "type": t.get("type"),
                                  "code": t.get("code"), "level": t.get("level") if isinstance(t.get("level"), int) else None})
    for e in w.get("external_ids") or []:
        if e.get("id"):
            out["work_external_ids"].append({"dataset": ds, "work_id": wid, "source": e.get("source"), "id": str(e["id"])})
    for c in w.get("citations_by_year") or []:
        out["work_citations_by_year"].append({"dataset": ds, "work_id": wid, "year": c.get("year"), "count": c.get("cited_by_count")})
    for t in w.get("topics") or []:
        out["work_topics"].append({"dataset": ds, "work_id": wid, "topic": t.get("display_name"),
                                   "subfield": (t.get("subfield") or {}).get("display_name"),
                                   "field": (t.get("field") or {}).get("display_name"),
                                   "domain": (t.get("domain") or {}).get("display_name"), "score": num(t.get("score"))})
    for g in w.get("groups") or []:
        out["work_groups"].append({"dataset": ds, "work_id": wid, "group_id": g.get("id"), "group_name": g.get("name"),
                                   "minciencias_rank": latest(g.get("ranking"), "minciencias")[0]})
    if out.get("work_abstracts") is not None:
        for a in w.get("abstracts") or []:
            text = rebuild_abstract(a.get("abstract"))
            if text:
                out["work_abstracts"].append({"dataset": ds, "work_id": wid, "source": a.get("source"), "abstract": text})
        if not w.get("abstracts") and w.get("abstract"):
            text = rebuild_abstract(w["abstract"])
            if text:
                out["work_abstracts"].append({"dataset": ds, "work_id": wid, "source": "openalex", "abstract": text})

    for pos, a in enumerate(w.get("authors") or [], 1):
        aid = a.get("id") or None
        orcid = ext_id(a.get("external_ids"), "orcid")
        scid = ext_id(a.get("external_ids"), "scienti")
        out["authorships"].append({"dataset": ds, "work_id": wid, "position": pos, "author_id": aid,
                                   "full_name": a.get("full_name"), "sex": a.get("sex") or None, "orcid": orcid,
                                   "scienti_id": scid, "openalex_author_id": ext_id(a.get("external_ids"), "openalex")})
        for f in a.get("affiliations") or []:
            addr = (f.get("addresses") or [{}])[0] or {}
            level = aff_level(f.get("types"))
            fid = f.get("id") or None
            out["affiliations"].append({
                "dataset": ds, "work_id": wid, "position": pos, "author_id": aid, "aff_id": fid,
                "aff_name": f.get("name"), "level": level,
                "institution_id": fid.split("_")[0] if fid and level in ("faculty", "department") else (fid if level == "institution" else None),
                "country_code": addr.get("country_code") or None, "country": addr.get("country") or None,
                "city": addr.get("city") or None,
                "aff_types": sorted({str(t.get("type")) for t in f.get("types") or [] if t.get("type")}),
                "years": [y for y in f.get("years") or [] if isinstance(y, int)],
            })
        if aid:
            rec = authors.get(aid)
            if rec is None:
                cat, cdate = latest(a.get("ranking"), "minciencias")
                rec = authors[aid] = {
                    "dataset": ds, "author_id": aid, "full_name": a.get("full_name"), "sex": a.get("sex") or None,
                    "orcid": orcid, "scienti_id": scid, "minciencias_category": cat, "minciencias_date": cdate,
                    "categoria_laboral": latest(a.get("ranking"), "categoría_laboral")[0],
                    "tipo_contrato": latest(a.get("ranking"), "tipo_contrato")[0],
                    "jornada_laboral": latest(a.get("ranking"), "jornada_laboral")[0], "n_works": 0,
                }
            rec["n_works"] += 1

    sid = src.get("id")
    if sid and sid not in journals:
        apc = src.get("apc") or {}
        journals[sid] = {
            "dataset": ds, "source_id": sid, "name": src.get("name"), "issn_l": ext_id(src.get("external_ids"), "issn_l"),
            "issns": sorted({str(e["id"]) for e in src.get("external_ids") or [] if e.get("source") == "issn" and e.get("id")}),
            "publisher": pub.get("name") if isinstance(pub, dict) else None,
            "publisher_country": pub.get("country_code") if isinstance(pub, dict) else None,
            "types": sorted({f"{t.get('source')}:{t.get('type')}" for t in src.get("types") or []}),
            "apc_charges": num(apc.get("charges")), "apc_currency": apc.get("currency"),
        }
        seen = set()
        for r in srank:
            row = (r.get("source"), str(r.get("rank")) if r.get("rank") not in (None, "") else None,
                   to_date(r.get("from_date")), to_date(r.get("to_date")), r.get("order") if isinstance(r.get("order"), int) else None)
            if row not in seen:
                seen.add(row)
                out["journal_rankings"].append(dict(zip(("source", "rank", "from_date", "to_date", "order"), row),
                                                    dataset=ds, source_id=sid))


def page_files(path):
    if os.path.isfile(path):
        return [path]
    files = glob.glob(os.path.join(path, "*page_*.json.gz")) + glob.glob(os.path.join(path, "*page_*.json"))
    return sorted(files, key=lambda f: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", os.path.basename(f))])


def signature(w):
    """What tells apart two products that the API gives the same id (since November 2026 the id is the
    product's first external id, e.g. a DOI, and a few DOIs or handles belong to several products)."""
    key = json.dumps([sorted(json.dumps(e, sort_keys=True) for e in w.get("external_ids") or []), w.get("title"), w.get("titles")],
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(key.encode()).hexdigest()


def same_start(full, cut):
    """True when a full author list from /work/{id}/authors begins with the record's cut list (same product)."""
    ids = [a.get("id") or a.get("full_name") for a in cut]
    return [a.get("id") or a.get("full_name") for a in full[:len(ids)]] == ids


def authors_file(adir, wid):
    """File of a work's full author list. Ids can be DOIs or handles with slashes, so they are percent-encoded."""
    return os.path.join(adir, quote(str(wid), safe="") + ".json.gz")


def read_json(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")) as f:
        return json.load(f)


def crawl_all_expected(pages):
    """Expected works for a part made by crawl_all.py, from the state.json next to it: the sum of the
    year slices for PART/, or noyear_expected for PART_noyear/ (a count of works with no year, since the
    year_asc pages of that part also hold a few works with a year). None when there is no such state."""
    if not os.path.isdir(pages):
        return None
    pages = os.path.abspath(pages.rstrip("/"))
    spath = os.path.join(os.path.dirname(pages), "state.json")
    if not os.path.exists(spath):
        return None
    state, part = read_json(spath), os.path.basename(pages)
    if part.endswith("_noyear") and isinstance(state.get(part[:-7]), dict):
        n = state[part[:-7]].get("noyear_expected")
        return ("noyear", n) if n is not None else None
    if isinstance(state.get(part), dict) and isinstance(state[part].get("slices"), dict):
        return ("year", sum(s.get("count", 0) for s in state[part]["slices"].values()))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pages", help="directory with page_*.json[.gz] files, or one JSON file")
    ap.add_argument("--dataset", required=True, help="short name stored in every row, e.g. udea_le100")
    ap.add_argument("--db", default="data/impactu.duckdb")
    ap.add_argument("--no-abstracts", action="store_true", help="skip the work_abstracts table")
    ap.add_argument("--authors-dir", help="full author lists from fetch_authors.py (default PAGES_DIR_authors)")
    args = ap.parse_args()

    files = page_files(args.pages)
    if not files:
        raise SystemExit(f"no JSON pages in {args.pages}")
    manifest = {}
    mpath = os.path.join(args.pages, "manifest.json") if os.path.isdir(args.pages) else None
    if mpath and os.path.exists(mpath):
        manifest = read_json(mpath)

    tmp = tempfile.mkdtemp(prefix="impactu_build_")
    handles = {t: gzip.open(os.path.join(tmp, f"{t}.ndjson.gz"), "wt", encoding="utf-8") for t in SCHEMA}
    seen, authors, journals = {}, {}, {}  # api id -> signatures of the distinct products with that id
    adir = args.authors_dir or (args.pages.rstrip("/") + "_authors" if os.path.isdir(args.pages) else "")
    n_full = n_cut = 0
    n_raw = n_dup = n_noyear = n_shared = 0
    meta_count = None
    # crawl_all.py pages each carry the count of their own year slice, not of the part
    sliced = crawl_all_expected(args.pages)
    t0 = time.time()

    def n_works():
        return sum(len(v) for v in seen.values())

    try:
        for i, path in enumerate(files, 1):
            d = read_json(path)
            items = d.get("data") if isinstance(d, dict) and "data" in d else ([d] if isinstance(d, dict) and "id" in d else d)
            if isinstance(d, dict) and isinstance(d.get("meta"), dict):
                meta_count = d["meta"].get("count", meta_count)
            out = {t: [] for t in SCHEMA if t not in ("authors", "journals")}
            if args.no_abstracts:
                out["work_abstracts"] = None
            for w in items or []:
                n_raw += 1
                sigs, sig = seen.setdefault(w.get("id"), []), signature(w)
                if sig in sigs:
                    n_dup += 1
                    continue
                sigs.append(sig)
                if len(sigs) > 1:  # another product with the same API id: keep it, under id#2, id#3, ...
                    w["api_id"], w["id"] = w.get("id"), f"{w.get('id')}#{len(sigs)}"
                    n_shared += 1
                if not isinstance(w.get("year_published"), int):
                    n_noyear += 1
                if (w.get("authors_count") or 0) > len(w.get("authors") or []):
                    fa = authors_file(adir, w.get("api_id", w.get("id"))) if adir else ""
                    full = (read_json(fa).get("data") or []) if fa and os.path.exists(fa) else []
                    if full and same_start(full, w.get("authors") or []):
                        w["authors"] = full
                        n_full += 1
                    if (w.get("authors_count") or 0) > len(w.get("authors") or []):
                        n_cut += 1
                flatten(w, args.dataset, os.path.basename(path), out, authors, journals)
            for t, rows in out.items():
                for r in rows or []:
                    handles[t].write(json.dumps(r, ensure_ascii=False) + "\n")
            if i % 50 == 0 or i == len(files):
                print(f"{i}/{len(files)} files, {n_works()} works, {time.time() - t0:.0f}s", flush=True)
        for r in authors.values():
            handles["authors"].write(json.dumps(r, ensure_ascii=False) + "\n")
        for r in journals.values():
            handles["journals"].write(json.dumps(r, ensure_ascii=False) + "\n")
        for h in handles.values():
            h.close()

        os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
        con = duckdb.connect(args.db)
        con.execute("""CREATE TABLE IF NOT EXISTS datasets (dataset VARCHAR PRIMARY KEY, pages_dir VARCHAR,
            api_path VARCHAR, params VARCHAR, n_files INTEGER, n_works INTEGER, n_duplicates INTEGER,
            api_count INTEGER, built_at TIMESTAMP)""")
        con.execute("BEGIN")
        for t, cols in SCHEMA.items():
            coldefs = ", ".join(f'"{c}" {ty}' for c, ty in cols.items())
            con.execute(f"CREATE TABLE IF NOT EXISTS {t} ({coldefs})")
            con.execute(f"DELETE FROM {t} WHERE dataset = ?", [args.dataset])
            f = os.path.join(tmp, f"{t}.ndjson.gz")
            if os.path.getsize(f) > 40:
                colspec = "{" + ", ".join(f"'{c}': '{ty}'" for c, ty in cols.items()) + "}"
                con.execute(f"INSERT INTO {t} SELECT * FROM read_json('{f}', format='newline_delimited', columns={colspec})")
        api = manifest.get("count", sliced[1] if sliced else meta_count)
        con.execute("DELETE FROM datasets WHERE dataset = ?", [args.dataset])
        con.execute("INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?, ?, ?, now())",
                    [args.dataset, os.path.abspath(args.pages), manifest.get("api_path"),
                     json.dumps(manifest.get("params")) if manifest.get("params") else None,
                     len(files), n_works(), n_dup, api])
        con.execute("COMMIT")
        for t in SCHEMA:
            n = con.execute(f"SELECT count(*) FROM {t} WHERE dataset = ?", [args.dataset]).fetchone()[0]
            print(f"  {t}: {n}")
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"dataset {args.dataset}: {n_works()} unique works from {n_raw} records ({n_dup} duplicates), "
          f"{'expected (state.json)' if sliced and 'count' not in manifest else 'API count'} {api}, "
          f"{time.time() - t0:.0f}s -> {args.db}")
    if n_shared:
        print(f"{n_shared} products share their API id with another product; they are kept as <id>#2, #3, ... (api_id keeps the id)")
    if n_full:
        print(f"{n_full} cut author lists replaced by full lists from {adir}")
    if n_cut:
        print(f"WARNING: {n_cut} works keep a cut author list (first 100 only); run fetch_authors.py {args.pages} and rebuild")
    got = n_works()
    if sliced and sliced[0] == "noyear" and "count" not in manifest:
        got = n_noyear
        if n_works() > n_noyear:
            print(f"{n_works() - n_noyear} works with a year came with the year-less pages; "
                  f"they are also in the year part, so count distinct work_id across datasets")
    if api and got < api:
        print(f"WARNING: {api - got} works missing (crawl incomplete or pages shifted)")


if __name__ == "__main__":
    main()
