import re

import tiktoken
from bs4 import BeautifulSoup
from llama_index.core.node_parser import SentenceSplitter


class MarkdownSplitter:
    def __init__(self, chunk_size=768, chunk_overlap=100, debug=False):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.debug = debug
        self.splitter = SentenceSplitter(
            chunk_overlap=chunk_overlap, chunk_size=chunk_size
        )
        self.current_headings = {i: None for i in range(1, 7)}
        self.last_table_header = None

    def split_markdown(self, markdown_text: str) -> list:
        split_texts = self._split_with_page_numbers(markdown_text)
        headings_added_texts = self._repeat_headings(split_texts)
        return headings_added_texts

    def _split_with_page_numbers(self, markdown_text: str) -> list:
        last_page_number = None
        split_texts = []
        for t in self.splitter.split_text(markdown_text):
            if self.debug:
                print(f"\nClosing tags for chunk:\n---\n{t}\n---\n")
            closed_text = self._close_page_tags(t)
            if self.debug:
                print(f"\nAfter closing tags:\n---\n{closed_text}\n---\n")
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
                    print(
                        f"\nAfter all split_with_page_numbers logic:\n---\n{closed_text}\n---\n"
                    )
                split_texts.append(closed_text)
        return split_texts

    def _repeat_headings(self, split_texts: list) -> list:
        headings_added_texts = []
        for text in split_texts:
            self.current_headings, min_level = self._get_all_headings(
                text, self.current_headings
            )
            text = self._repeat_table_header_if_necessary(text, self.last_table_header)
            self.last_table_header = self._get_last_table_header(
                text, truncate_tokens=self.chunk_size - self.chunk_overlap
            )
            text = self._prepend_headings(
                self.current_headings, text, to_level=min_level
            )
            headings_added_texts.append(text)
        return headings_added_texts

    def _close_page_tags(self, html_string: str) -> str:
        first_page_tag = re.search(r"<page_\d+>|</page_\d+>", html_string)
        if first_page_tag:
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
                if last_opening_tag_num != last_closing_tag_num:
                    html_string = f"{html_string}\n</page_{last_opening_tag_num}>"
        soup = BeautifulSoup(html_string.strip() + "\n", "html.parser")
        return str(soup).strip()

    def _get_heading(self, line: str):
        match = re.match(r"^#{1,6} ", line)
        if match:
            return len(match.group()) - 1, line[match.end() :]
        return None, None

    def _set_headings(self, headings: dict, heading_level: int, heading_text: str):
        headings[heading_level] = heading_text
        for i in range(heading_level + 1, 7):
            headings[i] = None

    def _prepend_headings(self, headings: dict, text: str, to_level=7) -> str:
        headings2 = {k: v for k, v in headings.items() if k < to_level}
        if not any(headings2.values()):
            return text.strip()
        headings_str = f'{" > ".join([v for v in headings2.values() if v])}'
        return f"<headings>{headings_str}</headings>\n{text}".strip()

    def _get_all_headings(self, text: str, existing_headings: dict):
        lines = text.split("\n")
        headings = existing_headings.copy()
        min_level = 7
        for line in lines:
            level, heading_text = self._get_heading(line)
            if level is not None:
                self._set_headings(headings, level, heading_text)
                min_level = min(min_level, level)
        return headings, min_level

    def _truncate_text_to_tokens(self, text: str, max_tokens: int) -> str:
        tokenizer = tiktoken.get_encoding("cl100k_base")
        tokens = tokenizer.encode(text)
        if len(tokens) > max_tokens:
            tokens = tokens[:max_tokens]
        truncated_text = tokenizer.decode(tokens)
        return truncated_text

    def _get_last_table_header(self, text: str, truncate_tokens: int = None) -> str:
        if truncate_tokens:
            text = self._truncate_text_to_tokens(text, truncate_tokens)
        last_table_row = ""
        last_table_header = ""
        lines = text.split("\n")
        for line in lines:
            if line.startswith("| ---") and line.endswith(" |") and last_table_row:
                last_table_header = last_table_row
                last_table_row = ""
            elif line.startswith("| ") and line.endswith(" |"):
                last_table_row = line
        return last_table_header

    def _repeat_table_header_if_necessary(
        self, text: str, last_table_header: str
    ) -> str:
        if not last_table_header:
            return text
        lines = text.split("\n")
        if len(lines) < 2:
            return text
        first_line_is_table_row = lines[0].startswith("| ") and lines[0].endswith(" |")
        first_line_is_table_header = (
            first_line_is_table_row
            and lines[1].startswith("| ---")
            and lines[1].endswith(" |")
        )
        if (
            first_line_is_table_row
            and not first_line_is_table_header
            and last_table_header
        ):
            return f"{last_table_header}\n{text}"
        return text

    def _token_count(self, text: str) -> int:
        tokenizer = tiktoken.get_encoding("cl100k_base")
        tokens = tokenizer.encode(text)
        return len(tokens)
