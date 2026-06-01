"""Vector store and database engine management."""

from django.conf import settings

from llama_index.vector_stores.postgres import PGVectorStore
from retrying import retry
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Lazy-initialized shared database engines (module-level for reuse across requests)
_pg_sync_engine = None
_pg_async_engine = None


def _get_connection_params():
    """Get current database connection params (respects test database names)."""
    return {
        "database": settings.DATABASES["vector_db"]["NAME"],
        "host": settings.DATABASES["vector_db"]["HOST"],
        "password": settings.DATABASES["vector_db"]["PASSWORD"],
        "user": settings.DATABASES["vector_db"]["USER"],
        "port": settings.DATABASES["vector_db"]["PORT"],
    }


def get_pg_engines():
    """Get or create shared PostgreSQL engines for vector store.

    Lazy initialization ensures test database names are used correctly.
    Returns tuple of (sync_engine, async_engine).
    """
    global _pg_sync_engine, _pg_async_engine

    if _pg_sync_engine is None or _pg_async_engine is None:
        connection_params = _get_connection_params()
        pg_sync_conn_string = f"postgresql+psycopg2://{connection_params['user']}:{connection_params['password']}@{connection_params['host']}:{connection_params['port']}/{connection_params['database']}"
        pg_async_conn_string = f"postgresql+asyncpg://{connection_params['user']}:{connection_params['password']}@{connection_params['host']}:{connection_params['port']}/{connection_params['database']}"

        _pg_sync_engine = create_engine(pg_sync_conn_string)
        _pg_async_engine = create_async_engine(pg_async_conn_string)

    return _pg_sync_engine, _pg_async_engine


class OttoVectorStore(PGVectorStore):
    # Override from LlamaIndex to reuse shared engines across all OttoVectorStore instances
    @retry(
        wait_exponential_multiplier=1000,
        wait_exponential_max=20000,
    )
    def _connect(self):
        # Use shared engines to avoid creating new connections on every RAG request
        pg_sync_engine, pg_async_engine = get_pg_engines()

        self._engine = pg_sync_engine
        self._session = sessionmaker(self._engine)

        self._async_engine = pg_async_engine
        self._async_session = sessionmaker(self._async_engine, class_=AsyncSession)  # type: ignore
