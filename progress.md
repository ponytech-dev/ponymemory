# Progress Log — PonyMemory 可靠性推进

## 2026-03-19 第一性原理分析
- 操作：对五层架构逐层压力测试
- 结果：Neo4j 判定冗余，Obsidian MCP 依赖可选，L3/L4 双写问题确认
- 下一步：执行简化（删 Neo4j，明确 L3/L4 主从关系）

## 2026-03-20 CLAUDE.md + MEMORY.md 精简
- 操作：4 个并行 agent 审查 292 行 CLAUDE.md，逐条判断保留/移走
- 结果：精简到 174 行（-41%），MEMORY.md 精简到 58 行（-37%）
- 产出：docs/iterative-loop-rules.md, docs/chinese-pdf-rules.md

## 2026-03-20 产品文档自动同步机制
- 操作：创建 doc-staleness-check.sh + Stop hook 注册 + 6 个 PRODUCT.md
- 结果：Stop hook 自动检测代码变更但文档未更新
- 注意：doc-staleness-check.sh 已注册到项目级 settings.json，但未注册到全局

## 2026-03-20 PonyWriterX hooks 注册
- 操作：3 个 Python hooks 注册到全局 settings.json
- 结果：并行运行，各自有去重逻辑
- 注意：尚未在真实 session 中验证

## 2026-03-20 Stop hook v2 重写
- 操作：降噪 + 简洁化（134行 → 110行）
- 结果：核心提醒压缩为 5 行，规则提取每 5 轮，记忆维护每 10 轮

## 2026-03-20 可视化数据 scope 分离
- 操作：generate_galaxy_data.py 加 --scope 参数
- 结果：global=767 节点，ponywriterx=249 节点
- L1 规则过滤：450 → 85（去掉表格行噪音）

## 2026-03-21 Skills/Plugins 清理与安装
- 删除：obsidian-bases, json-canvas, experience-recorder, obsidian-cli
- 禁用：semgrep（context 消耗大）, hookify（无规则空跑）, feature-dev（和 brainstorming 重叠）
- 安装：superpowers plugin, github-pr-creation/merge/review, markitdown
- 修复：plugin skills 不被 Skill tool 发现 → symlink 到项目级 skills 目录
- 增加：SLASH_COMMAND_TOOL_CHAR_BUDGET=20000

## 2026-03-21 三 Agent 并行诊断
- 操作：架构关系分析 + 开发计划制定 + 触发验证方案
- 发现 P0 bug：session_start.py embed_text() API 格式错误，L3 记忆从未注入
- 发现 P1 bug：PonyWriterX 活跃时双方 hooks 互相让位
- 产出：ponymemory/plans/2026-03-21-1200_reliability-push.md
- 下一步：**Phase 1 — 修复 embed_text() API 格式（一行代码）**

## 2026-03-21 Phase 1-7 全部完成
- Phase 1: embed_text() 改为 `{"texts":[text]}` + `embeddings[0]`，L3 首次成功注入（10条，6482字符）
- Phase 2: session_start.py + stop.py 删除 PonyWriterX 锁早退逻辑（双方互相让位的根因）
- Phase 3: 创建 MetaboFlow/ponylab/ponymemory 的 decisions.md（6/6 全覆盖）
- Phase 4: stop.py 锁bug同步修复，输出正常（记忆检查提醒）
- Phase 5: 锁机制本身正确，问题在 PonyMemory 错误退出（已修复）
- Phase 6: Neo4j 引用已全部标为历史标注，无运维指令
- Phase 7: 端到端冒烟测试通过（SessionStart 7247字符，StopHook 222字符）
- 待验证：跨 session Qdrant 记忆数增加（基线=34）
