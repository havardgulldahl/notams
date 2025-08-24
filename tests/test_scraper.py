import os
from pathlib import Path
import sys

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
    html_files = get_local_html_files("current")
    # parse_notam_files expects a list of files
    scraper.parse_notam_files(html_files, "ru-airports.csv", output=str(tmp_path) + "/")
    # Check if any GeoJSON files were created
    geojson_files = list(tmp_path.glob("*.geojson"))
    assert geojson_files, "No GeoJSON files were created"
