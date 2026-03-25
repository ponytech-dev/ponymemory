# PonyMemory v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace PonyMemory's unreliable additionalContext-based memory system with a fully code-guaranteed queue+worker architecture that writes to Qdrant and Obsidian without depending on Claude's compliance.

**Architecture:** SQLite queue receives events from Stop Hook (transcript changes) and PostToolUse Hook (file writes). A launchd-managed Python Worker process polls the queue, calls Haiku for fact extraction, BGE-M3 for embedding, then writes to Qdrant and Obsidian vault directly (no MCP dependency).

**Tech Stack:** Python 3.14, SQLite3, Anthropic SDK (Haiku), Qdrant HTTP API, BGE-M3 local service, launchd (macOS)

**Spec:** `docs/superpowers/specs/2026-03-25-ponymemory-v2-design.md`

---

## File Structure

### New files
- `ponymemory/db.py` — SQLite schema + queue CRUD operations
- `ponymemory/worker.py` — Main worker process (queue polling, dispatch, maintenance)
- `ponymemory/extractor.py` — Haiku fact extraction + quality gate
- `ponymemory/embedder.py` — BGE-M3 embedding + Qdrant write/search
- `ponymemory/obsidian_writer.py` — Direct file writes to Obsidian vault
- `ponymemory/router.py` — File classification/routing engine
- `ponymemory/health.py` — HTTP health check endpoint
- `ponymemory/hooks/post_tool_use.py` — PostToolUse Hook (new)
- `ponymemory/tests/test_db.py` — Queue/DB tests
- `ponymemory/tests/test_extractor.py` — Extraction tests
- `ponymemory/tests/test_router.py` — Routing tests
- `ponymemory/tests/test_obsidian_writer.py` — Obsidian write tests
- `ponymemory/tests/test_worker.py` — Worker integration tests
- `~/Library/LaunchAgents/com.ponymemory.worker.plist` — launchd config
- `~/.claude/.ponymemory_routes.yaml` — Routing rules config

### Modified files
- `ponymemory/hooks/stop.py` — Rewrite: transcript→queue instead of additionalContext
- `ponymemory/hooks/session_start.py` — Phase 3: dynamic query + priority fix + meta-index
- `~/.claude/settings.json` — Add PostToolUse hooks, remove timeouts

---

## Phase 1: Core Foundation

### Task 1: SQLite Database Layer

**Files:**
- Create: `ponymemory/db.py`
- Create: `ponymemory/tests/test_db.py`

- [ ] **Step 1: Write failing test for DB init**

```python
# ponymemory/tests/test_db.py
import os
import tempfile
import pytest
from ponymemory.db import init_db, get_db_path

def test_init_db_creates_tables():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        conn = init_db(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "queue" in tables
        assert "raw_observations" in tables
        assert "exec_log" in tables
        assert "maintenance_log" in tables
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_db.py::test_init_db_creates_tables -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement db.py with schema**

```python
# ponymemory/db.py
"""SQLite database layer for PonyMemory v2 queue and audit."""
import json
import os
import sqlite3
import time

