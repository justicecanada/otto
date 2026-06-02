from librarian.utils.extract_emails import parse_message_html


def test_parse_message_html_simple():
    html = "<html><body><p>Hello</p><p>World</p></body></html>"
    result = parse_message_html(html)
    assert "Hello" in result
    assert "World" in result
    assert result == "Hello\nWorld"


def test_parse_message_html_with_injected_header():
    html = """
    <html>
    <body>
    <div id="injectedHeader">
        From: Me
        To: You
        Subject: Test
    </div>
    <p>Body Content</p>
    </body>
    </html>
    """
    result = parse_message_html(html)
    assert "Body Content" in result
    assert "From: Me" not in result


def test_parse_message_html_with_styles_and_scripts():
    html = """
    <html>
    <head>
    <style>body { color: red; }</style>
    <script>alert('bad');</script>
    </head>
    <body>
    <p>Clean Content</p>
    </body>
    </html>
    """
    result = parse_message_html(html)
    assert "Clean Content" in result
    assert "body {" not in result
    assert "alert" not in result


def test_parse_message_html_nested_tags():
    html = "<div><p>Paragraph 1</p><div><br>Paragraph 2</div></div>"
    result = parse_message_html(html)
    assert "Paragraph 1" in result
    assert "Paragraph 2" in result


def test_parse_message_html_empty():
    assert parse_message_html("") == ""
    assert parse_message_html(None) == ""


def test_parse_message_html_malformed():
    html = "<p>Unclosed paragraph"
    result = parse_message_html(html)
    assert "Unclosed paragraph" in result
