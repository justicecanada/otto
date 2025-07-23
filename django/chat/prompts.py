from datetime import datetime

from django.utils.translation import gettext_lazy as _

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
    return (
        _("Current date: {time}").format(time=datetime.now().strftime("%Y-%m-%d"))
        + "\n"
    )
