"""Tests for download_survivor_data(): how survivoR.xlsx reaches the disk.

Two gunicorn workers each run the daily refresh, so both download at the same
moment into the same file, and request handlers read it at any time. Writing
the file in place let one of them read a half-written workbook ("Truncated
file header"). It is now written to a temporary file in the same directory and
swapped in with os.replace, so the path only ever holds a complete file.

The file is built from survivoR's dev/json tables (tests/test_survivor_json.py
covers how), and nothing is written until every table has arrived, so a
failed fetch leaves yesterday's file in place.
"""

import json
import os
import pathlib
import tempfile
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

OLD_BYTES = b"the previous survivoR.xlsx"


class _FakeResponse:
    def __init__(self, status, body=b""):
        self.status_code = status
        self.content = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} from upstream")

    def json(self):
        return json.loads(self.content)


@pytest.fixture()
def target(tmp_path, monkeypatch):
    """SURVIVOR_DATA_FILE in an empty temp directory."""
    path = tmp_path / "survivoR.xlsx"
    monkeypatch.setattr("app.data.SURVIVOR_DATA_FILE", str(path))
    return path


def _leftovers(path):
    """Anything in the data directory other than the file itself."""
    return sorted(p.name for p in path.parent.iterdir() if p != path)


def _writes(body):
    """A write callback that puts `body` at the path it is given."""

    def write(path):
        with open(path, "wb") as f:
            f.write(body)

    return write


class TestWriteAtomically:
    def test_a_successful_write_replaces_the_file(self, target):
        from app.data import _write_atomically

        target.write_bytes(OLD_BYTES)

        _write_atomically(str(target), _writes(b"new"))

        assert target.read_bytes() == b"new"
        assert _leftovers(target) == []

    def test_a_write_that_fails_partway_keeps_the_previous_file(self, target):
        from app.data import _write_atomically

        target.write_bytes(OLD_BYTES)

        def half_then_fail(tmp):
            with open(tmp, "wb") as f:
                f.write(b"half a work")
            raise OSError("disk full")

        with pytest.raises(OSError, match="disk full"):
            _write_atomically(str(target), half_then_fail)

        assert target.read_bytes() == OLD_BYTES
        assert _leftovers(target) == []

    def test_a_failed_first_write_creates_no_file(self, target):
        from app.data import _write_atomically

        def fail(tmp):
            with open(tmp, "wb") as f:
                f.write(b"half")
            raise OSError("disk full")

        with pytest.raises(OSError):
            _write_atomically(str(target), fail)

        assert not target.exists()
        assert _leftovers(target) == []

    def test_the_file_arrives_whole_by_rename_from_the_same_directory(
        self, target, monkeypatch
    ):
        """A rename within one directory is atomic; a copy or a write is not."""
        from app import data

        swaps = []
        real_replace = os.replace

        def spy(src, dst):
            with open(src, "rb") as f:
                swaps.append((os.path.dirname(src), f.read(), dst))
            real_replace(src, dst)

        monkeypatch.setattr(data.os, "replace", spy)

        data._write_atomically(str(target), _writes(b"whole"))

        assert swaps == [(str(target.parent), b"whole", str(target))]

    def test_two_writers_never_share_a_temporary_file(self, target):
        """Both workers download at 08:00; each must write its own temp file."""
        from app.data import _write_atomically

        seen = []

        def inner(tmp):
            seen.append(tmp)
            _writes(b"inner")(tmp)

        def outer(tmp):
            seen.append(tmp)
            _write_atomically(str(target), inner)
            _writes(b"outer")(tmp)

        _write_atomically(str(target), outer)

        assert len(set(seen)) == 2
        assert target.read_bytes() == b"outer"
        assert _leftovers(target) == []


# The fixture tables are survivoR's own dev/json files, trimmed (see
# tests/test_survivor_json.py for what they hold).
FIXTURE_JSON = (
    pathlib.Path(__file__).resolve().parent / "fixtures" / "survivoR" / "json-c720d36"
)
JSON_BASE = "https://raw.githubusercontent.com/doehm/survivoR/master/dev/json/"


