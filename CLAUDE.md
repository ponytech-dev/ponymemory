# PonyMemory — 全自动记忆系统

## 项目定位
PonyMemory 是 Claude Code 的全自动记忆系统，管理 5 层记忆的自动化读写和维护。
v2 架构：Hook 写入 SQLite 队列，Worker 进程（launchd 托管）异步处理记忆写入，用户零维护。

## 核心文件
- `ARCHITECTURE.md` — 完整架构设计（五层记忆、v2 queue+worker 模型）
- `db.py` — SQLite 队列层（事件入队/出队/状态管理）
- `worker.py` — Worker 主循环（轮询队列 → 提取 → 向量化 → 写入）
- `extractor.py` — Haiku 事实提取 + 质量门控
- `embedder.py` — BGE-M3 embedding + Qdrant HTTP 读写
- `obsidian_writer.py` — 直接写文件到 Obsidian vault（无 MCP 依赖）
- `router.py` — 文件分类路由引擎（决定写入哪个 Qdrant collection）
- `health.py` — HTTP 健康检查端点（:47777/health）
- `hooks/stop.py` v3 — transcript 增量读取 → SQLite 队列
- `hooks/post_tool_use.py` — 捕获文件写入/MCP 下载事件 → SQLite 队列
- `hooks/session_start.py` — Session 启动注入（Qdrant + Obsidian 状态）
- `hooks/pre_compact.py` — 压缩前紧急保存

## 实施规则
1. 先读 ARCHITECTURE.md 理解全局设计
2. 任何修改需保持五层分工不重叠
3. Hook 脚本修改后必须测试（`echo '{}' | python hook.py`）
4. L3（Qdrant 记忆）和 L4（Obsidian）存储不同粒度：L3 存摘要（50-200字，含 what+why+impact，附 source_path），L4 存完整文档
5. store_memory 前必须 search_memories 检查重复
6. L4 是权威来源，L3 是检索索引；冲突时 L4 优先；决策/里程碑/纠正必须双写 L3+L4
7. Worker 由 launchd 托管（`~/Library/LaunchAgents/com.ponymemory.worker.plist`），不手动启动
