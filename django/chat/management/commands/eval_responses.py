"""
True end-to-end evaluation of Otto Corporate QA code, using the actual Views and Models.
Assumes that the corporate knowledge base is loaded into the database.
"""

import json
import os
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

import tqdm
import yaml
from django_extensions.management.utils import signalcommand
from llama_index.core import ChatPromptTemplate, PromptTemplate, Settings
from llama_index.core.llms import ChatMessage, MessageRole
from structlog import get_logger

from chat.llm import OttoLLM
from chat.models import Chat, ChatOptions, Message
from chat.responses import chat_response, qa_response
from librarian.models import Library

logger = get_logger(__name__)

UserModel = get_user_model()

llm = OttoLLM(deployment="gpt-4o")
embed_model = llm.embed_model


Settings.llm = llm
Settings.embed_model = embed_model


# Copy/pasting some of the LlamaIndex eval code here because the FaithfulnessEvaluator
# and CorrectnessEvaluator keep timing out (annoying!)

FAITHFULNESS_TEMPLATE = PromptTemplate(
    "Please tell if a given piece of information "
    "is supported by the context.\n"
    "You need to answer with either YES or NO.\n"
    "Answer YES if any of the context supports the information, even "
    "if most of the context is unrelated. "
    "Also answer YES if the information makes a correct statement about the context "
    "not containing certain information. "
    "Some examples are provided below. \n\n"
    "Information: Apple pie is generally double-crusted.\n"
    "Context: An apple pie is a fruit pie in which the principal filling "
    "ingredient is apples. \n"
    "Apple pie is often served with whipped cream, ice cream "
    "('apple pie � la mode'), custard or cheddar cheese.\n"
    "It is generally double-crusted, with pastry both above "
    "and below the filling; the upper crust may be solid or "
    "latticed (woven of crosswise strips).\n"
    "Answer: YES\n"
    "Information: Apple pies tastes bad.\n"
    "Context: An apple pie is a fruit pie in which the principal filling "
    "ingredient is apples. \n"
    "Apple pie is often served with whipped cream, ice cream "
    "('apple pie � la mode'), custard or cheddar cheese.\n"
    "It is generally double-crusted, with pastry both above "
    "and below the filling; the upper crust may be solid or "
    "latticed (woven of crosswise strips).\n"
    "Answer: NO\n"
    "Information: I'm sorry, but there is no information in the context about George of the Jungle.\n"
    "Context: The statue of liberty was a gift from France to the United States. "
    "The statue was dedicated on October 28, 1886.\n"
    "Answer: YES\n"
    "Information: {generated_answer}\n"
    "Context: {context_str}\n"
    "Answer: "
)


CORRECTNESS_SYSTEM_TEMPLATE = """
You are an expert evaluation system for a question answering chatbot.

You are given the following information:
- a user query, and
- a generated answer

You may also be given a reference answer to use for reference in your evaluation.

Your job is to judge the relevance and correctness of the generated answer.
Output a single score that represents a holistic evaluation.
You must return your response in a line with only the score.
Do not return answers in any other format.
On a separate line provide your reasoning for the score as well.

Follow these guidelines for scoring:
- Your score has to be between 1 and 5, where 1 is the worst and 5 is the best.
- If the generated answer is not relevant to the user query, \
you should give a score of 1.
- If the generated answer is relevant but contains mistakes, \
you should give a score between 2 and 3.
- If the generated answer is relevant and fully correct, \
you should give a score between 4 and 5.

Example Response:
4.0
The generated answer has the exact same metrics as the reference answer, \
    but it is not as concise.

"""

CORRECTNESS_USER_TEMPLATE = """
## User Query
{query}

## Reference Answer
{reference_answer}

## Generated Answer
{generated_answer}
"""

CORRECTNESS_TEMPLATE = ChatPromptTemplate(
    message_templates=[
        ChatMessage(role=MessageRole.SYSTEM, content=CORRECTNESS_SYSTEM_TEMPLATE),
        ChatMessage(role=MessageRole.USER, content=CORRECTNESS_USER_TEMPLATE),
    ]
)


