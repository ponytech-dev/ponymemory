# PonyMemory 可靠性推进计划
创建时间：2026-03-21
状态：待执行

---

## 问题诊断汇总（调查结果）

### 已确认根因

| 问题 | 根因 | 严重度 |
|------|------|--------|
| session_start.py 搜索恒为空 | `embed_text()` 发送 `{"text":...}`，但 embed server 接受 `{"texts":[...]}` 返回 `{"embeddings":[...]}`。400 错误导致向量为 None，搜索跳过 | **P0 — 功能失效** |
| session_start 空输出 | embed 失败 → `search_qdrant_memories` 返回 [] → Obsidian 路径硬编码 `~/pony/obsidian-vault/` 正确但 CWD 未注入，project_name 推断为 "pony" → 无匹配文件 | P1 |
| Obsidian L4 部分为空 | Stop hook 是"提醒"模式，Claude 经常跳过写入。spaflow/decisions.md 只有 frontmatter，ponylab/ponymemory 的 decisions.md 根本不存在 | P1 |
| Stop hook 执行率低 | 架构限制：hook 输出是 additionalContext，Claude 看不到对话内容，依赖 Claude 自觉响应提醒 | P1 |
| Neo4j | MCP server 代码已移除，无容器，无 hooks 引用。**代码层面已完成，只差 CLAUDE.md/memory 清理** | P2 |
| .active_session 锁机制 | 锁文件检查代码存在且正确，但从未实际创建过锁文件。需要验证 PonyWriterX hooks 是否正确创建/删除锁 | P2 |
| 34 条记忆（实际数） | 数量少的真正原因是 session_start 搜索失效（P0），导致 Claude 以为记忆系统不工作，减少了主动存储 | P1 |

---

## Phase 1 — 修复 session_start embed 格式（P0，~5分钟）

**类型：写代码修复**

**问题**：`embed_text()` 使用 `{"text": text}` + 读 `result.get("embedding") or result.get("vector")`，但 embed server 实际 API 是 `{"texts": [text]}` + `{"embeddings": [vec]}`。

**修复位置**：`/Users/jiajun-agent/pony/ponymemory/hooks/session_start.py`，`embed_text()` 函数（第 42-56 行）

**修复内容**：
```python
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
```

**验证命令**：
```bash
# 验证 embed 格式正确
python3 -c "
import urllib.request, json
payload = json.dumps({'texts': ['test query']}).encode()
req = urllib.request.Request('http://localhost:8999/embed', data=payload, headers={'Content-Type':'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=10) as r:
    d = json.loads(r.read())
    print('OK, vec len:', len(d['embeddings'][0]))
"

# 验证 session_start 有输出
CWD=/Users/jiajun-agent/pony/ponymemory python3 /Users/jiajun-agent/pony/ponymemory/hooks/session_start.py | python3 -c "import json,sys; d=json.load(sys.stdin); print('ctx len:', len(d.get('additionalContext','')))"
# 期望: ctx len > 0
```

---

## Phase 2 — 修复 session_start project_name 推断（P1，~3分钟）

**类型：写代码修复**

**问题**：`get_project_name()` 用 `os.environ.get("CWD", os.getcwd())` 推断项目名。实际 Claude Code hooks 环境中 `CWD` 环境变量不一定存在或正确，导致推断为 "pony"，Obsidian 读取失败。

**修复**：增加 fallback 逻辑，当项目名为 "pony" 时改为搜索全局记忆（不过滤项目），同时验证 embed 修好后 Obsidian 读取是否可用。

**修复位置**：`session_start.py` 第 59-98 行，`search_qdrant_memories()` 的 filter 改为包含所有记忆：

```python
def search_qdrant_memories(project_name):
    query_text = f"{project_name} recent work decisions corrections"
    vector = embed_text(query_text)
    if not vector:
        return []

    try:
        # 当 project_name 为 "pony"（推断失败）时，不过滤项目，拉全局记忆
        filter_clause = None
        if project_name != "pony":
            filter_clause = {
                "must": [{"key": "project", "match": {"value": project_name}}]
            }

        payload = json.dumps({
            "vector": vector,
            "limit": 10,
            "with_payload": True,
            "filter": filter_clause,
        }).encode("utf-8")
        # ... 其余不变
```

**验证命令**：
```bash
# 不设 CWD，模拟推断失败场景
python3 /Users/jiajun-agent/pony/ponymemory/hooks/session_start.py | python3 -c "import json,sys; d=json.load(sys.stdin); print('ctx len:', len(d.get('additionalContext','')))"
# 期望: ctx len > 0（返回全局记忆）
```

---

