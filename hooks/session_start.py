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
import uuid
import time
import urllib.request
import urllib.error

# Allow importing from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import init_db, claim_next_item, delete_queue_item, mark_failed, reset_stuck_records

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8999")
MEMORY_COLLECTION = "session_memories"
MAX_CONTEXT_CHARS = 8000


VAULT = os.path.expanduser("~/pony/obsidian-vault/")

TYPE_TO_FILE = {
    "correction": "decisions.md",
    "decision": "decisions.md",
    "preference": "decisions.md",
    "finding": "findings.md",
    "milestone": "_project.md",
}


def process_pending_queue():
    """Process pending queue items at session start (code-level, no LLM needed).

    Reads pending conversation lines from SQLite queue, embeds with BGE-M3,
    stores in Qdrant, writes to Obsidian. Processes up to 20 items to avoid
    blocking session start too long.
    """
    try:
        conn = init_db()
    except Exception as e:
        print(f"[PonyMemory] queue DB init failed: {e}", file=sys.stderr)
        return 0

    processed = 0
    max_items = 20  # Don't process too many at session start

    reset_stuck_records(conn, threshold_seconds=120)

    for _ in range(max_items):
        item = claim_next_item(conn)
        if item is None:
            break

        item_dict = dict(item)
        try:
            if item_dict["type"] in ("conversation_line", "conversation"):
                _process_conversation_item(item_dict)
            # tool_event and mcp_download will be handled by Claude via decision:block

            delete_queue_item(conn, item_dict["id"])
            processed += 1
        except Exception as e:
            print(f"[PonyMemory] queue item {item_dict.get('id')} failed: {e}", file=sys.stderr)
            mark_failed(conn, item_dict["id"], str(e))

    conn.close()
    if processed > 0:
        print(f"[PonyMemory] Processed {processed} pending queue items", file=sys.stderr)
    return processed


