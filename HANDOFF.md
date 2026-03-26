# HANDOFF — PonyMemory v2 Queue+Worker 架构

## 当前状态

**Branch**: `feat/v2-queue-worker`（已 push）
**Tests**: 71 tests 全部通过

## 已完成

### Phase 1: Core Foundation
- [x] `db.py` — SQLite 队列层（14 tests）
- [x] `hooks/stop.py` v3 — transcript 增量读取 → SQLite 队列（3 tests）
- [x] `extractor.py` — Haiku 事实提取 + 质量门控（4 tests）
- [x] `embedder.py` — BGE-M3 embedding + Qdrant 读写（11 tests）
- [x] `obsidian_writer.py` — Obsidian 直写文件（7 tests）
- [x] `worker.py` — Worker 主循环 + process_conversation（4 tests）
- [x] settings.json — 移除 ponymemory hooks 的 timeout 限制
- [x] anthropic SDK 安装

### Phase 2: Tool-Level Capture + Process Management
- [x] `hooks/post_tool_use.py` — PostToolUse Hook（file event capture，9 tests）
- [x] `router.py` — 文件分类路由引擎（13 tests）
- [x] launchd plist — `~/Library/LaunchAgents/com.ponymemory.worker.plist`
- [x] Degradation chain — fallback.jsonl
- [x] settings.json — PostToolUse hooks 已添加

### Phase 3: Read Enhancement
- [x] `hooks/session_start.py` — dynamic query + priority fix

### Phase 4: Smart Enhancement
- [x] Daily Consolidation — worker.py `maybe_run_maintenance`
- [ ] **Task 18: Zotero Integration** ← 唯一未完成项

### Phase 5: Observability
- [x] `health.py` — HTTP 健康检查端点（localhost:47777/health）

## 唯一剩余任务

### Task 18: Zotero Integration
- [ ] 安装 pyzotero：`.venv/bin/pip install pyzotero`
- [ ] 实现 PDF 检测（`~/files/papers/`）→ Pyzotero import → Qdrant 索引
- [ ] 在 router.py 中添加 Zotero 路由规则
- [ ] 写测试 + commit

## 关键文件

- Spec: `docs/superpowers/specs/2026-03-25-ponymemory-v2-design.md`
- Plan: `docs/superpowers/plans/2026-03-25-ponymemory-v2.md`（Task 18 详情在 §Phase 4）

## 重要提醒

- Worker 由 launchd 托管，开机自动启动；手动启停：`launchctl load/unload ~/Library/LaunchAgents/com.ponymemory.worker.plist`
- 健康检查：`curl localhost:47777/health`
- Stop Hook v3 已部署；Worker 未运行时队列会积累（无害，重启后自动处理）
