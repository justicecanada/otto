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
        "<page_2>\nHere's some more text to put it over the limit.\n</page_2>",
    ]
    result = markdown_splitter._split_with_page_numbers(markdown_text)
    assert result == expected_output


def test_close_page_tags_simple():
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


def test_close_page_tags_complex_1():
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


def test_close_page_tags_complex_2():
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


def test_get_all_headings_1(markdown_splitter):
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


def test_get_all_headings_2(markdown_splitter):
    # Test with no headings
    existing_headings = {
        1: None,
        2: None,
        3: None,
        4: None,
        5: None,
        6: None,
    }
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


def test_get_all_headings_3(markdown_splitter):
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


def test_table_heading_helpers():
    markdown_splitter = MarkdownSplitter()
    row = "| Header 1 | Header 2 |"
    assert markdown_splitter._is_table_row(row)
    assert not markdown_splitter._is_table_underline(row)

    underline = "| --- | --- |"
    assert markdown_splitter._is_table_row(underline)
    assert markdown_splitter._is_table_underline(underline)

    not_a_row = "This is not a table row."
    assert not markdown_splitter._is_table_row(not_a_row)
    assert not markdown_splitter._is_table_underline(not_a_row)


def test_get_last_table_header_1():
    # Chunk overlap complicates the behaviour. First, test without it.
    # Case 1: No table headers
    markdown_splitter = MarkdownSplitter(chunk_overlap=0)
    text = "Some text."
    expected_output = ""
    result = markdown_splitter._get_last_table_header(text)
    assert result == expected_output


def test_get_last_table_header_2(markdown_splitter):
    # Case 2: Chunk contains a single table only.
    text = """
<page_1>
| Header 1 | Header 2 |
| --- | --- |
| Row 1 | Row 1 |
| Row 2 | Row 2 |
</page_1>
"""
    expected_output = "| Header 1 | Header 2 |\n| --- | --- |"
    result = markdown_splitter._get_last_table_header(text)
    assert result == expected_output


def test_get_last_table_header_3(markdown_splitter):
    # Case 3: Chunk contains table cells but no headers.
    text = """
<page_1>
| --- | --- |
| Row 1 | Row 1 |
</page_1>
"""
    expected_output = ""
    result = markdown_splitter._get_last_table_header(text)
    assert result == expected_output


def test_get_last_table_header_4(markdown_splitter):
    # Case 4: Chunk contains table rows but no header.
    text = """
<page_4>
| Row 1 | Row 1 |
</page_4>
"""
    expected_output = ""
    result = markdown_splitter._get_last_table_header(text)
    assert result == expected_output


def test_get_last_table_header_5(markdown_splitter):
    # Case 5: Chunk contains text as well as a single table
    text = """
<page_1>
Some text.
| Header 1 | Header 2 |
| --- | --- |
| Row 1 | Row 1 |
</page_1>
"""
    expected_output = "| Header 1 | Header 2 |\n| --- | --- |"
    result = markdown_splitter._get_last_table_header(text)
    assert result == expected_output


def test_get_last_table_header_6(markdown_splitter):
    # Case 6: Chunk contains multiple tables
    text = """
<page_1>
| Header 1 | Header 2 |
| --- | --- |
| Row 1 | Row 1 |

Another table:

| Header 3 | Header 4 | Header 5 |
| --- | --- | --- |
| Row 2 | Row 2 | Row 2 |
</page_1>
"""
    expected_output = "| Header 3 | Header 4 | Header 5 |\n| --- | --- | --- |"
    result = markdown_splitter._get_last_table_header(text)
    assert result == expected_output


def test_get_last_table_header_7(markdown_splitter):
    # Case 7: Chunk contains only a header row as last row of chunk
    text = """
<page_2>
Even more text.

### Heading 3
Some more text.

# New heading 1

| Header 3 | Header 4 |
</page_2>
""".strip()
    expected_output = "| Header 3 | Header 4 |"
    result = markdown_splitter._get_last_table_header(text)
    print(result)
    assert result == expected_output