def _process_conversation_item(item):
    """Embed and store a conversation line in Qdrant + Obsidian."""
    payload = item.get("payload", "{}")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return

    # Extract text from conversation line(s)
    text_parts = []
    entries = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        msg = entry.get("message", {})
        content_blocks = msg.get("content", [])
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif isinstance(block, str):
                text_parts.append(block)

    text = " ".join(text_parts).strip()
    if len(text) < 50:
        return  # Too short to be meaningful

    # Embed with BGE-M3
    vector = embed_text(text[:2000])  # Truncate for embedding
    if vector is None:
        return  # BGE-M3 unavailable, skip silently

    # Store in Qdrant
    project = item.get("project", "pony")
    point_id = str(uuid.uuid4())
    qdrant_payload = {
        "text": text[:500],  # Store preview
        "memory_type": "conversation",
        "project": project,
        "tags": ["auto-captured"],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    body = json.dumps({
        "points": [{"id": point_id, "vector": vector, "payload": qdrant_payload}]
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{MEMORY_COLLECTION}/points",
            data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[PonyMemory] Qdrant write failed: {e}", file=sys.stderr)
        return

    # Write to Obsidian (append to decisions.md as conversation note)
    obsidian_dir = os.path.join(VAULT, f"01-Projects/{project}")
    os.makedirs(obsidian_dir, exist_ok=True)
    decisions_path = os.path.join(obsidian_dir, "decisions.md")
    timestamp = time.strftime("%Y-%m-%d %H:%M")
    entry = f"\n## {timestamp} [conversation]\n\n{text[:300]}\n"
    try:
        with open(decisions_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"[PonyMemory] Obsidian write failed: {e}", file=sys.stderr)


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
        payload = json.dumps({"texts": [text]}).encode("utf-8")
        req = urllib.request.Request(
            f"{EMBED_URL}/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            embeddings = result.get("embeddings", [])
            return embeddings[0] if embeddings else None
    except Exception as e:
        print(f"[PonyMemory] embed_text failed: {e}", file=sys.stderr)
        return None


def build_query(project_name):
    """从 HANDOFF.md 和 task_plan.md 动态构建查询关键词"""
    keywords = [project_name]
    cwd = os.environ.get("CWD", os.getcwd())

    # Extract from HANDOFF.md first line
    handoff = os.path.join(cwd, "HANDOFF.md")
    if os.path.isfile(handoff):
        with open(handoff, encoding="utf-8") as f:
            content = f.read()[:500]
            first_line = content.split("\n")[0].strip("# ").strip()
            if first_line:
                keywords.append(first_line)

    # Extract from task_plan.md Objective
    task_plan = os.path.join(cwd, "task_plan.md")
    if os.path.isfile(task_plan):
        with open(task_plan, encoding="utf-8") as f:
            for line in f:
                if line.startswith("## Objective"):
                    obj = next(f, "").strip()
                    if obj:
                        keywords.append(obj)
                    break

    return " ".join(keywords)


def get_meta_index():
    """获取可用记忆工具的元索引，尝试从 Qdrant 获取条目数"""
    mem_count = "?"
    paper_count = "?"
    note_count = "?"
    try:
        for collection, var_name in [("session_memories", "mem"), ("papers", "paper"), ("notes", "note")]:
            req = urllib.request.Request(f"{QDRANT_URL}/collections/{collection}")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
                count = data.get("result", {}).get("points_count", "?")
                if var_name == "mem":
                    mem_count = count
                elif var_name == "paper":
                    paper_count = count
                elif var_name == "note":
                    note_count = count
    except Exception:
        pass

    return f"""## 可用记忆工具
- search_memories(query): 过去的工作决策、用户反馈、技术纠正（{mem_count}条）
  触发场景：用户提到"之前/记得/上次"、设计决策需要一致性、不确定是否被纠正过
- search_papers(query): 代谢组学/质谱/生信论文（{paper_count} chunks）
  触发场景：需要引用文献、讨论技术方法
- search_notes(query): 日常笔记、会议记录（{note_count} chunks）
- zotero_search_items(query): Zotero 文献库搜索（需要 Zotero 运行）
  触发场景：查找已管理的参考文献、获取论文元数据或全文
"""


def search_qdrant_memories(project_name):
    """直接通过 Qdrant HTTP API 搜索相关记忆"""
    query_text = build_query(project_name)
    vector = embed_text(query_text)
    if not vector:
        return []

    try:
        payload = json.dumps({
            "vector": vector,
            "limit": 10,
            "with_payload": True,
            "filter": None,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{MEMORY_COLLECTION}/points/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            points = result.get("result", [])

        memories = []
        for p in points:
            pl = p.get("payload", {})
            score = p.get("score", 0)
            if score < 0.3:
                continue
            text = pl.get("text", "")
            mtype = pl.get("memory_type", "unknown")
            project = pl.get("project", "")
            ts = str(pl.get("timestamp", ""))[:10]
            memories.append(f"- [{mtype}] {text} (project={project}, date={ts}, score={score:.2f})")

        return memories
    except Exception as e:
        print(f"[PonyMemory] Qdrant search failed: {e}", file=sys.stderr)
        return []


def read_obsidian_project(project_name):
    """读取 Obsidian 项目状态文件"""
    vault = os.path.expanduser("~/pony/obsidian-vault/")
    context_parts = []

    candidates = [project_name, project_name.capitalize()]
    for name in candidates:
        project_file = os.path.join(vault, f"01-Projects/{name}/_project.md")
        if os.path.isfile(project_file):
            try:
                with open(project_file, encoding="utf-8") as f:
                    context_parts.append(f"## 项目状态（{name}）\n{f.read()[:1000]}")
            except Exception as e:
                print(f"[PonyMemory] read _project.md failed: {e}", file=sys.stderr)
            # decisions
            decisions_file = os.path.join(vault, f"01-Projects/{name}/decisions.md")
            if os.path.isfile(decisions_file):
                try:
                    with open(decisions_file, encoding="utf-8") as f:
                        content = f.read()
                        if len(content) > 200:
                            context_parts.append(f"## 最近决策（{name}）\n{content[-800:]}")
                except Exception as e:
                    print(f"[PonyMemory] read decisions.md failed: {e}", file=sys.stderr)
            break

    return "\n\n".join(context_parts)


def read_handoff():
    """读取 HANDOFF.md"""
    cwd = os.environ.get("CWD", os.getcwd())
    handoff = os.path.join(cwd, "HANDOFF.md")
    if os.path.isfile(handoff):
        try:
            with open(handoff, encoding="utf-8") as f:
                return f"## HANDOFF（进行中任务）\n{f.read()[:1000]}"
        except Exception as e:
            print(f"[PonyMemory] read HANDOFF.md failed: {e}", file=sys.stderr)
    return ""


def read_pending_rules():
    """读取待确认的规则"""
    pending = os.path.expanduser("~/pony/ponymemory/pending_rules.md")
    if os.path.isfile(pending):
        try:
            with open(pending, encoding="utf-8") as f:
                content = f.read().strip()[:500]
                if content:
                    return f"## 待确认规则\n{content}"
        except Exception as e:
            print(f"[PonyMemory] read pending_rules.md failed: {e}", file=sys.stderr)
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
            try:
                with open(rules_file, encoding="utf-8") as f:
                    content = f.read()
                    if len(content) > 50:
                        return f"## 领域经验规则（{domain}）\n{content[:800]}"
            except Exception as e:
                print(f"[PonyMemory] read domain rules failed: {e}", file=sys.stderr)
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
            with open(os.path.join(projects_dir, f), encoding="utf-8") as fh:
                proj = json.load(fh)
                if proj.get("status") == "ACTIVE":
                    pid = proj.get("id", f)
                    title = proj.get("title", "Unknown")
                    path = proj.get("path", "?")
                    mode = proj.get("collaboration_mode", "standard")
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
        except Exception as e:
            print(f"[PonyMemory] read PonyWriterX project {f} failed: {e}", file=sys.stderr)
    return ""


def main():
    # Process any pending queue items from previous sessions
    queue_processed = process_pending_queue()

    project_name = get_project_name()
    context_sections = []

    # 1. HANDOFF（最高优先级——当前进行中的任务）
    handoff = read_handoff()
    if handoff:
        context_sections.append(handoff)

    # 2. PonyWriterX 活跃项目
    pwx = read_active_ponywriterx_project()
    if pwx:
        context_sections.append(pwx)

    # 3. 待确认规则
    pending = read_pending_rules()
    if pending:
        context_sections.append(pending)

    # 4. 领域经验规则
    domain_rules = read_domain_rules(project_name)
    if domain_rules:
        context_sections.append(domain_rules)

    # 5. Obsidian 项目状态
    obsidian_context = read_obsidian_project(project_name)
    if obsidian_context:
        context_sections.append(f"# L4 Obsidian 项目记忆\n{obsidian_context}")

    # 6. Qdrant 记忆搜索（最低优先级——可截断，直接 HTTP，不依赖 MCP）
    memories = search_qdrant_memories(project_name)
    if memories:
        context_sections.append(
            f"# L3 Qdrant 记忆（{len(memories)} 条相关）\n" + "\n".join(memories)
        )

    additional_context = "\n\n---\n\n".join(context_sections) if context_sections else ""

    if additional_context:
        # 大小保护：截断到 MAX_CONTEXT_CHARS
        if len(additional_context) > MAX_CONTEXT_CHARS:
            additional_context = additional_context[:MAX_CONTEXT_CHARS] + "\n\n[... 截断，超出 8000 字符上限]"
        additional_context = (
            f"# PonyMemory 自动注入（项目：{project_name}）\n\n"
            + additional_context
            + "\n\n---\n"
            "提醒：如有 pending_rules，请在首次回复中呈现给用户确认。"
        )

    # 追加元索引（不受 MAX_CONTEXT_CHARS 截断影响，始终存在）
    meta_index = get_meta_index()
    if additional_context:
        additional_context = additional_context + "\n\n" + meta_index
    else:
        additional_context = meta_index

    output = {}
    if additional_context:
        output["additionalContext"] = additional_context

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[PonyMemory] session_start fatal: {e}", file=sys.stderr)
        print(json.dumps({}))
