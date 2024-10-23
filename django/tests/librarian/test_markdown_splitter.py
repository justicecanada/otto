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


# TODO: Overlap is causing issues with page tags closing. Need to fix this.
# def test_split_with_page_numbers_overlap():
#     markdown_splitter = MarkdownSplitter(chunk_size=30, chunk_overlap=15)
#     markdown_text = """
# <page_1>
# # I'm a heading!
# </page 1>
# <page_2>
# Blah blah blah, how about that text!
# Here's some more text to put it over the limit.
# </page_2>
# """