def test_repeat_table_header_1():
    markdown_splitter = MarkdownSplitter(chunk_size=40, chunk_overlap=20)
    # Get the chunks with _split_with_page_numbers
    text = """
<page_1>
| Header 1 | Header 2 |
| --- | --- |
| Row 1 | Row 1 |
| Row 2 | Row 2 |
| Row 3 | Row 3 |
| Row 4 | Row 4 |
</page_1>
""".strip()
    chunks = markdown_splitter._split_with_page_numbers(text)
    # The chunks should be split into two:
    # the first chunk containing the header and the first two rows
    # the second chunk containing the last two rows
    expected_chunks = [
        "<page_1>\n| Header 1 | Header 2 |\n| --- | --- |\n| Row 1 | Row 1 |\n| Row 2 | Row 2 |\n</page_1>",
        "<page_1>\n| Row 3 | Row 3 |\n| Row 4 | Row 4 |\n</page_1>",
    ]
    assert chunks == expected_chunks

    # Test _repeat_table_header_if_necessary
    # Extract the last table header
    last_table_header = markdown_splitter._get_last_table_header(chunks[0])
    # Repeat the table header in the second chunk
    repeated_chunk = markdown_splitter._repeat_table_header_if_necessary(
        chunks[1], last_table_header
    )
    expected_repeated_chunk = "<page_1>\n| Header 1 | Header 2 |\n| --- | --- |\n| Row 3 | Row 3 |\n| Row 4 | Row 4 |\n</page_1>"
    assert repeated_chunk == expected_repeated_chunk


def test_repeat_table_header_2(markdown_splitter):
    # Test with two chunks that don't contain tables at all
    chunks = "Some text.", "Some more text."
    last_table_header = markdown_splitter._get_last_table_header(chunks[0])
    repeated_chunk = markdown_splitter._repeat_table_header_if_necessary(
        chunks[1], last_table_header
    )
    expected_repeated_chunk = "Some more text."
    assert repeated_chunk == expected_repeated_chunk

    # Same thing but with page tags
    chunks = "<page_1>\nSome text.\n</page_1>", "<page_1>\nSome more text.\n</page_1>"
    last_table_header = markdown_splitter._get_last_table_header(chunks[0])
    repeated_chunk = markdown_splitter._repeat_table_header_if_necessary(
        chunks[1], last_table_header
    )
    expected_repeated_chunk = "<page_1>\nSome more text.\n</page_1>"
    assert repeated_chunk == expected_repeated_chunk


def test_repeat_table_header_3(markdown_splitter):
    # Test with a chunk that contains a table and a chunk that doesn't
    chunk_1 = """
<page_1>
| Header 1 | Header 2 |
| --- | --- |
| Row 1 | Row 1 |
| Row 2 | Row 2 |
</page_1>
""".strip()
    chunk_2 = "Some text."
    last_table_header = markdown_splitter._get_last_table_header(chunk_1)
    repeated_chunk = markdown_splitter._repeat_table_header_if_necessary(
        chunk_2, last_table_header
    )
    expected_repeated_chunk = "Some text."
    assert repeated_chunk == expected_repeated_chunk


def test_repeat_table_header_4(markdown_splitter):
    # Test with two chunks each containing a distinct table
    chunk_1 = """
<page_1>
| Header 1 | Header 2 |
| --- | --- |
| Row 1 | Row 1 |
| Row 2 | Row 2 |
</page_1>
""".strip()
    chunk_2 = """
<page_1>
| Header 3 | Header 4 |
| --- | --- |
| Row 3 | Row 3 |
</page_1>
""".strip()
    last_table_header = markdown_splitter._get_last_table_header(chunk_1)
    repeated_chunk = markdown_splitter._repeat_table_header_if_necessary(
        chunk_2, last_table_header
    )
    expected_repeated_chunk = chunk_2
    assert repeated_chunk == expected_repeated_chunk