## Phase 3 — 创建缺失的 Obsidian decisions.md（P1，~5分钟）

**类型：验证/创建文件**

**问题**：ponylab 和 ponymemory 没有 decisions.md；spaflow/decisions.md 只有 frontmatter。

**操作**：

```bash
# 创建 ponymemory/decisions.md
cat > /Users/jiajun-agent/pony/obsidian-vault/01-Projects/ponymemory/decisions.md << 'EOF'
---
title: PonyMemory Decisions
type: decisions
---

# PonyMemory 决策记录

## 2026-03-20 — 移除 Neo4j，改为 Qdrant payload 图查询
- 决策：Neo4j 冗余，Qdrant payload 可实现同等图查询（neighbors/stats）
- 原因：运维复杂度减半，无性能损失
- 影响：graph_get_entity/graph_query 工具已改为纯 Qdrant 实现

## 2026-03-20 — Stop hook 改为"降噪+强制"模式
- 决策：每次响应注入简洁记忆检查指令，每5轮触发规则提取，每10轮触发维护
- 原因：旧版每轮输出大量文本，Claude 忽略率高
- 影响：提醒精简，聚焦核心动作

## 2026-03-21 — embed_text() 修复
- 决策：session_start.py embed 格式从 {"text":...} 改为 {"texts":[...]}，读 embeddings[0]
- 原因：embed server 实际 API 不匹配，导致向量恒为 None，Qdrant 搜索全部失效
EOF

# 创建 ponylab/decisions.md
cat > /Users/jiajun-agent/pony/obsidian-vault/01-Projects/ponylab/decisions.md << 'EOF'
---
title: PonyLab Decisions
type: decisions
---

# PonyLab 决策记录

（待补充）
EOF
```

**验证命令**：
```bash
wc -l /Users/jiajun-agent/pony/obsidian-vault/01-Projects/ponymemory/decisions.md
wc -l /Users/jiajun-agent/pony/obsidian-vault/01-Projects/ponylab/decisions.md
# 期望：两者均 >5 行
```

---

## Phase 4 — Stop hook 改造：降低"提醒"依赖（P1，~10分钟）

**类型：写代码修复**

**核心问题**：stop hook 架构上无法访问对话内容，只能输出提醒文本。"提醒"模式依赖 Claude 自觉执行，执行率不可控。

**可行的改进方向**（不改变 hooks 架构限制）：

### 4a. 强化提醒可见度
现有 stop hook 每轮都注入，但内容格式让 Claude 容易"扫读跳过"。改为：
- 加粗 + 分行，减少视觉噪音
- 明确要求 Claude 在**下一条回复开头**确认是否有内容需要存储

修改 `stop.py` 第 73-80 行记忆检查提醒：
```python
sections.append(
    "**[PonyMemory 记忆检查]** 本轮是否发生：用户纠正 / 技术决策 / 新发现 / 里程碑？\n"
    f"→ 有：search_memories 查重 → store_memory(project=\"{project_name}\") | update_memory\n"
    "→ 无：跳过。格式：50-200字，含 what+why+impact。纠正/决策同步写 Obsidian decisions.md。"
)
```

### 4b. 降低每轮强度，提高关键事件识别
当前每轮都注入记忆检查，会造成"狼来了"效应。改为：
- 每轮只注入 1 行简短提示
- 每 3 轮注入一次完整检查指令

```python
if count % 3 == 0:
    sections.append(full_memory_check_instruction)
else:
    sections.append("💾 PonyMemory: 本轮有重要内容？→ store_memory")
```

**验证命令**：
```bash
# 验证 stop hook 能正常输出
echo '{}' | python3 /Users/jiajun-agent/pony/ponymemory/hooks/stop.py | python3 -c "import json,sys; d=json.load(sys.stdin); print('ctx len:', len(d.get('additionalContext','')))"
# 期望: ctx len > 50
```

---

## Phase 5 — 验证 .active_session 锁机制（P2，~5分钟）

**类型：验证/测试**

**问题**：PonyWriterX hooks 和 PonyMemory hooks 通过 `~/.ponywriterx/.active_session` 互斥，但锁文件从未在实际中创建过。

**验证步骤**：

