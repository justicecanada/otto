import re
from typing import List, Tuple

import tiktoken
from bs4 import BeautifulSoup
from llama_index.core.node_parser import SentenceSplitter
from structlog import get_logger

logger = get_logger(__name__)


class MarkdownSplitter:
    def __init__(self, chunk_size=768, chunk_overlap=100, debug=False):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.debug = debug
        self.current_headings = {i: None for i in range(1, 7)}
        self.last_table_header = None

    def split_markdown(self, markdown_text: str) -> List[str]:
        """
        Split markdown into chunks.
        Preserves <page_n> tags and includes heading breadcrumbs.
        Repeats table headers when a table is split across chunks.
        """
        split_texts = self._split_with_page_numbers(markdown_text)
        headings_added_texts = self._repeat_headings(split_texts)
        return headings_added_texts

    def _split_with_page_numbers(self, markdown_text: str) -> List[str]:
        """
        Splits text into chunks while preserving <page_n> tags.
        Corrects missing or extra page tags due to splitting.
        """
        # First, split by line breaks. This preserves table rows, header tags, etc.
        line_split_texts = []
        current_tokens = 0
        current_text = ""
        # TODO: This could be improved to avoid the following scenarios:
        # 1. Chunk ends with a heading
        # 2. Chunk ends with a table header (with or without underline row)
        # Although this content would be repeated in the next chunk,
        # it would improve retrieval to simply split right before these elements.
        for line in markdown_text.split("\n"):
            line_tokens = self._token_count(line)
            if current_tokens + line_tokens > self.chunk_size:
                line_split_texts.append(current_text)
                current_text = ""
                current_tokens = 0
            current_text += line + "\n"
            current_tokens += line_tokens
        if current_text:
            line_split_texts.append(current_text)

        # Ensure each chunk is within token limits. Lines > chunk_size will get split.
        sentence_split_texts = []
        sentence_splitter = SentenceSplitter(
            chunk_overlap=self.chunk_overlap, chunk_size=self.chunk_size
        )
        for text in line_split_texts:
            sentence_split_texts.extend(sentence_splitter.split_text(text))

        # Fix page tags.
        last_page_number = None
        split_texts = []
        for t in sentence_split_texts:
            if self.debug:
                logger.debug(f"\nClosing tags for chunk:\n---\n{t}\n---\n")
            try:
                closed_text = self._close_page_tags(t)
            except Exception as e:
                logger.error(f"Error closing page tags: {e}")
                closed_text = t
            closing_tags = re.findall(r"</page_\d+>", closed_text)
            if last_page_number and not closing_tags:
                closed_text = f"<page_{last_page_number}>\n{closed_text}\n</page_{last_page_number}>"
            elif closing_tags:
                last_page_number = int(re.search(r"\d+", closing_tags[-1]).group())
            # Make sure that there is content OTHER than page tags alone.
            lines = closed_text.split("\n")
            content_lines = [L for L in lines if not re.match(r"</?page_\d+>", L)]
            if "".join(content_lines).replace("\n", "").strip():
                if self.debug:
                    logger.debug(
                        f"\nAfter all split_with_page_numbers logic:\n---\n{closed_text}\n---\n"
                    )
                split_texts.append(closed_text)
        return split_texts

    def _close_page_tags(self, html_string: str) -> str:
        """
        Corrects missing or extra page tags within a single chunk, e.g.
        * chunk starts midway through a page (no <page_n> at start)
        * chunk ends midway through a page (no </page_n> at end)
        * chunk starts with </page_n-1> (with no content for that page)
        * chunk ends with <page_n+1> (with no content for that page)
        """
        html_string = html_string.strip()
        # Closing tag as the first line?
        if html_string.split("\n", 1)[0].startswith("</page_"):
            html_string = html_string.split("\n", 1)[1]
        # Opening tag as the last line?
        if html_string.rsplit("\n", 1)[-1].startswith("<page_"):
            html_string = html_string.rsplit("\n", 1)[0]
        first_page_tag = re.search(r"<page_\d+>|</page_\d+>", html_string)
        if first_page_tag:
            # Missing opening tag at start?
            if first_page_tag.group().startswith("</page_"):
                first_page_num = int(re.search(r"\d+", first_page_tag.group()).group())
                html_string = f"<page_{first_page_num}>\n{html_string}"
            opening_tags = re.findall(r"<page_\d+>", html_string)
            last_opening_tag = opening_tags[-1] if opening_tags else None
            closing_tags = re.findall(r"</page_\d+>", html_string)
            last_closing_tag = closing_tags[-1] if closing_tags else None
            if last_opening_tag and last_closing_tag:
                last_opening_tag_num = int(re.search(r"\d+", last_opening_tag).group())
                last_closing_tag_num = int(re.search(r"\d+", last_closing_tag).group())
                if self.debug:
                    logger.debug(
                        f"\nLast opening tag: {last_opening_tag_num}, last closing tag: {last_closing_tag_num}"
                    )
                # Missing closing tag at end?
                if last_opening_tag_num != last_closing_tag_num:
                    html_string = (
                        f"{html_string.strip()}\n</page_{last_opening_tag_num}>"
                    )
        # Catch any additional issues with HTML tags
        soup = BeautifulSoup(html_string.strip() + "\n", "html.parser")
        output = str(soup).strip()
        if self.debug:
            logger.debug(f"\nAfter _close_tags:\n---\n{output}\n---\n")
        return output

    def _get_heading(self, line: str) -> Tuple[int, str]:
        """
        Returns the heading level and text if the line is a heading.
        Returns (None, None) if the line is not a heading.
        """
        match = re.match(r"^#{1,6} ", line)
        if match:
            return len(match.group()) - 1, line[match.end() :]
        return None, None

    def _set_headings(
        self, headings: dict, heading_level: int, heading_text: str
    ) -> None:
        """
        Modifies the headings dictionary in place to reflect the current heading.
        Resets all headings below the current level.
        """
        if heading_level < 1 or heading_level > 6:
            return
        headings[heading_level] = heading_text
        for i in range(heading_level + 1, 7):
            headings[i] = None

    def _prepend_headings(self, headings: dict, text: str, to_level=7) -> str:
        """
        Converts the headings dictionary into a breadcrumb trail (Heading 1 > Heading 2)
        and prepends it to the text, wrapped in a <headings> tag.
        Only includes headings above the specified level.
        """
        headings2 = {k: v for k, v in headings.items() if k < to_level}
        if not any(headings2.values()):
            return text
        headings_str = f'{" > ".join([v for v in headings2.values() if v])}'
        return f"<headings>{headings_str}</headings>\n{text}"

    def _get_all_headings(
        self, text: str, existing_headings: dict
    ) -> Tuple[dict, dict, int]:
        """
        Extracts all markdown headings from the text.
        Returns a tuple with:
        * updated copy of headings dictionary (state at the end of the text),
        * headings to prepend (usually the same as the updated headings),
        * the minimum level of the headings to prepend
        Note on "minimum": "# This" is level 1, "## This" is level 2, etc.
        """
        lines = text.split("\n")
        headings = existing_headings.copy()
        min_level = 7
        last_level = 0
        headings_for_prepend = None
        for line in lines:
            level, heading_text = self._get_heading(line)
            if level is not None:
                if level <= last_level and not headings_for_prepend:
                    headings_for_prepend = existing_headings
                self._set_headings(headings, level, heading_text)
                if not headings_for_prepend:
                    min_level = min(min_level, level)
                last_level = level
        return (
            headings,
            headings_for_prepend if headings_for_prepend else headings,
            min_level,
        )

    def _is_table_row(self, line):
        return line.startswith("| ") and line.endswith(" |")

    def _is_table_underline(self, line):
        return line.startswith("| ---") and line.endswith(" |")

    def _get_last_table_header(self, text: str) -> str:
        """
        Extracts the last table header from a markdown text.
        """
        last_table_row = ""
        last_table_header = ""
        lines = text.split("\n")
        # Remove the page number tags (Opening or closing)
        lines = [line for line in lines if not re.match(r"</?page_\d+>", line)]
        for line in lines:
            if self._is_table_underline(line) and last_table_row:
                last_table_header = last_table_row + "\n" + line
                last_table_row = ""
            elif self._is_table_row(line):
                last_table_row = line
        # Edge case: table header is the last line
        if (
            len(lines) > 1
            and self._is_table_row(lines[-1])
            and not self._is_table_row(lines[-2])
        ):
            last_table_header = last_table_row
        return last_table_header

    def _repeat_table_header_if_necessary(
        self, text: str, last_table_header: str
    ) -> str:
        """
        Prepends the last table header to the text if:
        * there is a previous table header AND
        * the text starts with a table row but not a table header
        """
        if not last_table_header:
            return text
        original_text = text

        # Remove page tags from the start of the text but keep them for later
        lines = text.split("\n")
        if not lines:
            return original_text
        prepend_line = ""
        if lines[0].startswith("<page_") and lines[0].endswith(">"):
            prepend_line = lines[0] + "\n"
            lines = lines[1:]
            text = "\n".join(lines)
        if len(lines) < 2:
            return original_text
        first_line_is_table_row = lines[0].startswith("| ") and lines[0].endswith(" |")
        if self.debug:
            logger.debug(f"First line is table row: {first_line_is_table_row}")
        first_line_is_table_header = (
            first_line_is_table_row
            and lines[1].startswith("| ---")
            and lines[1].endswith(" |")
        )
        if self.debug:
            logger.debug(f"First line is table header: {first_line_is_table_header}")
        if (
            first_line_is_table_row
            and not first_line_is_table_header
            and last_table_header
        ):
            return f"{prepend_line}{last_table_header}\n{text}"
        return original_text

    def _repeat_headings(self, split_texts: List[str]) -> List[str]:
        """
        Prepends table headers and headings at the start of the next chunk
        when a table and/or section is split across chunks.
        If a table is split across chunks, the header row is repeated.
        if a section is split across chunks, parent headings are added as breadcrumbs.
        """
        headings_added_texts = []
        for text in split_texts:
            self.current_headings, headings_for_prepend, min_level = (
                self._get_all_headings(text, self.current_headings)
            )
            text = self._repeat_table_header_if_necessary(text, self.last_table_header)
            self.last_table_header = self._get_last_table_header(text)
            text = self._prepend_headings(
                headings_for_prepend, text, to_level=min_level
            )
            headings_added_texts.append(text)
        return headings_added_texts

    def _token_count(self, text: str) -> int:
        tokenizer = tiktoken.get_encoding("cl100k_base")
        tokens = tokenizer.encode(text)
        return len(tokens)
