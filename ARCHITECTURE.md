# PonyMemory — Claude Code 全自动记忆系统架构

> 设计一次，自动运转，Claude Code 越用越聪明，用户零维护。

## 一、系统总览（v2 Queue+Worker 模型）

```
                         PonyMemory v2 全自动记忆系统
                         ============================

Claude Code Session
  │
  ├─ [SessionStart Hook] ─→ 检索 Qdrant 记忆 + 读取 Obsidian 项目状态 + HANDOFF + 领域规则
  │                         注入方式：additionalContext
  │
  ├─ [对话过程 + Write/MCP 工具调用]
  │        │
  │        ├─ [Stop Hook v3] ─────→ 读取 transcript 增量 → 写入 SQLite 队列（conversations）
  │        └─ [PostToolUse Hook] ─→ 捕获文件写入/MCP 下载事件 → 写入 SQLite 队列（file_events）
  │
  ├─ [PreCompact Hook] ──→ 压缩前紧急保存关键上下文到 Qdrant（直接调用 MCP，不经队列）
  │
  └─ [Worker 进程（launchd 托管，常驻）]
           │  轮询 SQLite 队列（每 5 秒）
           ├─ conversations → Haiku 提取事实 → 质量门控 → BGE-M3 向量化 → Qdrant 写入
           │                                              → Obsidian 直写文件（decisions/findings/milestones）
           ├─ file_events → router.py 分类 → BGE-M3 → Qdrant collection 路由写入
           └─ 降级链：Qdrant/BGE-M3 不可用 → fallback.jsonl 本地保存
```

**健康端点**：Worker 暴露 `GET http://localhost:47777/health`，返回 Qdrant/BGE-M3 状态 + 队列计数。

## 二、五层记忆架构

每层有且仅有一个职责，严格不重叠。

| 层 | 机制 | 加载方式 | 存储内容 | 生命周期 |
|----|------|---------|---------|---------|
| L1 | CLAUDE.md | 每次自动加载 | 行为规则（怎么做） | git 版本化 |
| L2 | Claude memory/ | 每次自动加载 | 用户偏好、反馈、快速引用 | 永久轻量 |
| L3 | Qdrant（Worker 写入） | SessionStart 注入 + 按需搜索 | 对话事实、纠正、决策（语义检索） | Worker 自动去重更新 |
| L4 | Obsidian | Worker 直写文件（无 MCP） | 报告、决策、发现、任务日志（人类可读） | 永久结构化 |
| L5 | Qdrant 知识库 + Context7 | 按需搜索 | 论文/笔记/文档 + API 文档 | 永久 |

### 分工原则
- 规则约束 → L1（自动加载，硬性约束）
- 高频轻量 → L2（用户偏好、反馈修正、外部系统路径引用）
- AI 记忆 → L3（Worker 作为写入者，Qdrant 做语义检索）
- 深度知识 → L4（报告、调研、决策——数据量大，按需加载）
- 语义检索 → L5（论文/笔记向量搜索，Context7 实时 API 文档）
- 层间不存重复内容；L2 可存指向 L4 的路径引用

### Layer 1: CLAUDE.md — 行为规则

- 全局 `~/pony/CLAUDE.md`：跨项目通用规则
- 项目级 `{项目}/CLAUDE.md`：项目特定规则
- 每次 session 自动加载，无需搜索

### Layer 2: Claude memory/ — 用户偏好

- 路径：`~/.claude/projects/-Users-jiajun-agent-pony/memory/`
- 内容：用户偏好、反馈、快速引用、项目状态索引
- MEMORY.md 前 200 行自动加载

### Layer 3: Qdrant — AI 自动记忆

**核心设计**：Stop Hook v3 将 transcript 增量写入 SQLite 队列；Worker 异步调用 Haiku 提取事实，BGE-M3 向量化，写入 Qdrant。Claude Code 本身不再直接调用 store_memory。

