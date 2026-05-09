from pathlib import Path

from scripts.scraper import (
    extract_direct_notam_url,
    parse_html_entries,
    parse_html_list,
)


def test_parse_html_list_onclick_extraction():
    html = """
    <html>
    <body>
        <table>
            <tr>
                <td onclick="location='A2602050053_eng.html'" width="">Some Title</td>
            </tr>
            <tr>
                <td onclick="location='B1234567890_eng.html'" width="">Another Title</td>
            </tr>
            <tr>
                <!-- Should be ignored -->
                <td onclick="something_else" width="">Ignored</td>
            </tr>
        </table>
    </body>
    </html>
    """
    files = parse_html_list(html)
    assert "A2602050053_eng.html" in files
    assert "B1234567890_eng.html" in files
    assert len(files) == 2


def test_parse_html_entries_from_click_counter_fixture() -> None:
    html = Path("tests/test_data/2026-05-09_caica_ru.html").read_text(
        encoding="utf-8", errors="replace"
    )

    entries = parse_html_entries(html)
    files = parse_html_list(html)

    assert len(entries) == 10
    assert len(files) == 10
    assert entries[0]["filename"] == "A2605091253_eng.html"
    assert (
        entries[0]["url"]
        == "https://www.caica.ru/ANI_Official/notam/notam_series/A2605091253_eng.html"
    )
    assert "U2605091253_eng.html" in files
    assert "Z2605091253_eng.html" in files


def test_extract_direct_notam_url_from_click_counter() -> None:
    onclick = (
        "location='//www.caica.ru/stcl/clicks.php?uri=www.caica.ru/ANI_Official/"
        "notam/notam_series/A2605091253_eng.html&lasttime=1778332463&filetime="
        "1778331180&user=%C3%EE%F1%F2%FC'"
    )

    url = extract_direct_notam_url(onclick)

    assert (
        url
        == "https://www.caica.ru/ANI_Official/notam/notam_series/A2605091253_eng.html"
    )
