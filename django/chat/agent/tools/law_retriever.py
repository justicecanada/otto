from llama_index.core.schema import MetadataMode
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

    def _trim_redundant_nodes(self, nodes):
        trimmed_nodes = []
        added_ids = set()
        section_ids = set([node.metadata["section_id"] for node in nodes])
        while nodes:
            node = nodes.pop(0)
            if node.metadata["section_id"] in added_ids:
                continue
            elif node.metadata["section_id"] in section_ids:
                # Find the parent node in the nodes list, remove it, and make it the "node"
                parent_index = next(
                    (
                        i
                        for i, n in enumerate(nodes)
                        if n.metadata["section_id"] == node.metadata["parent_id"]
                    ),
                    None,
                )
                if parent_index is not None:
                    node = nodes.pop(parent_index)
            trimmed_nodes.append(node)
            added_ids.add(node.metadata["section_id"])
        return trimmed_nodes

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
            vector_store_kwargs={"hnsw_ef_search": 512},
        )
        try:
            results = retriever.retrieve(query)
            if not results:
                return "No relevant law sections found."
            nodes = self._trim_redundant_nodes([result.node for result in results])
            section_texts = []
            for node in nodes:
                try:
                    section_texts.append(
                        node.get_content(metadata_mode=MetadataMode.LLM)
                    )
                except Exception as e:
                    continue
            return "\nRetrieved law sections:\n\n" + "\n\n---\n\n".join(section_texts)
        except Exception as e:
            return f"Error retrieving law sections: {e}"