def exponential_backoff_retry(func, max_retries=5, base_delay=1):
    """Retry a function with exponential backoff."""
    delay = base_delay
    for i in range(max_retries):
        try:
            return func()
        except Exception as e:
            logger.error(f"Attempt {i + 1} failed: {e}")
            time.sleep(delay)
            delay *= 2
    return {}  # Return empty dict if all retries fail


class Command(BaseCommand):
    help = "Evaluate the chatbot on a set of reference questions and answers."

    def add_arguments(self, parser):
        parser.add_argument(
            "filename",
            type=str,
            help="Specify filename to load the evaluation set from (in django/chat/eval/)",
        )

    @signalcommand
    def handle(self, *args, **options):
        filename = options.get("filename")
        user = _create_test_user()

        # Load YAML file to dict
        path = os.path.join(settings.BASE_DIR, "chat", "eval", filename)
        with open(path, "r") as file:
            eval_set = yaml.safe_load(file)

        # Evaluate each instance
        results = []
        for eval_instance in tqdm.tqdm(eval_set):
            try:
                results.append(_test_qa_response(eval_instance, user))
            except Exception as e:
                logger.error(f"Failed to evaluate instance: {e}")

        # Save results to file
        results_path = os.path.join(
            settings.BASE_DIR,
            "chat",
            "eval",
            "results",
            f"{filename.split('.')[0]}.json",
        )
        with open(results_path, "w") as file:
            json.dump(results, file)

        # Print out the numeric results
        faithfulness_scores = [
            r["faithfulness"]
            for r in results
            if "faithfulness" in r and r["faithfulness"] is not None
        ]
        correctness_scores = [
            r["correctness"]
            for r in results
            if "correctness" in r and r["correctness"] is not None
        ]
        sources_found = [
            r["p_sources_found"]
            for r in results
            if "p_sources_found" in r and r["p_sources_found"] is not None
        ]

        try:
            logger.debug(
                f"Average faithfulness: {100 * sum(faithfulness_scores) / len(faithfulness_scores):.2f}%"
            )
        except ZeroDivisionError:
            logger.error("No faithfulness scores available.")

        try:
            logger.debug(
                f"Average correctness: {100 * sum(correctness_scores) / len(correctness_scores):.2f}%"
            )
        except ZeroDivisionError:
            logger.error("No correctness scores available.")

        try:
            logger.debug(
                f"Average % sources found: {100 * sum(sources_found) / len(sources_found):.2f}%"
            )
        except ZeroDivisionError:
            logger.error("No sources found scores available.")

        user.delete()

        # System output indicating to user completeness
        self.stdout.write(
            self.style.SUCCESS(
                f"Evaluated {len(eval_set)} instances and saved results to {results_path}. View with eval_results.ipynb"
            )
        )


def _create_test_user():
    # Check if user exists
    user = UserModel.objects.filter(upn="eval_user").first()
    if user:
        user.delete()
    user = UserModel.objects.create_user(upn="eval_user", password="password")
    # Add user to group "Otto admin"
    user.groups.add(Group.objects.get(name="Otto admin"))
    user.save()
    return user


