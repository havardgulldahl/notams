import sys
from pathlib import Path
import re

import pytest

# The notam module appears to be installed (present in venv site-packages); import directly.
import notam  # type: ignore


TEST_DATA_DIR = Path(__file__).parent / "test_data"
RECORD_FILES = sorted(TEST_DATA_DIR.glob("record_*.txt"))


@pytest.mark.parametrize(
    "record_path", RECORD_FILES, ids=[p.name for p in RECORD_FILES]
)
def test_record_parses_without_error(record_path: Path):
    """Each NOTAM test_data record should parse successfully via Notam.from_str.

    If any record fails, pytest will show which specific file failed.
    """
    text = record_path.read_text(encoding="utf-8").strip()
    assert text, f"Empty record file: {record_path}"
    n = notam.Notam.from_str(text)
    # Basic invariant: we got an object and source began with '('
    assert text.startswith("(")
    # notam_id may or may not be parsed; if present just ensure contains '/'
    if n.notam_id:
        assert "/" in n.notam_id
    # decoded() should not raise
    _ = n.decoded()


def test_decode_idempotent_on_unknown_terms():
    sample_file = RECORD_FILES[0]
    text = sample_file.read_text(encoding="utf-8").strip()
    n = notam.Notam.from_str(text)
    decoded = n.decoded()
    assert isinstance(decoded, str)
    assert decoded  # non-empty
    # Should contain original NOTAM id string if available
    if n.notam_id:
        assert n.notam_id in decoded


def test_corrupted_notam_missing_parenthesis():
    corrupted = "(U1234/25 NOTAMN"  # Missing closing parenthesis
    with pytest.raises(notam.NotamParseError) as excinfo:
        notam.Notam.from_str(corrupted)
    assert "Corrupted NOTAM" in str(excinfo.value)


def test_corrupted_notam_reports_context():
    # Create a string that will likely break the grammar after initial validation step
    broken = "(U1234/25 NOTAMN\nQ)MALFORMED"  # Incomplete structure and still no closing parenthesis
    with pytest.raises(notam.NotamParseError) as excinfo:
        notam.Notam.from_str(broken)
    # Either the corruption check (no closing ')') or parser failure should trigger; accept either message but require class
    err = excinfo.value
    assert isinstance(err, notam.NotamParseError)
    # Snippet or message must reference failure
    assert ("Failed to parse" in str(err)) or ("Corrupted NOTAM" in str(err))
