#!/usr/bin/env python3
"""
PonyMemory mem0 MCP Server
基于 mem0ai SDK，复用已有 Qdrant + BGE-M3 embedding service。
提供自动记忆存储和检索功能。
"""
import json
import os
import asyncio
from mem0 import Memory
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

USER_ID = os.environ.get("MEM0_USER_ID", "jiajun")

# mem0 配置：复用已有 Qdrant + BGE-M3
config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "url": os.environ.get("QDRANT_URL", "http://localhost:6333"),
            "collection_name": os.environ.get("MEM0_COLLECTION", "mem0_memories"),
            "embedding_model_dims": 1024,
        },
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "api_key": "not-needed",
            "openai_base_url": os.environ.get("EMBED_URL", "http://localhost:8999"),
            "model": "bge-m3",
            "embedding_dims": 1024,
        },
    },
    "llm": {
        "provider": "anthropic",
        "config": {
            "model": os.environ.get("MEM0_LLM_MODEL", "claude-sonnet-4-6"),
            "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        },
    },
}

memory = None
server = Server("mem0")


def get_memory():
    global memory
    if memory is None:
        memory = Memory.from_config(config)
    return memory


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="add_memory",
            description="自动存储记忆。mem0 会自动提取事实、去重、处理矛盾。用于存储用户纠正、决策、发现、session摘要等。",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要存储的内容（自然语言）"},
                    "metadata": {
                        "type": "object",
                        "description": "附加元数据，如 {\"type\": \"correction\", \"project\": \"ponylabASMS\"}",
                    },
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="search_memory",
            description="语义搜索记忆。返回与查询最相关的已存储记忆。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索查询"},
                    "top_k": {"type": "integer", "description": "返回数量", "default": 5},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_all_memories",
            description="列出所有已存储的记忆。",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="delete_memory",
            description="删除指定 ID 的记忆。",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "要删除的记忆 ID"},
                },
                "required": ["memory_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        m = get_memory()
        if name == "add_memory":
            text = arguments["text"]
            metadata = arguments.get("metadata", {})
            result = m.add(text, user_id=USER_ID, metadata=metadata)
            return [types.TextContent(type="text", text=json.dumps(
                {"status": "stored", "result": str(result)}, ensure_ascii=False
            ))]
        elif name == "search_memory":
            query = arguments["query"]
            top_k = arguments.get("top_k", 5)
            results = m.search(query, user_id=USER_ID, limit=top_k)
            formatted = []
            for r in results.get("results", results) if isinstance(results, dict) else results:
                if isinstance(r, dict):
                    formatted.append({
                        "memory": r.get("memory", r.get("text", "")),
                        "score": r.get("score", 0),
                        "metadata": r.get("metadata", {}),
                        "id": r.get("id", ""),
                    })
            return [types.TextContent(type="text", text=json.dumps(
                formatted, ensure_ascii=False, indent=2
            ))]
        elif name == "get_all_memories":
            results = m.get_all(user_id=USER_ID)
            formatted = []
            for r in results.get("results", results) if isinstance(results, dict) else results:
                if isinstance(r, dict):
                    formatted.append({
                        "memory": r.get("memory", r.get("text", "")),
                        "metadata": r.get("metadata", {}),
                        "id": r.get("id", ""),
                    })
            return [types.TextContent(type="text", text=json.dumps(
                formatted, ensure_ascii=False, indent=2
            ))]
        elif name == "delete_memory":
            memory_id = arguments["memory_id"]
            m.delete(memory_id)
            return [types.TextContent(type="text", text=json.dumps(
                {"status": "deleted", "id": memory_id}
            ))]
        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
