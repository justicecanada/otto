"""Custom retrievers for Otto LLM operations."""

from concurrent.futures import ThreadPoolExecutor

from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.schema import QueryBundle


class OttoFusionRetriever(QueryFusionRetriever):
    """Threaded variant of QueryFusionRetriever to safely parallelize multi-retriever
    fusion without invoking nested event loops.

    Differences from upstream:
    - Ignores parent run_async_tasks pathway (which can trigger nested event loop errors)
      and instead uses a ThreadPoolExecutor when parallel=True.
    - Keeps num_queries=1 in current usage (no query generation overhead), but retains
      fusion logic for score weighting.
    - If parallel=False, falls back to parent's synchronous behavior.
    """

    def __init__(
        self,
        retrievers,
        llm=None,
        query_gen_prompt=None,
        mode="relative_score",
        similarity_top_k=5,
        num_queries=1,
        parallel: bool = True,
        max_workers: int | None = None,
        retriever_weights=None,
        **kwargs,
    ):
        # Force use_async False in parent; we manage our own concurrency
        super().__init__(
            retrievers,
            llm=llm,
            query_gen_prompt=query_gen_prompt,
            mode=mode,
            similarity_top_k=similarity_top_k,
            num_queries=num_queries,
            use_async=False,
            retriever_weights=retriever_weights,
            **kwargs,
        )
        self._parallel = parallel
        self._max_workers = max_workers

    def _run_threaded_queries(self, queries):
        """Run underlying retriever.retrieve concurrently via threads."""
        results = {}
        # Build all (query, retriever) pairs
        tasks = []
        for query in queries:
            for i, retriever in enumerate(self._retrievers):
                tasks.append((query, i, retriever))
        max_workers = self._max_workers or min(32, len(tasks) or 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(retriever.retrieve, query): (query.query_str, idx)
                for query, idx, retriever in tasks
            }
            for future in future_map:
                key = future_map[future]
                results[key] = future.result()
        return results

    def _retrieve(self, query_bundle: QueryBundle):  # type: ignore[override]
        queries = [query_bundle]
        if self.num_queries > 1:
            # Preserve compatibility if num_queries > 1 in future usage
            queries.extend(self._get_queries(query_bundle.query_str))

        if self._parallel and len(self._retrievers) > 1:
            results = self._run_threaded_queries(queries)
        else:
            # Fallback to simple sync iteration
            results = {}
            for query in queries:
                for i, retriever in enumerate(self._retrievers):
                    results[(query.query_str, i)] = retriever.retrieve(query)

        if self.mode == "reciprocal_rerank":
            return self._reciprocal_rerank_fusion(results)[: self.similarity_top_k]
        elif self.mode == "relative_score":
            return self._relative_score_fusion(results)[: self.similarity_top_k]
        elif self.mode == "dist_based_score":
            return self._relative_score_fusion(results, dist_based=True)[
                : self.similarity_top_k
            ]
        elif self.mode == "simple":
            return self._simple_fusion(results)[: self.similarity_top_k]
        else:
            raise ValueError(f"Invalid fusion mode: {self.mode}")

    async def _aretrieve(self, query_bundle: QueryBundle):  # type: ignore[override]
        """Provide a true async path by delegating to underlying async retrievers if available.

        If any underlying retriever lacks 'aretrieve', we fall back to thread pool to
        avoid blocking the loop.
        """
        queries = [query_bundle]
        if self.num_queries > 1:
            queries.extend(self._get_queries(query_bundle.query_str))

        # Check capability
        can_async = all(hasattr(r, "aretrieve") for r in self._retrievers)
        if can_async:
            import asyncio

            tasks = []
            task_keys = []
            for query in queries:
                for i, retriever in enumerate(self._retrievers):
                    tasks.append(retriever.aretrieve(query))
                    task_keys.append((query.query_str, i))
            task_results = await asyncio.gather(*tasks)
            results = {k: v for k, v in zip(task_keys, task_results)}
        else:
            # Fall back to threaded sync retrieval to preserve non-blocking behavior
            results = self._run_threaded_queries(queries)

        if self.mode == "reciprocal_rerank":
            return self._reciprocal_rerank_fusion(results)[: self.similarity_top_k]
        elif self.mode == "relative_score":
            return self._relative_score_fusion(results)[: self.similarity_top_k]
        elif self.mode == "dist_based_score":
            return self._relative_score_fusion(results, dist_based=True)[
                : self.similarity_top_k
            ]
        elif self.mode == "simple":
            return self._simple_fusion(results)[: self.similarity_top_k]
        else:
            raise ValueError(f"Invalid fusion mode: {self.mode}")
