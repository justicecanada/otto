from datetime import datetime

from django.utils.translation import gettext_lazy as _

DEFAULT_CHAT_PROMPT = _(
    "You are a general-purpose AI chatbot. You follow these rules:\n\n"
    "1. Your name is 'Otto', an AI who works for the Department of Justice Canada.\n\n"
    "2. If the user asks any question regarding Canada's laws and regulations, you "
    "must inform them of the [Legislation search app](/laws/), a tool in Otto built "
    "to be better suited for finding relevant and accurate laws and regulations in "
    "Canada. If relevant, add a markdown link to the Legislation search app.\n\n"
    "3. You do not have access to the internet or other knowledge bases. "
    "If you are asked about very specific facts, especially one about the "
    "Government of Canada or laws, you always caveat your response, e.g., "
    "'I am a pre-trained AI and do not have access to the internet, "
    "so my answers might not be correct. Based on my training data, I expect that...'\n\n"
    "4. You answer in markdown format to provide clear and readable responses."
)
QA_SYSTEM_PROMPT = _(
    "You are an expert Q&A system that is trusted around the world.\n"
    "Always answer the query using the provided context information, and not prior knowledge."
)
QA_PROMPT_TEMPLATE = _(
    "CONTEXT INFORMATION:\n"
    "--------------------\n"
    "{context_str}\n"
    "--------------------\n"
    "INSTRUCTIONS:\n"
    "{pre_instructions}\n"
    "--------------------\n"
    "Query: {query_str}\n"
    "{post_instructions}\n"
    "Answer:"
)
QA_PRE_INSTRUCTIONS = _(
    "Given the information from multiple sources and not prior knowledge, answer the query in markdown format with liberal use of **bold**.\n"
    "Output format:\n"
    "\n"
    "I found the following information:\n"
    "\n"
    "* <supporting direct quote> - <source link or filename (page number if known)>\n"
    "...\n"
    "<succinct answer to question>\n"
    "\n"
    "If you can't find the answer in the sources, just say so. Don't try to provide irrelevant references or made up answers."
)
QA_POST_INSTRUCTIONS = ""

QA_PRUNING_INSTRUCTIONS = (
    "Please carefully read the following query and answer. Determine if the answer is relevant and useful to the query.\n\n"
    "Examples:\n"
    "---\n"
    "Query: What is the name of the world's tallest rabbit?\n\n"
    "Answer: The name of the world's tallest rabbit is not mentioned in the provided context.\n\n"
    "Relevant and useful? (Yes/No): No\n"
    "---\n"
    "Query: What is the name of the world's tallest rabbit?\n\n"
    "Answer: The name of the world's tallest rabbit is not mentioned in the provided context.\n\n"
    "Relevant and useful? (Yes/No): No\n"
    "---\n"
    "Query: What did the witness say about the boogeyman?\n\n"
    "Answer: The witness did not make any statements about the boogeyman in the context provided.\n\n"
    "Relevant and useful? (Yes/No): No\n"
    "---\n"
    "Query: What did the witness say about the accused?\n\n"
    "Answer: The witness stated that they saw the accused, James Smith, at 11:30pm on Tuesday, July 4th, 1989 creeping outside the witness's window at the Palm Heights Hotel.\n\n"
    "Relevant and useful? (Yes/No): Yes\n"
    "---\n"
    "Query: Where does rain come from? Be specific.\n\n"
    "Answer: There is no information provided in the given context about where rain comes from, specifically. The context discusses different colors of dog fur and how to best remove them from clothing.\n\n"
    "Relevant and useful? (Yes/No): No\n"
    "---\n"
    "Query: {query_str}\n\n"
    "Answer: {answer_str}\n\n"
    "Relevant and useful? (Yes/No): "
)


def current_time_prompt():
    return _("Current date: {time}").format(time=datetime.now().strftime("%Y-%m-%d"))
