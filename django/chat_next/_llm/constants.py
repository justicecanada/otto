"""
Constants for Code Interpreter, file handling, and tool cost tracking.
"""

import os

# File extensions supported by OpenAI Code Interpreter
# See: https://platform.openai.com/docs/assistants/tools/code-interpreter
CODE_INTERPRETER_SUPPORTED_EXTENSIONS = {
    # Programming languages
    ".c",
    ".cpp",
    ".cs",
    ".css",
    ".html",
    ".java",
    ".js",
    ".json",
    ".md",
    ".php",
    ".py",
    ".rb",
    ".sh",
    ".tex",
    ".ts",
    ".txt",
    ".xml",
    # Documents
    ".csv",
    ".doc",
    ".docx",
    ".pdf",
    ".pptx",
    ".xls",
    ".xlsx",
    # Images
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    # Archives and data
    ".pkl",
    ".tar",
    ".tsv",
    ".yaml",
    ".yml",
    ".zip",
}


def _get_file_extension(filename: str) -> str:
    """Extract lowercase file extension from filename."""
    _, ext = os.path.splitext(filename)
    return ext.lower()


def is_code_interpreter_supported(filename: str) -> bool:
    """Check if a file extension is supported by Code Interpreter."""
    return _get_file_extension(filename) in CODE_INTERPRETER_SUPPORTED_EXTENSIONS


# File extensions supported for direct context stuffing (input_file) in the Responses API.
# This is a subset of CODE_INTERPRETER_SUPPORTED_EXTENSIONS; only plain-text and PDF files
# can be sent as input_file items — binary/archive formats cause API validation errors.
# Based on OpenAI API validation: https://platform.openai.com/docs/guides/text
CONTEXT_STUFFING_EXTENSIONS = (
    ".art",
    ".bat",
    ".brf",
    ".c",
    ".cls",
    ".css",
    ".diff",
    ".eml",
    ".es",
    ".h",
    ".hs",
    ".htm",
    ".html",
    ".ics",
    ".ifb",
    ".java",
    ".js",
    ".json",
    ".ksh",
    ".ltx",
    ".mail",
    ".markdown",
    ".md",
    ".mht",
    ".mhtml",
    ".mjs",
    ".nws",
    ".patch",
    ".pdf",
    ".pl",
    ".pm",
    ".pot",
    ".py",
    ".rst",
    ".scala",
    ".sh",
    ".shtml",
    ".srt",
    ".sty",
    ".tex",
    ".text",
    ".txt",
    ".vcf",
    ".vtt",
    ".xml",
    ".yaml",
    ".yml",
)

# Mapping from internal tool type to cost type (used in cost_types.yaml).
# Only tools with cost implications need to be listed here.
TOOL_COST_TYPES = {
    "code_interpreter": "code-interpreter",
}
