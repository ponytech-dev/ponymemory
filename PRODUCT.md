# PonyMemory — 产品文档

> 最后更新：2026-03-20
> 自动维护：代码变更时由 Claude Code 同步更新

## 定位

Claude Code 的全自动记忆系统。管理五层记忆的自动化读写和维护，让 Claude Code 越用越聪明，用户零维护。**PonyMemory 是基础设施层，所有项目共享，所有 session 自动运行。**

### 与 PonyWriterX 的边界

| | PonyMemory | PonyWriterX Knowledge Galaxy |
|--|--|--|
| **是什么** | 记忆基础设施（后端 + 自动化） | 可视化前端（展示层） |
| **覆盖范围** | 所有项目（编程 + 科研 + 运维） | 仅 PonyWriterX 科研场景 |
| **运行方式** | 每个 session 自动运行（Hooks） | 用户主动打开网页查看 |
| **产出** | 五层记忆数据 | 3D 大脑 + 知识星空的交互式渲染 |
| **数据关系** | PonyMemory 生产数据 | Knowledge Galaxy 消费数据 |

**原则**：PonyMemory 负责数据的采集、存储、检索、维护。Knowledge Galaxy 只是数据的一种可视化消费方式。PonyMemory 即使没有 Knowledge Galaxy 也完整运行；Knowledge Galaxy 离开 PonyMemory 的数据则无法渲染。

## 目标用户

- Claude Code 用户，希望 AI 助手记住跨 session 的上下文
- 多项目开发者，需要 AI 在不同项目间保持记忆连续性
- 科研人员，需要积累领域知识和研究过程记忆

## 核心功能

### 五层记忆（全自动，跨所有项目）

| 层 | 名称 | 存储载体 | 加载方式 | 内容 | 自动化 |
|----|------|---------|---------|------|--------|
| L1 | 行为规则 | CLAUDE.md | 每次自动加载 | 项目约束、验证标准、安全策略 | ✅ Claude Code 原生 |
| L2 | 用户偏好 | Claude memory/ | 每次自动加载 | 偏好、反馈纠正、习惯模式 | ✅ Claude Code 原生 |
| L3 | 情景记忆 | Qdrant session_memories | SessionStart 注入 + 按需搜索 | 对话事实、技术决策、用户纠正 | ✅ Hooks 自动触发 |
| L4 | 结构化归档 | Obsidian vault | MCP 按需触发 | 项目状态、决策记录、迭代报告 | ✅ Stop Hook 提醒 |
| L5 | 知识检索 | Qdrant RAG + Context7 | 按需搜索 | 论文、笔记、文档、API 文档 | ✅ 按需自动 |

### 自动化 Hooks（所有 session 自动运行）

| Hook | 触发时机 | 自动执行的动作 |
|------|---------|---------------|
| **SessionStart** | 每次新 session 启动 | ① 检索 Qdrant 项目相关记忆 ② 读取 Obsidian 项目状态 ③ 检查 HANDOFF.md ④ 注入到 context |
| **Stop** | Claude 每次响应后 | ① 增量记忆存储（纠正/决策/发现） ② 自动规则提取 ③ 条件 Git Push（≥3 commits） ④ 每 10 轮记忆维护（去重/矛盾/清理） ⑤ 产品文档过期检测 |
| **PreCompact** | Context 压缩前 | ① 紧急保存进行中任务到 Qdrant ② 更新 Obsidian 状态 ③ 注入 HANDOFF.md |
| **Compact** | Context 压缩后 | ① 重新注入 HANDOFF.md 恢复上下文 |

### 记忆维护（自动，零用户干预）

- **增量维护**：每次 Stop Hook 检测值得记忆的事实 → search → store/update
- **批量维护**：每 10 轮响应自动触发 → 去重 + 矛盾解决 + 过时清理
- **L3/L4 主从关系**：L4（Obsidian）是权威来源，L3（Qdrant）是检索索引。冲突时 L4 优先

### 可视化（Brain Atlas → Knowledge Galaxy 数据供应）

PonyMemory 提供 `generate_galaxy_data.py` 脚本，从五层记忆提取真实数据生成 `galaxy-data.json`：

```
数据提取管线：
L1: 解析 CLAUDE.md → 提取规则条目
L2: 解析 MEMORY.md + memory/*.md → 提取偏好条目
L3: HTTP 查询 Qdrant session_memories → 提取记忆条目
L4: 扫描 Obsidian vault → 提取文件列表
L5: 查询 Qdrant papers/notes/documents → 提取知识文档（按 source_file 去重）
```

