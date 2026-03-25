# PonyMemory v2 — 全代码保证记忆系统设计

Created: 2026-03-25

## 1. 问题陈述

PonyMemory v1 的根本缺陷：所有记忆写入依赖 Stop Hook 输出 `additionalContext` 文字提醒 Claude 去执行 MCP 工具调用。

压力测试结论：
- `additionalContext` 被注入为 `system-reminder` 块（低权重位置），不是强制执行机制
- prompt-based 指令合规率 70-90%，context 压力下更低
- Anthropic 标记"context 中有规则但被跳过"为已知问题类别（GitHub #26432）
- 多个 Stop Hook 同时注入增加噪声，实际执行率可能更低

**结论：additionalContext 文字提醒作为记忆触发器不可行。v2 必须在代码层面保证所有记忆操作的执行。**

## 2. 设计原则

1. **代码保证优于文字提醒**：所有记忆写入由 Python 代码直接执行，不依赖 Claude 的"自觉性"
2. **异步不阻塞**：Hook 只负责快速入队（<50ms），Worker 异步处理全链路
3. **单存多索引**：文件只有一份物理存储，Qdrant 和 Obsidian 通过 source_path 指回原始文件
4. **降级不丢数据**：任何服务不可用时，数据暂存本地队列，服务恢复后自动补写
5. **长期在线**：Worker 进程由 launchd 管理，开机自启、崩溃自重启
6. **Obsidian 不依赖 MCP**：直接写文件到 vault 目录，Obsidian 是否在线无影响

## 3. 架构总览

```
Claude Code Session                      PonyMemory Worker（launchd 常驻）
    │                                        │
    ├─ PostToolUse Hook (<20ms)              │
    │   Write/MCP 工具 → SQLite queue        │
    │                                        │
    ├─ Stop Hook (<50ms)                     ├── 队列轮询（1s）
    │   transcript 增量 → SQLite queue       ├── Haiku 事实提取（5-30s/条）
    │                                        ├── 质量门控（HARD-GATE）
    ├─ SessionStart Hook (~200ms)            ├── BGE-M3 embedding
    │   动态查询 Qdrant + 读本地文件          ├── 去重检查（Qdrant search）
    │   → 元索引 + context 注入              ├── Qdrant 写入（L3 摘要索引）
    │                                        ├── Obsidian 直写文件（L4 全文）
    │                                        ├── /health 端点（port 47777）
    │                                        ├── 每小时去重/衰减维护
    │                                        └── 每日巩固（经历→规则转化）
    │
存储层
    ├─ SQLite ~/.claude/.ponymemory.db（队列+审计+fallback）
    ├─ Qdrant localhost:6333（L3 经历记忆 + L4 知识库索引）
    ├─ Obsidian vault ~/pony/obsidian-vault/（直写，不经 MCP）
    └─ ~/files/（L5 原始材料，路由引擎管理）
```

## 4. 功能层级定义（5 层）

