# Example queries

Run them with `python scripts/q.py --db colombia/colombia.duckdb "SQL"` (add `--csv out.csv` or
`--parquet out.parquet` to export, `-f file.sql` for long queries), or open the database in any DuckDB client.
`ALL` below means every dataset of the Colombia build; filter `dataset` to use a subset.

Works per year and ImpactU type:

```sql
SELECT year, impactu_type, count(DISTINCT work_id) n
FROM works WHERE year BETWEEN 2015 AND 2025
GROUP BY ALL ORDER BY year, n DESC;
```

Universidad de Antioquia (ROR `03bp5hc83`) articles with at least one co-author affiliated in Spain:

```sql
WITH udea AS (SELECT DISTINCT work_id FROM affiliations WHERE institution_id = '03bp5hc83'),
     es   AS (SELECT DISTINCT work_id FROM affiliations WHERE country_code = 'ES' AND level = 'institution')
SELECT DISTINCT w.work_id, w.year, w.title, w.source_name, w.scimago_quartile, w.citations_openalex
FROM works w JOIN udea USING (work_id) JOIN es USING (work_id)
WHERE w.impactu_type = 'Artículo de revista'
ORDER BY w.citations_openalex DESC NULLS LAST;
```

Top partner countries of Colombian institutions per year:

```sql
SELECT w.year, a.country_code, count(DISTINCT w.work_id) n
FROM works w JOIN affiliations a USING (dataset, work_id)
WHERE a.level = 'institution' AND a.country_code <> 'CO' AND w.year >= 2015
GROUP BY w.year, a.country_code
QUALIFY row_number() OVER (PARTITION BY w.year ORDER BY n DESC) <= 5
ORDER BY w.year, n DESC;
```

Quartile distribution of journal articles by year:

```sql
PIVOT (SELECT DISTINCT work_id, year, coalesce(nullif(scimago_quartile, '-'), 'none') q
       FROM works WHERE impactu_type = 'Artículo de revista' AND year >= 2015)
ON q USING count(*) GROUP BY year ORDER BY year;
```

Products of one research group, most cited first:

```sql
SELECT DISTINCT w.work_id, w.year, w.title, w.citations_openalex
FROM works w JOIN work_groups g USING (dataset, work_id)
WHERE g.group_id = 'COL0008423'
ORDER BY w.citations_openalex DESC NULLS LAST;
```

A keyword in titles or abstracts (DuckDB scans the text; no index needed):

```sql
SELECT DISTINCT w.work_id, w.year, w.title
FROM works w LEFT JOIN work_abstracts ab USING (dataset, work_id)
WHERE w.title ILIKE '%dengue%' OR ab.abstract ILIKE '%dengue%';
```

Notes: DuckDB's JSON operators bind more loosely than `IS NULL`, so write `(j->>'x') IS NOT NULL`; and
`QUALIFY` needs explicit `GROUP BY` columns (not `GROUP BY ALL`).