@pytest.fixture()
def upstream(monkeypatch):
    """Serve survivoR's dev/json tables from the fixtures, by URL.

    `served[url]` can be replaced with an int (an HTTP status), an exception
    to raise, or other bytes. `calls` lists the URLs requested, in order.
    """
    from app.data import SURVIVOR_TABLES

    served = {
        f"{JSON_BASE}{table}.json": (FIXTURE_JSON / f"{table}.json").read_bytes()
        for table in SURVIVOR_TABLES.values()
    }
    calls = []

    def fake_get(url, timeout=None, **_kwargs):
        calls.append(url)
        body = served.get(url, 404)
        if isinstance(body, Exception):
            raise body
        if isinstance(body, int):
            return _FakeResponse(body)
        return _FakeResponse(200, body)

    monkeypatch.setattr("app.data.requests.get", fake_get)
    return SimpleNamespace(served=served, calls=calls)


def _url(table):
    return f"{JSON_BASE}{table}.json"


def _sheet_names(path):
    with pd.ExcelFile(path) as book:
        return sorted(book.sheet_names)


class TestDownloadSurvivorData:
    def test_fetches_each_table_the_app_reads_once(self, target, upstream):
        from app import data

        data.download_survivor_data()

        assert upstream.calls == [
            _url(table) for table in data.SURVIVOR_TABLES.values()
        ]

    def test_writes_every_sheet_into_survivor_xlsx(self, target, upstream):
        from app import data

        data.download_survivor_data()

        assert _sheet_names(target) == sorted(data.SURVIVOR_TABLES)
        castaways = pd.read_excel(target, "Castaways")
        assert set(castaways["season"]) == {50, 51}

    def test_a_download_replaces_the_previous_file(self, target, upstream):
        from app import data

        target.write_bytes(OLD_BYTES)

        data.download_survivor_data()

        assert target.read_bytes() != OLD_BYTES
        assert "Castaways" in _sheet_names(target)
        assert _leftovers(target) == []

    @pytest.mark.parametrize(
        "failure",
        [404, 503, requests.ConnectionError("reset"), requests.Timeout("slow")],
        ids=["404", "503", "connection-error", "timeout"],
    )
    def test_a_failed_fetch_keeps_the_previous_file(self, target, upstream, failure):
        from app import data

        target.write_bytes(OLD_BYTES)
        upstream.served[_url("tribe_mapping")] = failure

        with pytest.raises(requests.RequestException):
            data.download_survivor_data()

        assert target.read_bytes() == OLD_BYTES
        assert _leftovers(target) == []

    @pytest.mark.parametrize(
        "body",
        [b"[]", b"{}", b'["not a row"]', b"<html>rate limited</html>"],
        ids=["empty-list", "object", "list-of-strings", "not-json"],
    )
    def test_a_table_that_is_not_rows_keeps_the_previous_file(
        self, target, upstream, body
    ):
        from app import data

        target.write_bytes(OLD_BYTES)
        upstream.served[_url("castaways")] = body

        with pytest.raises(ValueError):
            data.download_survivor_data()

        assert target.read_bytes() == OLD_BYTES
        assert _leftovers(target) == []

    @pytest.mark.parametrize(
        ("failure", "error"),
        [(503, requests.HTTPError), (b"{}", ValueError)],
        ids=["fetch-fails", "not-rows"],
    )
    def test_nothing_is_written_until_every_table_has_arrived(
        self, target, upstream, monkeypatch, failure, error
    ):
        """A failure on the last table must not have started a write."""
        from app import data

        writes = []
        monkeypatch.setattr(data, "_write_atomically", lambda *a: writes.append(a))
        last = list(data.SURVIVOR_TABLES.values())[-1]
        upstream.served[_url(last)] = failure

        with pytest.raises(error):
            data.download_survivor_data()

        assert writes == []

    def test_a_failed_first_download_creates_no_file(self, target, upstream):
        from app import data

        upstream.served[_url("episodes")] = 503

        with pytest.raises(requests.HTTPError):
            data.download_survivor_data()

        assert not target.exists()
        assert _leftovers(target) == []

    def test_the_download_goes_through_the_atomic_swap(
        self, target, upstream, monkeypatch
    ):
        from app import data

        swaps = []
        real_replace = os.replace

        def spy(src, dst):
            assert os.path.dirname(src) == os.path.dirname(dst)
            assert _sheet_names(src) == sorted(data.SURVIVOR_TABLES)
            swaps.append(dst)
            real_replace(src, dst)

        monkeypatch.setattr(data.os, "replace", spy)

        data.download_survivor_data()

        assert swaps == [str(target)]


