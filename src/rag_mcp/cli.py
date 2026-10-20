"""One entry point for setup, manual data management and both MCP tools."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID


def main() -> None:
    parser = argparse.ArgumentParser(prog="rag-mcp")
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("RAG_CONFIG", ".env")))
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("setup", help="Prepare local PostgreSQL, BGE-M3 and configuration")
    put = commands.add_parser("put", help="Manually insert or replace a memory, recomputing its embedding")
    put.add_argument("--namespace", required=True)
    content = put.add_mutually_exclusive_group(required=True)
    content.add_argument("--content")
    content.add_argument("--file", type=Path)
    put.add_argument("--id", type=UUID, help="Existing record ID to replace")
    put.add_argument("--metadata", type=json.loads, default={})
    put.add_argument("--source")
    search = commands.add_parser("search", help="Call the memory_search MCP tool")
    search.add_argument("query")
    search.add_argument("--namespace", required=True)
    search.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        if args.command:
            sys.stdout.reconfigure(encoding="utf-8")
    from rag_mcp.config import load_env

    try:
        load_env(args.config)
        if args.command == "setup":
            from rag_mcp.memory.setup import setup_local
            setup_local(args.config.resolve())
        elif args.command == "put":
            from rag_mcp.memory.admin import put_memory
            text = args.file.read_text(encoding="utf-8") if args.file else args.content
            result = asyncio.run(put_memory(args.namespace, text, args.metadata, args.source, args.id))
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "search":
            print(json.dumps(asyncio.run(_search(args)), ensure_ascii=False, indent=2))
        else:
            from rag_mcp.server import main as serve
            serve()
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from None


async def _search(args) -> dict:
    from mcp import Client
    from rag_mcp.server import create_server
    async with Client(create_server(), read_timeout_seconds=400) as client:
        result = await client.call_tool("memory_search", {
            "namespace": args.namespace, "query": args.query, "limit": args.limit,
        })
        if result.is_error:
            raise RuntimeError(" ".join(item.text for item in result.content if hasattr(item, "text")))
        return result.structured_content


if __name__ == "__main__":
    main()