```bash
# 1. 检查 PonyWriterX session_start.py 是否创建锁
grep -n "active_session\|lock\|LOCK" /Users/jiajun-agent/.ponywriterx/hooks/session_start.py

# 2. 检查 PonyWriterX stop.py 是否删除锁
grep -n "active_session\|lock\|LOCK" /Users/jiajun-agent/.ponywriterx/hooks/stop.py

# 3. 手动测试锁互斥
touch ~/.ponywriterx/.active_session
CWD=/Users/jiajun-agent/pony/ponymemory python3 /Users/jiajun-agent/pony/ponymemory/hooks/session_start.py
# 期望：输出 {}（让位给 PonyWriterX）
rm ~/.ponywriterx/.active_session

# 4. 确认无锁时 PonyMemory hooks 正常运行
CWD=/Users/jiajun-agent/pony/ponymemory python3 /Users/jiajun-agent/pony/ponymemory/hooks/session_start.py | python3 -c "import json,sys; d=json.load(sys.stdin); print('has ctx:', 'additionalContext' in d)"
# 期望：has ctx: True
```

**如果 PonyWriterX hooks 未创建锁**：在 `~/.ponywriterx/hooks/session_start.py` 开头添加创建锁逻辑，stop.py 末尾添加删除锁逻辑。

---

## Phase 6 — 清理 Neo4j 残留文档（P2，~3分钟）

**类型：清理/删除**

**状态**：代码层面已完成（MCP server 已移除 Neo4j，hooks 无引用，无容器）。只需清理文档引用。

**操作**：

```bash
# 检查 CLAUDE.md 和 ARCHITECTURE.md 是否有 Neo4j 引用
grep -n "Neo4j\|neo4j" /Users/jiajun-agent/pony/ponymemory/CLAUDE.md
grep -n "Neo4j\|neo4j" /Users/jiajun-agent/pony/ponymemory/ARCHITECTURE.md

# 检查全局 CLAUDE.md
grep -n "Neo4j\|neo4j" /Users/jiajun-agent/pony/CLAUDE.md

# 需要将上述文件中的 Neo4j 相关描述更新为：
# "图查询通过 Qdrant payload 实现（graph_get_entity/graph_query 工具），无需独立 Neo4j 服务"
```

**验证命令**：
```bash
grep -rn "Neo4j\|neo4j" /Users/jiajun-agent/pony/ponymemory/ | grep -v ".pyc"
# 期望：只剩注释性描述，无"Neo4j（Docker :7687）"类运维指令
```

---

## Phase 7 — 端到端验证（~5分钟）

**类型：验证/测试**

完成 Phase 1-6 后，做完整冒烟测试：

```bash
# Test 1: session_start 返回非空 context
CWD=/Users/jiajun-agent/pony/ponylabASMS python3 /Users/jiajun-agent/pony/ponymemory/hooks/session_start.py \
  | python3 -c "import json,sys; d=json.load(sys.stdin); ctx=d.get('additionalContext',''); print('ctx lines:', len(ctx.splitlines())); print(ctx[:500])"

# Test 2: stop hook 正常运行
echo '{}' | python3 /Users/jiajun-agent/pony/ponymemory/hooks/stop.py \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('ok, ctx len:', len(d.get('additionalContext','')))"

# Test 3: pre_compact hook 正常运行
echo '{}' | python3 /Users/jiajun-agent/pony/ponymemory/hooks/pre_compact.py \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('ok, ctx len:', len(d.get('additionalContext','')))"

# Test 4: Qdrant 当前记忆数量
curl -s http://localhost:6333/collections/session_memories | python3 -c "import json,sys; print('points:', json.load(sys.stdin)['result']['points_count'])"
```

**通过标准**：
- Test 1 输出 >10 行 context，包含 Qdrant 记忆条目
- Test 2/3 输出 ctx len > 50
- Test 4 点数 ≥ 34（不减少）

---

## 执行顺序

```
Phase 1 (P0, 5min) → Phase 2 (P1, 3min) → Phase 7 部分验证
Phase 3 (P1, 5min) → Phase 4 (P1, 10min)
Phase 5 (P2, 5min) → Phase 6 (P2, 3min)
Phase 7 完整验证
```

总计估时：~36 分钟

---

## 遗留问题（架构层面，本计划不覆盖）

1. **Stop hook "提醒"模式根本矛盾**：hooks 架构固有限制，hook 看不到对话内容。Phase 4 的改进是在限制内的最优解。若要彻底解决，需要 Claude Code 官方支持 PostResponse hook 并传入对话摘要——目前不可行。

2. **Qdrant 记忆增长缓慢**：修复 session_start 后，Claude 会在 session 开始看到记忆存在，应能提高主动存储率。但根本上仍依赖 Claude 响应 stop hook 提醒——接受此限制。

3. **L4 Obsidian 自动写入**：findings.md 完全空置问题。findings 对应的是"审查发现/bug"，当前工作流中 Claude 从未被触发写这类内容。可在 stop hook 提醒中增加 findings 检查——作为 Phase 4 的扩展项。