**可视化映射**（Brain Atlas）：

| 层 | 大脑区域 | 颜色 | 映射逻辑 |
|----|---------|------|---------|
| L1 | 前额叶皮层（Prefrontal Cortex） | 青色 #00bcd4 | 执行控制 → 行为规则 |
| L2 | 海马体（Hippocampus） | 橙色 #ff9800 | 记忆巩固 → 用户偏好 |
| L3 | 新皮层（Neocortex） | 蓝色 #42a5f5 | 长期语义记忆 → AI 事实记忆 |
| L4 | 颞叶（Temporal Lobe） | 紫色 #ab47bc | 陈述性记忆 → 结构化文档 |
| L5 | 后顶叶皮层（Posterior Parietal） | 青绿色 #26a69a | 信息整合 → 知识检索 |

**数据消费者**：
- PonyWriterX Knowledge Galaxy（3D WebGL 渲染，科研场景）
- PonyMemory Brain Atlas（GitHub Pages，技术展示）
- 未来：其他项目的可视化仪表盘

## 核心工作流

```
所有项目的每个 session，全自动运行：

Session 启动
  → SessionStart Hook 自动触发
  → 检索 Qdrant 记忆（按项目过滤）
  → 读取 Obsidian 项目状态（_project.md + decisions.md）
  → 检查 HANDOFF.md
  → 注入到 Claude context window

对话过程（Claude Code 作为操作者）
  → 检测到值得记忆的事实（纠正/决策/发现/里程碑）
  → search_memories 检查重复
  → store_memory 或 update_memory（Qdrant，50-200 字，含 what + why + impact）
  → 同步写入 Obsidian（L4 全文 + L3 摘要）

每次响应后
  → Stop Hook 自动触发
  → 增量记忆存储提醒
  → 自动规则提取（"不要...""应该..."模式 → CLAUDE.md 或 memory/）
  → 每 10 轮：批量维护（去重/矛盾解决/清理 >30 天 session_summary）
  → 产品文档过期检测（代码变更但 PRODUCT.md 未更新）

Context 压缩前
  → PreCompact Hook 自动触发
  → 紧急保存进行中任务到 Qdrant
  → 更新 Obsidian 项目状态
```

## 技术架构

```
ponymemory/                              ← 项目仓库
├── ARCHITECTURE.md                      ← 完整架构设计
├── PRODUCT.md                           ← 本文件
├── CLAUDE.md                            ← 项目规则
├── hooks/                               ← Hook 脚本源码
│   ├── session_start.py
│   ├── stop.py
│   └── pre_compact.py
├── scripts/                             ← 工具脚本
├── configs/                             ← 配置文件
└── docs/                                ← 文档

~/pony/scripts/
└── qdrant-mcp-server.py                 ← 统一 MCP 服务器（Qdrant + BGE-M3）

~/pony/jiajun-agent-system/scripts/hooks/
├── check-handoff.sh                     ← HANDOFF.md 检查
├── doc-staleness-check.sh               ← 产品文档过期检测
├── save-plan.sh                         ← Plan 归档
└── ...                                  ← 其他自动化 hooks

~/.claude/projects/.../memory/           ← L2 记忆文件
~/pony/obsidian-vault/                   ← L4 Obsidian vault
```

### Qdrant Collections

| Collection | 层 | 用途 | 维度 |
|-----------|-----|------|------|
| session_memories | L3 | 情景记忆（纠正/决策/发现/里程碑） | 1024 (cosine) |
| papers | L5 | 论文向量 | 1024 |
| notes | L5 | 笔记向量 | 1024 |
| documents | L5 | 文档向量 | 1024 |

### MCP 工具清单

| 工具 | 用途 | 自动化调用 |
|------|------|-----------|
| store_memory | 存储记忆（支持 supersedes） | Stop Hook 提醒 |
| search_memories | 语义搜索（memory_type/project 过滤） | SessionStart 自动 |
| update_memory | 更新记忆（合并 payload + 重新 embedding） | 维护时自动 |
| delete_memory | 删除记忆 | 维护时自动 |
| list_all_memories | 分页列出（支持过滤） | 维护时自动 |
| search_papers | 搜索论文集合 | 按需 |
| search_notes | 搜索笔记集合 | 按需 |
| search_all | 跨集合搜索 | 按需 |
| get_document_info | 获取文档元数据 | 按需 |

### Hooks 注册位置

