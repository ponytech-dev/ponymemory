# PonyMemory 工具角色详细说明

## 工具总览

| # | 工具 | 角色 | 写入方式 | 读取方式 | 人工介入 |
|---|------|------|---------|---------|---------|
| 1 | mem0 | AI 自动记忆 | 全自动（LLM 提取） | SessionStart 自动 + 按需 | 无 |
| 2 | Cognee | 知识图谱 | 按需 cognify/codify | 按需 search | 无 |
| 3 | Qdrant MCP | 知识库搜索 | ingest 批量 + store_memory | 按需 search | 偶尔 ingest |
| 4 | Obsidian | 结构化归档 | 规则驱动自动写入 | SessionStart 自动 + 按需 | 可浏览/编辑 |
| 5 | Basic Memory | Markdown 归档 | Stop hook 自动写 | 人类浏览 | 无 |
| 6 | Context7 | API 文档 | 无（只读） | 写代码时自动 | 无 |

---

## 1. mem0（elvismdev/mem0-mcp-selfhosted）

### MCP 工具列表

| 工具 | 用途 | 自动触发条件 |
|------|------|------------|
| `add_memory(text, metadata)` | 存储新记忆 | 用户纠正/决策/发现/session结束 |
| `search_memories(query, user_id)` | 语义搜索记忆 | SessionStart + 对话中按需 |
| `get_memories(user_id)` | 列出所有记忆 | 维护/调试时 |
| `update_memory(memory_id, text)` | 更新已有记忆 | 信息变更时（自动触发） |
| `delete_memory(memory_id)` | 删除记忆 | 信息过时/错误时 |
| `delete_all_memories(user_id)` | 清空所有记忆 | 仅维护用 |
| `list_entities()` | 列出知识图谱实体 | 维护/调试时 |
| `delete_entities(entity_id)` | 删除实体 | 维护时 |
| `search_graph(query)` | 图谱搜索 | 需要关系推理时 |
| `get_entity(entity_id)` | 获取实体详情 | 按需 |

### 配置

```bash
claude mcp add --scope user --transport stdio mem0 \
  --env MEM0_USER_ID=jiajun \
  --env MEM0_QDRANT_URL=http://localhost:6333 \
  --env MEM0_COLLECTION=mem0_memories \
  --env MEM0_EMBED_PROVIDER=openai \
  --env MEM0_EMBED_URL=http://localhost:8999 \
  --env MEM0_EMBED_MODEL=bge-m3 \
  --env MEM0_EMBED_DIMS=1024 \
  --env MEM0_PROVIDER=anthropic \
  --env MEM0_LLM_MODEL=claude-sonnet-4-6 \
  --env MEM0_ENABLE_GRAPH=false \
  -- uvx --from git+https://github.com/elvismdev/mem0-mcp-selfhosted.git mem0-mcp-selfhosted
```

### 内部机制
1. 用户对话 → LLM 自动提取结构化事实
2. 每条事实 → BGE-M3 embedding → Qdrant 向量搜索已有记忆
3. LLM 决策：ADD（新）/ UPDATE（更新）/ DELETE（矛盾）/ NOOP（重复）
4. 写入 Qdrant `mem0_memories` collection

### 已知限制
- 无 TTL/时间衰减，旧记忆不会自动过期 → 需要 Cron 清理
- 每条写入需要一次 LLM 调用 → 有 API 成本
- embedding 服务需要 OpenAI 兼容格式 → 可能需要代理

---

## 2. Cognee（topoteretes/cognee）

### MCP 工具列表

| 工具 | 用途 | 触发条件 |
|------|------|---------|
| `cognify(data)` | 文本/文档 → 知识图谱 | 大量文档导入后 |
| `codify(repo_path)` | 代码库 → 代码图谱 | 新项目第一次接触 |
| `search(query, search_type)` | 知识图谱搜索 | 需要关系推理时 |
| `prune()` | 清理低质量节点 | 每月维护 |
| `cognee_add_developer_rules(rules)` | 添加开发规则 | 项目初始化 |
| `list_data()` | 列出数据集 | 维护时 |
| `delete(dataset)` | 删除数据集 | 维护时 |

### 配置

```bash
# 环境变量（.env 或 claude mcp add --env）
VECTOR_DB_PROVIDER=qdrant
VECTOR_DB_URL=http://localhost:6333
GRAPH_DB_PROVIDER=kuzu          # 本地文件型，零配置
LLM_API_KEY=${ANTHROPIC_API_KEY}

claude mcp add --scope user --transport stdio cognee \
  -- python -m cognee.api.v1.cognee_mcp
```

### 与 mem0 的分工
- **mem0**：对话事实（短文本，高频，自动）
- **Cognee**：文档/代码关系（长文本，低频，按需）
- **不冲突**：各自使用不同的 Qdrant collection

