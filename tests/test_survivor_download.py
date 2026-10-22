"""Tests for download_survivor_data(): how survivoR.xlsx reaches the disk.

Two gunicorn workers each run the daily refresh, so both download at the same
moment into the same file, and request handlers read it at any time. Writing
the file in place let one of them read a half-written workbook ("Truncated
file header"). It is now written to a temporary file in the same directory and
swapped in with os.replace, so the path only ever holds a complete file.
"""

import os

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


class TestDownloadSurvivorData:
    def test_a_download_replaces_the_previous_file(self, target, monkeypatch):
        from app import data

        target.write_bytes(OLD_BYTES)
        monkeypatch.setattr(
            data.requests, "get", lambda *a, **k: _FakeResponse(200, b"fresh")
        )

        data.download_survivor_data()

        assert target.read_bytes() == b"fresh"
        assert _leftovers(target) == []

    def test_a_failed_download_keeps_the_previous_file(self, target, monkeypatch):
        from app import data

        target.write_bytes(OLD_BYTES)
        monkeypatch.setattr(data.requests, "get", lambda *a, **k: _FakeResponse(503))

        with pytest.raises(requests.HTTPError):
            data.download_survivor_data()

        assert target.read_bytes() == OLD_BYTES
        assert _leftovers(target) == []

    def test_the_download_goes_through_the_atomic_swap(self, target, monkeypatch):
        from app import data

        swaps = []
        real_replace = os.replace

        def spy(src, dst):
            swaps.append(dst)
            real_replace(src, dst)

        monkeypatch.setattr(data.os, "replace", spy)
        monkeypatch.setattr(
            data.requests, "get", lambda *a, **k: _FakeResponse(200, b"fresh")
        )

        data.download_survivor_data()

        assert swaps == [str(target)]
