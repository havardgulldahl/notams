from scripts.scraper import parse_html_list


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
