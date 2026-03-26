# Findings — PonyMemory 可靠性推进

## Key Decisions

- **Neo4j 移除**（2026-03-19）：第一性原理分析结论——冗余。Qdrant entity_names + 语义搜索覆盖 95%。代码已清理，文档待清理
- **Stop hook v2 重写**（2026-03-20）：从 134 行重构为 110 行。核心提醒从 20 行表格压缩为 5 行，规则提取从每次改为每 5 轮。已部署
- **CLAUDE.md 精简**（2026-03-20）：292 行 → 174 行（-41%）。迭代循环规则移到 docs/iterative-loop-rules.md，中文 PDF 规则移到 docs/chinese-pdf-rules.md
- **MEMORY.md 精简**（2026-03-20）：92 行 → 58 行（-37%）。删除与 CLAUDE.md/ARCHITECTURE.md 的三重重复
- **PonyWriterX hooks 注册**（2026-03-20）：3 个 Python hooks 注册到全局 settings.json，与 ponymemory hooks 并行运行
- **PonyMemory 和 PonyWriterX 的边界**（2026-03-20）：PonyMemory = 数据生产（采集/存储/检索/维护），Knowledge Galaxy = 数据消费（渲染）
- **generate_galaxy_data.py 加 --scope 参数**（2026-03-20）：global（全局 767 节点）vs ponywriterx（过滤后 249 节点）

## Critical Bug Found

- **session_start.py embed_text() API 格式错误**（2026-03-21 Agent 验证确认）：
  - 发送 `{"text": "..."}` 但 BGE-M3 API 要求 `{"texts": ["..."]}`
  - 导致向量返回 None → Qdrant 搜索跳过 → L3 记忆**从未成功注入过任何 session**
  - 这是 PonyMemory "感觉不工作"的根因
  - 修复：改一行代码，embed_text() 用正确的 API 格式

- **PonyWriterX 活跃时双方互相让位**：
  - ponymemory hooks 检测到 .active_session 锁 → 让位
  - ponywriterx hooks 检测到 HAS_GLOBAL_HOOKS=True → 跳过记忆搜索
  - 结果：PonyWriterX 项目活跃时无 Qdrant 记忆注入

## Rejected Approaches

- **Stop hook 直接写入 Qdrant**：被否决。hook 运行时看不到对话内容，不知道"写什么"。只能通过提醒 Claude 来写
- **PonyMemory 合并进 PonyWriterX**：被否决。PonyMemory 是全局基础设施（跨所有项目），PonyWriterX 是科研产品。但两者可以共享基础设施（Qdrant/BGE-M3）
- **Neo4j 用于科研知识图谱**：被否决。当前实现太浅（对话碎片），科研图谱应由 Obsidian wikilinks + 领域 MCP（ChEMBL/PubChem）实现

## Skills/Plugins 清理（2026-03-21）

- 删除 4 个低频 Skills（obsidian-bases, json-canvas, experience-recorder, obsidian-cli）
- 禁用 3 个 Plugins（semgrep, hookify, feature-dev）
- 安装 superpowers plugin（含 brainstorming/debugging/verification/TDD 14 个 skills）
- 安装 3 个 GitHub PR skills + markitdown CLI
- 发现 plugin skills 不被 Skill tool 发现的问题 → 用 symlink 解决
- L1 规则过滤：从 450 条降到 85 条（去掉表格行和信息性描述）