DEFAULT_DB_PATH = os.path.expanduser("~/.claude/.ponymemory.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at REAL NOT NULL,
    claimed_at REAL,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS raw_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    project TEXT,
    text TEXT NOT NULL,
    source TEXT,
    created_at REAL NOT NULL,
    processed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS exec_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    hook TEXT NOT NULL,
    session_id TEXT,
    lines_captured INTEGER,
    queue_written INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS maintenance_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    task TEXT NOT NULL,
    items_processed INTEGER,
    items_removed INTEGER,
    duration_seconds REAL
);
"""


def init_db(db_path=None):
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def write_to_queue(conn, session_id, project, queue_type, payload):
    conn.execute(
        "INSERT INTO queue (session_id, project, type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, project, queue_type, json.dumps(payload, ensure_ascii=False), time.time()),
    )
    conn.commit()


def claim_next_item(conn):
    cursor = conn.execute(
        "UPDATE queue SET status='processing', claimed_at=? "
        "WHERE id = (SELECT id FROM queue WHERE status='pending' ORDER BY created_at LIMIT 1) "
        "RETURNING *",
        (time.time(),),
    )
    row = cursor.fetchone()
    conn.commit()
    return dict(row) if row else None


def delete_queue_item(conn, item_id):
    conn.execute("DELETE FROM queue WHERE id=?", (item_id,))
    conn.commit()


def mark_failed(conn, item_id, error_message):
    conn.execute(
        "UPDATE queue SET status='failed', error_message=?, retry_count=retry_count+1 WHERE id=?",
        (error_message, item_id),
    )
    conn.commit()


def reset_stuck_records(conn, threshold_seconds=120):
    cutoff = time.time() - threshold_seconds
    conn.execute(
        "UPDATE queue SET status='pending', claimed_at=NULL WHERE status='processing' AND claimed_at < ?",
        (cutoff,),
    )
    conn.commit()


def store_raw_observation(conn, session_id, project, text, source):
    conn.execute(
        "INSERT INTO raw_observations (session_id, project, text, source, created_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, project, text, source, time.time()),
    )
    conn.commit()


def log_exec(conn, hook, session_id, lines_captured, queue_written, error=None):
    conn.execute(
        "INSERT INTO exec_log (timestamp, hook, session_id, lines_captured, queue_written, error) VALUES (?, ?, ?, ?, ?, ?)",
        (time.time(), hook, session_id, lines_captured, queue_written, error),
    )
    conn.commit()


def count_by_status(conn, status):
    row = conn.execute("SELECT COUNT(*) FROM queue WHERE status=?", (status,)).fetchone()
    return row[0]
```

- [ ] **Step 4: Write full test suite for queue operations**

```python
# append to ponymemory/tests/test_db.py

def test_write_and_claim(tmp_db):
    from ponymemory.db import write_to_queue, claim_next_item, delete_queue_item
    write_to_queue(tmp_db, "sess1", "ponylab", "conversation", [{"text": "hello"}])
    item = claim_next_item(tmp_db)
    assert item is not None
    assert item["project"] == "ponylab"
    assert item["status"] == "processing"
    delete_queue_item(tmp_db, item["id"])
    assert claim_next_item(tmp_db) is None

def test_claim_returns_none_when_empty(tmp_db):
    from ponymemory.db import claim_next_item
    assert claim_next_item(tmp_db) is None

def test_mark_failed(tmp_db):
    from ponymemory.db import write_to_queue, claim_next_item, mark_failed
    write_to_queue(tmp_db, "sess1", "test", "conversation", {})
    item = claim_next_item(tmp_db)
    mark_failed(tmp_db, item["id"], "timeout")
    row = tmp_db.execute("SELECT * FROM queue WHERE id=?", (item["id"],)).fetchone()
    assert row["status"] == "failed"
    assert row["retry_count"] == 1

def test_reset_stuck(tmp_db):
    from ponymemory.db import write_to_queue, claim_next_item, reset_stuck_records
    write_to_queue(tmp_db, "sess1", "test", "conversation", {})
    item = claim_next_item(tmp_db)
    # Backdate claimed_at
    tmp_db.execute("UPDATE queue SET claimed_at=? WHERE id=?", (0, item["id"]))
    tmp_db.commit()
    reset_stuck_records(tmp_db, threshold_seconds=1)
    row = tmp_db.execute("SELECT * FROM queue WHERE id=?", (item["id"],)).fetchone()
    assert row["status"] == "pending"

@pytest.fixture
def tmp_db():
    import tempfile
    from ponymemory.db import init_db
    with tempfile.TemporaryDirectory() as tmpdir:
        conn = init_db(os.path.join(tmpdir, "test.db"))
        yield conn
        conn.close()
```

- [ ] **Step 5: Run all DB tests**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_db.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/jiajun-agent/pony/ponymemory
git add db.py tests/test_db.py
git commit -m "feat(v2): add SQLite queue database layer"
```

---

### Task 2: Stop Hook v3 (transcript → queue)

**Files:**
- Modify: `ponymemory/hooks/stop.py` (full rewrite)
- Create: `ponymemory/tests/test_stop_hook.py`

- [ ] **Step 1: Write test for transcript incremental reading**

```python
# ponymemory/tests/test_stop_hook.py
import json
import os
import tempfile
import pytest

def test_read_transcript_incremental_first_read():
    from ponymemory.hooks.stop import read_transcript_incremental
    with tempfile.TemporaryDirectory() as tmpdir:
        transcript = os.path.join(tmpdir, "session.jsonl")
        lines = [
            {"type": "user", "message": {"content": [{"type": "text", "text": "hello"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
        ]
        with open(transcript, "w") as f:
            for l in lines:
                f.write(json.dumps(l) + "\n")

        result = read_transcript_incremental(transcript, cursor_dir=tmpdir)
        assert len(result) == 2
        assert result[0]["type"] == "user"

def test_read_transcript_incremental_second_read_empty():
    from ponymemory.hooks.stop import read_transcript_incremental
    with tempfile.TemporaryDirectory() as tmpdir:
        transcript = os.path.join(tmpdir, "session.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "user"}) + "\n")

        read_transcript_incremental(transcript, cursor_dir=tmpdir)
        result = read_transcript_incremental(transcript, cursor_dir=tmpdir)
        assert len(result) == 0

def test_read_transcript_incremental_appended():
    from ponymemory.hooks.stop import read_transcript_incremental
    with tempfile.TemporaryDirectory() as tmpdir:
        transcript = os.path.join(tmpdir, "session.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "user", "seq": 1}) + "\n")

        read_transcript_incremental(transcript, cursor_dir=tmpdir)

        with open(transcript, "a") as f:
            f.write(json.dumps({"type": "assistant", "seq": 2}) + "\n")

        result = read_transcript_incremental(transcript, cursor_dir=tmpdir)
        assert len(result) == 1
        assert result[0]["seq"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_stop_hook.py -v`
Expected: FAIL — import error

- [ ] **Step 3: Rewrite stop.py v3**

```python
#!/usr/bin/env python3
"""PonyMemory Stop Hook v3 — transcript → SQLite queue (no additionalContext)."""
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import init_db, write_to_queue, log_exec

DEFAULT_CURSOR_DIR = os.path.expanduser("~/.claude/.ponymemory_cursors/")


def get_project_name():
    cwd = os.environ.get("CWD", os.getcwd())
    pony_dir = os.path.expanduser("~/pony/")
    if cwd.startswith(pony_dir):
        relative = cwd[len(pony_dir):]
        parts = relative.strip("/").split("/")
        if parts and parts[0]:
            return parts[0]
    return "pony"


def read_transcript_incremental(transcript_path, cursor_dir=None):
    if cursor_dir is None:
        cursor_dir = DEFAULT_CURSOR_DIR

    session_hash = hashlib.md5(transcript_path.encode()).hexdigest()[:8]
    cursor_file = os.path.join(cursor_dir, f"{session_hash}.cursor")

    last_pos = 0
    if os.path.exists(cursor_file):
        with open(cursor_file) as f:
            last_pos = int(f.read().strip() or "0")

    if not os.path.exists(transcript_path):
        return []

    with open(transcript_path, "rb") as f:
        f.seek(last_pos)
        content = f.read()
        new_pos = last_pos + len(content)

    if not content:
        return []

    os.makedirs(cursor_dir, exist_ok=True)
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


def main():
    data = json.loads(sys.stdin.read())

    if data.get("stop_hook_active"):
        print(json.dumps({}))
        return

    transcript_path = data.get("transcript_path", "")
    session_id = data.get("session_id", "")

    if not transcript_path or not os.path.exists(transcript_path):
        print(json.dumps({}))
        return

    new_lines = read_transcript_incremental(transcript_path)

    conversation_lines = [
        l for l in new_lines if l.get("type") in ("user", "assistant")
    ]

    error = None
    queue_written = 0
    if conversation_lines:
        try:
            conn = init_db()
            write_to_queue(conn, session_id, get_project_name(), "conversation", conversation_lines)
            queue_written = 1
            log_exec(conn, "stop", session_id, len(conversation_lines), queue_written)
            conn.close()
        except Exception as e:
            error = str(e)
            print(f"[PonyMemory] stop queue write failed: {e}", file=sys.stderr)

    print(json.dumps({}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[PonyMemory] stop fatal: {e}", file=sys.stderr)
        print(json.dumps({}))
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_stop_hook.py -v`
Expected: All PASS

- [ ] **Step 5: Manual integration test**

```bash
# Create test transcript first
echo '{"type":"user","message":{"content":[{"type":"text","text":"我们决定用SQLite做队列"}]}}' > /tmp/test_transcript.jsonl
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"同意"}]}}' >> /tmp/test_transcript.jsonl

# Run hook
echo '{"session_id":"test123","transcript_path":"/tmp/test_transcript.jsonl","cwd":"/Users/jiajun-agent/pony/ponymemory"}' | python3 /Users/jiajun-agent/pony/ponymemory/hooks/stop.py
```
Expected: `{}` output, check `~/.claude/.ponymemory.db` has a queue entry

- [ ] **Step 6: Commit**

```bash
cd /Users/jiajun-agent/pony/ponymemory
git add hooks/stop.py tests/test_stop_hook.py
git commit -m "feat(v2): rewrite stop hook - transcript to SQLite queue"
```

---

### Task 3: Install Anthropic SDK dependency

- [ ] **Step 1: Install anthropic package**

```bash
cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/pip install anthropic
```

- [ ] **Step 2: Verify import works**

```bash
.venv/bin/python -c "import anthropic; print(anthropic.__version__)"
```

- [ ] **Step 3: Commit**

```bash
cd /Users/jiajun-agent/pony/ponymemory
git commit -m "chore: add anthropic SDK dependency"
```

---

### Task 4: Haiku Fact Extractor

**Files:**
- Create: `ponymemory/extractor.py`
- Create: `ponymemory/tests/test_extractor.py`

- [ ] **Step 1: Write test for conversation formatting**

```python
# ponymemory/tests/test_extractor.py
def test_format_conversation():
    from ponymemory.extractor import format_conversation
    lines = [
        {"type": "user", "message": {"content": [{"type": "text", "text": "用 BGE-M3 还是 OpenAI embedding？"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "推荐 BGE-M3，本地运行无 API 成本"}]}},
    ]
    result = format_conversation(lines)
    assert "BGE-M3" in result
    assert "User:" in result
    assert "Assistant:" in result

def test_format_conversation_skips_tool_blocks():
    from ponymemory.extractor import format_conversation
    lines = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {}},
            {"type": "text", "text": "文件内容如下"},
        ]}},
    ]
    result = format_conversation(lines)
    assert "文件内容如下" in result
    assert "tool_use" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_extractor.py -v`
Expected: FAIL

- [ ] **Step 3: Implement extractor.py**

```python
# ponymemory/extractor.py
"""Haiku-based fact extraction from conversation transcripts."""
import json
import os

EXTRACT_PROMPT = """分析以下对话片段，提取值得长期记忆的内容。

