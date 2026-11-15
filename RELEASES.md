# Releases

Each release is a folder with the raw pages (one `.tar` per part), the database (`colombia.duckdb.zst`),
`state.json`, the logs and `SHA256SUMS`. Check a download with `sha256sum -c SHA256SUMS`.

| Release | API | Downloaded | Works | Database | Where |
| --- | --- | --- | ---: | --- | --- |
| `impactu-dev-2026-11-08` | `api.dev.impactu.colav.co` | lists 2026-10-26 to 11-05, full author lists 11-08 | 2,891,978 | DuckDB 2.0.0, 4.5 GB (1.3 GB zstd) | Google Drive (link on request) |

## impactu-dev-2026-11-08

- Datasets: `colombia_le100` (1,881,861 works with ≤100 authors and a year), `colombia_le100_noyear`
  (1,004,250), `colombia_gt100` (5,889 with >100 authors), `colombia_gt100_noyear` (10). 2,891,978 distinct
  works = the API total at the start of the crawl (2,886,080 + 5,898).
- All works have their full author list (5,899 lists fetched from `/work/{id}/authors`).
- `work_id` is ImpactU's internal 24-character id of that time. Since November 2026 the API identifies a
  product by its first external id (DOI, OpenAlex, ScienTI, ...), so join with newer data through
  `work_external_ids`, not `work_id`.
- Built with the scripts of this repository's first commit.
- The release also has a Universidad de Antioquia subset from 2026-10-25/26 (`udea.duckdb.zst`, datasets
  `udea_le100` with 134,386 works and `udea_gt100` with 978, plus OpenAlex reference lists in
  `work_references`) with its raw pages.
