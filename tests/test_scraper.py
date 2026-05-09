import json
import os
from pathlib import Path
import sys
import pytest

# Ensure project root is on sys.path so we can import from scripts package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import scraper  # type: ignore


def make_sample_html(records: list[str]) -> str:
    # Wrap NOTAM records separated by blank lines into minimal HTML
    body = "\n\n".join(records)
    return f"<html><body><pre>{body}</pre></body></html>"


def write_airports_csv(path: Path):
    path.write_text(
        "ident,name,latitude_deg,longitude_deg\nTEST,Test Airport,55.0,37.0\nUUUU,Moscow UUUU,55.75,37.62\n",
        encoding="utf-8",
    )


def test_parse_notam_files_creates_geojson(tmp_path: Path, monkeypatch):
    # Create sample NOTAM record consistent enough for pynotam
    sample_record = (
        "(U0216/25 NOTAMN\n"
        "Q)UUWV/QOAXX/IV/BO/A/000/999/5535N03716E999\n"
        "A)UUUU B)2503060800 C)PERM\n"
        "E)SUP 03/25 TO AIP RUSSIA PUBLISHED\n"
        "CONCERNING SECTION GEN 2.5 LIST OF RADIO NAVIGATION AIDS\n"
        "WEF 250417\n"
        "SUP 03/25 TO AIP RUSSIA PUBLISHED AT:\n"
        "HTTP: WWW.CAICA.RU/ACCESS TO ON-LINE AERONAUTICAL INFORMATION/\n"
        "OFFICIAL AERONAUTICAL INFO (AIP PRODUCTS)/ AIP SUP.\n"
        ")\n"
    )

    html_content = make_sample_html([sample_record])
    html_file = tmp_path / "A2501010000_eng.html"
    html_file.write_text(html_content, encoding="utf-8")

    airports_csv = tmp_path / "airports.csv"
    write_airports_csv(airports_csv)

    # Run parser
    scraper.parse_notam_files(
        [str(html_file)], airports_csv=str(airports_csv), output=str(tmp_path) + "/"
    )

    # Expect output file A.geojson
    out_file = tmp_path / "A.geojson"
    assert out_file.exists(), "GeoJSON output file was not created"
    data = out_file.read_text(encoding="utf-8")
    assert '"FeatureCollection"' in data
    assert (
        '"features": []' not in data
    ), "No features decoded (pynotam failed to parse sample)"
    # Check parsed NOTAM id present
    assert "U0216/25" in data


def test_parse_notam_files_recovers_common_malformed_records(tmp_path: Path) -> None:
    records = [
        (
            "(A2119/26 NOTAMN\n"
            "Q)UHMM/QLCXX//A/000/999/6444N17744E005\n"
            "A)UHMA B)2604062340 C)2606300700 EST\n"
            "E)RWY 02/20: RCLL EVERY SECOND WORKING, SPACING 30M.)"
        ),
        (
            "(C2041/26 NOTAMN\n"
            "Q)UNNT/QSPAH/IV/BO/AE/000/999/6043N07740E025\n"
            "A)UNSS B)2605040140 C)2605311250\n"
            "E)STREZHEVOY TWR OPR HR (AIRSPACE CLASS C) CALL SIGN KARAVAY-RADAR:\n\n"
            "0140-1250.)"
        ),
        (
            "(U0427/26 NOTAMN\n"
            "Q)UUWV/QNDXX//E/000/999/5551N03256E150\n"
            "A)UUWV B)2604010000 C)2612242359\n"
            "E)DME BELY BJ 114.2MHZ CH89X: RANGE AT FL200 IS 100KM.)"
        ),
    ]

    html_content = make_sample_html(records)
    html_file = tmp_path / "A2605091253_eng.html"
    html_file.write_text(html_content, encoding="utf-8")

    airports_csv = tmp_path / "airports.csv"
    write_airports_csv(airports_csv)

    result = scraper.parse_notam_files(
        [str(html_file)], airports_csv=str(airports_csv), output=str(tmp_path) + "/"
    )

    assert result["decoded_count"] == 3
    assert result["decode_failures"] == 0
    assert result["interpretation_failures"] == []


# Return list of HTML files in the 'current' directory
def get_local_html_files(directory: str) -> list[str]:
    html_files = []
    for fname in os.listdir(directory):
        if fname.lower().endswith(".html"):
            html_files.append(os.path.join(directory, fname))
    if not html_files:
        raise FileNotFoundError("No HTML file found in the current/ directory")
    return html_files


def test_parse_current_notam_files(tmp_path: Path):
    try:
        html_files = get_local_html_files("current")
    except FileNotFoundError:
        pytest.skip("No HTML file found in current/ for integration parsing")
    # parse_notam_files expects a list of files
    scraper.parse_notam_files(html_files, "ru-airports.csv", output=str(tmp_path) + "/")
    # Check if any GeoJSON files were created
    geojson_files = list(tmp_path.glob("*.geojson"))
    assert geojson_files, "No GeoJSON files were created"