### 已知限制
- 图谱构建速度慢（多次 LLM 调用提取实体和关系）
- MCP 工具文档不够详细
- Kuzu 图谱文件需要定期备份

---

## 3. Qdrant MCP（增强版）

### 工具列表（现有 + 新增）

| 工具 | 用途 | 状态 |
|------|------|------|
| `search_papers(query, category, top_k)` | 搜索论文库 | 现有 |
| `search_notes(query, tags, top_k)` | 搜索笔记库 | 现有 |
| `search_all(query, top_k)` | 跨 collection 搜索 | 现有 |
| `get_document_info(source_file)` | 查询文件元数据 | 现有 |
| `store_memory(text, type, project, tags)` | **新增** 存储记忆 | 待实现 |
| `search_memories(query, type, project)` | **新增** 搜索记忆 | 待实现 |

### 新增工具设计

```python
# store_memory: 存储到 session_summaries collection
# 字段：text, type(session_summary/finding/decision), project, tags, timestamp
# embedding: 调用 localhost:8999 的 BGE-M3

# search_memories: 搜索 session_summaries collection
# 支持按 type 和 project 过滤
```

---

## 4. Obsidian MCP

### 工具列表

| 工具 | 用途 | 自动触发条件 |
|------|------|------------|
| `view(path)` | 读取文件 | SessionStart 读 _project.md |
| `create(path, content)` | 创建新文件 | 新项目/新任务/新计划 |
| `insert(path, insert_line, new_str)` | 插入内容 | 追加 decisions.md/findings.md |
| `str_replace(path, old_str, new_str)` | 替换内容 | 更新 _project.md status |
| `get_workspace_files()` | 列出所有文件 | SessionStart 判断项目是否存在 |
| `get_current_file()` | 获取当前文件 | 上下文感知 |
| `obsidian_api(endpoint, method, body)` | 直接 API 调用 | 高级操作 |

### 自动触发规则

```
SessionStart:
  get_workspace_files → 检查 01-Projects/{项目}/
  ├── 存在 → view _project.md + decisions.md
  └── 不存在 → create 项目目录 + _project.md

用户纠正:
  insert decisions.md (格式: ## YYYY-MM-DD [主题])

设计确认:
  create plans/YYYY-MM-DD_{简述}.md

里程碑完成:
  str_replace _project.md status

迭代完成:
  create iterative-reports/YYYY-MM-DD_{轮次}.md

Stop:
  str_replace _project.md (更新最新状态)
```

---

## 5. Basic Memory

### 工具列表

| 工具 | 用途 | 触发条件 |
|------|------|---------|
| `write_note(title, content, folder)` | 写入 Markdown | Stop hook 写 session 摘要 |
| `read_note(identifier)` | 读取笔记 | 人类浏览时 |
| `search_notes(query)` | 混合搜索 | 按需 |
| `recent_activity(timeframe)` | 最近变更 | 按需 |
| `build_context(url)` | 知识图遍历 | 需要关联信息时 |

### 配置

```bash
pip install basic-memory
claude mcp add --scope user --transport stdio basic-memory \
  -- basic-memory server --vault ~/pony/obsidian-vault/03-AI-Memory/
```

### 输出格式
Basic Memory 输出标准 Markdown + `[[WikiLink]]`，直接在 Obsidian 中可见。
Stop hook 每次 session 结束自动写一份 session 摘要到此处。

---

## 6. Context7

### 工具列表

| 工具 | 用途 | 触发条件 |
|------|------|---------|
| `resolve-library-id(libraryName)` | 查找库 ID | 写代码前 |
| `query-docs(libraryId, query)` | 查询文档 | 写代码时 |

### 使用方式
已作为 plugin 启用。写代码时在 prompt 中加 `use context7` 或 Claude 自动识别需要时调用。
无需配置，无需维护。

---

## 工具间协作模式

### 模式 1：新 Session 恢复上下文
```
SessionStart hook
  → mem0.search_memories("项目名 + 最近") → 注入事实记忆
  → Obsidian.view(_project.md) → 注入项目状态
  → Obsidian.view(decisions.md) → 注入历史决策
```

### 模式 2：用户纠正判断
```
检测到用户纠正
  → mem0.add_memory(type="correction", text="...")  // AI 下次检索
  → Obsidian.insert(decisions.md, "## 2026-03-15 ...")  // 人类可查
```

### 模式 3：写调研报告
```
开始写报告前
  → Qdrant.search_papers("相关主题")  // 搜索论文
  → Obsidian.view(iterative-reports/)  // 读历史报告
  → Cognee.search("实体关系")  // 理解知识结构
```

### 模式 4：Session 结束
```
Stop hook
  → 生成摘要
  → mem0.add_memory(session_summary)  // AI 记忆
  → Basic Memory.write_note(摘要.md)  // 人类可读
  → Obsidian.str_replace(_project.md)  // 更新状态
  → 条件 git push
```
