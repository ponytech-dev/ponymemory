# PonyMemory — Claude Code 全自动记忆系统架构

> 设计一次，自动运转，Claude Code 越用越聪明，用户零维护。

## 一、系统总览

```
                         PonyMemory 全自动记忆系统
                         ========================

Claude Code Session
  │
  ├─ [SessionStart Hook] ─→ 自动检索 Qdrant 记忆 + Obsidian 项目状态 + HANDOFF + 领域规则
  │
  ├─ [对话过程] ──────────→ Claude Code 作为操作者：
  │                         → 事实提取 → store_memory（写入 Qdrant）
  │                         → 去重判断 → search_memories → update_memory / 跳过
  │                         → Qdrant 按需搜索知识库（论文/笔记/文档）
  │                         → Context7 自动注入 API 文档（写代码时）
  │                         → Obsidian 按规则自动写入（决策/发现/计划）
  │
  ├─ [Stop Hook] ─────────→ 增量记忆提醒（每次响应后触发）
  │                         → store_memory 存储纠正/决策/发现（如有）
  │                         → 自动规则提取 → CLAUDE.md / memory/
  │                         → 条件 Git Push（≥3 unpushed commits）
  │                         → 每 10 轮自动记忆维护（去重/矛盾解决/清理）
  │
  ├─ [PreCompact Hook] ──→ 压缩前紧急保存关键上下文到 Qdrant 记忆
  │
  └─ [无 Cron] ──────────→ 所有维护通过 Stop Hook 触发（零额外 API 成本）
```

## 二、五层记忆架构

每层有且仅有一个职责，严格不重叠。

| 层 | 机制 | 加载方式 | 存储内容 | 生命周期 |
|----|------|---------|---------|---------|
| L1 | CLAUDE.md | 每次自动加载 | 行为规则（怎么做） | git 版本化 |
| L2 | Claude memory/ | 每次自动加载 | 用户偏好、反馈、快速引用 | 永久轻量 |
| L3 | Qdrant（Claude Code 操作） | SessionStart 注入 + 按需搜索 | 对话事实、纠正、决策（语义检索） | Claude Code 主动去重更新 |
| L4 | Obsidian | MCP 按需触发 | 报告、决策、发现、任务日志（人类可读） | 永久结构化 |
| L5 | Qdrant 知识库 + Context7 | 按需搜索 | 论文/笔记/文档 + API 文档 | 永久 |

### 分工原则
- 规则约束 → L1（自动加载，硬性约束）
- 高频轻量 → L2（用户偏好、反馈修正、外部系统路径引用）
- AI 记忆 → L3（Claude Code 作为操作者，Qdrant 做语义检索）
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

**核心设计**：mem0 已移除。Claude Code 直接作为操作者，通过 Qdrant MCP 工具执行事实提取、去重、冲突解决。

| 属性 | 值 |
|------|-----|
| MCP 服务器 | `qdrant-search`（统一入口） |
| 向量存储 | Qdrant `session_memories` collection（1024 维，cosine） |
| Embedding | BGE-M3 via localhost:8999/embed |
| LLM | Claude Code 自身（事实提取和去重判断由 Claude Code 完成） |

**Qdrant 记忆工具**：

| 工具 | 用途 |
|------|------|
| store_memory | 存储记忆到 Qdrant。支持 supersedes。每条 50-200 字，含 what + why + impact，附 source_path 指向 L4 完整文档 |
| search_memories | 语义搜索，支持 memory_type/project 过滤 |
| update_memory | 更新已有记忆（合并旧 payload + 重新 embedding） |
| delete_memory | 删除记忆（Qdrant） |
| list_all_memories | 分页列出所有记忆，支持过滤 |

**存什么**：用户纠正、技术决策、项目状态变化、session 摘要
**不存什么**：代码片段、论文内容、结构化文档

**Claude Code 操作流程**：
```
对话中检测到值得记忆的事实
  → search_memories(query) 检查是否已有相似记忆
  → 如有相似：update_memory(id, 合并后的文本) 或 store_memory(supersedes=旧ID)
  → 如无重复：store_memory(text, source_path=L4文档路径)
```

