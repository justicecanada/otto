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
    markdown_splitter = MarkdownSplitter(chunk_size=20, chunk_overlap=0)
    markdown_text = """
<page_1>
# I'm a heading!
</page 1>
<page_2>
Blah blah blah, how about that text!
Here's some more text to put it over the limit.
</page_2>
"""
    expected_output = [
        "<page_1>\n# I'm a heading!\n</page_1>",
        "<page_2>\nBlah blah blah, how about that text!\n</page_2>",
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


def test_stuff_texts_empty(markdown_splitter):
    split_texts = []
    expected_output = []
    result = markdown_splitter._stuff_texts(split_texts)
    assert result == expected_output

    split_texts = [""]
    expected_output = []
    result = markdown_splitter._stuff_texts(split_texts)
    assert result == expected_output

    split_texts = ["\n"]
    expected_output = []
    result = markdown_splitter._stuff_texts(split_texts)
    assert result == expected_output


def test_stuff_texts_single_chunk(markdown_splitter):
    split_texts = ["# I'm a heading!\nBlah blah blah, how about that text!"]
    expected_output = ["# I'm a heading!\nBlah blah blah, how about that text!"]
    result = markdown_splitter._stuff_texts(split_texts)
    assert result == expected_output


def test_stuff_texts_multiple_chunks(markdown_splitter):
    split_texts = [
        "# I'm a heading!\nBlah blah blah, how about that text!",
        "Here's some more text to put it over the limit.",
    ]
    expected_output = [
        "# I'm a heading!\nBlah blah blah, how about that text!\nHere's some more text to put it over the limit.",
    ]
    result = markdown_splitter._stuff_texts(split_texts)
    assert result == expected_output