@pytest.fixture()
def system_temp(tmp_path_factory, monkeypatch):
    """An empty directory standing in for the system temp directory.

    openpyxl's write-only mode streams each sheet into a file of its own there
    (openpyxl.*) and zips them into the workbook on save. It deletes them on
    save, or at process exit, and a gunicorn worker runs for weeks.
    """
    path = tmp_path_factory.mktemp("system-temp")
    monkeypatch.setattr(tempfile, "tempdir", str(path))
    return path


def _with_value(upstream, table, column, value):
    """Serve `table` with `column` of its first row set to `value`."""
    records = json.loads(upstream.served[_url(table)])
    records[0][column] = value
    upstream.served[_url(table)] = json.dumps(records).encode()


# Values that pass the shape check but that the workbook cannot hold. The
# first is a date column (every other episode_date is YYYY-MM-DD), so the
# build turns it into a date, and there is no 30 February.
UNWRITABLE = [
    ("episodes", "episode_date", "2026-02-30"),
    ("castaways", "castaway", "Q\x07"),
    ("castaways", "castaway", ["Q"]),
    ("castaways", "castaway", {"name": "Q"}),
]
UNWRITABLE_IDS = ["impossible-date", "control-character", "list", "object"]


class TestAValueTheWorkbookCannotHold:
    """One bad value used to pass the check, then abort the write halfway and
    leave openpyxl's per-sheet temp files behind, every day until upstream
    fixed it. It is now refused with the tables, before any writing starts."""

    @pytest.mark.parametrize(
        ("table", "column", "value"), UNWRITABLE, ids=UNWRITABLE_IDS
    )
    def test_is_refused_before_the_workbook_is_started(
        self, target, upstream, monkeypatch, table, column, value
    ):
        from app import data

        writes = []
        monkeypatch.setattr(
            data, "write_survivor_workbook", lambda *a: writes.append(a)
        )
        _with_value(upstream, table, column, value)

        with pytest.raises(ValueError, match=f"{table}.*{column}"):
            data.download_survivor_data()

        assert writes == []

    @pytest.mark.parametrize(
        ("table", "column", "value"), UNWRITABLE, ids=UNWRITABLE_IDS
    )
    def test_keeps_the_previous_file_and_leaves_no_temp_files(
        self, target, upstream, system_temp, table, column, value
    ):
        from app import data

        target.write_bytes(OLD_BYTES)
        _with_value(upstream, table, column, value)

        with pytest.raises(ValueError):
            data.download_survivor_data()

        assert target.read_bytes() == OLD_BYTES
        assert _leftovers(target) == []
        assert sorted(p.name for p in system_temp.iterdir()) == []

    def test_the_tables_as_published_pass(self, target, upstream, system_temp):
        """The check must not refuse the real data."""
        from app import data

        data.download_survivor_data()

        assert _sheet_names(target) == sorted(data.SURVIVOR_TABLES)
        assert sorted(p.name for p in system_temp.iterdir()) == []


