import re
from datetime import datetime

from django.utils.translation import gettext as _

import extract_msg
import PyPDF2
import tiktoken
from langchain.prompts import PromptTemplate
from llama_index.core import PromptTemplate
from llama_index.core.prompts import PromptType
from structlog import get_logger

from chat.llm import OttoLLM
from text_extractor.utils import create_searchable_pdf


def extract_email_details(file_path):
    msg = extract_msg.Message(file_path)
    msg_date = msg.date

    if isinstance(msg_date, str):
        msg_date = datetime.strptime(msg_date, "%a, %d %b %Y %H:%M:%S %z")

    body = msg.body or ""
    preview_context = (
        " ".join(body.split(". ")[:2]) + "..." if body else "No content available"
    )
    if len(preview_context) > 0:
        preview_context = summary(body)

    attachments = []
    for attachment in msg.attachments:
        saved_result = attachment.save()
        if isinstance(saved_result, tuple):
            url = saved_result[1]
        else:
            url = saved_result

        mime = attachment.extension.replace(attachment.extension[0], "", 1)
        a = {"url": url, "mime": mime, "name": attachment.name}

        if mime == "pdf":
            print(f"Processing PDF: {attachment.name}")
            try:
                with open(url, "rb") as pdf_file:

                    # Uses text extractor to extract text from Image-based PDFs
                    ocr_pdf, extracted_text, _ = create_searchable_pdf(
                        pdf_file, add_header=False
                    )
                    pdf_summary = summary(extracted_text)
                    print(
                        f"\n\n{'-'*80}\nSummary for {attachment.name}:\n{pdf_summary}\n{'-'*80}\n\n"
                    )
                    a["ocr_summary"] = pdf_summary

            except Exception as e:
                print(f"Error reading PDF {attachment.name}: {e}")
                a["ocr_summary"] = "OCR failed"

        attachments.append(a)

    return {
        "sender": msg.sender,
        "receiver": msg.to,
        "sent_date": msg_date,
        "preview_context": preview_context,
        "attachments": attachments,
        "subject": msg.subject,
        "participants": msg.recipients if msg.recipients else [],
    }


def clean_subject(subject):
    """
    Remove common prefixes like 'RE:', 'Re:', 'FW:', 'Fw:', and whitespace from the
    subject.
    """
    if subject:
        return re.sub(r"^(re:\s*|fw:\s*)", "", subject, flags=re.IGNORECASE).strip()
    return subject


def summary(text):

    text = str(text)
    model = "gpt-4o"  # can change this later on
    llm = OttoLLM(model)
    summary = summarize_long_text_direct(text, llm, length="short")
    return summary


def summarize_long_text_direct(
    text,
    llm,
    length="short",
    target_language="en",
    custom_prompt=None,
    gender_neutral=True,
    instructions=None,
):

    gender_neutral_instructions = {
        "en": "Avoid personal pronouns unless the person's gender is clearly indicated.",
        "fr": "Évitez les pronoms personnels sauf si le genre de la personne est clairement indiqué.",
    }
    text = str(text)
    if len(text) == 0:
        return _("No text provided.")

    length_prompts = {
        "short": {
            "en": """<document>
{docs}
</document>
<instruction>
Write a TL;DR summary of document in English - 3 or 4 sentences max. If document is shorter than this, just output the document verbatim.
</instruction>
TL;DR:
""",
            "fr": """<document>
{docs}
</document>
<instruction>
Écrivez un résumé "TL;DR" en français - 3 ou 4 phrases maximum. Si le document est plus court, affichez-le tel quel.
</instruction>
Résumé :
""",
        },
        "medium": {
            "en": """<document>
{docs}
</document>
<instruction>
Rewrite the text (in English) in a medium sized summary format and make sure the length is around two or three paragraphs. If document is shorter than this, just output the document verbatim.
</instruction>
Summary:
""",
            "fr": """<document>
{docs}
</document>
<instruction>
Réécrivez le texte (en anglais) sous forme de résumé moyen et assurez-vous que la longueur est d'environ deux ou trois paragraphes. Si le document est plus court, affichez-le tel quel.
</instruction>
Résumé :
""",
        },
        "long": {
            "en": """<document>
{docs}
</document>
<instruction>
Rewrite the text (in English) as a detailed summary, using multiple paragraphs if necessary. (If the input is short, output 1 paragraph only)

Some rules to follow:
* Simply rewrite; do not say "This document is about..." etc. Include *all* important details.
* There is no length limit - be as detailed as possible.
* **Never extrapolate** on the text. The summary must be factual and not introduce any new ideas.
* If document is short, just output the document verbatim.
</instruction>
Detailed summary:
""",
            "fr": """<document>
{docs}
</document>
<instruction>
Réécrivez le texte (en anglais) sous forme de résumé détaillé, en utilisant plusieurs paragraphes si nécessaire. (Si la saisie est courte, affichez 1 seul paragraphe)

Quelques règles à suivre :
* Réécrivez simplement ; ne dites jamais "Ce document concerne..." etc. Incluez *tous* les détails importants.
* Il n'y a pas de limite de longueur : soyez aussi détaillé que possible.
* **Ne faites jamais d'extrapolation** sur le texte. Le résumé doit être factuel et ne doit pas introduire de nouvelles idées.
* Si le document est court, affichez-le tel quel.
</instruction>
Résumé détaillé :
""",
        },
    }

    if custom_prompt and "{docs}" in custom_prompt:
        length_prompt_template = custom_prompt
    elif custom_prompt:
        length_prompt_template = (
            """
<document>
{docs}
</document>
<instruction>
"""
            + f"{custom_prompt}\n</instruction>"
        )
    else:
        length_prompt_template = length_prompts[length][target_language]
        if gender_neutral:
            length_prompt_template = length_prompt_template.replace(
                "</instruction>",
                gender_neutral_instructions[target_language] + "\n</instruction>",
            )
        if instructions:
            length_prompt_template = length_prompt_template.replace(
                "</instruction>", instructions + "\n</instruction>"
            )

    # Tree summarizer prompt requires certain variables
    # Note that we aren't passing in a query here, so the query will be empty
    length_prompt_template = length_prompt_template.replace("{docs}", text)
    template = PromptTemplate(length_prompt_template, prompt_type=PromptType.SUMMARY)

    response = llm.complete(prompt=template.format())
    return response
