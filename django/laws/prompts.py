# Laws AI answer prompts
from django.utils.translation import gettext_lazy as _

# NOTE: Based on the default system prompt from llama-index source code.
system_prompt_tmpl = (
    "Formatting re-enabled\n"
    "You are an expert Q&A system that is trusted around the world.\n"
    "Always answer the query using the provided context information, "
    "and not prior knowledge.\n"
    "Some rules to follow:\n"
    "1. Never directly reference the given context in your answer.\n"
    "2. Avoid statements like 'Based on the context, ...' or "
    "'The context information ...' or anything along "
    "those lines."
)

qa_prompt_instruction_tmpl = (
    "<context>\n"
    "{context_str}\n"
    "</context>\n"
    "<instruction>\n"
    "{additional_instructions}\n"
    "Given the context information and not prior knowledge, "
    "answer the query below.\n"
    "</instruction>\n"
    "<query>\n"
    "{query_str}\n"
    "</query>"
)

default_additional_instructions = _(
    """If the context information is entirely unrelated to the provided query, don't try to answer the question; just say 'Sorry, I cannot answer that question.'.

Use markdown formatting (headings, LaTeX math, tables, etc) as necessary including the liberal use of bold.

Cite sources inline, DIRECTLY after a sentence that makes a claim using the exact title of the section, as an anchor link to the section_id.

For example: "Murder is a crime [My Example Act, Section 3(2)](#SOR-2020-123_eng_subsection_3(2)). First degree murder requires premeditation [My Example Act, Section 1](#SOR-2020-123_eng_section_1).".

If there are multiple answers depending on contextual factors, detail each scenario."""
)