| Hook | 全局 settings.json | 项目 settings.json |
|------|-------------------|-------------------|
| SessionStart | `~/.claude/settings.json` PonyWriterX 检测 | `~/pony/.claude/settings.json` 项目检测 + Obsidian 恢复 |
| Stop | `~/.claude/settings.json` PonyWriterX stop.py | `~/pony/.claude/settings.json` HANDOFF + 规则提取 + doc 检测 |
| PreCompact | `~/.claude/settings.json` PonyWriterX pre_compact.py | — |
| Compact | — | `~/pony/.claude/settings.json` HANDOFF 重注入 |

## 依赖与基础设施

| 服务 | 端口 | 运行方式 | 必需？ |
|------|------|---------|--------|
| Qdrant | :6333 | Docker | ✅ L3/L5 核心 |
| BGE-M3 embedding | :8999 | Docker | ✅ 向量化 |
| Obsidian MCP | :22360 | npx mcp-remote | ⚠️ L4 需要（Obsidian 须运行） |

**已移除/评估中**：
- ~~Neo4j~~ — 2026-03-19 第一性原理分析结论：冗余。Qdrant entity_names 过滤 + 语义搜索覆盖 95% 场景
- ~~mem0~~ — 2026-03-15 移除。Claude Code 替代其 LLM 层
- ~~cognee~~ — 已移除

## 当前限制

- L3 和 L4 对同一事件存在双写 → 已修正为 L3/L4 主从关系（L4 全文 + L3 摘要索引）
- Hook 输出是"提醒"而非"强制"，Claude 可能忽略
- 记忆维护计数器在 context 压缩后重置
- Obsidian MCP 依赖 Obsidian 应用运行中

## 集成点

| 接口 | 说明 | 方向 |
|------|------|------|
| Qdrant MCP | 记忆和知识库的统一入口 | PonyMemory ↔ Qdrant |
| Obsidian MCP | 项目状态和知识归档 | PonyMemory ↔ Obsidian |
| Claude Code Hooks | 生命周期自动化 | Claude Code → PonyMemory |
| BGE-M3 HTTP API | 文本向量化 | PonyMemory → BGE-M3 |
| galaxy-data.json | 可视化数据供应 | PonyMemory → Knowledge Galaxy |
| generate_galaxy_data.py | 数据提取脚本 | PonyMemory 内部 |

## 自动化保障——所有项目、所有 session

PonyMemory 的自动化不依赖用户记忆或主动调用：

```
用户打开任何项目 session
  ↓
settings.json 中的 Hooks 自动注册（全局 + 项目级）
  ↓
SessionStart → 自动注入记忆 + 项目状态
  ↓
Stop → 自动提醒存储 + 规则提取 + 维护 + 文档检测
  ↓
PreCompact → 自动紧急保存
  ↓
用户无需执行任何操作，记忆系统在后台持续运行
```

**跨项目覆盖验证**：

| 项目 | SessionStart 注入 | Stop 记忆存储 | 产品文档检测 |
|------|------------------|--------------|-------------|
| PonyWriterX | ✅ | ✅ | ✅ |
| PonylabASMS | ✅ | ✅ | ✅ |
| Ponylab | ✅ | ✅ | ✅ |
| SpaFlow | ✅ | ✅ | ✅ |
| MetaboFlow | ✅ | ✅ | ✅ |
| PonyMemory | ✅ | ✅ | ✅ |

## 文档索引

| 文档 | 路径 |
|------|------|
| 完整架构设计 | `ARCHITECTURE.md` |
| 项目规则 | `CLAUDE.md` |
| MCP 服务器源码 | `~/pony/scripts/qdrant-mcp-server.py` |
| Hook 脚本 | `hooks/` |
| Galaxy 数据生成 | `~/pony/ponywriterX/apps/skill/galaxy/generate_galaxy_data.py` |
| 全局 Hooks 配置 | `~/.claude/settings.json` |
| 项目 Hooks 配置 | `~/pony/.claude/settings.json` |

## 变更日志（最近 5 条）

| 日期 | 变更 |
|------|------|
| 2026-03-20 | PRODUCT.md 全面更新：明确与 PonyWriterX 边界、可视化数据供应架构、自动化跨项目覆盖表 |
| 2026-03-19 | 第一性原理分析：Neo4j 冗余，建议移除。产品文档自动同步机制（doc-staleness-check.sh） |
| 2026-03-19 | L3/L4 主从关系确立：L4 全文 + L3 摘要索引，消除双写冗余 |
| 2026-03-15 | mem0 移除，迁移到 Qdrant 直连 |
