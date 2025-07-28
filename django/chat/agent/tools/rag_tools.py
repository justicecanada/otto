from smolagents import Tool


class RetrieverTool(Tool):
    name = "retriever"
    description = "Uses semantic search to retrieve the parts of transformers documentation that could be most relevant to answer your query."
    inputs = {
        "query": {
            "type": "string",
            "description": "The query to perform. This should be semantically close to your target documents. Use the affirmative form rather than a question.",
        }
    }
    output_type = "string"

    # def __init__(self, docs, **kwargs):
    #     super().__init__(**kwargs)
    #     # Initialize the retriever with our processed documents
    #     self.retriever = BM25Retriever.from_documents(
    #         docs, k=10  # Return top 10 most relevant documents
    #     )

    # def forward(self, query: str) -> str:
    #     """Execute the retrieval based on the provided query."""
    #     assert isinstance(query, str), "Your search query must be a string"

    #     # Retrieve relevant documents
    #     docs = self.retriever.invoke(query)

    #     # Format the retrieved documents for readability
    #     return "\nRetrieved documents:\n" + "".join(
    #         [
    #             f"\n\n===== Document {str(i)} =====\n" + doc.page_content
    #             for i, doc in enumerate(docs)
    #         ]
    #     )
