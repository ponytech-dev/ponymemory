# PonyMemory 社区调研发现

> 调研日期：2026-03-15

## 一、关键发现摘要

### 已验证可行的方案

| 方案 | 来源 | 验证程度 |
|------|------|---------|
| mem0 复用已有 Qdrant 实例 | 官方文档 + 社区案例 | 完全验证 |
| mem0 通过 openai provider 指向自定义 embedding | 官方文档 + PR #4275 修复 | 完全验证（Python SDK）|
| mem0 SessionStart/Stop hooks 自动闭环 | DEV.to + elvismdev README | 完全验证 |
| Basic Memory 文件与 Obsidian 直接兼容 | 官方文档 + 用户反馈 | 完全验证 |
| Cognee 使用 Kuzu（无 Neo4j）本地部署 | 官方文档 + PyPI | 完全验证 |
| Cognee 复用已有 Qdrant 实例 | Qdrant 官方集成文档 | 完全验证 |
| claude-mem 全自动 hooks 记忆 | 4,100 stars 项目 | 完全验证 |
| claude-diary PreCompact 自动生成日记 | 339 stars + 作者说明 | 完全验证 |
| Claude Code Hooks → Obsidian 自动日志 | Daniel Donbavand 博文 | 完全验证 |
| Tool Search 降低多 MCP token 消耗 | Claude Code 内置功能 | 完全验证 |

### 理论可行但无完整验证

| 方案 | 状态 |
|------|------|
| 自动从对话提取规则到 CLAUDE.md（全自动，无人触发）| 基于 claude-diary 推断可行 |
| mem0 + Cognee 同时运行共存 | 架构分析可行，无完整案例 |
| 测试通过后自动 git push | 技术可行，社区不推荐 |
| 记忆质量自动评估 | 领域空白，无成熟方案 |

### 已知的坑和限制

1. **SessionStart hook 可能不触发**：新对话（非恢复）时不触发（Issue #10373），需 CLAUDE.md 兜底
2. **mem0 无 TTL 机制**：旧记忆不自动过期，需手动/Cron 清理
3. **mem0 每次写入需 LLM 调用**：有 API 成本
4. **多 MCP server token 消耗**：6 个 server ~49 工具 ≈ 14,700 tokens (7.4%)
5. **Cognee 图谱构建慢**：多次 LLM 调用，不适合高频触发
6. **Obsidian MCP 需要 Obsidian 运行**：后台不运行则 MCP 不可用

---

## 二、核心项目参考

### mem0 生态

