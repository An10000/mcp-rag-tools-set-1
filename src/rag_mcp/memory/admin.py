"""Manual database initialization and writes; these are not agent tools."""

import json
import os
from importlib.resources import files
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

from rag_mcp.memory import embeddings, retrieval


def admin_url() -> str:
    value = os.environ.get("DATABASE_ADMIN_URL", "").strip()
    if not value:
        raise ValueError("Set DATABASE_ADMIN_URL for manual database administration.")
    return value


async def initialize_database() -> None:
    schema = files("rag_mcp.memory").joinpath("resources/schema.sql").read_text(encoding="utf-8")
    try:
        async with await psycopg.AsyncConnection.connect(admin_url(), autocommit=True, connect_timeout=5) as connection:
            await connection.execute(schema, prepare=False)
            password = os.environ["POSTGRES_READER_PASSWORD"]
            cursor = await connection.execute("SELECT 1 FROM pg_roles WHERE rolname='rag_reader'")
            command = "ALTER" if await cursor.fetchone() else "CREATE"
            await connection.execute(sql.SQL(command + " ROLE rag_reader LOGIN PASSWORD {}").format(sql.Literal(password)))
            await connection.execute("GRANT CONNECT ON DATABASE rag_memory TO rag_reader")
            await connection.execute("GRANT USAGE ON SCHEMA public TO rag_reader")
            await connection.execute("GRANT SELECT ON public.memory_spaces, public.memories TO rag_reader")
    except psycopg.Error:
        raise RuntimeError("Database initialization failed; check the administrator connection and privileges.") from None


async def put_memory(
    namespace: str, content: str, metadata: dict | None = None,
    source_url: str | None = None, identifier: UUID | None = None,
) -> dict:
    """Insert a record or replace one ID in the same namespace, regenerating its vector."""
    namespace, content, _ = retrieval.validate_inputs(namespace, content, 1, None)
    metadata = {} if metadata is None else metadata
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object.")
    encoded = json.dumps(metadata, allow_nan=False)
    if len(encoded.encode("utf-8")) > 16384:
        raise ValueError("metadata must be at most 16 KiB.")
    model = embeddings.EmbeddingSettings.from_env()
    expected = (model.provider, model.model, model.revision, model.dimensions)
    try:
        async with await psycopg.AsyncConnection.connect(admin_url(), connect_timeout=5) as connection:
            await connection.execute(
                "INSERT INTO public.memory_spaces(namespace,embedding_provider,embedding_model,embedding_revision,embedding_dimensions) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(namespace) DO NOTHING", (namespace, *expected),
            )
            cursor = await connection.execute(
                "SELECT embedding_provider,embedding_model,embedding_revision,embedding_dimensions "
                "FROM public.memory_spaces WHERE namespace=%s FOR SHARE", (namespace,),
            )
            if await cursor.fetchone() != expected:
                raise ValueError("Namespace embedding configuration differs; use a new namespace to change models.")
            if identifier is not None:
                cursor = await connection.execute(
                    "SELECT id FROM public.memories WHERE id=%s AND namespace=%s FOR UPDATE", (identifier, namespace),
                )
                if await cursor.fetchone() is None:
                    raise ValueError("Record ID does not exist in this namespace.")
            vector = await embeddings.embed_query(content, settings=model)
            cursor = await connection.execute(
                "INSERT INTO public.memories(id,namespace,content,metadata,source_url,embedding) "
                "VALUES (%s,%s,%s,%s::jsonb,%s,%s::vector) "
                "ON CONFLICT(id) DO UPDATE SET content=excluded.content,metadata=excluded.metadata,"
                "source_url=excluded.source_url,embedding=excluded.embedding,updated_at=now(),"
                "version=memories.version+1,deleted_at=NULL "
                "WHERE memories.namespace=excluded.namespace RETURNING id,version",
                (identifier or uuid4(), namespace, content, encoded, source_url, json.dumps(vector)),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("That record ID belongs to another namespace.")
    except psycopg.Error:
        raise RuntimeError("Memory write failed; check the administrator connection, schema and privileges.") from None
    return {"id": str(row[0]), "namespace": namespace, "version": row[1]}