只提取以下类型：
- correction: 用户纠正了 AI 的判断或做法
- decision: 做出了技术或设计决策
- milestone: 完成了重要里程碑
- finding: 发现了重要问题或事实
- preference: 用户表达了偏好或工作方式要求

对每个提取项，返回 JSON 数组：
[{{"text": "50-200字摘要，含 what + why + impact", "memory_type": "correction|decision|milestone|finding|preference", "tags": ["标签"], "quality_score": 0.0-1.0}}]

如果没有值得记忆的内容，返回空数组 []。
不要返回任何 markdown 格式或代码块，只返回纯 JSON。

对话片段：
{conversation}"""

QUALITY_THRESHOLD = 0.6


def format_conversation(lines):
    parts = []
    for entry in lines:
        msg = entry.get("message", {})
        role = msg.get("role", entry.get("type", "unknown"))
        content_blocks = msg.get("content", [])

        texts = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block["text"])
            elif isinstance(block, str):
                texts.append(block)

        if texts:
            label = "User" if role in ("user", "human") else "Assistant"
            parts.append(f"{label}: {' '.join(texts)}")

    return "\n\n".join(parts)


def extract_facts(conversation_text, project):
    try:
        import anthropic
    except ImportError:
        return []

    if len(conversation_text) < 50:
        return []

    client = anthropic.Anthropic()
    prompt = EXTRACT_PROMPT.format(conversation=conversation_text[:4000])

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        facts = json.loads(text.strip())
        if not isinstance(facts, list):
            return []

        for fact in facts:
            fact["project"] = project

        return facts
    except Exception:
        return []


def filter_by_quality(facts):
    passed = []
    failed = []
    for fact in facts:
        if fact.get("quality_score", 0) >= QUALITY_THRESHOLD:
            passed.append(fact)
        else:
            failed.append(fact)
    return passed, failed
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_extractor.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/jiajun-agent/pony/ponymemory
git add extractor.py tests/test_extractor.py
git commit -m "feat(v2): add Haiku fact extractor with quality gate"
```

---

### Task 5: BGE-M3 Embedder + Qdrant Writer

**Files:**
- Create: `ponymemory/embedder.py`
- Create: `ponymemory/tests/test_embedder.py`

- [ ] **Step 1: Write failing tests**

```python
# ponymemory/tests/test_embedder.py
from unittest.mock import patch, MagicMock
import json

def test_embed_text_returns_vector():
    from ponymemory.embedder import embed_text
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"embeddings": [[0.1]*10]}).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = embed_text("test")
        assert result is not None
        assert len(result) == 10

def test_embed_text_returns_none_on_failure():
    from ponymemory.embedder import embed_text
    with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        result = embed_text("test")
        assert result is None

def test_check_qdrant_health_ok():
    from ponymemory.embedder import check_qdrant_health
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        assert check_qdrant_health() == "ok"

def test_check_qdrant_health_down():
    from ponymemory.embedder import check_qdrant_health
    with patch("urllib.request.urlopen", side_effect=Exception("refused")):
        assert check_qdrant_health() == "down"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_embedder.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement embedder.py**

```python
# ponymemory/embedder.py
"""BGE-M3 embedding and Qdrant read/write operations."""
import json
import os
import time
import urllib.request
import urllib.error
import uuid

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8999")
MEMORY_COLLECTION = "session_memories"


def embed_text(text):
    try:
        payload = json.dumps({"texts": [text]}).encode("utf-8")
        req = urllib.request.Request(
            f"{EMBED_URL}/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            embeddings = result.get("embeddings", [])
            return embeddings[0] if embeddings else None
    except Exception:
        return None


def search_qdrant(vector, project, top_k=3, collection=None):
    if collection is None:
        collection = MEMORY_COLLECTION
    try:
        payload = json.dumps({
            "vector": vector,
            "limit": top_k,
            "with_payload": True,
            "filter": {"must": [{"key": "project", "match": {"value": project}}]} if project else None,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{collection}/points/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("result", [])
    except Exception:
        return []


def store_qdrant_memory(fact, vector, collection=None):
    if collection is None:
        collection = MEMORY_COLLECTION
    point_id = str(uuid.uuid4())
    payload = {
        "text": fact["text"],
        "memory_type": fact.get("memory_type", "note"),
        "project": fact.get("project", ""),
        "tags": fact.get("tags", []),
        "source_path": fact.get("source_path", ""),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    body = json.dumps({
        "points": [{"id": point_id, "vector": vector, "payload": payload}]
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{QDRANT_URL}/collections/{collection}/points",
        data=body,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return point_id
    except Exception:
        return None


def check_qdrant_health():
    try:
        req = urllib.request.Request(f"{QDRANT_URL}/healthz")
        with urllib.request.urlopen(req, timeout=2):
            return "ok"
    except Exception:
        return "down"


def check_bge_m3_health():
    try:
        payload = json.dumps({"texts": ["test"]}).encode("utf-8")
        req = urllib.request.Request(
            f"{EMBED_URL}/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3):
            return "ok"
    except Exception:
        return "down"
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_embedder.py -v`
Expected: All PASS

- [ ] **Step 5: Verify BGE-M3 and Qdrant are reachable**

```bash
curl -s http://localhost:6333/healthz && echo " qdrant ok"
curl -s -X POST http://localhost:8999/embed -H 'Content-Type: application/json' -d '{"texts":["test"]}' | head -c 100 && echo " bge ok"
```
Expected: Both return OK

- [ ] **Step 6: Commit**

```bash
cd /Users/jiajun-agent/pony/ponymemory
git add embedder.py tests/test_embedder.py
git commit -m "feat(v2): add BGE-M3 embedder and Qdrant writer"
```

---

### Task 6: Obsidian Direct Writer

**Files:**
- Create: `ponymemory/obsidian_writer.py`
- Create: `ponymemory/tests/test_obsidian_writer.py`

- [ ] **Step 1: Write failing test**

```python
# ponymemory/tests/test_obsidian_writer.py
import os
import tempfile
from ponymemory.obsidian_writer import write_obsidian_entry

def test_write_decision():
    with tempfile.TemporaryDirectory() as vault:
        fact = {"text": "决定用 BGE-M3", "memory_type": "decision"}
        write_obsidian_entry("testproject", fact, vault_path=vault)
        path = os.path.join(vault, "01-Projects/testproject/decisions.md")
        assert os.path.exists(path)
        content = open(path).read()
        assert "决定用 BGE-M3" in content
        assert "[decision]" in content

def test_write_finding():
    with tempfile.TemporaryDirectory() as vault:
        fact = {"text": "发现内存泄漏", "memory_type": "finding"}
        write_obsidian_entry("testproject", fact, vault_path=vault)
        path = os.path.join(vault, "01-Projects/testproject/findings.md")
        assert os.path.exists(path)
        assert "发现内存泄漏" in open(path).read()

def test_write_milestone():
    with tempfile.TemporaryDirectory() as vault:
        # Create _project.md first
        os.makedirs(os.path.join(vault, "01-Projects/testproject"))
        with open(os.path.join(vault, "01-Projects/testproject/_project.md"), "w") as f:
            f.write("# Project\n")
        fact = {"text": "v2 发布", "memory_type": "milestone"}
        write_obsidian_entry("testproject", fact, vault_path=vault)
        content = open(os.path.join(vault, "01-Projects/testproject/_project.md")).read()
        assert "v2 发布" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_obsidian_writer.py -v`
Expected: FAIL

- [ ] **Step 3: Implement obsidian_writer.py**

```python
# ponymemory/obsidian_writer.py
"""Direct file writes to Obsidian vault (no MCP dependency)."""
import os
from datetime import datetime

DEFAULT_VAULT = os.path.expanduser("~/pony/obsidian-vault/")

TYPE_TO_FILE = {
    "correction": "decisions.md",
    "decision": "decisions.md",
    "preference": "decisions.md",
    "finding": "findings.md",
    "milestone": "_project.md",
}


def write_obsidian_entry(project, fact, vault_path=None):
    if vault_path is None:
        vault_path = DEFAULT_VAULT

    mtype = fact.get("memory_type", "note")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = fact.get("text", "")

    target_file = TYPE_TO_FILE.get(mtype, "decisions.md")
    target_path = os.path.join(vault_path, f"01-Projects/{project}/{target_file}")
    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    if target_file == "_project.md":
        entry = f"\n- ✅ {timestamp}: {text}"
    else:
        entry = f"\n## {timestamp} [{mtype}]\n\n{text}\n"

    with open(target_path, "a", encoding="utf-8") as f:
        f.write(entry)
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_obsidian_writer.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/jiajun-agent/pony/ponymemory
git add obsidian_writer.py tests/test_obsidian_writer.py
git commit -m "feat(v2): add Obsidian direct file writer"
```

---

### Task 7: Worker Main Loop

**Files:**
- Create: `ponymemory/worker.py`
- Create: `ponymemory/tests/test_worker.py`

- [ ] **Step 1: Write integration test for worker processing**

```python
# ponymemory/tests/test_worker.py
import json
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

def test_process_conversation_skips_short():
    from ponymemory.worker import process_conversation
    item = {"payload": json.dumps([{"type": "user", "message": {"content": [{"type": "text", "text": "hi"}]}}]), "project": "test", "session_id": "s1"}
    # Should not raise, just skip
    with patch("ponymemory.worker.extract_facts", return_value=[]):
        process_conversation(item, db_conn=MagicMock())

def test_process_conversation_writes_quality_fact():
    from ponymemory.worker import process_conversation
    fake_fact = [{"text": "决定用方案B因为性能更好", "memory_type": "decision", "tags": [], "quality_score": 0.9, "project": "test"}]
    item = {
        "payload": json.dumps([
            {"type": "user", "message": {"content": [{"type": "text", "text": "我们用方案A还是B？方案B性能更好，但复杂度高"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "推荐方案B"}]}},
        ]),
        "project": "test",
        "session_id": "s1",
    }
    with patch("ponymemory.worker.extract_facts", return_value=fake_fact), \
         patch("ponymemory.worker.embed_text", return_value=[0.1]*1024), \
         patch("ponymemory.worker.search_qdrant", return_value=[]), \
         patch("ponymemory.worker.store_qdrant_memory", return_value="point-id"), \
         patch("ponymemory.worker.write_obsidian_entry"):
        process_conversation(item, db_conn=MagicMock())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_worker.py -v`
Expected: FAIL

- [ ] **Step 3: Implement worker.py**

```python
#!/usr/bin/env python3
"""PonyMemory v2 Worker — queue-driven memory processor."""
import json
import logging
import os
import signal
import sys
import time

from db import init_db, claim_next_item, delete_queue_item, mark_failed, reset_stuck_records, store_raw_observation, count_by_status
from extractor import format_conversation, extract_facts, filter_by_quality
from embedder import embed_text, search_qdrant, store_qdrant_memory, check_qdrant_health, check_bge_m3_health
from obsidian_writer import write_obsidian_entry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("ponymemory-worker")

PID_FILE = os.path.expanduser("~/.claude/.ponymemory_worker.pid")
DEDUP_SIMILARITY_THRESHOLD = 0.9
MAINTENANCE_INTERVALS = {
    "dedup": 3600,
    "backfill": 3600,
    "decay": 86400,
    "consolidate": 86400,
}
last_maintenance = {k: 0.0 for k in MAINTENANCE_INTERVALS}


def write_pid_file():
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def process_conversation(item, db_conn=None):
    payload = json.loads(item["payload"]) if isinstance(item["payload"], str) else item["payload"]
    project = item["project"]

    conversation_text = format_conversation(payload)
    if len(conversation_text) < 50:
        return

    facts = extract_facts(conversation_text, project)
    if not facts:
        return

    passed, failed = filter_by_quality(facts)

    for fact in failed:
        if db_conn:
            store_raw_observation(db_conn, item.get("session_id", ""), project, fact.get("text", ""), "low_quality")

    for fact in passed:
        vector = embed_text(fact["text"])
        if vector is None:
            if db_conn:
                store_raw_observation(db_conn, item.get("session_id", ""), project, fact["text"], "embedding_failed")
            continue

        similar = search_qdrant(vector, project, top_k=3)
        if similar and similar[0].get("score", 0) > DEDUP_SIMILARITY_THRESHOLD:
            log.info(f"Dedup: skipping similar memory (score={similar[0]['score']:.2f})")
            continue

        point_id = store_qdrant_memory(fact, vector)
        if point_id:
            log.info(f"Stored memory {point_id}: {fact['memory_type']} for {project}")

        write_obsidian_entry(project, fact)


def process_tool_event(item, db_conn=None):
    # Phase 2 implementation
    pass


def process_mcp_download(item, db_conn=None):
    # Phase 2 implementation
    pass


def maybe_run_maintenance(conn):
    now = time.time()
    for task, interval in MAINTENANCE_INTERVALS.items():
        if now - last_maintenance[task] >= interval:
            last_maintenance[task] = now
            log.info(f"Running maintenance: {task}")
            # Phase 4/5 implementation
            pass


def main_loop():
    conn = init_db()
    write_pid_file()
    log.info("PonyMemory Worker started")

    def shutdown(signum, frame):
        log.info("Shutting down...")
        remove_pid_file()
        conn.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        try:
            reset_stuck_records(conn, threshold_seconds=120)
            item = claim_next_item(conn)

            if item is None:
                time.sleep(1)
                maybe_run_maintenance(conn)
                continue

            try:
                if item["type"] == "conversation":
                    process_conversation(item, db_conn=conn)
                elif item["type"] == "tool_event":
                    process_tool_event(item, db_conn=conn)
                elif item["type"] == "mcp_download":
                    process_mcp_download(item, db_conn=conn)

                delete_queue_item(conn, item["id"])

            except Exception as e:
                log.error(f"Failed to process {item['id']}: {e}")
                mark_failed(conn, item["id"], str(e))

        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main_loop()
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_worker.py -v`
Expected: All PASS

- [ ] **Step 5: Manual smoke test (start worker, write to queue, verify processing)**

```bash
# Terminal 1: start worker
cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python worker.py

# Terminal 2: manually insert a queue item
.venv/bin/python -c "
from db import init_db, write_to_queue
conn = init_db()
write_to_queue(conn, 'test-session', 'ponymemory', 'conversation', [
    {'type': 'user', 'message': {'content': [{'type': 'text', 'text': '我们决定用 SQLite 做队列而不是 Redis，因为不需要额外依赖'}]}},
    {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '同意，SQLite 对个人系统已经足够'}]}}
])
conn.close()
print('Queue item written')
"

# Check worker log for processing
# Check Qdrant: curl localhost:6333/collections/session_memories/points/count
# Check Obsidian: cat ~/pony/obsidian-vault/01-Projects/ponymemory/decisions.md
```

- [ ] **Step 6: Commit**

```bash
cd /Users/jiajun-agent/pony/ponymemory
git add worker.py tests/test_worker.py
git commit -m "feat(v2): add worker main loop with conversation processing"
```

---

### Task 8: Update settings.json (remove timeouts)

**Files:**
- Modify: `~/.claude/settings.json`

- [ ] **Step 1: Remove timeout from Stop Hook**

In `~/.claude/settings.json`, remove `"timeout": 5000` from the ponymemory Stop Hook entry, and `"timeout": 10000` from SessionStart.

- [ ] **Step 2: Verify hooks still trigger**

Start a new Claude Code session, check that hooks fire without errors.

- [ ] **Step 3: Commit settings change**

Note: settings.json is not in a git repo, just verify it works.

---

### Task 9: End-to-end Phase 1 validation

- [ ] **Step 1: Start worker in background**

```bash
cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python worker.py &
```

- [ ] **Step 2: Start a Claude Code session, have a brief conversation with a decision**

- [ ] **Step 3: Verify the pipeline**

```bash
# Check SQLite queue was written
.venv/bin/python -c "from db import init_db, count_by_status; c=init_db(); print('pending:', count_by_status(c,'pending'), 'failed:', count_by_status(c,'failed'))"

# Check Qdrant has new memory
curl -s localhost:6333/collections/session_memories/points/count

# Check Obsidian has new entry
ls -la ~/pony/obsidian-vault/01-Projects/ponymemory/decisions.md
```

- [ ] **Step 4: Commit all Phase 1 work and push**

```bash
cd /Users/jiajun-agent/pony/ponymemory
git add -A
git commit -m "feat(v2): Phase 1 complete - core queue+worker memory system"
git push
```

---

## Phase 2: Tool-Level Capture + Process Management

### Task 10: PostToolUse Hook

**Files:**
- Create: `ponymemory/hooks/post_tool_use.py`

- [ ] **Step 1: Implement post_tool_use.py**

```python
#!/usr/bin/env python3
"""PonyMemory PostToolUse Hook — capture file writes and MCP downloads."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import init_db, write_to_queue, log_exec

