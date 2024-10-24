import pytest

from librarian.utils.markdown_splitter import MarkdownSplitter


@pytest.fixture
def markdown_splitter():
    return MarkdownSplitter()


def test_split_with_page_numbers_no_page_tags():
    markdown_splitter = MarkdownSplitter(chunk_size=20, chunk_overlap=0)
    markdown_text = """
# I'm a heading!
Blah blah blah, how about that text!
Here's some more text to put it over the limit.
"""
    expected_output = [
        "# I'm a heading!\nBlah blah blah, how about that text!",
        "Here's some more text to put it over the limit.",
    ]
    result = markdown_splitter._split_with_page_numbers(markdown_text)
    assert result == expected_output


def test_split_with_page_numbers_single_page_tag():
    markdown_splitter = MarkdownSplitter(chunk_size=20, chunk_overlap=0)
    markdown_text = """
<page_1>
# I'm a heading!
Blah blah blah, how about that text!
Here's some more text to put it over the limit.
</page_1>
"""
    expected_output = [
        "<page_1>\n# I'm a heading!\n</page_1>",
        "<page_1>\nBlah blah blah, how about that text!\n</page_1>",
        "<page_1>\nHere's some more text to put it over the limit.\n</page_1>",
    ]
    result = markdown_splitter._split_with_page_numbers(markdown_text)
    assert result == expected_output


def test_split_with_page_numbers_multiple_page_tags():
    markdown_splitter = MarkdownSplitter(chunk_size=40, chunk_overlap=0)
    markdown_text = """
<page_1>
# I'm a heading!
</page_1>
<page_2>
Blah blah blah, how about that text!
Here's some more text to put it over the limit.
</page_2>
"""
    expected_output = [
        "<page_1>\n# I'm a heading!\n</page_1>\n<page_2>\nBlah blah blah, how about that text!\n</page_2>",
        "<page_2>\nHere's some more text to put it over the limit.\n</page_2>",
    ]
    result = markdown_splitter._split_with_page_numbers(markdown_text)
    assert result == expected_output


def test_split_with_page_numbers_no_content_between_page_tags():
    markdown_splitter = MarkdownSplitter(chunk_size=20, chunk_overlap=0)
    markdown_text = """
<page_1>
</page_1>
<page_2>
</page_2>
"""
    expected_output = []
    result = markdown_splitter._split_with_page_numbers(markdown_text)
    assert result == expected_output


def test_split_with_page_numbers_overlap():
    markdown_splitter = MarkdownSplitter(chunk_size=40, chunk_overlap=30)
    markdown_text = """
<page_1>
# I'm a heading!
</page_1>
<page_2>
Blah blah blah, how about that text!
Here's some more text to put it over the limit.
</page_2>
"""
    expected_output = [
        "<page_1>\n# I'm a heading!\n</page_1>\n<page_2>\nBlah blah blah, how about that text!\n</page_2>",
        "<page_2>\nBlah blah blah, how about that text!\nHere's some more text to put it over the limit.\n</page_2>",
    ]
    result = markdown_splitter._split_with_page_numbers(markdown_text)
    assert result == expected_output


