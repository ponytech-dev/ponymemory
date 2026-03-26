# PonyMemory 自动化规则详细说明

## 一、Hook 触发矩阵

### SessionStart Hook

| 动作 | 工具 | 条件 | 注入方式 |
|------|------|------|---------|
| 搜索相关记忆 | mem0 | 每次 | additionalContext |
| 读项目状态 | Obsidian | 项目已存在 | additionalContext |
| 创建项目文件夹 | Obsidian | 新项目 | CLAUDE.md 规则 |
| 读 HANDOFF.md | 文件系统 | 文件存在 | additionalContext |
| 检查 pending_rules | 文件系统 | 文件存在 | additionalContext |

**已知限制**：Claude Code 的 SessionStart hook 在全新对话（非恢复）时可能不触发。
**兜底方案**：CLAUDE.md 元规则强制 Claude 在首次响应前执行相同操作。

### PreCompact Hook

| 动作 | 工具 | 条件 |
|------|------|------|
| 保存关键上下文 | mem0 | 每次压缩 |
| 更新任务状态 | Obsidian | 有进行中任务 |
| 注入 HANDOFF.md | 文件系统 | 文件存在 |

### Stop Hook

| 动作 | 工具 | 条件 |
|------|------|------|
| 生成 session 摘要 | Claude API | 每次 |
| 存储到 AI 记忆 | mem0 | 每次 |
| 存储到 Markdown | Basic Memory | 每次 |
| 更新项目状态 | Obsidian | 有进展 |
| 提取规则候选 | Claude API | 检测到纠正/偏好 |
| 条件 Git Push | git | 满足条件时 |
| 清理 HANDOFF | 文件系统 | 任务完成 |
| 标记任务完成 | Obsidian | 任务完成 |

---

## 二、CLAUDE.md 元规则（驱动对话中的自动触发）

以下规则写入全局 ~/pony/CLAUDE.md，替代现有的 Obsidian 长期记忆系统章节：

```markdown
## 自动记忆系统（PonyMemory）

### 核心原则
- mem0 是你的自动大脑，不用想"该不该存"，该存的时候直接存
- Obsidian 是结构化归档，用于决策/计划/发现的持久化
- 搜知识用 Qdrant，搜记忆用 mem0，搜关系用 Cognee

### 自动写入规则（检测到即执行，不用问用户）

| 检测到 | 写 mem0 | 写 Obsidian | 写哪个文件 |
|--------|---------|------------|-----------|
| 用户纠正你的判断 | add_memory(type=correction) | insert | decisions.md |
| 重要技术决策确认 | add_memory(type=decision) | insert | decisions.md |
| 发现 bug 或问题 | add_memory(type=finding) | insert | findings.md |
| 设计方案确认 | add_memory(type=decision) | create | plans/YYYY-MM-DD_{简述}.md |
| 里程碑完成 | add_memory(type=milestone) | str_replace | _project.md status |
| 任务 ≥3 步 | — | create | claude-tasks/{task}/_task.md |
| 迭代循环完成 | add_memory(type=iteration) | create | iterative-reports/ |

### 自动读取规则

| 时机 | 必须读什么 |
|------|----------|
| 新 session 首次响应前 | mem0.search + Obsidian _project.md + decisions.md |
| 开始迭代新一轮 | Obsidian 上一轮报告 + Qdrant 相关论文 |
| 写调研报告前 | Obsidian 历史报告 + Qdrant 论文库 |
| 用户说"记得/之前" | mem0.search + Obsidian search |
| 接触新代码库 | Cognee.codify (如未做过) |

### 新项目检测
每次新 session，检测当前工作目录：
1. get_workspace_files → 检查 01-Projects/{项目名}/
2. 存在 → 这是已有项目的新 session，读取 _project.md
3. 不存在 → 这是新项目，创建：
   - 01-Projects/{项目名}/_project.md
   - 01-Projects/{项目名}/decisions.md
   - 01-Projects/{项目名}/findings.md

### 规则提取元规则
Session 即将结束或 Context 即将压缩时：
1. 扫描对话，检测用户纠正、偏好表达、技术决策
2. 分类：
   - 跨项目通用 → 全局 CLAUDE.md
   - 项目特定 → 项目 CLAUDE.md
   - 个人偏好 → Claude memory/
   - 领域知识 → Obsidian 02-Experience/
3. 以选择题呈现给用户确认后写入
4. 如果用户无响应 → 暂存到 pending_rules.md
```

---

## 三、Obsidian 写入格式规范

