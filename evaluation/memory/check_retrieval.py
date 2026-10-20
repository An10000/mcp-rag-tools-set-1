"""Three smoke checks against a disposable pgvector database; no test framework."""

import asyncio
import os
import sys
from importlib.resources import files
from uuid import uuid4

import httpx
import psycopg

from rag_mcp.memory import embeddings, retrieval as database

SAMPLES = [
    ("使用 PostgreSQL 保存长期记忆。", '{"category":"decision"}', "[1,0,0]"),
    ("后续再增加向量索引。", '{"category":"decision"}', "[0.8,0.2,0]"),
    ("网页检索使用 Tavily。", '{"category":"note"}', "[1,0,0]"),
]


async def main():
    dsn = os.environ["MEMORY_TEST_DATABASE_URL"]
    namespace = "test:" + uuid4().hex
    settings = database.DatabaseSearchSettings(dsn)
    model = embeddings.EmbeddingSettings("http://test/v1", "fixed-test-vector", "v1", 3)
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as connection:
        schema = files("rag_mcp.memory").joinpath("resources/schema.sql")
        await connection.execute(schema.read_text(encoding="utf-8"), prepare=False)
        try:
            await connection.execute(
                "INSERT INTO public.memory_spaces VALUES (%s,%s,%s,%s,%s,DEFAULT)",
                (namespace, model.provider, model.model, model.revision, model.dimensions),
            )
            for content, metadata, vector in SAMPLES:
                await connection.execute(
                    "INSERT INTO public.memories(namespace,content,metadata,embedding) VALUES (%s,%s,%s::jsonb,%s::vector)",
                    (namespace, content, metadata, vector),
                )
            transport = httpx.MockTransport(lambda request: httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1, 0, 0]}]},
            ))
            async with httpx.AsyncClient(transport=transport) as client:
                async def search(filters):
                    return await database.memory_search(
                        namespace, "数据库选型", metadata_filter=filters,
                        settings=settings, embedding_settings=model, client=client,
                    )

                result = await search({"category": "decision"})
                assert [match.content for match in result.matches] == [row[0] for row in SAMPLES[:2]]
                assert abs(result.matches[0].distance) < 1e-6
                assert result.matches[0].distance < result.matches[1].distance
                print("PASS: metadata filtering and cosine ranking")

                assert (await search({"category": "missing"})).matches == []
                print("PASS: no matches returns an empty list")

                try:
                    await database.memory_search(namespace, " ")
                except ValueError:
                    print("PASS: blank query is rejected")
                else:
                    raise AssertionError("Blank query was accepted")
        finally:
            await connection.execute("DELETE FROM public.memories WHERE namespace=%s", (namespace,))
            await connection.execute("DELETE FROM public.memory_spaces WHERE namespace=%s", (namespace,))
    print("3 smoke checks passed; sample records removed.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
