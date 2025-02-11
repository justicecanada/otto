from django.core.exceptions import ObjectDoesNotExist
from django.core.management import call_command
from django.core.management.base import BaseCommand

from langdetect import detect
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters

from chat.llm import OttoLLM
from chat.models import Preset
from laws.models import Law
from otto.models import Group, User


def fake_laws_search(query):
    # time.sleep(60)
    # We don't want to search Document nodes - only chunks
    filters = [
        MetadataFilter(
            key="node_type",
            value="chunk",
            operator="==",
        ),
    ]

    # Trim query to 10000 characters
    query_too_long = len(query) > 10000
    if query_too_long:
        query = query[:10000] + "..."

    selected_laws = Law.objects.all()

    top_k = 25
    doc_id_list = [law.node_id_en for law in selected_laws] + [
        law.node_id_fr for law in selected_laws
    ]

    filters.append(
        MetadataFilter(
            key="doc_id",
            value=doc_id_list,
            operator="in",
        )
    )

    filters.append(
        MetadataFilter(
            key="in_force_start_date",
            value="1980-01-01",
            operator=">=",
        )
    )

    filters.append(
        MetadataFilter(
            key="in_force_start_date",
            value="2030-01-01",
            operator="<=",
        )
    )

    filters.append(
        MetadataFilter(
            key="last_amended_date",
            value="1980-01-01",
            operator=">=",
        )
    )

    filters = MetadataFilters(filters=filters)
    # filters = None
    mock = True
    vector_ratio = 1

    if vector_ratio == 1:
        pg_idx = OttoLLM(mock_embedding=mock).get_index("laws_lois__", use_jsonb=True)
        retriever = pg_idx.as_retriever(
            vector_store_query_mode="default",
            similarity_top_k=top_k,
            filters=filters,
            vector_store_kwargs={"hnsw_ef_search": 128},
        )
    elif vector_ratio == 0:
        pg_idx = OttoLLM(mock_embedding=mock).get_index("laws_lois__", use_jsonb=True)
        retriever = pg_idx.as_retriever(
            vector_store_query_mode="sparse",
            similarity_top_k=top_k,
            filters=filters,
        )
        retriever._vector_store.is_embedding_query = False
    else:
        llm = OttoLLM(mock_embedding=mock)
        pg_idx = llm.get_index("laws_lois__", use_jsonb=True)
        vector_retriever = pg_idx.as_retriever(
            vector_store_query_mode="default",
            similarity_top_k=max(top_k * 2, 100),
            filters=filters,
            vector_store_kwargs={"hnsw_ef_search": 128},
        )
        pg_idx2 = OttoLLM(mock_embedding=mock).get_index("laws_lois__", use_jsonb=True)
        text_retriever = pg_idx2.as_retriever(
            vector_store_query_mode="sparse",
            similarity_top_k=max(top_k * 2, 100),
            filters=filters,
        )
        text_retriever._vector_store.is_embedding_query = False
        retriever = QueryFusionRetriever(
            retrievers=[vector_retriever, text_retriever],
            mode="relative_score",
            llm=llm.llm,
            similarity_top_k=top_k,
            num_queries=1,
            use_async=False,
            retriever_weights=[vector_ratio, 1 - vector_ratio],
        )

    try:
        sources = retriever.retrieve(query)
    except:
        sources = None
    return sources


class Command(BaseCommand):
    help = "Tests Preset.objects.get_accessible_presets()"

    def handle(self, *args, **options):
        from time import time

        start = time()
        for j in range(10):
            sources = fake_laws_search(
                f"what is the most important law in the world? ({j})"
            )
        print(time() - start)