def test_repeat_table_header_5(markdown_splitter):
    # Test with a chunk that contains multiple tables, and a chunk that continues the last table
    chunk_1 = """
<page_1>
| Header 1 | Header 2 |
| --- | --- |
| Row 1 | Row 1 |
| Row 2 | Row 2 |

Some text.

| Header 3 | Header 4 |
| --- | --- |
| Row 3 | Row 3 |
</page_1>
""".strip()
    chunk_2 = """
<page_1>
| Row 4 | Row 4 |
</page_1>
""".strip()
    last_table_header = markdown_splitter._get_last_table_header(chunk_1)
    assert last_table_header == "| Header 3 | Header 4 |\n| --- | --- |"
    repeated_chunk = markdown_splitter._repeat_table_header_if_necessary(
        chunk_2, last_table_header
    )
    print(repeated_chunk)
    expected_repeated_chunk = """
<page_1>
| Header 3 | Header 4 |
| --- | --- |
| Row 4 | Row 4 |
</page_1>
""".strip()
    assert repeated_chunk == expected_repeated_chunk


def test_repeat_headings():
    """
    Integrates several of the functions tested above.
    """
    markdown_splitter = MarkdownSplitter()
    # Get the chunks with _split_with_page_numbers
    split_texts = [
        """
<page_1>
# Heading 1
| Header 1 | Header 2 |
| --- | --- |
| Row 1 | Row 1 |
| Row 2 | Row 2 |
</page_1>
""".strip(),
        """
<page_1>
| Row 3 | Row 3 |
| Row 4 | Row 4 |

## Heading 2
Some text.
</page_1>
<page_2>
Some more text.
</page_2>
""".strip(),
        """
<page_2>
Even more text.

### Heading 3
Some more text.

# New heading 1

| Header 3 | Header 4 |
</page_2>
""".strip(),
        """
<page_2>
| --- | --- |
| Row 5 | Row 5 |
| Row 6 | Row 6 |

# New heading 1b

Text again. "New heading 1" should still be in breadcrumbs.
</page_2>
""".strip(),
        """
<page_2>
# New heading 1c

</page_2>
<page_3>
This time, "New heading 1b" should NOT be in breadcrumbs.
</page_3>
""".strip(),
        """
<page_3>
## New heading 2

This time, "New heading 1c" should be in breadcrumbs, but not "New heading 2".
</page_3>
""".strip(),
    ]
    expected_texts = [
        """
<page_1>
# Heading 1
| Header 1 | Header 2 |
| --- | --- |
| Row 1 | Row 1 |
| Row 2 | Row 2 |
</page_1>
""".strip(),
        """
<headings>Heading 1</headings>
<page_1>
| Header 1 | Header 2 |
| --- | --- |
| Row 3 | Row 3 |
| Row 4 | Row 4 |

## Heading 2
Some text.
</page_1>
<page_2>
Some more text.
</page_2>
""".strip(),
        """
<headings>Heading 1 > Heading 2</headings>
<page_2>
Even more text.

### Heading 3
Some more text.

# New heading 1

| Header 3 | Header 4 |
</page_2>
""".strip(),
        """
<headings>New heading 1</headings>
<page_2>
| Header 3 | Header 4 |
| --- | --- |
| Row 5 | Row 5 |
| Row 6 | Row 6 |

# New heading 1b

Text again. "New heading 1" should still be in breadcrumbs.
</page_2>
""".strip(),
        """
<page_2>
# New heading 1c

</page_2>
<page_3>
This time, "New heading 1b" should NOT be in breadcrumbs.
</page_3>
""".strip(),
        """
<headings>New heading 1c</headings>
<page_3>
## New heading 2

This time, "New heading 1c" should be in breadcrumbs, but not "New heading 2".
</page_3>
""".strip(),
    ]
    result = markdown_splitter._repeat_headings(split_texts)
    for i, text in enumerate(result):
        print("\n\nCHUNK:\n", text)
        assert text == expected_texts[i]


