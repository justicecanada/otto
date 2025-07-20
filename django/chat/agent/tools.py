from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from smolagents import Tool

from chat.llm import OttoLLM


# Tool to retrieve laws using LlamaIndex and OttoLLM
class LawRetrieverTool(Tool):
    name = "law_retriever"
    description = (
        "Retrieves relevant sections of Canadian Laws, Legislation, Acts, Regulations."
    )
    inputs = {
        "query": {
            "type": "string",
            "description": "The query to search for relevant law sections. Include the name of the law if known, or a legal topic, question or keywords.",
        }
    }
    output_type = "string"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.llm = OttoLLM()
        self.pg_idx = self.llm.get_index("laws_lois__", hnsw=True)

    def forward(self, query: str) -> str:
        assert isinstance(query, str), "Your search query must be a string"
        # Only retrieve law chunks (not full docs)
        filters = MetadataFilters(
            filters=[MetadataFilter(key="node_type", value="chunk", operator="==")]
        )
        retriever = self.pg_idx.as_retriever(
            vector_store_query_mode="default",
            similarity_top_k=10,
            filters=filters,
            vector_store_kwargs={"hnsw_ef_search": 256},
        )
        try:
            results = retriever.retrieve(query)
            if not results:
                return "No relevant law sections found."
            section_texts = []
            for result in results:
                try:
                    section_texts.append(result.node.get_content())
                except Exception as e:
                    continue
            return "\nRetrieved law sections:\n" + "\n==========\n".join(section_texts)
        except Exception as e:
            return f"Error retrieving law sections: {e}"
