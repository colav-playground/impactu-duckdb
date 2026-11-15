# ImpactU in DuckDB

Download **every research product** of [ImpactU](https://impactu.colav.co), Colombia's open research information
system built by Colav at the Universidad de Antioquia, from its public API, and load it into a
[DuckDB](https://duckdb.org) database you can query with SQL on a laptop.

The release of 2026-11-08 holds **2,891,978 works**, the API's full total, with 14.8 million authorships,
16.3 million affiliation rows, 1.5 million abstracts, journal quartiles at the publication date and 7,625
Minciencias research groups.

Fork this repository to make your own snapshot of a later ImpactU release, of another ImpactU API, or of one
institution only. Every step can be interrupted and resumed.

## Contents

| Path | What it does |
| --- | --- |
| [`scripts/reproduce.sh`](scripts/reproduce.sh) | The whole pipeline: crawl, full author lists, build, check |
| [`scripts/crawl_all.py`](scripts/crawl_all.py) | Downloads all of `/search/works`, one publication year at a time, as raw gzipped pages |
| [`scripts/crawl.py`](scripts/crawl.py) | Downloads one products list (an institution, faculty, group, person or journal) |
| [`scripts/fetch_authors.py`](scripts/fetch_authors.py) | Gets the full author list of works whose list record was cut to 100 authors |
| [`scripts/build.py`](scripts/build.py) | Flattens the raw pages into DuckDB tables ([schema](docs/schema.md)) |
| [`scripts/check.py`](scripts/check.py) | Compares the database with the totals the API reported |
| [`scripts/q.py`](scripts/q.py) | Runs SQL and prints it or exports CSV/Parquet ([examples](docs/queries.md)) |
| [`scripts/openalex_refs.py`](scripts/openalex_refs.py) | Optional: adds OpenAlex reference lists |
| [`scripts/release.sh`](scripts/release.sh) | Packs a build for sharing (tars, zstd database, checksums) and uploads it with rclone |

## Quick start: use a published release

1. Download `colombia.duckdb.zst` from a release (see [RELEASES.md](RELEASES.md)) and unpack it:

   ```bash
   zstd -d colombia.duckdb.zst
   ```

2. Install the Python packages:

   ```bash
   pip install -r requirements.txt
   ```

3. Query:

   ```bash
   python scripts/q.py --db colombia.duckdb "SELECT year, count(DISTINCT work_id) n FROM works WHERE year >= 2020 GROUP BY year ORDER BY year"
   python scripts/q.py --db colombia.duckdb --schema
   ```

The database was written by DuckDB 2.0, so use DuckDB 2.0 or later (Python, CLI, R or any other client).

## Reproduce the full download

You need Python 3.10+, about **20 GB of free disk** (4.5 GB of raw pages, 4.5 GB of database, temporary
files while building) and a machine that can stay on for about a week.

```bash
git clone https://github.com/<you>/impactu-duckdb.git && cd impactu-duckdb
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PY=.venv/bin/python nohup scripts/reproduce.sh colombia > colombia.log 2>&1 &
tail -f colombia.log
```

If it stops (reboot, network, server outage), run the same `reproduce.sh` command again. It continues
from the first page that is not on disk. Pages are written atomically, so a half-written page is never kept,
and server errors are retried with waits of up to 15 minutes. Only one crawl can run per folder.

What it does, and how long each step took in 2026:

1. **Crawl (5–6 days).** `crawl_all.py` splits the products into four parts in `colombia/`:

   | Folder | Contents |
   | --- | --- |
   | `le100/` | works with ≤100 authors, one year at a time (`y2015_page_0001.json.gz`, 250 per page) |
   | `gt100/` | works with >100 authors, 100 per page |
   | `le100_noyear/`, `gt100_noyear/` | works with no publication year |
   | `state.json` | counts of every slice and what is finished |

   It works one year at a time because on the full list every request costs 30–85 s of database time, and on
   one year it costs 2–15 s. About a million works have no year (mostly Minciencias-only records). The years
   filter can't select them, so they are paged from the whole list sorted `year_asc`, where they come first.
   `python scripts/crawl_all.py --plan` only counts the slices.
2. **Full author lists (a few hours).** Lists return only the first 100 authors of a work. `fetch_authors.py`
   calls `/work/{id}/authors` for each of the ~6,000 works with more, saving `gt100_authors/<id>.json.gz`.
3. **Build (about 1 hour).** `build.py` loads each part as a dataset (`colombia_le100`, ...) into
   `colombia/colombia.duckdb`. It drops repeated works, puts the full author lists back and warns about missing
   works.
4. **Check.** `check.py` compares distinct works with the API's totals and exits with an error if any are missing.

Settings, as environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `IMPACTU_API_BASE` | `https://api.dev.impactu.colav.co` | the API to download from |
| `PAUSE` | `3` | seconds between requests |
| `PY` | `python3` | Python with `duckdb` and `requests` |

The same variables work for the individual scripts. Please keep the pause: the API is a shared public
service, and very heavy requests have taken the dev server down for minutes.

## Download a subset

For one institution, faculty, research group, person or journal, crawl its products list instead (minutes to
hours instead of days):

```bash
# Universidad de Antioquia (ROR 03bp5hc83), works with at most 100 authors
python scripts/crawl.py /affiliation/institution/03bp5hc83/research/products data/udea_le100
# ... and with more than 100 authors (lists cut to 100, so fetch the full lists)
python scripts/crawl.py /affiliation/institution/03bp5hc83/research/products data/udea_gt100 --authors-count 101-
python scripts/fetch_authors.py data/udea_gt100
python scripts/build.py data/udea_le100 --dataset udea_le100 --db data/udea.duckdb
python scripts/build.py data/udea_gt100 --dataset udea_gt100 --db data/udea.duckdb
```

Other routes: `/affiliation/{faculty|department|group}/{id}/research/products`,
`/person/{id}/research/products`, `/source/{id}/products`. Add filters with `--param key=value` (for example
`--param years=2020,2025`). Find ids with the API's completer (`/completer/person/{text}`, ...) or on
impactu.colav.co. The [API documentation](https://help.impactu.colav.co) lists every route and filter.

## Publish your own release

```bash
scripts/release.sh colombia impactu-dev-YYYY-MM-DD                  # pack into releases/impactu-dev-YYYY-MM-DD/
scripts/release.sh colombia impactu-dev-YYYY-MM-DD gdrive:impactu   # ... and upload with rclone
```

It writes one `.tar` per raw folder (the pages are already gzipped), the database compressed with zstd,
`state.json` and `SHA256SUMS`. Any [rclone](https://rclone.org) remote works: Google Drive, S3, Dropbox, ...
For a Google Drive folder someone shared with you, set `RCLONE_DRIVE_ROOT_FOLDER_ID` to the id in the folder's
URL and use `gdrive:` as the destination. For a permanent, citable copy, Zenodo accepts uploads of up to 50 GB.
Then add a row to [RELEASES.md](RELEASES.md).

## Caveats

- **Which API.** The routes used here are the ones of the dev API, which serves the same data as the
  ImpactU website. Production (`api.impactu.colav.co`) still has different routes (`/app/...`) and ids; point
  `IMPACTU_API_BASE` there only once it serves the same routes.
- **The data change while you download.** A crawl takes days. Works added, merged or re-dated during the crawl
  can be missed or repeated. `build.py` drops repeats, and `check.py` reports the gap against the totals counted
  at the start.
- **No-year pages.** The no-year folders also contain a few works that have a year, which are in the year
  folders too, so count `DISTINCT work_id` across datasets.
- **Shared ids.** The API's product id is the first external id (often the DOI), and a few ids belong to several
  products. They are all kept: `work_id` gets `#2`, `#3`, ... and `api_id` keeps the API's id
  ([schema](docs/schema.md)). `fetch_authors.py` can only fetch one author list per id; `build.py` uses it only
  for the product it matches.
- **Citations.** OpenAlex and Scholar counts differ, and both are valid. Say which one you used.
- **Empty fields.** References, licences, SDGs and similar fields are still pending in ImpactU's ETL. Read an
  empty value as "not recorded in ImpactU", not "none".
- **What counts as an article.** It depends on the source of the type. `impactu_type = 'Artículo de revista'`
  is the broadest. `openalex_type = 'article'` excludes reviews. `scienti_code LIKE '11%'` is what researchers
  registered in CvLAC. State which one you use.
- **Personal data.** Records include author names, sex as recorded, ORCID and CvLAC ids, and HR categories for
  institutions that sent HR files. National ID numbers are not part of the API. Respect ImpactU's terms and
  those of its sources (OpenAlex, Minciencias, Google Scholar, Scimago, Publindex) when you redistribute data.

## Development

```bash
.venv/bin/pip install -e ".[test]"
.venv/bin/ruff check scripts tests && .venv/bin/pytest -q
```

The tests build a database from three real works in `tests/fixtures/` and need no network. The
**smoke test (live API)** workflow, which you start by hand in the Actions tab, crawls one researcher's
products, fetches their full author lists, builds and queries them. Run it before starting a long crawl
against a new API release. If the API changes a field, update `SCHEMA` and `flatten()` in `scripts/build.py`
and rebuild from the raw pages, with no new download.

## License

Code: [MIT](LICENSE). The data belong to ImpactU and its sources; this repository contains no data.