### L3/L4 主从关系
- **L4 是权威来源，L3 是检索索引**。冲突时 L4 优先
- 写入时同时更新 L3（摘要）和 L4（全文）
- L3 摘要字数：50-200 字，包含 what + why + impact

### L3/L4 协作规则

| 场景 | L3 能回答 | 需要 L4 |
|------|---------|---------|
| 确认事实（"X 是否已完成？"） | ✅ | |
| 了解原因（"为什么做 X？"） | ⚠️ 通常够 | 需要完整决策上下文时 |
| 查看执行细节 | | ✅ |
| 回顾项目历史 | | ✅ |

写入规则：
- L3 store_memory: 50-200 字摘要 + source_path 指向 L4
- L4 Obsidian: 完整文档（背景、过程、细节）
- 双写时机：决策/里程碑/纠正 → 必须双写
- 纯事实（"今天跑了 E2E 测试"）→ 只写 L3

### Layer 4: Obsidian — 结构化知识归档

| 属性 | 值 |
|------|-----|
| MCP 服务器 | `obsidian`（mcp-remote localhost:22360） |
| 工具 | view, create, insert, str_replace, get_workspace_files |

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

### Stop（stop.py）— 增量模式 + 自动维护
```
触发：每次 Claude 完成一轮响应（不是 session 结束！）
执行：
1. 增量记忆提醒：
   - 用户纠正 → store_memory(memory_type=correction) + Obsidian decisions.md
   - 技术决策 → store_memory(memory_type=decision) + Obsidian decisions.md
   - 发现 bug → store_memory(memory_type=finding) + Obsidian findings.md
   - 里程碑 → store_memory(memory_type=milestone) + Obsidian _project.md
   - 存储前必须 search_memories 检查重复，有则 update_memory
2. 自动规则提取（检测"不要...""应该..."等模式）
3. 条件 Git Push（≥3 unpushed commits + 前置检查）
4. 自动记忆维护（每 10 轮触发）：
   - list_all_memories 扫描所有记忆
   - 合并重复 → update_memory
   - 解决矛盾 → 保留最新，delete_memory 旧的
   - 清理过时 → >30天的 session_summary 考虑删除
5. Session 摘要提醒（当日首次有实质进展时创建）
```

### PreCompact（pre_compact.py）
```
触发：Context 即将被压缩
执行：
1. 提醒保存进行中任务到 Qdrant（store_memory）
2. 提醒更新 Obsidian 任务状态
3. 提醒写 session 摘要
4. 注入 HANDOFF.md 内容
```

## 四、MCP 服务器清单

| 服务器 | 运行环境 | 脚本路径 | 关键配置 |
|--------|---------|---------|---------|
| qdrant-search | scripts/.venv (Python 3.14) | scripts/qdrant-mcp-server.py | Qdrant + BGE-M3 |
| obsidian | npx mcp-remote | localhost:22360 | Obsidian 需运行 |

**已移除**：
- ~~mem0~~ → Claude Code 替代其 LLM 层，直接操作 Qdrant
- ~~cognee~~ → 已移除，图谱功能不再使用

## 五、维护机制

**不使用 Cron**，改为 Stop Hook 触发式维护（零额外 API 成本）：

| 触发条件 | 维护任务 |
|---------|---------|
| 每 10 轮响应 | 轻量维护（去重/矛盾/过时清理） |
| 手动 `/memory-maintain` | 完整维护 |

## 六、文件清单

```
~/pony/ponymemory/
├── ARCHITECTURE.md          # 本文件
├── CLAUDE.md                # 项目级规则
├── hooks/
│   ├── session_start.py     # SessionStart hook
│   ├── stop.py              # Stop hook（增量模式 + 自动维护）
│   └── pre_compact.py       # PreCompact hook
├── scripts/
│   └── mem0-mcp-server.py   # 已废弃（保留参考）
├── plans/                   # 计划文件
└── docs/                    # 文档

~/pony/scripts/
└── qdrant-mcp-server.py     # 统一 MCP 服务器（Qdrant）
```

## 七、依赖服务

| 服务 | 地址 | 用途 |
|------|------|------|
| Qdrant | localhost:6333 | 向量数据库（Docker） |
| BGE-M3 Embedding | localhost:8999 | 向量化（Flask + /embed） |
| Obsidian | localhost:22360 | 笔记系统 MCP |
