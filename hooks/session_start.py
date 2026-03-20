#!/usr/bin/env python3
"""
PonyMemory SessionStart Hook
触发时机：每次 session 启动
功能：
  1. 直接查询 Qdrant 搜索项目相关记忆（HTTP REST API，不依赖 MCP）
  2. 读取 Obsidian 项目状态 + decisions
  3. 读取 HANDOFF / 待确认规则 / 领域经验
  4. 全部注入 additionalContext
"""
import json
import os
import sys
import subprocess
import urllib.request
import urllib.error


QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8999")
PONYWRITERX_LOCK = os.path.expanduser("~/.ponywriterx/.active_session")


def is_ponywriterx_active():
    """PonyWriterX Skill 运行中时，PonyMemory hooks 让位"""
    return os.path.isfile(PONYWRITERX_LOCK)
MEMORY_COLLECTION = "session_memories"


def get_project_name():
    """从当前工作目录推断项目名"""
    cwd = os.environ.get("CWD", os.getcwd())
    pony_dir = os.path.expanduser("~/pony/")
    if cwd.startswith(pony_dir):
        relative = cwd[len(pony_dir):]
        parts = relative.strip("/").split("/")
        if parts and parts[0]:
            return parts[0]
    return "pony"


def embed_text(text):
    """通过本地 BGE-M3 服务获取向量"""
    try:
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{EMBED_URL}/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("embedding") or result.get("vector")
    except Exception:
        return None


def search_qdrant_memories(project_name):
    """直接通过 Qdrant HTTP API 搜索相关记忆"""
    query_text = f"{project_name} recent work decisions corrections"
    vector = embed_text(query_text)
    if not vector:
        return []

    try:
        # 搜索 session_memories 集合
        payload = json.dumps({
            "vector": vector,
            "limit": 10,
            "with_payload": True,
            "filter": None,  # 不限制项目，拿到全局记忆
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{MEMORY_COLLECTION}/points/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            points = result.get("result", [])

        memories = []
        for p in points:
            payload = p.get("payload", {})
            score = p.get("score", 0)
            if score < 0.3:
                continue
            text = payload.get("text", "")
            mtype = payload.get("memory_type", "unknown")
            project = payload.get("project", "")
            ts = payload.get("timestamp", "")[:10]
            memories.append(f"- [{mtype}] {text} (project={project}, date={ts}, score={score:.2f})")

        return memories
    except Exception:
        return []


def read_obsidian_project(project_name):
    """读取 Obsidian 项目状态文件"""
    vault = os.path.expanduser("~/pony/obsidian-vault/")
    context_parts = []

    # 尝试多种项目名格式
    candidates = [project_name, project_name.capitalize(), "PonyWriterX", "PonylabASMS", "SpaFlow"]
    for name in candidates:
        project_file = os.path.join(vault, f"01-Projects/{name}/_project.md")
        if os.path.isfile(project_file):
            with open(project_file) as f:
                context_parts.append(f"## 项目状态（{name}）\n{f.read()[:1000]}")
            # decisions
            decisions_file = os.path.join(vault, f"01-Projects/{name}/decisions.md")
            if os.path.isfile(decisions_file):
                with open(decisions_file) as f:
                    content = f.read()
                    if len(content) > 200:
                        context_parts.append(f"## 最近决策（{name}）\n{content[-800:]}")
            break

    return "\n\n".join(context_parts)


def read_handoff():
    """读取 HANDOFF.md"""
    cwd = os.environ.get("CWD", os.getcwd())
    handoff = os.path.join(cwd, "HANDOFF.md")
    if os.path.isfile(handoff):
        with open(handoff) as f:
            return f"## HANDOFF（进行中任务）\n{f.read()[:1000]}"
    return ""


def read_pending_rules():
    """读取待确认的规则"""
    pending = os.path.expanduser("~/pony/ponymemory/pending_rules.md")
    if os.path.isfile(pending):
        with open(pending) as f:
            content = f.read().strip()
            if content:
                return f"## 待确认规则\n{content}"
    return ""


def read_domain_rules(project_name):
    """读取领域经验规则"""
    vault = os.path.expanduser("~/pony/obsidian-vault/03-Knowledge/")
    domain_map = {
        "ponylabASMS": "mass-spectrometry",
        "ponylab": "mass-spectrometry",
        "spaflow": "pharma",
        "ponywriterX": "writing",
        "ponymemory": "ai",
    }
    domain = domain_map.get(project_name, "")
    if domain:
        rules_file = os.path.join(vault, domain, "learned_rules.md")
        if os.path.isfile(rules_file):
            with open(rules_file) as f:
                content = f.read()
                if len(content) > 50:
                    return f"## 领域经验规则（{domain}）\n{content[:800]}"
    return ""


def read_active_ponywriterx_project():
    """读取 PonyWriterX 活跃项目状态"""
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
                    pid = proj.get("id", f)
                    title = proj.get("title", "Unknown")
                    path = proj.get("path", "?")
                    mode = proj.get("collaboration_mode", "standard")
                    # Find current stage
                    current_stage = "INTAKE"
                    for s in proj.get("stages", []):
                        if s.get("status") == "APPROVED":
                            current_stage = s.get("stage_name", current_stage)
                    return (
                        f"## PonyWriterX 活跃项目\n"
                        f"- ID: {pid}\n"
                        f"- 标题: {title}\n"
                        f"- 路径: {path}\n"
                        f"- 模式: {mode}\n"
                        f"- 最后完成阶段: {current_stage}\n"
                    )
        except Exception:
            pass
    return ""


def main():
    # PonyWriterX Skill 运行中 → 让 PonyWriterX 自己的 hooks 接管
    if is_ponywriterx_active():
        print(json.dumps({}))
        return

    project_name = get_project_name()
    context_sections = []

    # 1. Qdrant 记忆搜索（直接 HTTP，不依赖 MCP）
    memories = search_qdrant_memories(project_name)
    if memories:
        context_sections.append(
            f"# L3 Qdrant 记忆（{len(memories)} 条相关）\n" + "\n".join(memories)
        )

    # 2. Obsidian 项目状态
    obsidian_context = read_obsidian_project(project_name)
    if obsidian_context:
        context_sections.append(f"# L4 Obsidian 项目记忆\n{obsidian_context}")

    # 3. HANDOFF
    handoff = read_handoff()
    if handoff:
        context_sections.append(handoff)

    # 4. 待确认规则
    pending = read_pending_rules()
    if pending:
        context_sections.append(pending)

    # 5. 领域经验规则
    domain_rules = read_domain_rules(project_name)
    if domain_rules:
        context_sections.append(domain_rules)

    # 6. PonyWriterX 活跃项目
    pwx = read_active_ponywriterx_project()
    if pwx:
        context_sections.append(pwx)

    additional_context = "\n\n---\n\n".join(context_sections) if context_sections else ""

    if additional_context:
        additional_context = (
            f"# PonyMemory 自动注入（项目：{project_name}）\n\n"
            + additional_context
            + "\n\n---\n"
            "提醒：如有 pending_rules，请在首次回复中呈现给用户确认。"
        )

    output = {}
    if additional_context:
        output["additionalContext"] = additional_context

    print(json.dumps(output))


if __name__ == "__main__":
    main()
