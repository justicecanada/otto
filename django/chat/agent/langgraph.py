import os

from django.conf import settings

from langchain_openai import AzureChatOpenAI
from langgraph.prebuilt import create_react_agent

from .tools.tool_registry import AVAILABLE_TOOLS


def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"


langchain_model = AzureChatOpenAI(
    azure_deployment="gpt-4.1",
    model="gpt-4.1",
    api_key=settings.AZURE_OPENAI_KEY,
    azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
    api_version=settings.AZURE_OPENAI_VERSION,
    streaming=True,
)
langgraph_agent = create_react_agent(
    model=langchain_model, tools=[get_weather], prompt="You are a helpful assistant"
)


async def langgraph_agent_replacer(response_stream):
    # Collect tool call steps and extract final answer from langgraph response stream
    import json

    steps = []
    final_answer = ""

    async for langgraph_message in response_stream:
        print("====langgraph step====")
        print(langgraph_message)
        messages = langgraph_message.get("messages", [])
        if not messages:
            continue
        msg = messages[-1]
        msg_type = type(msg).__name__
        # 1. Tool call (AIMessage with tool_calls in additional_kwargs, no content)
        if (
            msg_type == "AIMessage"
            and hasattr(msg, "additional_kwargs")
            and msg.additional_kwargs
            and msg.additional_kwargs.get("tool_calls")
            and (not getattr(msg, "content", None) or not msg.content.strip())
        ):
            tool_calls = msg.additional_kwargs["tool_calls"]
            for call in tool_calls:
                tool_name = call.get("name")
                args = call.get("args")
                if not args and "function" in call:
                    raw_args = call["function"].get("arguments")
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except Exception:
                        args = raw_args or {}
                    tool_name = call["function"].get("name", tool_name)
                if tool_name in AVAILABLE_TOOLS:
                    tool_name = str(AVAILABLE_TOOLS[tool_name]["name"])
                steps.append({"name": tool_name, "arguments": args or {}})
                yield final_answer, steps
        # 2. Tool output (ToolMessage)
        elif msg_type == "ToolMessage":
            output = getattr(msg, "content", None)
            steps.append({"output": output})
            if output:
                yield final_answer, steps
        # 3. Final answer (AIMessage with content)
        elif msg_type == "AIMessage":
            content = getattr(msg, "content", None)
            if content and content.strip():
                final_answer = content.strip()
    yield final_answer, steps