| 属性 | 值 |
|------|-----|
| MCP 服务器（读） | `qdrant-search`（SessionStart/PreCompact 用于读） |
| Worker 写入 | Qdrant HTTP API 直连（无 MCP 依赖） |
| 向量存储 | Qdrant `session_memories` collection（1024 维，cosine） |
| Embedding | BGE-M3 via localhost:8999/embed |
| LLM（提取） | Claude Haiku（worker.py 调用 Anthropic SDK） |

**Qdrant MCP 工具**（SessionStart 读取用）：

| 工具 | 用途 |
|------|------|
| search_memories | 语义搜索，支持 memory_type/project 过滤 |
| list_all_memories | 分页列出所有记忆，支持过滤 |

**存什么**：用户纠正、技术决策、项目状态变化、session 摘要
**不存什么**：代码片段、论文内容、结构化文档

### L3/L4 主从关系
- **L4 是权威来源，L3 是检索索引**。冲突时 L4 优先
- Worker 写入时同时更新 L3（摘要）和 L4（全文）
- L3 摘要字数：50-200 字，包含 what + why + impact

### Layer 4: Obsidian — 结构化知识归档

| 属性 | 值 |
|------|-----|
| 写入方式 | Worker 直接写文件（`obsidian_writer.py`，无 MCP 依赖） |
| MCP（读） | `obsidian`（mcp-remote localhost:22360），SessionStart 读取项目状态 |

**Vault 结构**：
```
01-Projects/
  {项目名}/
    _project.md          # 项目状态概要（<100行）
    decisions.md          # 用户纠正、技术决策
    findings.md           # 审查发现、bug 记录
    iterative-reports/    # 迭代循环报告
    plans/                # 已确认设计方案
03-Knowledge/
  _session_summaries/     # 日期命名的 session 摘要
  {领域}/
    learned_rules.md      # 领域经验规则
    explorations/         # 探索记录
```

### Layer 5: Qdrant 知识库 + Context7 — 语义检索

**Qdrant MCP**（`qdrant-search`）：
- Collections: papers, notes, documents, session_memories
- 工具: search_papers, search_notes, search_all, get_document_info

**Context7**（plugin）：
- 实时 API 文档注入，写代码时自动调用

## 三、Hooks 体系

### SessionStart（session_start.py）
```
触发：每次新 session 启动
执行：
1. 检测 CWD → 推断项目名
2. 读取 Obsidian 项目状态（_project.md + decisions.md）
3. search_memories 搜索项目相关记忆
4. 读取 HANDOFF.md（如存在）
5. 读取 pending_rules.md（如存在）
6. 读取领域经验规则
注入方式：additionalContext
```

### Stop Hook v3（stop.py）— 队列写入模式
```
触发：每次 Claude 完成一轮响应
执行：
1. 读取 ~/.claude/projects/.../transcript.jsonl 增量部分（记录上次偏移量）
2. 将增量对话写入 SQLite 队列（db.py write_queue_item）
3. Worker 异步消费队列（不阻塞 Hook）
注意：不再直接调用 store_memory / Obsidian MCP
```

### PostToolUse Hook（post_tool_use.py）
```
触发：每次工具调用完成后
执行：
1. 检测 Write/Edit 工具 → 提取文件路径 + 内容预览
2. 检测 MCP 工具（文档下载等）→ 提取资源信息
3. 过滤忽略路径（node_modules / .git / tmp / HANDOFF / plans 等）
4. 写入 SQLite 队列（file_events 类型）
```

### PreCompact（pre_compact.py）
```
触发：Context 即将被压缩
执行：
1. 提醒保存进行中任务到 Qdrant（直接调用 MCP store_memory）
2. 提醒更新 Obsidian 任务状态
3. 提醒写 session 摘要
4. 注入 HANDOFF.md 内容
```

## 四、Worker 进程

**进程管理**：launchd（`~/Library/LaunchAgents/com.ponymemory.worker.plist`）

