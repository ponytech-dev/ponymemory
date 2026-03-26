#!/usr/bin/env python3
"""
PonyMemory Cognee MCP Server
Knowledge graph construction and querying via Cognee (Kuzu local graph DB).
Selective injection only — not for bulk dump.
"""
import json
import os
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# Configure Cognee to use local Qdrant + BGE-M3
os.environ.setdefault("ENABLE_BACKEND_ACCESS_CONTROL", "false")
os.environ.setdefault("LLM_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
os.environ.setdefault("LLM_PROVIDER", "anthropic")
os.environ.setdefault("LLM_MODEL", os.environ.get("COGNEE_LLM_MODEL", "claude-sonnet-4-6"))

import cognee

server = Server("cognee")

_initialized = False


async def ensure_init():
    global _initialized
    if not _initialized:
        # Configure Cognee vector store to use existing Qdrant
        cognee.config.set_vector_db_config({
            "vector_db_provider": "qdrant",
            "vector_db_url": os.environ.get("QDRANT_URL", "http://localhost:6333"),
        })
        _initialized = True


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="cognee_add",
            description="Add text/code to Cognee knowledge graph. Use for important code modules, architecture docs, or domain knowledge that benefits from graph relationships.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text or code content to add"},
                    "dataset_name": {"type": "string", "description": "Dataset name (e.g., project name)", "default": "default"},
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="cognee_cognify",
            description="Process added data into knowledge graph (extract entities, relationships). Run after cognee_add.",
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_name": {"type": "string", "description": "Dataset to process", "default": "default"},
                },
            },
        ),
        types.Tool(
            name="cognee_search",
            description="Search Cognee knowledge graph. Returns entities, relationships, and context from the graph.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "search_type": {
                        "type": "string",
                        "description": "Search type: INSIGHTS (graph patterns), CHUNKS (raw text), GRAPH_COMPLETION (relationship traversal)",
                        "default": "INSIGHTS",
                        "enum": ["INSIGHTS", "CHUNKS", "GRAPH_COMPLETION"],
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="cognee_codify",
            description="Analyze Python code and build code knowledge graph (classes, functions, dependencies). Use for understanding codebase architecture.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code_path": {"type": "string", "description": "Path to Python file or directory"},
                    "dataset_name": {"type": "string", "description": "Dataset name", "default": "code"},
                },
                "required": ["code_path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        await ensure_init()

        if name == "cognee_add":
            text = arguments["text"]
            dataset_name = arguments.get("dataset_name", "default")
            await cognee.add(text, dataset_name)
            return [types.TextContent(type="text", text=json.dumps({
                "status": "added",
                "dataset": dataset_name,
                "text_length": len(text),
            }))]

        elif name == "cognee_cognify":
            dataset_name = arguments.get("dataset_name", "default")
            await cognee.cognify(dataset_name)
            return [types.TextContent(type="text", text=json.dumps({
                "status": "cognified",
                "dataset": dataset_name,
            }))]

        elif name == "cognee_search":
            query = arguments["query"]
            search_type_str = arguments.get("search_type", "INSIGHTS")
            from cognee.api.v1.search import SearchType
            search_type_map = {
                "INSIGHTS": SearchType.INSIGHTS,
                "CHUNKS": SearchType.CHUNKS,
                "GRAPH_COMPLETION": SearchType.GRAPH_COMPLETION,
            }
            search_type = search_type_map.get(search_type_str, SearchType.INSIGHTS)
            results = await cognee.search(search_type, query=query)
            # Format results
            formatted = []
            for r in results if results else []:
                if isinstance(r, dict):
                    formatted.append(r)
                elif hasattr(r, "__dict__"):
                    formatted.append(str(r))
                else:
                    formatted.append(str(r))
            return [types.TextContent(type="text", text=json.dumps(
                formatted[:10], ensure_ascii=False, indent=2, default=str
            ))]

        elif name == "cognee_codify":
            code_path = arguments["code_path"]
            dataset_name = arguments.get("dataset_name", "code")
            if not os.path.exists(code_path):
                return [types.TextContent(type="text", text=f"Path not found: {code_path}")]
            # Read code and add
            if os.path.isfile(code_path):
                with open(code_path) as f:
                    code = f.read()
                await cognee.add(code, dataset_name)
            elif os.path.isdir(code_path):
                for root, _, files in os.walk(code_path):
                    for fname in files:
                        if fname.endswith(".py"):
                            fpath = os.path.join(root, fname)
                            with open(fpath) as f:
                                code = f.read()
                            if code.strip():
                                await cognee.add(f"# File: {fpath}\n{code}", dataset_name)
            await cognee.cognify(dataset_name)
            return [types.TextContent(type="text", text=json.dumps({
                "status": "codified",
                "path": code_path,
                "dataset": dataset_name,
            }))]

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
