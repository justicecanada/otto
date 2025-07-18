import os


def agent_response_string(user_message, chat):
    """
    Generate a response string for the agent based on the user message and chat context.
    """
    # Placeholder for actual agent response logic
    # return f"Agent response to: {user_message} in chat: {chat.id}"

    from smolagents import CodeAgent, LiteLLMModel, VisitWebpageTool, WebSearchTool

    model = LiteLLMModel(
        model_id="azure/gpt-4.1",
        api_base=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version=os.environ.get("AZURE_OPENAI_VERSION"),
    )

    # Create an agent with no tools
    agent = CodeAgent(tools=[WebSearchTool(), VisitWebpageTool()], model=model)

    # Run the agent with a task
    result = agent.run(user_message.text, stream=False)
    return str(result)


def format_tool_call(tool_call):
    """
    Format a ToolCall or ActionStep object for display.
    """
    # Try to extract the most relevant fields
    lines = []

    if type(tool_call).__name__ == "ActionStep":
        # Show the step number if available
        # step_number = getattr(tool_call, "step_number", None)
        # if step_number is not None:
        #     lines.append(f"### Step {step_number}\n")

        # Show the LLM's output/thoughts if available
        # Try common attribute names
        llm_output = getattr(tool_call, "model_output_message", None)
        if llm_output and hasattr(llm_output, "content"):
            lines.append(
                llm_output.content.replace("<code>", "```python")
                .replace("</code>", "```\n")
                .replace("Thought: ", "")
            )
        lines.append("\n\n---\n\n")

    elif type(tool_call).__name__ == "FinalAnswerStep":
        # Show the final answer
        lines.append("### Final Answer\n")
        final_answer = getattr(tool_call, "output", None)
        if final_answer:
            lines.append(
                final_answer.replace("<code>", "```").replace("</code>", "```\n")
            )

    return "\n".join(lines)


def agent_response_generator(user_message, chat):
    """
    Generate a response string for the agent based on the user message and chat context.
    """
    # Placeholder for actual agent response logic
    # return f"Agent response to: {user_message} in chat: {chat.id}"

    from smolagents import CodeAgent, LiteLLMModel, VisitWebpageTool, WebSearchTool

    model = LiteLLMModel(
        model_id="azure/gpt-4.1",
        api_base=os.environ.get("AZURE_OPENAI_ENDPOINT"),
        api_key=os.environ.get("AZURE_OPENAI_KEY"),
        api_version=os.environ.get("AZURE_OPENAI_VERSION"),
    )

    # Create an agent with no tools
    agent = CodeAgent(tools=[WebSearchTool(), VisitWebpageTool()], model=model)

    # Run the agent with a task
    generator = agent.run(user_message.text, stream=True)
    for tool_call in generator:
        yield format_tool_call(tool_call)
        print(str(tool_call))
        print("\n\n")