def _test_qa_response(eval_instance, user):
    try:
        # Create chat and build chat history
        chat = Chat.objects.create(user=user, mode=eval_instance["mode"])
        chat.save()
        chat_options = ChatOptions.objects.get(chat=chat)
        for message in eval_instance["history"]:
            if message.get("user", None):
                last_message = Message.objects.create(
                    chat=chat, text=message["user"], is_bot=False
                )
            elif message.get("ai", None):
                last_message = Message.objects.create(
                    chat=chat, text=message["ai"], is_bot=True
                )
            if "library_name" in eval_instance:
                library = Library.objects.get(name=eval_instance["library_name"])

        if library is not None:
            chat_options.qa_library = library
            chat_options.save()

        # Expected answer will be evaluated against the response by GPT-4
        expected_answer = eval_instance["responses"][0]

        # Expected sources are evaluated on substring match with returned sources
        expected_sources = eval_instance.get("sources", [])

        response_message = Message.objects.create(
            chat=chat, is_bot=True, parent=last_message
        )
        response_message.save()

        # Get response from appropriate chat function
        logger.debug(f"Asking Otto: {last_message.text}")
        logger.debug(f"(Expected answer: {expected_answer})")
        if eval_instance["mode"] == "qa":
            chat.save()
            chat.refresh_from_db()
            response = qa_response(chat, response_message)
            list(
                response
            )  # Need to exhaust the StreamingHttpResponse generator for message text to update
            response_message.refresh_from_db()
            response_str = response_message.text
            source_nodes = Message.objects.get(id=response_message.id).sources.all()
            logger.info(f"Response from Otto: {response_str}")
        elif eval_instance["mode"] == "chat":
            response_str = chat_response(chat, response_message, eval=True)
            logger.debug(f"Response from Otto: {response_str}")
        else:
            logger.debug(f"Mode {eval_instance['mode']} not recognized, skipping...")
            return {}
        response_sources = [source_node.node_text for source_node in source_nodes]

        from concurrent.futures import ThreadPoolExecutor

        # We want to run _eval_correctness, _eval_faithfulness and _eval_sources in parallel
        correctness_eval = None
        faithfulness_eval = None
        sources_eval = None

        with ThreadPoolExecutor(max_workers=3) as executor:
            correctness_future = executor.submit(
                _eval_correctness, last_message.text, response_str, expected_answer
            )
            if eval_instance["mode"] == "qa":
                faithfulness_future = executor.submit(
                    _eval_faithfulness, response_str, response_sources
                )
                sources_future = executor.submit(
                    _eval_sources, response_sources, expected_sources
                )

            correctness_eval = correctness_future.result()
            if eval_instance["mode"] == "qa":
                faithfulness_eval = faithfulness_future.result()
                sources_eval = sources_future.result()

        # Clean up: Delete chat
        chat.delete()
        return {
            "conversation": eval_instance["history"],
            "expected_response": expected_answer,
            "returned_response": response_str,
            "expected_sources": expected_sources,
            "returned_sources": response_sources,
            "faithfulness": faithfulness_eval,
            "correctness": correctness_eval,
            "p_sources_found": sources_eval,
            "mostly_correct": correctness_eval > 0.5,
            "any_sources_found": None if sources_eval is None else sources_eval > 0,
        }
    except Exception as e:
        logger.debug(f"Error: {e}")
        return {}


def _eval_faithfulness(response_str, response_sources):
    eval_response = llm.predict(
        prompt=FAITHFULNESS_TEMPLATE,
        generated_answer=response_str,
        context_str=response_sources,
    )
    return 1.0 if "yes" in eval_response.lower() else 0.0


def _eval_correctness(query_str, response_str, expected_answer):
    eval_response = llm.predict(
        prompt=CORRECTNESS_TEMPLATE,
        query=query_str,
        generated_answer=response_str,
        reference_answer=expected_answer,
    )
    # Normally a score between 1 and 5 - let's normalize to range (0, 1)
    return (float(eval_response.split("\n")[0]) - 1) / 4


def _eval_sources(response_sources, expected_sources, debug=False):
    # Special case: When no sources are expected, this test doesn't apply
    if len(expected_sources) == 0:
        return None
    logger.info(f"Response sources: {[r[:100] for r in response_sources]}")
    logger.info(f"Expected sources: {[e[:100] for e in expected_sources]}")
    source_positions = [-1] * len(expected_sources)
    for i, source in enumerate(expected_sources):
        for j, response_source in enumerate(response_sources):
            if source in response_source:
                if debug:
                    logger.info(f"Source {i} found at position {j}: {source}")
                source_positions[i] = j
                break
    for i, source in enumerate(expected_sources):
        if source_positions[i] == -1:
            if debug:
                logger.debug(f"Source {i} not found: {source}")
    # Return the proportion of sources found
    p_found = 1 - source_positions.count(-1) / len(source_positions)
    return p_found