class TestAWriteThatFailsPartway:
    """Whatever the check above does not foresee, a failed write still
    removes openpyxl's temp files instead of leaving them to process exit."""

    def test_a_failure_between_sheets_leaves_no_temp_files(self, tmp_path, system_temp):
        from app.data import write_survivor_workbook

        def tables():
            yield "First", [{"season": 51, "castaway": "Q"}] * 50
            yield "Second", [{"season": 51}]
            raise OSError("disk full")

        with pytest.raises(OSError, match="disk full"):
            write_survivor_workbook(str(tmp_path / "book.xlsx"), tables())

        assert sorted(p.name for p in system_temp.iterdir()) == []

    def test_a_cell_openpyxl_refuses_leaves_no_temp_files(self, tmp_path, system_temp):
        """Straight to the writer, past the check: openpyxl refuses the cell."""
        from openpyxl.utils.exceptions import IllegalCharacterError

        from app.data import write_survivor_workbook

        tables = [("First", [{"castaway": "Q"}]), ("Second", [{"castaway": "Q\x07"}])]

        with pytest.raises(IllegalCharacterError):
            write_survivor_workbook(str(tmp_path / "book.xlsx"), tables)

        assert sorted(p.name for p in system_temp.iterdir()) == []

    def test_the_guard_sees_openpyxl_temp_files(self, tmp_path, system_temp):
        """The temp directory above is the one openpyxl writes to, so an empty
        one means the files were removed, not written somewhere else."""
        from openpyxl import Workbook

        workbook = Workbook(write_only=True)
        workbook.create_sheet("Only").append(["season"])

        try:
            assert [p.name.startswith("openpyxl.") for p in system_temp.iterdir()] == [
                True
            ]
        finally:
            workbook.save(str(tmp_path / "book.xlsx"))

        assert sorted(p.name for p in system_temp.iterdir()) == []


class TestTextStartingWithEquals:
    """openpyxl takes a string starting with = for a formula. A formula cell
    has no stored value, so pd.read_excel read a castaway named =Q back as
    NaN. survivoR's values are data, never formulas."""

    def test_a_castaway_named_like_a_formula_reads_back_as_text(self, target, upstream):
        from app import data

        _with_value(upstream, "castaways", "castaway", "=Q")

        data.download_survivor_data()

        castaways = pd.read_excel(target, "Castaways")
        assert castaways["castaway"].iloc[0] == "=Q"

    def test_no_cell_is_written_as_a_formula(self, tmp_path):
        """Column names too, and whatever follows the =."""
        from openpyxl import load_workbook

        from app.data import write_survivor_workbook

        path = tmp_path / "book.xlsx"
        records = [{"=name": "=Q", "season": 51, "note": "=SUM(A1:A2)", "eq": "="}]

        write_survivor_workbook(str(path), [("Sheet", records)])

        frame = pd.read_excel(path, "Sheet")
        assert list(frame.columns) == ["=name", "season", "note", "eq"]
        assert frame.iloc[0].tolist() == ["=Q", 51, "=SUM(A1:A2)", "="]
        book = load_workbook(path)
        try:
            types = {
                c.value: c.data_type for row in book["Sheet"].iter_rows() for c in row
            }
        finally:
            book.close()
        assert types["=Q"] == "s"
        assert types["=SUM(A1:A2)"] == "s"
        assert types["=name"] == "s"
        assert types[51] == "n"


class TestOneWayIn:
    """Every copy of survivoR.xlsx comes from download_survivor_data()."""

    def _python_sources(self):
        repo = pathlib.Path(__file__).resolve().parent.parent
        return sorted((repo / "app").rglob("*.py")) + sorted(repo.glob("*.py"))

    def test_the_scan_reads_the_scripts_too(self):
        names = {path.name for path in self._python_sources()}

        assert {"data.py", "seed.py", "add_season.py", "analyze_scoring.py"} <= names

    def test_nothing_fetches_survivors_xlsx_export(self):
        """Upstream regenerates the xlsx only now and then; it went stale."""
        stale = [
            path.name
            for path in self._python_sources()
            if "dev/xlsx" in path.read_text()
        ]

        assert stale == []

    def test_the_json_source_is_still_named(self):
        """The guard above must not pass because the download vanished."""
        sources = {path.name: path.read_text() for path in self._python_sources()}

        assert "/dev/json/" in sources["data.py"]
