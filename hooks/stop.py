#!/usr/bin/env python3
"""
PonyMemory Stop Hook (增量模式)
触发时机：每次 Claude 完成一轮响应
功能：
  1. 强制要求存储记忆（不是建议，是硬规则）
  2. 注入规则提取提醒
  3. 条件 git push 检查
  4. 周期性记忆维护
"""
import json
import os
import sys
from datetime import datetime


PONYWRITERX_LOCK = os.path.expanduser("~/.ponywriterx/.active_session")


def is_ponywriterx_active():
    return os.path.isfile(PONYWRITERX_LOCK)


def get_project_name():
    cwd = os.environ.get("CWD", os.getcwd())
    pony_dir = os.path.expanduser("~/pony/")
    if cwd.startswith(pony_dir):
        relative = cwd[len(pony_dir):]
        parts = relative.strip("/").split("/")
        if parts and parts[0]:
            return parts[0]
    return "pony"


def check_unpushed_commits():
    """检查未 push 的 commit 数量"""
    cwd = os.environ.get("CWD", os.getcwd())
    try:
        import subprocess
        result = subprocess.run(
            ["git", "log", "--oneline", "@{u}..HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        if result.returncode == 0:
            lines = [l for l in result.stdout.strip().split("\n") if l]
            return len(lines)
    except Exception:
        pass
    return 0


def main():
    if is_ponywriterx_active():
        print(json.dumps({}))
        return

    project_name = get_project_name()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M")

    reminders = []

    # 1. 强制记忆存储（不是建议，是硬规则）
    reminders.append(
        "## ⚠️ PonyMemory 强制记忆检查（硬规则，不可跳过）\n\n"
        "**你必须在回复用户之前完成以下检查。这不是建议，是必须执行的操作。**\n\n"
        "扫描本轮对话，如果发生了以下任何一种情况，**立即调用 MCP 工具存储**：\n\n"
        "| 事件 | 操作 | memory_type |\n"
        "|------|------|-------------|\n"
        "| 用户纠正了你的判断 | `store_memory` + Obsidian `decisions.md` | correction |\n"
        "| 做了技术决策 | `store_memory` + Obsidian `decisions.md` | decision |\n"
        "| 发现了 bug/问题 | `store_memory` | finding |\n"
        "| 里程碑/阶段完成 | `store_memory` + Obsidian `_project.md` | milestone |\n"
        "| 用户表达了偏好/习惯 | `store_memory` | preference |\n"
        "| 学到了领域知识 | `store_memory` | knowledge |\n\n"
        "**存储前**：先 `search_memories(query=...)` 检查重复。有相似记忆则 `update_memory`。\n\n"
        f"**必填字段**：`project: \"{project_name}\"`, `tags: [相关标签]`, `entities: [{{name, type}}]`\n\n"
        "**如果本轮没有以上任何事件，跳过即可。但必须明确判断，不能默认跳过。**"
    )

    # 2. 规则提取提醒
    reminders.append(
        "## 自动规则提取\n"
        "扫描本轮对话，检测是否有可提取的规则：\n"
        "- 用户纠正行为（'不要...'、'应该...'、'别...'）→ Claude memory feedback 文件\n"
        "- 通用规则 → 全局 CLAUDE.md\n"
        "- 项目特定规则 → 项目 CLAUDE.md\n"
        "如果检测到，以选择题呈现给用户确认。"
    )

    # 3. 条件 Git Push 检查
    unpushed = check_unpushed_commits()
    if unpushed >= 3:
        reminders.append(
            f"## Git Push 提醒\n"
            f"检测到 {unpushed} 个未 push 的 commit。\n"
            "请检查是否满足 push 条件后执行 git push。"
        )

    # 4. 记忆维护触发（每 10 轮响应触发一次）
    response_count_file = os.path.expanduser("~/.claude/.ponymemory_response_count")
    try:
        count = int(open(response_count_file).read().strip()) if os.path.isfile(response_count_file) else 0
    except Exception:
        count = 0
    count += 1
    try:
        os.makedirs(os.path.dirname(response_count_file), exist_ok=True)
        with open(response_count_file, "w") as f:
            f.write(str(count))
    except Exception:
        pass

    if count % 10 == 0:
        reminders.append(
            "## 记忆维护（自动触发，每 10 轮一次）\n"
            f"当前响应计数：{count}。执行轻量维护：\n"
            "1. `list_all_memories(limit=50)` 扫描所有记忆\n"
            "2. 语义相似的记忆 → `update_memory` 合并\n"
            "3. 同一主题结论冲突 → 保留最新的，`delete_memory` 旧的\n"
            "4. timestamp > 30 天且 type=session_summary → 考虑删除\n"
            "如果记忆数量 < 10 条可跳过维护。"
        )

    output = {}
    if reminders:
        output["additionalContext"] = "\n\n---\n\n".join(reminders)

    print(json.dumps(output))


if __name__ == "__main__":
    main()
