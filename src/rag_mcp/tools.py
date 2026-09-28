"""MCP tool registration. Tool logic and descriptions live in their own modules."""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from rag_mcp import web


def register_tools(mcp: MCPServer) -> None:
    """Register all tools on the given server."""

    @mcp.tool(
        name="web_search",
        title="Web Search",
        description=web.WEB_SEARCH_DESCRIPTION,
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    )
    async def web_search_tool(
        query: Annotated[str, Field(description=web.QUERY_DESCRIPTION)],
        max_results: Annotated[
            int,
            Field(ge=web.MIN_MAX_RESULTS, le=web.MAX_MAX_RESULTS, description=web.MAX_RESULTS_DESCRIPTION),
        ] = web.DEFAULT_MAX_RESULTS,
        include_domains: Annotated[
            list[str] | None,
            Field(max_length=web.MAX_INCLUDE_DOMAINS, description=web.INCLUDE_DOMAINS_DESCRIPTION),
        ] = None,
    ) -> web.SearchResponse:
        try:
            return await web.web_search(query, max_results, include_domains)
        except (ValueError, web.WebSearchError) as exc:
            raise ToolError(str(exc)) from exc