def test_markdown_splitter_no_page_numbers():
    """
    End to end regression test of the MarkdownSplitter.split_markdown function.
    Update the expected_output if the MarkdownSplitter implementation changes.
    """
    markdown_splitter = MarkdownSplitter(chunk_size=768, chunk_overlap=100)
    text = """# Heading level 1, on page 1\n\nParagraph page 1. Here is some more text to make Azure more confident that this is indeed a paragraph. And so on, and so on. Until the end of time.\n\n## Heading level 2, on page 2\n\nParagraph page 2, which also requires more text to make Azure more confident that this is indeed a paragraph. And so on, and so on. Until the end of time.\n\n\n\n| __Table header 1__ | Table header 2 | Table header 3 | Table header 4 |\n| --- | --- | --- | --- |\n| Data row 1, cell 1 | Data row 1, cell 2 | Data row 1, cell 3 | Data row 1, cell 4 |\n| Data row 2, cell 1 | Data row 2, cell 2 | Data row 2, cell 3 | Data row 2, cell 4 |\n\n### Page 3 with lorem ipsum\n\nI digress. Here’s some Latin. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Integer nec odio. Praesent libero. Sed cursus ante dapibus diam. Sed nisi. Nulla quis sem at nibh elementum imperdiet. Duis sagittis ipsum. Praesent mauris. Fusce nec tellus sed augue semper porta. Mauris massa. Vestibulum lacinia arcu eget nulla. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Curabitur sodales ligula in libero. Sed dignissim lacinia nunc. Curabitur tortor. Pellentesque nibh. Aenean quam. In scelerisque sem at dolor. Maecenas mattis. Sed convallis tristique sem. Proin ut ligula vel nunc egestas porttitor. Morbi lectus risus, iaculis vel, suscipit quis, luctus non, massa. Fusce ac turpis quis ligula lacinia aliquet.\n\nMauris ipsum. Nulla metus metus, ullamcorper vel, tincidunt sed, euismod in, nibh. Quisque volutpat condimentum velit. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Nam nec ante. Sed lacinia, urna non tincidunt mattis, tortor neque adipiscing diam, a cursus ipsum ante quis turpis. Nulla facilisi. Ut fringilla. Suspendisse potenti. Nunc feugiat mi a tellus consequat imperdiet. Vestibulum sapien. Proin quam. Etiam ultrices. Suspendisse in justo eu magna luctus suscipit. Sed lectus. Integer euismod lacus luctus magna. Quisque cursus, metus vitae pharetra auctor, sem massa mattis sem, at interdum magna augue eget diam.\n\nVestibulum ante ipsum primis in faucibus orci luctus et ultrices posuere cubilia Curae; Morbi lacinia molestie dui. Praesent blandit dolor. Sed non quam. In vel mi sit amet augue congue elementum. Morbi in ipsum sit amet pede facilisis laoreet. Donec lacus nunc, viverra nec, blandit vel, egestas et, augue. Vestibulum tincidunt malesuada tellus. Ut ultrices ultrices enim. Curabitur sit amet mauris. Morbi in dui quis est pulvinar ullamcorper. Nulla facilisi. Integer lacinia sollicitudin massa. Cras metus. Sed aliquet risus a tortor. Integer id quam. Morbi mi. Quisque nisl felis, venenatis tristique, dignissim in, ultrices sit amet, augue. Proin sodales libero eget ante. Nulla quam. Aenean laoreet. Vestibulum nisi lectus, commodo ac, facilisis ac, ultricies eu, pede. Ut orci risus, accumsan porttitor, cursus quis, aliquet eget, justo. Sed pretium blandit orci.\n\n#### Page 4 with lorem ipsum\n\nI digress. Here’s some Latin. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Integer nec odio. Praesent libero. Sed cursus ante dapibus diam. Sed nisi. Nulla quis sem at nibh elementum imperdiet. Duis sagittis ipsum. Praesent mauris. Fusce nec tellus sed augue semper porta. Mauris massa. Vestibulum lacinia arcu eget nulla. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Curabitur sodales ligula in libero. Sed dignissim lacinia nunc. Curabitur tortor. Pellentesque nibh. Aenean quam. In scelerisque sem at dolor. Maecenas mattis. Sed convallis tristique sem. Proin ut ligula vel nunc egestas porttitor. Morbi lectus risus, iaculis vel, suscipit quis, luctus non, massa. Fusce ac turpis quis ligula lacinia aliquet.\n\nMauris ipsum. Nulla metus metus, ullamcorper vel, tincidunt sed, euismod in, nibh. Quisque volutpat condimentum velit. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Nam nec ante. Sed lacinia, urna non tincidunt mattis, tortor neque adipiscing diam, a cursus ipsum ante quis turpis. Nulla facilisi. Ut fringilla. Suspendisse potenti. Nunc feugiat mi a tellus consequat imperdiet. Vestibulum sapien. Proin quam. Etiam ultrices. Suspendisse in justo eu magna luctus suscipit. Sed lectus. Integer euismod lacus luctus magna. Quisque cursus, metus vitae pharetra auctor, sem massa mattis sem, at interdum magna augue eget diam.\n\nVestibulum ante ipsum primis in faucibus orci luctus et ultrices posuere cubilia Curae; Morbi lacinia molestie dui. Praesent blandit dolor. Sed non quam. In vel mi sit amet augue congue elementum. Morbi in ipsum sit amet pede facilisis laoreet. Donec lacus nunc, viverra nec, blandit vel, egestas et, augue. Vestibulum tincidunt malesuada tellus. Ut ultrices ultrices enim. Curabitur sit amet mauris. Morbi in dui quis est pulvinar ullamcorper. Nulla facilisi. Integer lacinia sollicitudin massa. Cras metus. Sed aliquet risus a tortor. Integer id quam. Morbi mi. Quisque nisl felis, venenatis tristique, dignissim in, ultrices sit amet, augue. Proin sodales libero eget ante. Nulla quam. Aenean laoreet. Vestibulum nisi lectus, commodo ac, facilisis ac, ultricies eu, pede. Ut orci risus, accumsan porttitor, cursus quis, aliquet eget, justo. Sed pretium blandit orci."""
    expected_output = [
        "# Heading level 1, on page 1\n\nParagraph page 1. Here is some more text to make Azure more confident that this is indeed a paragraph. And so on, and so on. Until the end of time.\n\n## Heading level 2, on page 2\n\nParagraph page 2, which also requires more text to make Azure more confident that this is indeed a paragraph. And so on, and so on. Until the end of time.\n\n\n\n| __Table header 1__ | Table header 2 | Table header 3 | Table header 4 |\n| --- | --- | --- | --- |\n| Data row 1, cell 1 | Data row 1, cell 2 | Data row 1, cell 3 | Data row 1, cell 4 |\n| Data row 2, cell 1 | Data row 2, cell 2 | Data row 2, cell 3 | Data row 2, cell 4 |\n\n### Page 3 with lorem ipsum\n\nI digress. Here’s some Latin. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Integer nec odio. Praesent libero. Sed cursus ante dapibus diam. Sed nisi. Nulla quis sem at nibh elementum imperdiet. Duis sagittis ipsum. Praesent mauris. Fusce nec tellus sed augue semper porta. Mauris massa. Vestibulum lacinia arcu eget nulla. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Curabitur sodales ligula in libero. Sed dignissim lacinia nunc. Curabitur tortor. Pellentesque nibh. Aenean quam. In scelerisque sem at dolor. Maecenas mattis. Sed convallis tristique sem. Proin ut ligula vel nunc egestas porttitor. Morbi lectus risus, iaculis vel, suscipit quis, luctus non, massa. Fusce ac turpis quis ligula lacinia aliquet.\n\nMauris ipsum. Nulla metus metus, ullamcorper vel, tincidunt sed, euismod in, nibh. Quisque volutpat condimentum velit. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Nam nec ante. Sed lacinia, urna non tincidunt mattis, tortor neque adipiscing diam, a cursus ipsum ante quis turpis. Nulla facilisi. Ut fringilla. Suspendisse potenti. Nunc feugiat mi a tellus consequat imperdiet. Vestibulum sapien. Proin quam. Etiam ultrices. Suspendisse in justo eu magna luctus suscipit. Sed lectus. Integer euismod lacus luctus magna. Quisque cursus, metus vitae pharetra auctor, sem massa mattis sem, at interdum magna augue eget diam.",
        "<headings>Heading level 1, on page 1 > Heading level 2, on page 2 > Page 3 with lorem ipsum</headings>\nVestibulum ante ipsum primis in faucibus orci luctus et ultrices posuere cubilia Curae; Morbi lacinia molestie dui. Praesent blandit dolor. Sed non quam. In vel mi sit amet augue congue elementum. Morbi in ipsum sit amet pede facilisis laoreet. Donec lacus nunc, viverra nec, blandit vel, egestas et, augue. Vestibulum tincidunt malesuada tellus. Ut ultrices ultrices enim. Curabitur sit amet mauris. Morbi in dui quis est pulvinar ullamcorper. Nulla facilisi. Integer lacinia sollicitudin massa. Cras metus. Sed aliquet risus a tortor. Integer id quam. Morbi mi. Quisque nisl felis, venenatis tristique, dignissim in, ultrices sit amet, augue. Proin sodales libero eget ante. Nulla quam. Aenean laoreet. Vestibulum nisi lectus, commodo ac, facilisis ac, ultricies eu, pede. Ut orci risus, accumsan porttitor, cursus quis, aliquet eget, justo. Sed pretium blandit orci.\n\n#### Page 4 with lorem ipsum\n\nI digress. Here’s some Latin. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Integer nec odio. Praesent libero. Sed cursus ante dapibus diam. Sed nisi. Nulla quis sem at nibh elementum imperdiet. Duis sagittis ipsum. Praesent mauris. Fusce nec tellus sed augue semper porta. Mauris massa. Vestibulum lacinia arcu eget nulla. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Curabitur sodales ligula in libero. Sed dignissim lacinia nunc. Curabitur tortor. Pellentesque nibh. Aenean quam. In scelerisque sem at dolor. Maecenas mattis. Sed convallis tristique sem. Proin ut ligula vel nunc egestas porttitor. Morbi lectus risus, iaculis vel, suscipit quis, luctus non, massa. Fusce ac turpis quis ligula lacinia aliquet.\n\nMauris ipsum. Nulla metus metus, ullamcorper vel, tincidunt sed, euismod in, nibh. Quisque volutpat condimentum velit. Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos. Nam nec ante. Sed lacinia, urna non tincidunt mattis, tortor neque adipiscing diam, a cursus ipsum ante quis turpis. Nulla facilisi. Ut fringilla. Suspendisse potenti. Nunc feugiat mi a tellus consequat imperdiet. Vestibulum sapien. Proin quam. Etiam ultrices. Suspendisse in justo eu magna luctus suscipit. Sed lectus. Integer euismod lacus luctus magna. Quisque cursus, metus vitae pharetra auctor, sem massa mattis sem, at interdum magna augue eget diam.",
        "<headings>Heading level 1, on page 1 > Heading level 2, on page 2 > Page 3 with lorem ipsum > Page 4 with lorem ipsum</headings>\nVestibulum ante ipsum primis in faucibus orci luctus et ultrices posuere cubilia Curae; Morbi lacinia molestie dui. Praesent blandit dolor. Sed non quam. In vel mi sit amet augue congue elementum. Morbi in ipsum sit amet pede facilisis laoreet. Donec lacus nunc, viverra nec, blandit vel, egestas et, augue. Vestibulum tincidunt malesuada tellus. Ut ultrices ultrices enim. Curabitur sit amet mauris. Morbi in dui quis est pulvinar ullamcorper. Nulla facilisi. Integer lacinia sollicitudin massa. Cras metus. Sed aliquet risus a tortor. Integer id quam. Morbi mi. Quisque nisl felis, venenatis tristique, dignissim in, ultrices sit amet, augue. Proin sodales libero eget ante. Nulla quam. Aenean laoreet. Vestibulum nisi lectus, commodo ac, facilisis ac, ultricies eu, pede. Ut orci risus, accumsan porttitor, cursus quis, aliquet eget, justo. Sed pretium blandit orci.",
    ]
    result = markdown_splitter.split_markdown(text)
    assert result == expected_output