| 项目 | GitHub | Stars | 说明 |
|------|--------|-------|------|
| mem0 (官方) | [mem0ai/mem0](https://github.com/mem0ai/mem0) | 45k+ | 核心库 |
| mem0-mcp-selfhosted | [elvismdev/mem0-mcp-selfhosted](https://github.com/elvismdev/mem0-mcp-selfhosted) | 45 | Claude Code 专用，含 hooks |
| OpenMemory | [mem0.ai/openmemory](https://mem0.ai/openmemory) | — | 官方自托管方案 |

### Cognee 生态

| 项目 | GitHub | Stars | 说明 |
|------|--------|-------|------|
| cognee | [topoteretes/cognee](https://github.com/topoteretes/cognee) | 13.8k | 核心库，v0.5.5 |

### Basic Memory 生态

| 项目 | GitHub | Stars | 说明 |
|------|--------|-------|------|
| basic-memory | [basicmachines-co/basic-memory](https://github.com/basicmachines-co/basic-memory) | 2.6k | MCP server，v0.19.0 |

### Claude Code 记忆方案

| 项目 | GitHub | Stars | 说明 |
|------|--------|-------|------|
| claude-mem | [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem) | 4,100 | 5 hooks 全自动，SQLite + Chroma |
| claude-diary | [rlancemartin/claude-diary](https://github.com/rlancemartin/claude-diary) | 339 | PreCompact hook，自动更新 CLAUDE.md |

### Obsidian MCP 生态

| 项目 | GitHub | Stars | 说明 |
|------|--------|-------|------|
| obsidian-claude-code-mcp | [iansinnott/obsidian-claude-code-mcp](https://github.com/iansinnott/obsidian-claude-code-mcp) | 180 | Claude Code 专用 |
| obsidian-mcp-tools | [jacksteamdev/obsidian-mcp-tools](https://github.com/jacksteamdev/obsidian-mcp-tools) | 644 | 语义搜索 |
| obsidian-claude-pkm | [ballred/obsidian-claude-pkm](https://github.com/ballred/obsidian-claude-pkm) | — | 完整 PKM starter kit |

### Awesome 资源列表

| 项目 | GitHub | Stars |
|------|--------|-------|
| awesome-mcp-servers | [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) | 83.1k |
| context7 | [upstash/context7](https://github.com/upstash/context7) | 47.3k |

---

## 三、性能数据

### MCP Server Token 消耗基准

| MCP server 数量 | Token 消耗 | 占 200k 窗口 |
|----------------|-----------|-------------|
| 1-5 个 | 1k-5k | 0.5-2.5% |
| ~10 个 | 开始明显 | 5-10% |
| 20 个 | 66,000+ | 33% |
| 32 个，473 工具 | ~150,000 | 75% |

### 每个工具的 Token 成本
- 简单工具（无参数）：80-120 tokens
- 复杂工具（多参数）：300-450 tokens
- 平均：200-400 tokens

### Tool Search 优化效果
- 优化前：51k tokens
- 优化后：8.5k tokens
- 降幅：46.9%（实际更多，因为工具按需加载）

### PonyMemory 预估（6 servers, 49 工具）
- 无优化：~14,700 tokens (7.4%)
- Tool Search 优化后：~7,800 tokens (~4%)
- 完全可接受

---

## 四、Embedding 兼容性方案

### 当前 BGE-M3 服务接口

```
POST http://localhost:8999/embed
Body: {"texts": ["query"]}
Response: {"embeddings": [[float, ...]]}
```

### mem0 需要的 OpenAI 兼容接口

```
POST http://localhost:8999/v1/embeddings
Body: {"input": "query", "model": "bge-m3"}
Response: {
  "object": "list",
  "data": [{"object": "embedding", "embedding": [float, ...], "index": 0}],
  "model": "bge-m3",
  "usage": {"prompt_tokens": 0, "total_tokens": 0}
}
```

### 代理方案

在现有 embedding service 上加一个 `/v1/embeddings` 路由，转换请求格式。
代码在 `scripts/embedding-proxy.py`（待实现）。

或者修改现有 `embedding-service.py` 添加此路由。

---

## 五、参考文章链接

### 部署和配置
- [给 Claude Code 配置 mem0 持久记忆](https://dev.to/n3rdh4ck3r/how-to-give-claude-code-persistent-memory-with-a-self-hosted-mem0-mcp-server-h68)
- [Cognee 配置文档](https://docs.cognee.ai/setup-configuration/overview)
- [Qdrant + Cognee 集成](https://qdrant.tech/documentation/frameworks/cognee/)
- [Basic Memory MCP 介绍](https://mcp.so/server/basic-memory)

### 自动化和 Hooks
- [Claude Code Hooks 官方文档](https://code.claude.com/docs/en/hooks)
- [Claude Code Hooks + Obsidian 自动日志](https://danieldonbavand.com/2026/02/04/my-journal-writes-itself/)
- [PreCompact Context Recovery Hook](https://claudefa.st/blog/tools/hooks/context-recovery-hook)
- [嵌入记忆到 Claude Code](https://dev.to/shimo4228/embedding-memory-into-claude-code-from-session-loss-to-persistent-context-54d8)
- [GitButler: Claude Code Hooks 自动化工作流](https://blog.gitbutler.com/automate-your-ai-workflows-with-claude-code-hooks)

### 架构和性能
- [多 MCP Server Context Window 问题](https://github.com/anthropics/claude-code/issues/3036)
- [Memory MCP Token 优化最佳实践](https://blog.sd.idv.tw/en/posts/2025-08-07_memory-mcp-best-practices/)
- [Meta-MCP 架构压缩 60+ 工具](https://dev.to/tgfjt/a-practical-meta-mcp-architecture-for-claude-code-compressing-60-tools-into-just-two-oje)
- [Claude Code Tool Search 降低 46.9% Token](https://medium.com/@joe.njenga/claude-code-just-cut-mcp-context-bloat-by-46-9-51k-tokens-down-to-8-5k-with-new-tool-search-ddf9e905f734)

### 评估和对比
- [Zep: Is Mem0 Really SOTA?](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)
- [Cognee AI Memory Evals](https://www.cognee.ai/blog/deep-dives/ai-memory-evals-0825)
- [OpenMemory MCP 介绍](https://mem0.ai/blog/introducing-openmemory-mcp)