| 层 | 功能 | 载体 | 写入方式 | 读取方式 | 生命周期 |
|---|---|---|---|---|---|
| L1 规则+画像 | 行为约束、用户偏好、反馈 | CLAUDE.md（git）+ memory/*.md | 人工 + 巩固机制自动候选（pending_rules.md） | 每次自动全量加载 | 永久，版本化 |
| L2 工作记忆 | 当前任务状态 | HANDOFF.md, task_plan.md, findings.md, progress.md | 任务中写入 | Session 启动读取，compact 前巩固 | 巩固后转化为 L3 |
| L3 经历记忆 | 决策、纠正、里程碑、迭代报告 | Qdrant `session_memories` + Obsidian `decisions.md`/`iterative-reports/` | Worker 自动（Stop Hook → 队列 → Worker） | 元索引引导 + 信号词触发 + 启动注入近期条目 | 永久，有衰减 |
| L4 知识库 | 论文 chunks、文档 chunks、笔记 | Qdrant `papers`/`documents`/`notes` + Zotero（论文元数据） | Worker 自动（PostToolUse → 队列 → Worker）+ 路由引擎 | 元索引引导 + 信号词触发 | 永久，弱衰减 |
| L5 原始材料 | PDF/Word/数据文件/AI 产出物 | ~/files/（分子目录）+ Zotero 本地存储 | 路由引擎存文件 → 触发 L4 索引 | 通过 L3/L4 的 source_path 间接访问 | 永久，不可变 |

### 层间关系

- L2 → L3：工作记忆通过巩固机制转化为经历记忆（Stop Hook 每轮提取有价值的决策/发现）
- L3 → L1：反复出现的经历（被纠正 3 次同类错误）自动提炼为通用规则候选，写入 pending_rules.md
- L5 → L4：原始文件通过路由引擎分 chunk 向量化，索引到 Qdrant 知识库
- L3/L4 → L5：所有 Qdrant 条目的 source_path 字段指回原始文件位置

### 权威性规则

- L5（原始文件）> L4（人类编辑的 Obsidian）> L3（AI 生成的 Qdrant 索引）
- 冲突时以更权威的层为准

## 5. 组件详细设计

### 5.1 Stop Hook（stop.py v3）

**职责**：读取 transcript 增量内容，写入 SQLite 队列。不做任何 AI 调用。

**输入**：stdin JSON，含 `transcript_path`、`session_id`、`stop_hook_active`

**输出**：`{}` 空 JSON（不输出 additionalContext，不 block）

**关键逻辑**：

```python
def main():
    data = json.loads(sys.stdin.read())

    # 防无限循环
    if data.get("stop_hook_active"):
        print(json.dumps({}))
        return

    transcript_path = data.get("transcript_path", "")
    if not transcript_path or not os.path.exists(transcript_path):
        print(json.dumps({}))
        return

    # 增量读取（cursor 文件记录上次位置）
    new_lines = read_transcript_incremental(transcript_path)
    if not new_lines:
        print(json.dumps({}))
        return

    # 过滤：只保留 user + assistant 消息
    conversation_lines = [
        l for l in new_lines
        if l.get("type") in ("user", "assistant")
    ]

    if conversation_lines:
        write_to_queue(
            session_id=data.get("session_id"),
            project=get_project_name(),
            queue_type="conversation",
            payload=conversation_lines
        )

    print(json.dumps({}))
```

**增量读取机制**：

```python
CURSOR_DIR = os.path.expanduser("~/.claude/.ponymemory_cursors/")

def read_transcript_incremental(transcript_path):
    session_hash = hashlib.md5(transcript_path.encode()).hexdigest()[:8]
    cursor_file = os.path.join(CURSOR_DIR, f"{session_hash}.cursor")

    last_pos = 0
    if os.path.exists(cursor_file):
        with open(cursor_file) as f:
            last_pos = int(f.read().strip() or "0")

    with open(transcript_path, "rb") as f:
        f.seek(last_pos)
        content = f.read()
        new_pos = last_pos + len(content)

    os.makedirs(CURSOR_DIR, exist_ok=True)
    with open(cursor_file, "w") as f:
        f.write(str(new_pos))

    lines = []
    for line in content.decode("utf-8", errors="ignore").strip().split("\n"):
        if line.strip():
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return lines
```

**timeout 设置**：移除 settings.json 中的 `"timeout": 5000`，使用官方默认上限。

### 5.2 PostToolUse Hook（post_tool_use.py）

**职责**：捕获文件写入和 MCP 下载事件，写入 SQLite 队列。

**触发条件**：settings.json 中配置两个 PostToolUse Hook 入口：
- matcher `Write|Edit`：捕获文件写入
- matcher `mcp__gmail-agent__.*|mcp__google-workspace__.*`：捕获 MCP 下载

两个入口共用同一个 `post_tool_use.py` 脚本，通过 `tool_name` 区分处理逻辑。

**输入**：stdin JSON，含 `tool_name`、`tool_input`、`tool_response`

```python
WATCHED_TOOLS = {"Write", "Edit"}
WATCHED_MCP = {
    "mcp__gmail-agent__get_email",      # 邮件下载
    "mcp__google-workspace__download_chat_attachment",
    "mcp__google-workspace__get_drive_file_content",
}
IGNORED_PATHS = ["/tmp/", "_debug", "_temp", "node_modules", ".git/"]

def main():
    data = json.loads(sys.stdin.read())
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if tool_name in WATCHED_TOOLS:
        file_path = tool_input.get("file_path", "")
        if any(p in file_path for p in IGNORED_PATHS):
            print(json.dumps({}))
            return

        write_to_queue(
            session_id=data.get("session_id"),
            project=get_project_name(),
            queue_type="tool_event",
            payload={
                "tool": tool_name,
                "file_path": file_path,
                "content_preview": tool_input.get("content", "")[:500],
            }
        )

    elif tool_name in WATCHED_MCP:
        write_to_queue(
            session_id=data.get("session_id"),
            project=get_project_name(),
            queue_type="mcp_download",
            payload={
                "tool": tool_name,
                "input": tool_input,
                "response": data.get("tool_response", {}),
            }
        )

    print(json.dumps({}))
```

### 5.3 SessionStart Hook（session_start.py v2）

**改进点**：

1. **动态查询词**：从 HANDOFF.md / task_plan.md 提取关键词，不再用固定字符串

```python
def build_query(project_name):
    keywords = [project_name]

    # 从 HANDOFF.md 提取上下文
    cwd = os.environ.get("CWD", os.getcwd())
    handoff = os.path.join(cwd, "HANDOFF.md")
    if os.path.isfile(handoff):
        with open(handoff) as f:
            content = f.read()[:500]
            # 提取首行作为任务关键词
            first_line = content.split("\n")[0].strip("# ").strip()
            if first_line:
                keywords.append(first_line)

    # 从 task_plan.md 提取目标
    task_plan = os.path.join(cwd, "task_plan.md")
    if os.path.isfile(task_plan):
        with open(task_plan) as f:
            for line in f:
                if line.startswith("## Objective"):
                    obj = next(f, "").strip()
                    if obj:
                        keywords.append(obj)
                    break

    return " ".join(keywords)
```

2. **截断优先级修正**：HANDOFF > 领域规则 > Obsidian 项目状态 > Qdrant 记忆

```python
# 注入顺序（优先级从高到低）
context_sections = []
# 1. HANDOFF（正在做什么）— 最高优先级
if handoff: context_sections.append(handoff)
# 2. PonyWriterX 活跃项目
if pwx: context_sections.append(pwx)
# 3. 待确认规则
if pending: context_sections.append(pending)
# 4. 领域经验
if domain_rules: context_sections.append(domain_rules)
# 5. Obsidian 项目状态
if obsidian_context: context_sections.append(obsidian_context)
# 6. Qdrant 记忆（可被截断的最低优先级）
if memories: context_sections.append(memories_section)
```

3. **元索引注入**：在 context 末尾描述可用记忆库

```python
META_INDEX = """
## 可用记忆工具
- search_memories(query): 过去的工作决策、用户反馈、技术纠正（{mem_count}条）
  触发场景：用户提到"之前/记得/上次"、设计决策需要一致性、不确定是否被纠正过
- search_papers(query): 代谢组学/质谱/生信论文（{paper_count} chunks）
  触发场景：需要引用文献、讨论技术方法
- search_notes(query): 日常笔记、会议记录（{note_count} chunks）
"""
```

4. **字节切片修复**：`content[-800:]` 改为正确的 UTF-8 安全截取

```python
def safe_tail(text, max_chars=800):
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]  # Python str 是 Unicode，按字符切，不按字节
```

注：原代码 `content[-800:]` 对 Python str 对象已经是按字符切片（不是字节切片），但 `open(file, encoding="utf-8")` 返回的确实是 str。如果用 `rb` 模式读取则需要先 decode。保持当前 `encoding="utf-8"` 读取方式即可。

### 5.4 PonyMemory Worker

**常驻 Python 进程，launchd 管理。**

#### 5.4.1 进程管理

plist 路径：`~/Library/LaunchAgents/com.ponymemory.worker.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ponymemory.worker</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/jiajun-agent/pony/ponymemory/.venv/bin/python</string>
        <string>/Users/jiajun-agent/pony/ponymemory/worker.py</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>5</integer>
    <key>StandardOutPath</key>
    <string>/Users/jiajun-agent/pony/ponymemory/logs/worker.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/jiajun-agent/pony/ponymemory/logs/worker.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ANTHROPIC_API_KEY</key>
        <string>FROM_ENV</string>
    </dict>
</dict>
</plist>
```

PID 防重：`~/.claude/.ponymemory_worker.pid`

#### 5.4.2 队列处理主循环

```python
def main_loop():
    init_db()
    write_pid_file()

    while True:
        # 1. 自愈：重置 stuck >120s 的 processing 记录
        reset_stuck_records(threshold_seconds=120)

        # 2. claim 一条 pending 记录（原子操作）
        item = claim_next_item()
        if item is None:
            time.sleep(1)
            maybe_run_maintenance()
            continue

        try:
            # 3. 根据 type 分发处理
            if item["type"] == "conversation":
                process_conversation(item)
            elif item["type"] == "tool_event":
                process_tool_event(item)
            elif item["type"] == "mcp_download":
                process_mcp_download(item)

            # 4. 处理完成，删除队列记录
            delete_queue_item(item["id"])

        except Exception as e:
            # 5. 失败：标记为 failed，保留供排查
            mark_failed(item["id"], str(e))
            log_error(f"Failed to process {item['id']}: {e}")
```

#### 5.4.3 对话处理流程（process_conversation）

```python
def process_conversation(item):
    payload = json.loads(item["payload"])
    project = item["project"]

    # 1. 提取对话文本
    conversation_text = format_conversation(payload)
    if len(conversation_text) < 50:
        return  # 太短，跳过

    # 2. 调 Haiku 提取事实
    facts = extract_facts_with_haiku(conversation_text, project)
    if not facts:
        return

    # 3. 对每个事实执行写入
    for fact in facts:
        # 质量门控
        if fact.get("quality_score", 0) < 0.6:
            store_raw_observation(fact)
            continue

        # BGE-M3 embedding
        vector = embed_text(fact["text"])
        if vector is None:
            store_raw_observation(fact)  # 降级
            continue

        # Qdrant 去重检查
        similar = search_qdrant(vector, project, top_k=3)
        if similar and similar[0]["score"] > 0.9:
            update_qdrant_memory(similar[0]["id"], fact)
        else:
            store_qdrant_memory(fact, vector)

        # Obsidian 直写
        write_obsidian_entry(project, fact)
```

#### 5.4.4 Haiku 事实提取 Prompt

```python
EXTRACT_PROMPT = """分析以下对话片段，提取值得长期记忆的内容。

只提取以下类型：
- correction: 用户纠正了 AI 的判断或做法
- decision: 做出了技术或设计决策
- milestone: 完成了重要里程碑
- finding: 发现了重要问题或事实
- preference: 用户表达了偏好或工作方式要求

对每个提取项，返回 JSON 数组：
[{
  "text": "50-200字摘要，含 what + why + impact",
  "memory_type": "correction|decision|milestone|finding|preference",
  "tags": ["相关标签"],
  "quality_score": 0.0-1.0
}]

如果没有值得记忆的内容，返回空数组 []。

对话片段：
{conversation}
"""
```

#### 5.4.5 工具事件处理（process_tool_event）

```python
def process_tool_event(item):
    payload = json.loads(item["payload"])
    file_path = payload.get("file_path", "")
    project = item["project"]

    # 路由引擎：按路径规则分类
    route = classify_file(file_path)

    if route == "ignore":
        return  # plans/、/tmp/、_debug 等

    if route == "spec":
        # docs/superpowers/specs/* → L4 知识库索引
        index_document_to_qdrant(file_path, collection="documents", project=project)
        write_obsidian_milestone(project, f"设计文档: {os.path.basename(file_path)}")

    elif route == "iterative_report":
        # iterative-reports/* → L3 经历记忆
        summary = summarize_with_haiku(read_file(file_path))
        store_qdrant_memory({
            "text": summary,
            "memory_type": "milestone",
            "source_path": file_path,
        })

    elif route == "paper":
        # *.pdf in papers/ → L4 知识库
        chunks = chunk_document(file_path)
        for chunk in chunks:
            index_chunk_to_qdrant(chunk, collection="papers")

    elif route == "document":
        # 其他文档 → L4 知识库
        index_document_to_qdrant(file_path, collection="documents", project=project)


def process_mcp_download(item):
    """处理 MCP 工具下载的文件（邮件附件、Google Drive 文件等）"""
    payload = json.loads(item["payload"])
    tool_name = payload.get("tool", "")
    project = item["project"]

    # 从 tool_response 中提取文件路径或内容
    response = payload.get("response", {})

    # Gmail 附件：通常返回文件内容，需要先保存到 ~/files/_inbox/
    if "gmail" in tool_name:
        save_to_inbox(response, source="gmail")

    # Google Drive：通常返回文件内容或下载路径
    elif "google-workspace" in tool_name:
        file_path = save_to_inbox(response, source="google-drive")
        # 尝试自动分类（基于文件名和扩展名）
        route = classify_file(file_path)
        if route != "ignore":
            move_from_inbox(file_path, route)
            if route in ("paper", "document"):
                index_document_to_qdrant(file_path, collection=route_to_collection(route), project=project)


def save_to_inbox(response, source):
    """将下载的文件保存到 ~/files/_inbox/，带来源标记"""
    inbox = os.path.expanduser("~/files/_inbox/")
    os.makedirs(inbox, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}_{source}_{response.get('filename', 'unknown')}"
    filepath = os.path.join(inbox, filename)
    # 写入文件内容（具体格式取决于 MCP 工具的 response 结构）
    with open(filepath, "wb") as f:
        content = response.get("content", response.get("data", b""))
        if isinstance(content, str):
            content = content.encode("utf-8")
        f.write(content)
    return filepath
```

#### 5.4.6 文件路由引擎

```python
ROUTE_RULES = [
    # (路径模式, 路由目标)
    ("*/plans/*", "ignore"),
    ("*/plans/_archived/*", "spec"),  # 归档的计划有保留价值
    ("/tmp/*", "ignore"),
    ("*_debug*", "ignore"),
    ("*_temp*", "ignore"),
    ("*HANDOFF.md", "ignore"),
    ("*task_plan.md", "ignore"),
    ("*progress.md", "ignore"),
    ("*findings.md", "ignore"),  # 这些由 Stop Hook 的对话提取覆盖
    ("*/docs/superpowers/specs/*", "spec"),
    ("*/docs/superpowers/plans/*", "spec"),
    ("*/iterative-reports/*", "iterative_report"),
    ("*/ponywriterX/output/*", "paper"),
    ("*.pdf", "paper"),
    ("*.docx", "document"),
    ("*.md", "document"),
]

def classify_file(file_path):
    for pattern, route in ROUTE_RULES:
        if fnmatch.fnmatch(file_path, pattern):
            return route
    return "document"  # 默认归为文档
```

#### 5.4.7 Obsidian 直写

```python
VAULT = os.path.expanduser("~/pony/obsidian-vault/")

def write_obsidian_entry(project, fact):
    """按 memory_type 分发写入不同的 Obsidian 文件"""
    mtype = fact.get("memory_type", "note")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = fact.get("text", "")

    # 按类型路由到不同文件
    TYPE_TO_FILE = {
        "correction": "decisions.md",
        "decision": "decisions.md",
        "preference": "decisions.md",
        "finding": "findings.md",
        "milestone": "_project.md",
    }

    target_file = TYPE_TO_FILE.get(mtype, "decisions.md")
    target_path = os.path.join(VAULT, f"01-Projects/{project}/{target_file}")
    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    if target_file == "_project.md":
        # 里程碑追加到项目状态文件末尾
        entry = f"\n- ✅ {timestamp}: {text}"
    else:
        # 其他类型用标准格式
        entry = f"\n## {timestamp} [{mtype}]\n\n{text}\n"

    with open(target_path, "a", encoding="utf-8") as f:
        f.write(entry)

def write_obsidian_milestone(project, description):
    """更新 _project.md 状态"""
    project_path = os.path.join(VAULT, f"01-Projects/{project}/_project.md")
    if not os.path.isfile(project_path):
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n- ✅ {timestamp}: {description}"

    with open(project_path, "a", encoding="utf-8") as f:
        f.write(entry)
```

### 5.5 降级策略

```
主链路：Haiku API → BGE-M3 → Qdrant → Obsidian 直写

降级层 1（Haiku 失败）：
  → 存 raw_observations 表（原文，标记 needs_extraction=true）
  → Worker 空闲时重试

降级层 2（BGE-M3 不可用）：
  → 存 Qdrant 纯文本（embedding=null，标记 needs_embedding=true）
  → BGE-M3 恢复后 backfill

降级层 3（Qdrant 不可达）：
  → 写入 fallback.jsonl（~/.claude/.ponymemory_fallback.jsonl）
  → Worker 检测到 Qdrant 恢复后导入

降级层 4（所有外部服务失败）：
  → 队列条目保留，status=failed
  → Worker 重启后指数退避重试
  → 数据不丢失，只延迟写入
```

### 5.6 健康检查

```python
# HTTP GET localhost:47777/health
def health_handler():
    status = {
        "worker": "running",
        "uptime_seconds": time.time() - START_TIME,
        "qdrant": check_qdrant(),       # "ok" | "down"
        "bge_m3": check_bge_m3(),       # "ok" | "down"
        "queue_pending": count_pending(),
        "queue_failed": count_failed(),
        "queue_stuck": count_stuck(threshold=120),
        "last_processed_at": get_last_processed_time(),
        "memories_total": get_qdrant_count("session_memories"),
        "papers_total": get_qdrant_count("papers"),
    }
    return json.dumps(status)
```

### 5.7 定期维护

Worker 内置调度器，不依赖外部 Cron 或 Claude Code session。

```python
MAINTENANCE_TASKS = {
    "dedup": 3600,          # 每小时：相似度 >0.95 的条目合并
    "decay": 86400,         # 每天：清理 >90 天的 session_summary
    "consolidate": 86400,   # 每天：反复出现的经历提炼为规则候选
    "backfill": 3600,       # 每小时：重试 needs_embedding/needs_extraction
    "source_check": 86400,  # 每天：检查 source_path 指向的文件是否存在
}
```

#### 去重维护

```python
def run_dedup():
    memories = list_all_memories(limit=200)
    for i, mem_a in enumerate(memories):
        for mem_b in memories[i+1:]:
            if mem_a["project"] == mem_b["project"]:
                similarity = cosine_similarity(mem_a["vector"], mem_b["vector"])
                if similarity > 0.95:
                    # 保留更新的，删除更旧的
                    older = mem_a if mem_a["timestamp"] < mem_b["timestamp"] else mem_b
                    delete_memory(older["id"])
```

#### 巩固机制（经历 → 规则）

```python
def run_consolidation():
    # 查找近 30 天内 memory_type=correction 的条目
    corrections = search_memories_by_type("correction", days=30)

    # 按 project 分组
    by_project = group_by(corrections, "project")

    for project, items in by_project.items():
        if len(items) < 3:
            continue  # 不够频繁，不提炼

        # 调 Haiku 分析是否有共性模式
        pattern = analyze_pattern_with_haiku(items)
        if pattern:
            # 写入 pending_rules.md 待用户确认
            append_pending_rule(pattern)
```

## 6. SQLite Schema

```sql
-- 队列表（临时，处理完即删）
CREATE TABLE queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,
    type TEXT NOT NULL,         -- 'conversation' | 'tool_event' | 'mcp_download'
    payload TEXT NOT NULL,      -- JSON
    status TEXT DEFAULT 'pending',  -- 'pending' | 'processing' | 'failed'
    created_at REAL NOT NULL,
    claimed_at REAL,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0
);

-- 原始观察（质量不达标或 AI 失败的 fallback）
CREATE TABLE raw_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    project TEXT,
    text TEXT NOT NULL,
    source TEXT,               -- 'haiku_failed' | 'low_quality' | 'embedding_failed'
    created_at REAL NOT NULL,
    processed INTEGER DEFAULT 0
);

-- 执行审计日志
CREATE TABLE exec_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    hook TEXT NOT NULL,         -- 'stop' | 'post_tool_use' | 'session_start'
    session_id TEXT,
    lines_captured INTEGER,
    queue_written INTEGER,
    error TEXT
);

-- 维护日志
CREATE TABLE maintenance_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    task TEXT NOT NULL,         -- 'dedup' | 'decay' | 'consolidate' | 'backfill'
    items_processed INTEGER,
    items_removed INTEGER,
    duration_seconds REAL
);
```

## 7. 文件路由引擎

### 路由规则配置

```yaml
# ~/.claude/.ponymemory_routes.yaml
rules:
  - pattern: "*/plans/*.md"
    route: ignore
    reason: "计划是意图，不是知识"

  - pattern: "*/plans/_archived/*"
    route: spec
    reason: "归档计划有参考价值"

  - pattern: "/tmp/*"
    route: ignore

  - pattern: "*HANDOFF.md"
    route: ignore
    reason: "工作记忆，由 Stop Hook 对话提取覆盖"

  - pattern: "*/docs/superpowers/specs/*"
    route: spec
    index_to: [documents]
    obsidian_action: milestone

  - pattern: "*/docs/superpowers/plans/*"
    route: spec
    index_to: [documents]

  - pattern: "*/iterative-reports/*"
    route: iterative_report
    index_to: [session_memories]
    obsidian_action: append_decisions

  - pattern: "*/ponywriterX/output/*"
    route: paper
    index_to: [papers]
    file_store: ~/files/papers/ai-generated/

  - pattern: "*.pdf"
    route: paper
    index_to: [papers]
    file_store: ~/files/papers/

  - pattern: "*.docx"
    route: document
    index_to: [documents]
    file_store: ~/files/documents/

fallback:
  route: document
  index_to: [documents]
```

### MCP 下载文件路由

```yaml
mcp_routes:
  - tool: "mcp__gmail-agent__*"
    route: inbox
    file_store: ~/files/_inbox/
    reason: "邮件附件需人工确认分类"

  - tool: "mcp__google-workspace__get_drive_file_content"
    route: document
    index_to: [documents]
    file_store: ~/files/documents/google-drive/

  - tool: "mcp__google-workspace__download_chat_attachment"
    route: inbox
    file_store: ~/files/_inbox/
```

## 8. settings.json 变更

```json
{
  "hooks": {
    "SessionStart": [
      {
        "command": "python3 /Users/jiajun-agent/pony/ponymemory/hooks/session_start.py"
      }
    ],
    "Stop": [
      {
        "command": "python3 /Users/jiajun-agent/pony/ponymemory/hooks/stop.py"
      }
    ],
    "PreCompact": [
      {
        "command": "python3 /Users/jiajun-agent/pony/ponymemory/hooks/pre_compact.py"
      }
    ],
    "PostToolUse": [
      {
        "matcher": {
          "toolName": "Write|Edit"
        },
        "command": "python3 /Users/jiajun-agent/pony/ponymemory/hooks/post_tool_use.py"
      },
      {
        "matcher": {
          "toolName": "mcp__gmail-agent__.*|mcp__google-workspace__.*"
        },
        "command": "python3 /Users/jiajun-agent/pony/ponymemory/hooks/post_tool_use.py"
      }
    ]
  }
}
```

注意：移除所有 Hook 的 `"timeout"` 字段，使用官方默认上限。

## 9. 实施阶段

### Phase 1：核心基础（解决根本问题）
- [ ] SQLite schema 创建 + 队列读写函数
- [ ] Worker 骨架（主循环 + Haiku 提取 + Qdrant 写入）
- [ ] Stop Hook v3（transcript 增量 → 队列）
- [ ] Obsidian 直写函数（不经 MCP）
- [ ] PID 防重 + 基础日志
- [ ] 验证：手动写入队列 → Worker 处理 → Qdrant 和 Obsidian 均有记录

### Phase 2：工具级捕获 + 进程管理
- [ ] PostToolUse Hook（文件写入事件捕获）
- [ ] 文件路由引擎（路径规则分类）
- [ ] launchd plist 配置 + 开机自启
- [ ] 降级链实现（Haiku/BGE-M3/Qdrant 各级 fallback）
- [ ] 验证：Claude 写文件 → 自动出现在 Qdrant 索引中

### Phase 3：读取增强
- [ ] SessionStart 动态查询词
- [ ] 截断优先级修正（HANDOFF 最高）
- [ ] 元索引注入（记忆库描述）
- [ ] 验证：新 session 启动时注入的 context 与当前任务相关

### Phase 4：智能增强
- [ ] 每日巩固机制（反复纠正 → 规则候选）
- [ ] Zotero 联动（新 PDF → Pyzotero 导入 → Qdrant 索引）
- [ ] MCP 下载文件自动路由
- [ ] 验证：纠正 3 次同类错误后 pending_rules.md 出现候选规则

### Phase 5：可观测性
- [ ] /health HTTP 端点
- [ ] 执行率审计（exec_log 统计）
- [ ] 检索质量基准测试（黄金测试集 20-30 条）
- [ ] Worker dashboard（可选，复用 OpenClaw dashboard 模式）

## 10. 与现有系统的兼容

### 保留
- Qdrant MCP server（`qdrant-mcp-server.py`）：Claude Code 手动调用 search/store 的入口保留
- Obsidian MCP：读取方向保留（搜索、wikilinks），写入改为直写文件
- SessionStart Hook：保留并增强（Phase 3）
- 知识银河可视化：数据源不变（generate_galaxy_data.py 从 Qdrant 读取）

### 废弃
- Stop Hook v2 的 additionalContext 文字提醒
- PreCompact Hook 的文字提醒（改为 Worker 自动处理，或保留为 `decision: "block"` 模式做最后保底）
- 全局计数器 `~/.claude/.ponymemory_response_count`（维护改为 Worker 内置调度）

### 迁移
- 现有 Qdrant 数据：无需迁移，schema 兼容
- 现有 Obsidian 文件：无需迁移，直写只追加不覆盖
- settings.json：添加 PostToolUse Hook，移除 timeout 限制

## 11. 风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| Haiku API 成本（每轮 ~$0.0003） | 日均 100 轮 = $0.03/天，可接受；设日预算上限 |
| Worker 进程内存泄漏 | launchd KeepAlive 自动重启；每 24 小时主动重启一次 |
| SQLite 队列无限增长 | 处理完即删；failed 记录保留 7 天后清理 |
| transcript_path 指向旧文件 | session_id guard + cursor 文件按 session 隔离 |
| PostToolUse Hook 捕获过多临时文件 | IGNORED_PATHS 白名单 + 路由引擎 ignore 规则 |
| Obsidian vault 目录结构变更 | 写入前 `os.makedirs(exist_ok=True)` |