class DummyResponse:
    def __init__(self, text: str):
        self.text = text


def test_main_continues_when_timestamp_matches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "current").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "current" / ".scrape_timestamp").write_text(
        "2602050053", encoding="utf-8"
    )

    index_html = """
    <html>
      <body>
        <td onclick="location='A2602050053_eng.html'" width="">A</td>
      </body>
    </html>
    """
    file_html = "<html><body><pre>mock notam</pre></body></html>"
    responses = iter([DummyResponse(index_html), DummyResponse(file_html)])

    monkeypatch.setattr(scraper, "fetch", lambda url, timeout=10: next(responses))

    parse_calls: list[list[str]] = []

    def fake_parse_notam_files(
        html_files: list[str], airports_csv: str = "airports.csv", output: str = "."
    ) -> dict[str, object]:
        parse_calls.append(html_files)
        return {
            "decoded_count": 4,
            "decode_failures": 0,
            "expired_count": 0,
            "files_processed": len(html_files),
            "interpretation_failures": [],
        }

    monkeypatch.setattr(scraper, "parse_notam_files", fake_parse_notam_files)

    result = scraper.main()

    assert result == 0
    assert parse_calls == [["current/A2602050053_eng.html"]]
    history = json.loads((tmp_path / "docs" / "run_history.json").read_text())
    latest = history["runs"][-1]
    assert latest["status"] == "success"
    assert latest["decoded_count"] == 4
    assert latest["new_interpretation_failures_count"] == 0


def test_main_records_zero_result_when_index_is_empty(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "current").mkdir()
    (tmp_path / "docs").mkdir()

    monkeypatch.setattr(
        scraper,
        "fetch",
        lambda url, timeout=10: DummyResponse("<html><body></body></html>"),
    )

    result = scraper.main()

    assert result == 1
    history = json.loads((tmp_path / "docs" / "run_history.json").read_text())
    latest = history["runs"][-1]
    assert latest["status"] == "no_index_files"
    assert latest["zero_result"] is True
    assert latest["consecutive_zero_days"] == 1
    assert latest["escalate"] is False


def test_persist_run_summary_escalates_after_three_zero_days(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "run_history.json"
    summaries = [
        {
            "run_date": "2026-05-07",
            "status": "zero_active_notams",
            "zero_result": True,
            "files_found": 1,
            "files_downloaded": 1,
            "files_processed": 1,
            "decoded_count": 0,
            "decode_failures": 0,
            "expired_count": 0,
            "download_failures": 0,
            "scrape_timestamp": "2605070053",
            "error": "No active NOTAM features were decoded from the fetched files.",
        },
        {
            "run_date": "2026-05-08",
            "status": "zero_active_notams",
            "zero_result": True,
            "files_found": 1,
            "files_downloaded": 1,
            "files_processed": 1,
            "decoded_count": 0,
            "decode_failures": 0,
            "expired_count": 0,
            "download_failures": 0,
            "scrape_timestamp": "2605080053",
            "error": "No active NOTAM features were decoded from the fetched files.",
        },
        {
            "run_date": "2026-05-09",
            "status": "zero_active_notams",
            "zero_result": True,
            "files_found": 1,
            "files_downloaded": 1,
            "files_processed": 1,
            "decoded_count": 0,
            "decode_failures": 0,
            "expired_count": 0,
            "download_failures": 0,
            "scrape_timestamp": "2605090053",
            "error": "No active NOTAM features were decoded from the fetched files.",
        },
    ]

    latest = None
    for summary in summaries:
        latest = scraper.persist_run_summary(summary, history_path=history_path)

    assert latest is not None
    assert latest["consecutive_zero_days"] == 3
    assert latest["escalate"] is True

    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["runs"][-1]["status"] == "zero_active_notams"


def test_persist_interpretation_failures_detects_only_new_signatures(
    tmp_path: Path,
) -> None:
    failures_path = tmp_path / "interpretation_failures.json"
    first_failure = {
        "signature": "deadbeef0001",
        "notam_id": "A2119/26",
        "file": "A2605091253_eng.html",
        "error": "Failed to parse NOTAM (line 2, col 1)",
        "snippet": "(A2119/26 NOTAMN ...)",
    }
    second_failure = {
        "signature": "deadbeef0002",
        "notam_id": "U0427/26",
        "file": "U2605091253_eng.html",
        "error": "Failed to parse NOTAM (line 2, col 1)",
        "snippet": "(U0427/26 NOTAMN ...)",
    }

    first_state = scraper.persist_interpretation_failures(
        [first_failure],
        run_date="2026-05-09",
        failures_path=failures_path,
    )
    second_state = scraper.persist_interpretation_failures(
        [first_failure, second_failure],
        run_date="2026-05-10",
        failures_path=failures_path,
    )

    assert len(first_state["latest_new_failures"]) == 1
    assert first_state["latest_new_failures"][0]["notam_id"] == "A2119/26"
    assert len(second_state["latest_new_failures"]) == 1
    assert second_state["latest_new_failures"][0]["notam_id"] == "U0427/26"
