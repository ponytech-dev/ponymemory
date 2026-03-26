# HANDOFF — PonyMemory v2 (Plan B: Claude Code Native)

## 当前状态

**Branch**: `feat/v2-queue-worker`
**Tests**: 75 tests 全部通过
**架构**: Plan B — 无独立 Worker 进程，无 API Key，无 launchd

## 架构说明

```
Stop Hook（每轮响应后，代码层）
  → 读 transcript 增量 → 写 SQLite 队列
  → 检测有意义内容 → decision:block 强制 Claude 执行 store_memory

SessionStart Hook（每次 session 启动，代码层）
  → 处理队列中未消费的项（BGE-M3 + Qdrant + Obsidian 直写）
  → 注入项目 context + 记忆 + 元索引

PostToolUse Hook（文件写入/MCP 下载后，代码层）
  → 捕获事件 → 写 SQLite 队列
```

## 已完成

- [x] SQLite 队列层（db.py）
- [x] Stop Hook v4（transcript → queue + decision:block）
- [x] SessionStart 队列处理 + 动态查询 + 元索引
- [x] PostToolUse Hook（文件/MCP 事件捕获）
- [x] BGE-M3 embedder + Qdrant writer
- [x] Obsidian 直写（无 MCP 依赖）
- [x] 文件路由引擎
- [x] 降级链（fallback.jsonl）
- [x] 巩固机制（corrections → rules）
- [x] Health endpoint（:47777）

## 剩余

- [ ] Zotero 集成（PDF 管理）
- [ ] 实际使用验证（新 session 测试 decision:block 触发）