def test_close_page_tags():
    # Case 1: No page tags
    markdown_splitter = MarkdownSplitter()
    html_string = "This is some text."
    expected_output = "This is some text."
    result = markdown_splitter._close_page_tags(html_string)
    assert result == expected_output

    # Case 2: Starts with a closing page tag, and no other tags
    markdown_splitter = MarkdownSplitter()
    html_string = "</page_1>\nThis is some text."
    expected_output = "This is some text."
    result = markdown_splitter._close_page_tags(html_string)
    assert result == expected_output

    # Case 3: Starts with a closing page tag, followed by other pages
    markdown_splitter = MarkdownSplitter()
    html_string = "</page_1>\n<page_2>\nThis is some text.\n</page_2>"
    expected_output = "<page_2>\nThis is some text.\n</page_2>"
    result = markdown_splitter._close_page_tags(html_string)
    assert result == expected_output

    # Case 4: Starts with an opening page tag, and no other tags
    markdown_splitter = MarkdownSplitter()
    html_string = "<page_1>\nThis is some text."
    expected_output = "<page_1>\nThis is some text.\n</page_1>"
    result = markdown_splitter._close_page_tags(html_string)
    assert result == expected_output

    # Case 5: Multiple valid page tags
    markdown_splitter = MarkdownSplitter()
    html_string = """
<page_1>
This is some text.
</page_1>
<page_2>
This is some more text.
</page_2>
"""
    expected_output = html_string.strip()
    result = markdown_splitter._close_page_tags(html_string)
    assert result == expected_output

    # Case 6: Multiple valid page tags, but missing closing tag
    markdown_splitter = MarkdownSplitter()
    html_string = """
<page_1>
This is some text.
</page_1>
<page_2>
This is some more text.
"""
    expected_output = """
<page_1>
This is some text.
</page_1>
<page_2>
This is some more text.
</page_2>
""".strip()
    result = markdown_splitter._close_page_tags(html_string)
    assert result == expected_output

    # Case 7: Multiple valid page tags, but missing opening tag
    markdown_splitter = MarkdownSplitter()
    html_string = """
This is some text.
</page_1>
<page_2>
This is some more text.
</page_2>
"""
    expected_output = """
<page_1>
This is some text.
</page_1>
<page_2>
This is some more text.
</page_2>
""".strip()
    result = markdown_splitter._close_page_tags(html_string)
    assert result == expected_output

    # Case 8: Compound issues 1
    markdown_splitter = MarkdownSplitter()
    html_string = """
</page_1>
<page_2>
This is some text.
</page_2>
<page_3>
"""
    expected_output = """
<page_2>
This is some text.
</page_2>
""".strip()
    result = markdown_splitter._close_page_tags(html_string)
    assert result == expected_output

    # Case 9: Compound issues 2
    markdown_splitter = MarkdownSplitter()
    html_string = """
This is some text.
</page_1>
<page_2>
This is some more text.
"""
    expected_output = """
<page_1>
This is some text.
</page_1>
<page_2>
This is some more text.
</page_2>
""".strip()
    result = markdown_splitter._close_page_tags(html_string)
    assert result == expected_output


def test_get_heading():
    markdown_splitter = MarkdownSplitter()
    line = "# I'm a heading!"
    expected_output = (1, "I'm a heading!")
    result = markdown_splitter._get_heading(line)
    assert result == expected_output

    markdown_splitter = MarkdownSplitter()
    line = "I'm not a heading!"
    expected_output = (None, None)
    result = markdown_splitter._get_heading(line)
    assert result == expected_output

    markdown_splitter = MarkdownSplitter()
    line = "###### I'm a heading!"
    expected_output = (6, "I'm a heading!")
    result = markdown_splitter._get_heading(line)
    assert result == expected_output

    markdown_splitter = MarkdownSplitter()
    line = "######### Too many hashes!"
    expected_output = (None, None)
    result = markdown_splitter._get_heading(line)
    assert result == expected_output


def test_set_headings():
    markdown_splitter = MarkdownSplitter()
    headings = markdown_splitter.current_headings
    # Add some headings
    output = markdown_splitter._set_headings(headings, 1, "Heading 1")
    expected_output = None
    assert output == expected_output
    expected_headings = {1: "Heading 1", 2: None, 3: None, 4: None, 5: None, 6: None}
    assert markdown_splitter.current_headings == expected_headings

    # Add another heading at the next level
    markdown_splitter._set_headings(headings, 2, "Heading 2")
    expected_headings = {
        1: "Heading 1",
        2: "Heading 2",
        3: None,
        4: None,
        5: None,
        6: None,
    }
    assert markdown_splitter.current_headings == expected_headings

    # Add another heading at the same level
    markdown_splitter._set_headings(headings, 2, "Another Heading 2")
    expected_headings = {
        1: "Heading 1",
        2: "Another Heading 2",
        3: None,
        4: None,
        5: None,
        6: None,
    }
    assert markdown_splitter.current_headings == expected_headings

    # Add another heading at a higher level
    markdown_splitter._set_headings(headings, 1, "Another Heading 1")
    expected_headings = {
        1: "Another Heading 1",
        2: None,
        3: None,
        4: None,
        5: None,
        6: None,
    }
    assert markdown_splitter.current_headings == expected_headings

    # Add a heading, skipping a level
    markdown_splitter._set_headings(headings, 3, "Heading 3")
    expected_headings = {
        1: "Another Heading 1",
        2: None,
        3: "Heading 3",
        4: None,
        5: None,
        6: None,
    }
    assert markdown_splitter.current_headings == expected_headings

    # Try to add a heading at an invalid level (this should do nothing)
    markdown_splitter._set_headings(headings, 7, "Heading 7")
    assert markdown_splitter.current_headings == expected_headings
    markdown_splitter._set_headings(headings, 0, "Heading 0")
    assert markdown_splitter.current_headings == expected_headings


