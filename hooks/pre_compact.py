#!/usr/bin/env python3
"""
PonyMemory PreCompact Hook
触发时机：Context 即将被压缩前
功能：强制保存当前工作状态到 Qdrant + Obsidian（紧急保存）
"""
import json
import os
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


def read_handoff():
    cwd = os.environ.get("CWD", os.getcwd())
    handoff = os.path.join(cwd, "HANDOFF.md")
    if os.path.isfile(handoff):
        with open(handoff) as f:
            return f.read()[:1000]
    return ""


def find_active_ponywriterx():
    """查找活跃的 PonyWriterX 项目 navigator.json 路径"""
    projects_dir = os.path.expanduser("~/.ponywriterx/projects/")
    if not os.path.isdir(projects_dir):
        return ""
    for f in sorted(os.listdir(projects_dir), reverse=True):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(projects_dir, f)) as fh:
                proj = json.load(fh)
                if proj.get("status") == "ACTIVE":
                    pid = proj.get("id", "")
                    nav = os.path.expanduser(f"~/.ponywriterx/outputs/{pid}/navigator.json")
                    if os.path.isfile(nav):
                        return f"Navigator 文件存在：{nav}（压缩后可从此文件恢复上下文）"
                    return f"活跃项目 {pid}，无 navigator.json（如已过 OUTLINE_REVIEW，应生成）"
        except Exception:
            pass
    return ""


def main():
    if is_ponywriterx_active():
        print(json.dumps({}))
        return

    project_name = get_project_name()
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    context_parts = [
        "# ⚠️ PonyMemory PreCompact — 压缩前紧急保存（硬规则）",
        "",
        "**Context 即将被压缩。你必须在压缩前完成以下操作，否则信息将永久丢失。**",
        "",
        "## 1. 保存当前任务状态到 Qdrant",
        "调用 `store_memory` 存储：",
        f"- text: 当前正在进行的任务描述、进度、未完成步骤",
        f"- memory_type: 'task_state'",
        f"- project: '{project_name}'",
        f"- tags: ['pre_compact', 'auto_save']",
        "",
        "## 2. 保存关键数据到 Qdrant",
        "如果对话中出现了以下内容且未存储：",
        "- 用户纠正 → store_memory(type=correction)",
        "- 技术决策 → store_memory(type=decision)",
        "- 关键数字/参数/路径 → 写入 text 字段",
        "",
        "## 3. 更新 Obsidian 项目状态",
        f"调用 Obsidian MCP str_replace 更新 01-Projects/{{project}}/_project.md 的当前状态",
        "",
        "## 4. 创建或更新 HANDOFF.md",
        "如果有未完成的多步骤任务，在当前项目目录写 HANDOFF.md",
        "",
    ]

    handoff = read_handoff()
    if handoff:
        context_parts.append(f"## 已有 HANDOFF.md\n```\n{handoff}\n```")

    pwx_nav = find_active_ponywriterx()
    if pwx_nav:
        context_parts.append(f"## PonyWriterX\n{pwx_nav}")

    output = {"additionalContext": "\n".join(context_parts)}
    print(json.dumps(output))


if __name__ == "__main__":
    main()
