# Database schema

`build.py` flattens each work of the API into these tables. Every table has a `dataset` column (one per
downloaded part, e.g. `colombia_le100`). **Always filter or join on `dataset`**; join keys are
`(dataset, work_id)`, plus `position` for author-level joins. A work can be in two datasets (see
[the no-year caveat](../README.md#caveats)), so count `DISTINCT work_id` across datasets.

## works: one row per work

| Group | Columns |
| --- | --- |
| Identity | `work_id`, `title`, `year`, `date_published` (DATE, from the epoch at Colombian midnight), `language`, `doi`, `openalex_id`, `authors_count` (the true total, even when the list was cut) |
| Types | `impactu_type` (ImpactU's unified type, e.g. `Artículo de revista`, `Libro`), `openalex_type` (`article`, `review`, ...), `scienti_code` / `scienti_type` (most specific SCIENTI level, e.g. `111`), `minciencias_type` |
| Citations and access | `citations_openalex`, `citations_scholar`, `is_oa`, `oa_status`, `oa_url`, `apc_paid_usd` |
| Venue | `source_id`, `source_name`, `issn_l`, `publisher`, `publisher_country`, `scimago_quartile`, `scimago_sjr`, `publindex_rank` (all **at the publication date**, from the journal ranking row that covers it) |
| Pages and topic | `volume`, `issue`, `start_page`, `end_page`, `topic`, `subfield`, `field`, `domain` (OpenAlex primary topic) |
| Bookkeeping | `n_groups`, `page_file` (the raw page the work came from) |

## Other tables

| Table | One row per | Columns |
| --- | --- | --- |
| `authorships` | work × author | `position` (1-based signing order), `author_id` (10 digits = SCIENTI/CvLAC; 16 digits = ORCID-derived; `A…` = OpenAlex-only), `full_name`, `sex`, `orcid`, `scienti_id`, `openalex_author_id` |
| `affiliations` | work × author × affiliation | `position`, `author_id`, `aff_id`, `aff_name`, `level` (`institution` = ROR id, `faculty` e.g. `03bp5hc83_30`, `department`, `group` = `COL…`), `institution_id` (parent ROR id; NULL for groups), `country_code` (ISO-2), `country`, `city`, `aff_types`, `years` (years the author was affiliated) |
| `authors` | author per dataset | latest Minciencias `minciencias_category` (Junior / Asociado / Sénior / Emérito) and `minciencias_date`; HR fields `categoria_laboral`, `tipo_contrato`, `jornada_laboral` (only institutions that sent HR files); `n_works` |
| `work_groups` | work × research group | `group_id`, `group_name`, `minciencias_rank` (latest) |
| `work_types` | work × per-source type | `source`, `type`, `code`, `level`; use it for your own type definitions |
| `work_external_ids` | work × external id | `source` (`openalex`, `doi`, `pmid`, `mag`, `scholar`, `dspace`, `scienti`, ...), `id` (SCIENTI: `COD_RH-COD_PROD`) |
| `work_citations_by_year` | work × year | OpenAlex `year`, `count` |
| `work_topics` | work × OpenAlex topic | `topic`, `subfield`, `field`, `domain`, `score` |
| `work_abstracts` | work × source | abstract rebuilt as plain text from the inverted index, per `source` (`openalex`, `scholar`, `dspace`, `europepmc`) |
| `journals` | venue | `name`, `issn_l`, `issns`, `publisher`, `publisher_country`, `types`, `apc_charges`, `apc_currency` |
| `journal_rankings` | venue × ranking row | `source` (`scimago Best Quartile`, `scimago Category Quartile`, `scimago` = SJR, `scimago hindex`, `publindex`, `scienti`), `rank`, `from_date`, `to_date` |
| `datasets` | dataset | pages folder, API path and parameters, files, works, duplicates, `api_count` (what the API reported), `built_at` |
| `work_references`, `openalex_refs_status` | cited work / work | OpenAlex `referenced_works` in order, loaded by `openalex_refs.py`, not by `build.py` |

Fields that are not flattened (`subjects`, `keywords`, `external_urls`, bibtex, ...) can be read from the raw
pages with DuckDB:

```sql
WITH w AS (SELECT unnest(data) AS j
           FROM read_json('colombia/le100/y2024_page_*.json.gz', columns={'data': 'JSON[]'}))
SELECT j->>'id' AS work_id, j->'subjects' AS subjects FROM w LIMIT 10;
```

To keep such a field, add it to `SCHEMA` and `flatten()` in `scripts/build.py` and rebuild (no new download).
