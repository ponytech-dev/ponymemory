# PonyMemory — 全自动记忆系统

## 项目定位
PonyMemory 是 Claude Code 的全自动记忆系统，管理 5 层记忆的自动化读写和维护。
Claude Code 作为操作者直接维护 Qdrant，用户零维护。

## 核心文件
- `ARCHITECTURE.md` — 完整架构设计（五层记忆、自动触发规则、维护机制）
- `hooks/` — Hook 脚本（SessionStart/Stop/PreCompact）
- `scripts/mem0-mcp-server.py` — 已废弃（保留参考）
- `~/pony/scripts/qdrant-mcp-server.py` — 统一 MCP 服务器（Qdrant）

## 实施规则
1. 先读 ARCHITECTURE.md 理解全局设计
2. 任何修改需保持五层分工不重叠
3. Hook 脚本修改后必须测试（`echo '{}' | python hook.py`）
4. L3（Qdrant 记忆）和 L4（Obsidian）存储不同粒度：L3 存摘要（50-200字，含 what+why+impact，附 source_path），L4 存完整文档
5. store_memory 前必须 search_memories 检查重复
6. L4 是权威来源，L3 是检索索引；冲突时 L4 优先；决策/里程碑/纠正必须双写 L3+L4