### _project.md 模板
```markdown
---
project: {项目名}
status: active | paused | milestone-complete | done
last_updated: YYYY-MM-DD
---

## 项目概要
{一句话描述}

## 当前状态
{最近的进展和状态}

## 关键文件
- `src/main.py` — 主入口
- `tests/` — 测试目录

## 验证状态
- [ ] 测试通过
- [ ] 类型检查通过
- [ ] lint 无警告
```

### decisions.md 格式
```markdown
## YYYY-MM-DD {主题}
- **原判断**：{Claude 的原始判断}
- **用户纠正**：{用户的纠正内容}
- **采纳/拒绝**：{Claude 的决定}
- **理由**：{为什么这样决定}
- **影响范围**：{哪些代码/流程受影响}
```

### findings.md 格式
```markdown
## YYYY-MM-DD {发现主题}
- **类型**：bug | performance | security | design
- **严重度**：high | medium | low
- **描述**：{具体发现}
- **处理**：{已修复/待修复/已记录}
```

### iterative-reports 格式
```markdown
---
round: {轮次}
date: YYYY-MM-DD
status: completed | partial
---

## 目标
{本轮目标}

## 执行摘要
{做了什么}

## 结果
{具体数据}

## 问题和发现
{遇到的问题}

## 下一轮计划
{下一步}
```

---

## 四、Git Push 自动触发详细规则

### 触发条件（满足任一）

```python
def should_push():
    conditions = [
        iteration_complete(),      # 迭代循环完整一轮
        unpushed_commits() >= 3,   # 累积 3+ 未 push 的 commit
        milestone_complete(),      # 功能里程碑完成
        switching_project(),       # 即将切换项目
    ]
    return any(conditions) and all_prechecks_pass()
```

### 前置检查（全部通过才 push）

```python
def all_prechecks_pass():
    checks = [
        tests_pass(),              # pytest / npm test 通过
        type_check_pass(),         # pyright / tsc 通过
        no_debug_code(),           # 无 print() / console.log / debugger
        no_secrets_staged(),       # 无 .env / credentials
        clean_commit_messages(),   # 非 wip/temp/fix
        not_force_push_main(),     # 不是 main 的 force push
    ]
    return all(checks)
```

### 不 push 的情况

```python
def should_not_push():
    return (
        experimental_changes() or  # 实验性修改未验证
        tests_failing() or         # 测试未通过
        only_config_changes()      # 只有配置变更，等代码一起
    )
```

---

## 五、Cron 自动维护

### 维护任务

```cron
# 每周一 03:00 — 清理完成任务
0 3 * * 1 python ~/pony/ponymemory/scripts/weekly_cleanup.py

# 每月 1 日 04:00 — Cognee 图谱维护 + mem0 质量检查
0 4 1 * * python ~/pony/ponymemory/scripts/monthly_maintenance.py

# 每季度 — 提醒用户整合 learned_rules（发通知不自动执行）
0 5 1 1,4,7,10 * python ~/pony/ponymemory/scripts/quarterly_reminder.py
```

### weekly_cleanup.py 逻辑
```
1. Obsidian claude-tasks/ 下 status=done 且 >7天 → 移到 _archive/
2. Basic Memory: 合并同一周的 session 摘要
3. 输出清理报告到日志
```

### monthly_maintenance.py 逻辑
```
1. Cognee: prune() 清理低质量节点
2. mem0: 统计记忆数量
   - >1000 条 → 删除从未被检索且 >90天的记忆
3. Obsidian _session_summaries/ >90天 → 移到 _Archive/
4. 输出维护报告
```

---

## 六、错误处理和降级

### 服务不可用时的降级策略

| 服务 | 不可用时的降级 | 恢复后的动作 |
|------|--------------|------------|
| Qdrant (Docker) | mem0/Cognee/Qdrant MCP 全部不可用，仅靠 Obsidian + MEMORY.md | 重启 Docker |
| Embedding 服务 | mem0 无法写入新记忆，但可读已有 | 重启 embedding service |
| Obsidian | Obsidian MCP 不可用，记忆暂存到 pending 文件 | Obsidian 启动后补写 |
| mem0 MCP | 记忆不存储，不影响其他工具 | 下次 session 自动恢复 |
| Cognee MCP | 图谱功能不可用，用 Qdrant 向量搜索替代 | 按需 |

### Hook 失败处理

```
Hook 失败 → 记录错误到 ~/pony/ponymemory/logs/
不阻塞用户操作 → async: true
下次 session 启动时 → 检查 pending 操作并补执行
```
