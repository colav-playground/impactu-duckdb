"""Offline tests: build a database from the fixture pages and check the flattened tables."""
import json
import os
import subprocess
import sys

import duckdb
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
import build  # noqa: E402
import crawl_all  # noqa: E402

PAGES = os.path.join(HERE, "fixtures", "pages")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("db") / "test.duckdb")
    out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "build.py"), PAGES, "--dataset", "t", "--db", path],
                         capture_output=True, text=True, check=True).stdout
    assert "3 unique works from 4 records (1 duplicates)" in out
    assert "1 cut author lists replaced" in out
    con = duckdb.connect(path, read_only=True)
    yield con
    con.close()


def test_counts(db):
    assert db.execute("SELECT n_works, n_duplicates, api_count FROM datasets WHERE dataset = 't'").fetchone() == (3, 1, 3)
    assert db.execute("SELECT count(*) FROM works").fetchone()[0] == 3


def test_full_author_list_restored(db):
    n, cnt = db.execute("SELECT count(*), any_value(w.authors_count) FROM authorships a JOIN works w USING (dataset, work_id) "
                        "WHERE work_id = '6ab561fc5418a51e32652f3a'").fetchone()
    assert n == cnt == 7


def test_positions_and_affiliations(db):
    assert db.execute("SELECT min(position) FROM authorships").fetchone()[0] == 1
    levels = {r[0] for r in db.execute("SELECT DISTINCT level FROM affiliations").fetchall()}
    assert "institution" in levels and levels <= {"institution", "faculty", "department", "group"}


def test_abstracts_rebuilt(db):
    text = db.execute("SELECT abstract FROM work_abstracts LIMIT 1").fetchone()[0]
    assert isinstance(text, str) and " " in text


def test_rebuild_replaces_dataset(db, tmp_path):
    path = str(tmp_path / "again.duckdb")
    for _ in range(2):
        subprocess.run([sys.executable, os.path.join(SCRIPTS, "build.py"), PAGES, "--dataset", "t", "--db", path],
                       capture_output=True, check=True)
    con = duckdb.connect(path, read_only=True)
    assert con.execute("SELECT count(*) FROM works").fetchone()[0] == 3


def test_helpers():
    assert build.rebuild_abstract({"world": [1], "hello": [0]}) == "hello world"
    assert build.to_date(1622091600) == "2021-05-27"  # epoch at Colombian midnight
    assert build.aff_level([{"source": "staff", "type": "faculty"}]) == "faculty"
    assert build.aff_level([{"source": "scienti", "type": "group"}]) == "group"
    assert build.aff_level([{"source": "ror", "type": "education"}]) == "institution"
    ranks = [{"source": "scimago Best Quartile", "rank": "Q1", "from_date": 0, "to_date": 100}]
    assert build.rank_at(ranks, "scimago Best Quartile", 50) == "Q1"
    assert build.rank_at(ranks, "scimago Best Quartile", 150) is None


def test_year_slices_cover_everything():
    s = crawl_all.year_slices()
    assert s[0] == ("y0000", "0") and s[1] == ("y0001-1899", "1,1899") and s[-1][1].endswith(",9999")
    assert crawl_all.year_slices("2020-2021") == [("y2020", "2020"), ("y2021", "2021")]


def test_products_sharing_an_api_id_are_kept(tmp_path):
    """Since November 2026 a product's id is its first external id; a few DOIs belong to several products."""
    pages = tmp_path / "pages"
    pages.mkdir()
    a = {"id": "10.1/x", "title": "One", "external_ids": [{"source": "scienti", "id": "1-1"}], "authors": [], "authors_count": 0}
    b = {**a, "title": "Two", "external_ids": [{"source": "scienti", "id": "2-2"}]}
    (pages / "page_0001.json").write_text(json.dumps({"data": [a, b, a], "meta": {"count": 2}}))
    path = str(tmp_path / "s.duckdb")
    out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "build.py"), str(pages), "--dataset", "s", "--db", path],
                         capture_output=True, text=True, check=True).stdout
    assert "2 unique works from 3 records (1 duplicates)" in out
    con = duckdb.connect(path, read_only=True)
    assert con.execute("SELECT work_id, api_id, title FROM works ORDER BY work_id").fetchall() == [
        ("10.1/x", "10.1/x", "One"), ("10.1/x#2", "10.1/x", "Two")]


def test_full_author_list_must_match_the_record():
    cut = [{"id": "a"}, {"id": "b"}]
    assert build.same_start([{"id": "a"}, {"id": "b"}, {"id": "c"}], cut)
    assert not build.same_start([{"id": "z"}, {"id": "b"}, {"id": "c"}], cut)
