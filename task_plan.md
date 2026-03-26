# Task: PonyMemory 可靠性推进 — 从半成品到可靠运行
Created: 2026-03-21 01:00

## Objective
修复 PonyMemory 的所有已确认 bug，使五层记忆系统真正自动工作。

## Phases
- [x] Phase 1: 修复 session_start.py embed API 格式（P0） ✓ L3 注入成功，10条记忆，6482字符
- [x] Phase 2: 修复 PonyWriterX 活跃时双方互相让位的逻辑空洞（P1） ✓ session_start.py + stop.py 均删除锁早退逻辑
- [x] Phase 3: 创建缺失的 Obsidian decisions.md 文件（P1） ✓ MetaboFlow/ponylab/ponymemory 三个已创建，6/6全覆盖
- [x] Phase 4: Stop hook 降噪验证（P1） ✓ stop.py 输出正常（记忆检查提醒222字符），同时修复了锁早退bug
  待跨 session 验证：Qdrant 基线 = 34，后续 session 对话后应 >39
- [x] Phase 5: 验证 .active_session 锁机制（P2） ✓ 锁文件存在且内容正确，PonyMemory 已不受锁影响
- [x] Phase 6: 清理 Neo4j 文档残留（P2） ✓ ARCHITECTURE.md/PRODUCT.md 已标 ~~已移除~~，无运维指令残留
- [x] Phase 7: 端到端冒烟测试（验证） ✓ SessionStart注入7247字符(L3+L4+HANDOFF+领域规则)，StopHook提醒正常

## Success Criteria — 达成情况
- [x] SessionStart 能成功注入 L3 Qdrant 记忆到 context（10条记忆，7247字符）
- [ ] 新 session 对话 10 轮后 Qdrant 记忆数显著增加（>5 条新增）← 需跨 session 验证
- [x] PonyWriterX 活跃时也有记忆注入（锁存在时仍注入6482字符）
- [x] 所有 Obsidian 项目目录有 decisions.md（6/6）
