"""Register memory retrieval on the shared MCP server."""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from rag_mcp.memory import embeddings, retrieval


def register_tools(mcp: MCPServer) -> None:

    @mcp.tool(
        name="memory_search", title="Memory Search",
        description=retrieval.MEMORY_SEARCH_DESCRIPTION,
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    )
    async def memory_search_tool(
        namespace: Annotated[str, Field(description=retrieval.NAMESPACE_DESCRIPTION)],
        query: Annotated[str, Field(description=retrieval.QUERY_DESCRIPTION)],
        limit: Annotated[int, Field(ge=1, le=20, description=retrieval.LIMIT_DESCRIPTION)] = 5,
        metadata_filter: Annotated[
            dict[str, retrieval.MetadataScalar] | None, Field(description=retrieval.FILTER_DESCRIPTION),
        ] = None,
    ) -> retrieval.MemorySearchResponse:
        try:
            return await retrieval.memory_search(namespace, query, limit, metadata_filter)
        except (ValueError, retrieval.DatabaseSearchError, embeddings.EmbeddingError) as exc:
            raise ToolError(str(exc)) from exc