def test_prepend_headings():
    markdown_splitter = MarkdownSplitter()
    headings = {
        1: "Heading 1",
        2: "Heading 2",
        3: "Heading 3",
        4: "Heading 4",
        5: "Heading 5",
        6: "Heading 6",
    }
    text = "Some text."
    expected_output = """
<headings>Heading 1 > Heading 2 > Heading 3 > Heading 4 > Heading 5 > Heading 6</headings>
Some text.
""".strip()
    result = markdown_splitter._prepend_headings(headings, text)
    print(result)
    assert result == expected_output

    # Test with a lower to_level
    expected_output = """
<headings>Heading 1 > Heading 2 > Heading 3 > Heading 4</headings>
Some text.
""".strip()
    result = markdown_splitter._prepend_headings(headings, text, to_level=5)
    assert result == expected_output

    # Test with some None headings
    headings = {
        1: "Heading 1",
        2: "Heading 2",
        3: None,
        4: "Heading 4",
        5: None,
        6: None,
    }
    expected_output = """
<headings>Heading 1 > Heading 2 > Heading 4</headings>
Some text.
""".strip()
    result = markdown_splitter._prepend_headings(headings, text)
    assert result == expected_output

    # Test with a weird lower_to level (8)
    expected_output = """
<headings>Heading 1 > Heading 2 > Heading 4</headings>
Some text.
""".strip()
    result = markdown_splitter._prepend_headings(headings, text, to_level=8)
    assert result == expected_output

    # Test with no headings
    headings = {
        1: None,
        2: None,
        3: None,
        4: None,
        5: None,
        6: None,
    }
    expected_output = "Some text."
    result = markdown_splitter._prepend_headings(headings, text)
    assert result == expected_output


def test_get_all_headings():
    markdown_splitter = MarkdownSplitter()
    text = """
# Heading 1
Some text.
## Heading 2
Some more text.
"""
    existing_headings = {
        1: None,
        2: None,
        3: None,
        4: None,
        5: None,
        6: None,
    }
    expected_headings = {
        1: "Heading 1",
        2: "Heading 2",
        3: None,
        4: None,
        5: None,
        6: None,
    }
    expected_min_level = 1
    result = markdown_splitter._get_all_headings(text, existing_headings)
    assert result == (expected_headings, expected_headings, expected_min_level)
    # Check that the existing_headings dictionary was not modified
    assert existing_headings == {
        1: None,
        2: None,
        3: None,
        4: None,
        5: None,
        6: None,
    }

    # Test with no headings
    text = "Some text."
    expected_headings = {
        1: None,
        2: None,
        3: None,
        4: None,
        5: None,
        6: None,
    }
    expected_min_level = 7
    result = markdown_splitter._get_all_headings(text, existing_headings)
    assert result == (expected_headings, expected_headings, expected_min_level)

    # Test with some text that starts with higher level headings
    # then has a lower level heading
    existing_headings = {
        1: "Heading 1 - existing",
        2: "Heading 2 - existing",
        3: None,
        4: None,
        5: None,
        6: None,
    }
    text = """
### Heading 3
Some text.
#### Heading 4
Some more text.
## Heading 2 - new
Even more text.
"""
    expected_headings = {
        1: "Heading 1 - existing",
        2: "Heading 2 - new",
        3: None,
        4: None,
        5: None,
        6: None,
    }
    expected_headings_for_prepend = {
        1: "Heading 1 - existing",
        2: "Heading 2 - existing",
        3: None,
        4: None,
        5: None,
        6: None,
    }
    expected_min_level = 3
    result = markdown_splitter._get_all_headings(text, existing_headings)
    print(result)
    assert result == (
        expected_headings,
        expected_headings_for_prepend,
        expected_min_level,
    )
