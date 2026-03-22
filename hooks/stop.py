#!/usr/bin/env python3
"""
PonyMemory Stop Hook (v2 — 降噪 + 强制)
触发时机：每次 Claude 完成一轮响应
核心改进：区分"常规提醒"和"强制执行"，减少噪音提高执行率
"""
import fcntl
import json
import os
import subprocess
import sys


COUNTER_FILE = os.path.expanduser("~/.claude/.ponymemory_response_count")


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
    cwd = os.environ.get("CWD", os.getcwd())
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "@{u}..HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=3,
        )
        if result.returncode == 0:
            lines = [l for l in result.stdout.strip().split("\n") if l]
            return len(lines)
    except Exception:
        pass
    return 0


def get_response_count():
    """原子读-改-写计数器，使用 fcntl 文件锁防止竞态"""
    try:
        os.makedirs(os.path.dirname(COUNTER_FILE), exist_ok=True)
        # 创建文件如不存在
        if not os.path.isfile(COUNTER_FILE):
            with open(COUNTER_FILE, "w", encoding="utf-8") as f:
                f.write("0")
        with open(COUNTER_FILE, "r+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                count = int(f.read().strip() or "0")
            except ValueError:
                count = 0
            count += 1
            f.seek(0)
            f.write(str(count))
            f.truncate()
        return count
    except Exception as e:
        print(f"[PonyMemory] counter failed: {e}", file=sys.stderr)
        return 1


def main():
    project_name = get_project_name()
    count = get_response_count()

    sections = []

    # 核心：记忆存储指令（每次都注入）
    sections.append(
        "**记忆检查**（每次响应后必须执行）：\n"
        "本轮对话是否发生了：用户纠正 / 技术决策 / 发现问题 / 里程碑 / 用户偏好 / 领域知识？\n"
        f"→ 是：`search_memories` 查重 → `store_memory(project=\"{project_name}\")` 或 `update_memory`\n"
        "→ 否：跳过\n"
        "存储格式：50-200字，含 what + why + impact。纠正/决策同步写 Obsidian decisions.md。"
    )

    # 条件触发：Git Push
    unpushed = check_unpushed_commits()
    if unpushed >= 3:
        sections.append(
            f"**Git Push**：{unpushed} 个未 push commit，请执行 push。"
        )

    # 周期触发：记忆维护（每 10 轮）
    if count % 10 == 0:
        sections.append(
            f"**记忆维护**（第 {count} 轮，自动触发）：\n"
            "`list_all_memories(limit=50)` → 合并重复 → 删除矛盾旧条目 → 清理 >30天 session_summary"
        )

    # 周期触发：规则提取（每 5 轮）
    if count % 5 == 0:
        sections.append(
            "**规则提取**：扫描最近对话，是否有'不要/应该/别/禁止'模式？有则以选择题确认后写入对应层。"
        )

    output = {}
    if sections:
        output["additionalContext"] = "\n".join(sections)

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[PonyMemory] stop fatal: {e}", file=sys.stderr)
        print(json.dumps({}))
