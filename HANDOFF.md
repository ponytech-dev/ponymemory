# HANDOFF — PonyMemory v2 Queue+Worker 架构

## 当前状态

**Branch**: `feat/v2-queue-worker`（已 push）
**Phase 1 完成**：43 tests 全部通过，8 commits

## 已完成

### Phase 1: Core Foundation (Task 1-9)
- [x] `db.py` — SQLite 队列层（14 tests）
- [x] `hooks/stop.py` v3 — transcript 增量读取 → SQLite 队列（3 tests）
- [x] `extractor.py` — Haiku 事实提取 + 质量门控（4 tests）
- [x] `embedder.py` — BGE-M3 embedding + Qdrant 读写（11 tests）
- [x] `obsidian_writer.py` — Obsidian 直写文件（7 tests）
- [x] `worker.py` — Worker 主循环 + process_conversation（4 tests）
- [x] settings.json — 移除 ponymemory hooks 的 timeout 限制
- [x] anthropic SDK 安装

## 下一步

### Phase 2: Tool-Level Capture + Process Management (Task 10-15)
- [ ] Task 10: PostToolUse Hook (`hooks/post_tool_use.py`)
- [ ] Task 11: File Router (`router.py`)
- [ ] Task 12: launchd plist 配置
- [ ] Task 13: Degradation chain (fallback.jsonl)
- [ ] Task 14: settings.json 添加 PostToolUse hooks
- [ ] Task 15: Phase 2 验证 + push

### Phase 3-5 见 Plan 文档

## 关键文件

- Spec: `docs/superpowers/specs/2026-03-25-ponymemory-v2-design.md`
- Plan: `docs/superpowers/plans/2026-03-25-ponymemory-v2.md`
- 新模块: db.py, worker.py, extractor.py, embedder.py, obsidian_writer.py

## 重要提醒

- Worker 还未启动为常驻进程（Phase 2 Task 12）
- Stop Hook v3 已部署但 Worker 未运行时，队列会累积（无害）
- 执行方式: subagent-driven-development，每 task 一个 subagent