**Worker 主循环**（worker.py）：
```
每 5 秒轮询 SQLite 队列
  → claim_next_item() 取出一条 pending 记录
  → 按类型分发：
      conversations → process_conversation()
        → format_conversation() 格式化对话
        → extract_facts() Haiku 提取事实
        → filter_by_quality() 质量门控（score ≥ 0.6）
        → search_qdrant() 去重检查
        → store_qdrant_memory() 写入 Qdrant
        → write_obsidian_entry() 写入 Obsidian vault
      file_events → classify_file() 路由分类
        → embed_text() BGE-M3 向量化
        → 写入对应 Qdrant collection
  → 成功：delete_queue_item()
  → 失败：mark_failed()（最多 3 次重试）
  → 降级：Qdrant/BGE-M3 不可用时写 fallback.jsonl

每日维护任务（maybe_run_maintenance）：
  → 去重合并、矛盾解决、过时清理
```

**健康端点**（health.py）：`GET localhost:47777/health`
- 返回：Worker 状态、Qdrant 连通性、BGE-M3 连通性、队列计数

**降级链**：
```
Qdrant 可用 → 正常写入
Qdrant 不可用 → 写 ~/.claude/.ponymemory_fallback.jsonl
Worker 未运行 → 队列积累在 SQLite（无害，Worker 重启后处理）
```

## 五、MCP 服务器清单

| 服务器 | 运行环境 | 脚本路径 | 关键配置 |
|--------|---------|---------|---------|
| qdrant-search | scripts/.venv (Python 3.14) | scripts/qdrant-mcp-server.py | Qdrant + BGE-M3 |
| obsidian | npx mcp-remote | localhost:22360 | Obsidian 需运行（仅用于读） |

**已移除**：
- ~~Neo4j~~ → 2026-03-19 结论：冗余。Qdrant entity_names + 语义搜索覆盖 95% 场景
- ~~mem0~~ → Worker + Haiku 替代其 LLM 层，直接操作 Qdrant
- ~~cognee~~ → 已移除

## 六、文件清单

```
~/pony/ponymemory/
├── ARCHITECTURE.md          # 本文件
├── CLAUDE.md                # 项目级规则
├── db.py                    # SQLite 队列层
├── worker.py                # Worker 主循环
├── extractor.py             # Haiku 事实提取 + 质量门控
├── embedder.py              # BGE-M3 embedding + Qdrant HTTP 读写
├── obsidian_writer.py       # Obsidian vault 直写（无 MCP）
├── router.py                # 文件分类路由引擎
├── health.py                # HTTP 健康检查端点（:47777）
├── hooks/
│   ├── session_start.py     # SessionStart hook
│   ├── stop.py              # Stop hook v3（transcript → SQLite 队列）
│   ├── post_tool_use.py     # PostToolUse hook（文件事件捕获）
│   └── pre_compact.py       # PreCompact hook
├── tests/                   # 71 个单元测试（全部通过）
├── scripts/
│   └── mem0-mcp-server.py   # 已废弃（保留参考）
├── plans/                   # 计划文件
└── docs/                    # 文档

~/pony/scripts/
└── qdrant-mcp-server.py     # 统一 MCP 服务器（Qdrant）

~/Library/LaunchAgents/
└── com.ponymemory.worker.plist  # launchd Worker 配置
```

## 七、依赖服务

| 服务 | 地址 | 用途 | 必需？ |
|------|------|------|--------|
| Qdrant | localhost:6333 | 向量数据库（Docker） | ✅ L3/L5 核心 |
| BGE-M3 Embedding | localhost:8999 | 向量化（Flask + /embed） | ✅ 向量化 |
| Obsidian | localhost:22360 | 笔记系统 MCP（读取） | ⚠️ L4 读取需要 |
| Worker 健康端点 | localhost:47777 | Worker 状态监控 | ⚠️ 可选 |
| ~~Neo4j~~ | ~~localhost:7687~~ | ~~图遍历~~ | ❌ 已移除 |
