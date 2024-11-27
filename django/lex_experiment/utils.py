import concurrent.futures

from django.conf import settings

from openai import AzureOpenAI
from structlog import get_logger

logger = get_logger(__name__)

from otto.models import Cost


def lex_prompts(content):

    client = AzureOpenAI(
        api_key=settings.AZURE_OPENAI_KEY,
        api_version=settings.AZURE_OPENAI_VERSION,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
    )
    model_str = settings.DEFAULT_CHAT_MODEL
    system_prompt = "You are a legal professional tasked with reviewing a legal document. Your job is to extract and write down specific information from the content provided. Use only the facts presented in the content. When extracting information, write only the relevant details without any additional context or explanation. If the information is not found, simply state 'Not found' without providing further details."

    client = AzureOpenAI(
        api_key=settings.AZURE_OPENAI_KEY,
        api_version=settings.AZURE_OPENAI_VERSION,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
    )
    model_str = settings.DEFAULT_CHAT_MODEL

    questions = [
        "What is the Tax Court of Canada Court No.?",
        "What is the Appellant or Appellants Name?",
        "What is the Appellant's address?",
        "What is the Tax Court of Canada Class Level?",
        "What is the filed date of the Notice of Appeal? Please give the date in the format of YYYY-MM-DD.",
        "What is the Representative's name?",
        "What is the Representative's address?",
        "What are the taxation years?",
        "What is the total tax amount?",
        "What section or subsections are referred to in the Notice of Appeal?",
    ]

    def get_answer(question):
        response = client.chat.completions.create(
            model=model_str,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "### Content: " + content},
                {"role": "user", "content": "### Question: " + question},
            ],
            temperature=0.1,
        )
        return response.choices[0].message.content

    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = list(executor.map(get_answer, questions))

    # Calculate the cost based on the number of API calls
    num_api_calls = len(questions)
    cost = Cost.objects.new(cost_type="gpt-4o-mini-in", count=num_api_calls)
    # llm = OttoLLM()
    # cost = llm.create_costs()

    return [
        {"question": question, "answer": answer}
        for question, answer in zip(questions, results)
    ], (cost.usd_cost if cost else 0)