WATCHED_TOOLS = {"Write", "Edit"}
WATCHED_MCP = {
    "mcp__gmail-agent__get_email",
    "mcp__google-workspace__download_chat_attachment",
    "mcp__google-workspace__get_drive_file_content",
}
IGNORED_PATHS = ["/tmp/", "_debug", "_temp", "node_modules", ".git/"]


def get_project_name():
    cwd = os.environ.get("CWD", os.getcwd())
    pony_dir = os.path.expanduser("~/pony/")
    if cwd.startswith(pony_dir):
        relative = cwd[len(pony_dir):]
        parts = relative.strip("/").split("/")
        if parts and parts[0]:
            return parts[0]
    return "pony"


def main():
    data = json.loads(sys.stdin.read())
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    session_id = data.get("session_id", "")

    if tool_name in WATCHED_TOOLS:
        file_path = tool_input.get("file_path", "")
        if any(p in file_path for p in IGNORED_PATHS):
            print(json.dumps({}))
            return
        try:
            conn = init_db()
            write_to_queue(conn, session_id, get_project_name(), "tool_event", {
                "tool": tool_name,
                "file_path": file_path,
                "content_preview": tool_input.get("content", "")[:500],
            })
            log_exec(conn, "post_tool_use", session_id, 0, 1)
            conn.close()
        except Exception as e:
            print(f"[PonyMemory] post_tool_use queue write failed: {e}", file=sys.stderr)

    elif tool_name in WATCHED_MCP:
        try:
            conn = init_db()
            write_to_queue(conn, session_id, get_project_name(), "mcp_download", {
                "tool": tool_name,
                "input": tool_input,
                "response": data.get("tool_response", {}),
            })
            log_exec(conn, "post_tool_use", session_id, 0, 1)
            conn.close()
        except Exception as e:
            print(f"[PonyMemory] post_tool_use mcp queue write failed: {e}", file=sys.stderr)

    print(json.dumps({}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[PonyMemory] post_tool_use fatal: {e}", file=sys.stderr)
        print(json.dumps({}))
```

- [ ] **Step 2: Test with mock stdin**

```bash
echo '{"tool_name":"Write","tool_input":{"file_path":"/Users/jiajun-agent/pony/ponymemory/test.md","content":"test"},"session_id":"test","cwd":"/Users/jiajun-agent/pony/ponymemory"}' | python3 hooks/post_tool_use.py
```

- [ ] **Step 3: Commit**

```bash
git add hooks/post_tool_use.py && git commit -m "feat(v2): add PostToolUse hook for file event capture"
```

---

### Task 11: File Router

**Files:**
- Create: `ponymemory/router.py`
- Create: `ponymemory/tests/test_router.py`

- [ ] **Step 1: Write failing tests**

```python
# ponymemory/tests/test_router.py
from ponymemory.router import classify_file

def test_plans_ignored():
    assert classify_file("/Users/jiajun-agent/pony/ponymemory/plans/some-plan.md") == "ignore"

def test_archived_plans_are_spec():
    assert classify_file("/Users/jiajun-agent/pony/ponymemory/plans/_archived/old-plan.md") == "spec"

def test_handoff_ignored():
    assert classify_file("/Users/jiajun-agent/pony/ponymemory/HANDOFF.md") == "ignore"

def test_tmp_ignored():
    assert classify_file("/tmp/scratch.py") == "ignore"

def test_spec_classified():
    assert classify_file("/Users/jiajun-agent/pony/ponymemory/docs/superpowers/specs/2026-03-25-design.md") == "spec"

def test_iterative_report():
    assert classify_file("/Users/jiajun-agent/pony/ponymemory/iterative-reports/round1.md") == "iterative_report"

def test_pdf_is_paper():
    assert classify_file("/Users/jiajun-agent/pony/some-paper.pdf") == "paper"

def test_docx_is_document():
    assert classify_file("/Users/jiajun-agent/pony/report.docx") == "document"

def test_generic_md_is_document():
    assert classify_file("/Users/jiajun-agent/pony/notes.md") == "document"

def test_debug_ignored():
    assert classify_file("/Users/jiajun-agent/pony/output_debug_log.txt") == "ignore"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_router.py -v`
Expected: FAIL

- [ ] **Step 3: Implement router.py with ROUTE_RULES from spec §5.4.6**

- [ ] **Step 4: Run tests**

Run: `cd /Users/jiajun-agent/pony/ponymemory && .venv/bin/python -m pytest tests/test_router.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add router.py tests/test_router.py && git commit -m "feat(v2): add file routing engine"
```

---

### Task 12: launchd Configuration

**Files:**
- Create: `~/Library/LaunchAgents/com.ponymemory.worker.plist`

- [ ] **Step 1: Write plist file per spec §5.4.1**

- [ ] **Step 2: Resolve ANTHROPIC_API_KEY for launchd environment**

launchd does not inherit shell environment variables. Read the key from the existing config and set it for launchd:

```bash
# Extract ANTHROPIC_API_KEY from environment or config
echo $ANTHROPIC_API_KEY

# Set it in the plist by replacing FROM_ENV with the actual key value
# OR use launchctl setenv (persists until reboot):
launchctl setenv ANTHROPIC_API_KEY "sk-ant-..."

# OR modify worker.py to read the key from a file:
# e.g., read from ~/.claude/.anthropic_key at startup
```

Choose whichever method is safest for your setup. The simplest is modifying `worker.py` to read the key from a `.env` file or the existing `~/.claude.json` config.

- [ ] **Step 3: Load and verify**

```bash
launchctl load ~/Library/LaunchAgents/com.ponymemory.worker.plist
launchctl list | grep ponymemory
```

- [ ] **Step 4: Commit**

---

### Task 13: Degradation Chain

- [ ] **Step 1: Add fallback.jsonl writer to worker.py** for when Qdrant is unreachable
- [ ] **Step 2: Add backfill logic** to re-process raw_observations and fallback.jsonl on service recovery
- [ ] **Step 3: Test degradation and recovery**

```bash
# Stop Qdrant (Docker)
docker stop qdrant

# Write a test queue item (should go to fallback.jsonl)
cd /Users/jiajun-agent/pony/ponymemory
.venv/bin/python -c "
from db import init_db, write_to_queue
conn = init_db()
write_to_queue(conn, 'test-degrade', 'test', 'conversation', [
    {'type': 'user', 'message': {'content': [{'type': 'text', 'text': '降级测试'}]}}
])
conn.close()
"

# Wait for worker to attempt processing and write to fallback
sleep 10

# Verify fallback.jsonl exists
cat ~/.claude/.ponymemory_fallback.jsonl

# Restart Qdrant
docker start qdrant
sleep 5

# Wait for worker to detect recovery and backfill
sleep 30

# Verify the memory made it into Qdrant
curl -s localhost:6333/collections/session_memories/points/count
```
- [ ] **Step 4: Commit**

---

### Task 14: Update settings.json with PostToolUse hooks

- [ ] **Step 1: Add PostToolUse entries per spec §8**
- [ ] **Step 2: Verify hooks fire on Write tool usage**
- [ ] **Step 3: End-to-end test: Claude writes a file → appears in Qdrant index**

---

### Task 15: Phase 2 validation + push

```bash
git add -A && git commit -m "feat(v2): Phase 2 complete - PostToolUse + router + launchd + degradation"
git push
```

---

## Phase 3: Read Enhancement

### Task 16: SessionStart Dynamic Query

- [ ] **Step 1: Modify `session_start.py` `build_query()` to read HANDOFF.md and task_plan.md** per spec §5.3
- [ ] **Step 2: Fix injection priority** (HANDOFF first, Qdrant last)
- [ ] **Step 3: Add meta-index injection** with Qdrant collection counts
- [ ] **Step 4: Test by starting new session, verify dynamic context injection**
- [ ] **Step 5: Commit and push**

---

## Phase 4: Smart Enhancement

### Task 17: Daily Consolidation (experience → rules)

- [ ] **Step 1: Implement `run_consolidation()` in worker.py** per spec §5.7
- [ ] **Step 2: Test with 3+ corrections for same project → pending_rules.md generated**
- [ ] **Step 3: Commit**

### Task 18: Zotero Integration

- [ ] **Step 1: Install pyzotero** in .venv
- [ ] **Step 2: Implement PDF detection in ~/files/papers/ → Pyzotero import → Qdrant index**
- [ ] **Step 3: Test with a sample PDF**
- [ ] **Step 4: Commit**

### Task 19: MCP Download Auto-routing

- [ ] **Step 1: Implement `process_mcp_download()` in worker.py** per spec §5.4.5
- [ ] **Step 2: Test Gmail attachment → ~/files/_inbox/**
- [ ] **Step 3: Commit and push**

---

## Phase 5: Observability

### Task 20: Health Endpoint

- [ ] **Step 1: Add HTTP server thread in worker.py** (port 47777, GET /health)
- [ ] **Step 2: Return JSON with Qdrant/BGE-M3 status, queue counts**
- [ ] **Step 3: Test: `curl localhost:47777/health`**
- [ ] **Step 4: Commit**

### Task 21: Execution Audit

- [ ] **Step 1: Create audit script** `scripts/audit_exec.py` that reads exec_log and reports execution rate
- [ ] **Step 2: Test with a day's worth of data**
- [ ] **Step 3: Commit**

### Task 22: Retrieval Quality Benchmark

- [ ] **Step 1: Create golden test set** `tests/golden_queries.json` with 20-30 query→expected memory pairs
- [ ] **Step 2: Create benchmark script** `scripts/test_retrieval.py`
- [ ] **Step 3: Run benchmark, establish baseline**
- [ ] **Step 4: Commit and push**

---

## Final: Cleanup

### Task 23: Remove deprecated code

- [ ] **Step 1: Delete `~/.claude/.ponymemory_response_count`** (global counter no longer needed)
- [ ] **Step 2: Remove PonyWriterX stop hook duplicate memory logic** (if exists)
- [ ] **Step 3: Update ponymemory/CLAUDE.md and ARCHITECTURE.md** to reflect v2
- [ ] **Step 4: Update ponymemory/PRODUCT.md**
- [ ] **Step 5: Final commit and push**

```bash
git add -A && git commit -m "feat(v2): PonyMemory v2 complete - fully code-guaranteed memory system"
git push
```
