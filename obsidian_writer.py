"""obsidian_writer.py — Write directly to Obsidian vault files.

No MCP dependency. Obsidian app does not need to be running.
Writes are plain filesystem appends under DEFAULT_VAULT / 01-Projects/{project}/.
"""
import os
from datetime import datetime, timezone

DEFAULT_VAULT = os.path.expanduser("~/pony/obsidian-vault/")

# Maps memory_type -> filename within 01-Projects/{project}/
TYPE_TO_FILE: dict[str, str] = {
    "correction": "decisions.md",
    "decision": "decisions.md",
    "preference": "decisions.md",
    "finding": "findings.md",
    "milestone": "_project.md",
}


def _now_iso() -> str:
    """Return current UTC timestamp as ISO-8601 string (second precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _project_dir(project: str, vault_path: str) -> str:
    return os.path.join(vault_path, "01-Projects", project)


def write_obsidian_entry(project: str, fact: dict, vault_path: str | None = None) -> None:
    """Route *fact* to the correct Obsidian markdown file.

    Args:
        project:    Project name — becomes the directory under 01-Projects/.
        fact:       Dict with at least 'memory_type' and 'text' keys.
        vault_path: Override vault root (used in tests). Defaults to DEFAULT_VAULT.
    """
    vault = vault_path or DEFAULT_VAULT
    mtype = fact.get("memory_type", "decision")
    text = fact.get("text", "")
    timestamp = _now_iso()

    filename = TYPE_TO_FILE.get(mtype, "decisions.md")
    proj_dir = _project_dir(project, vault)
    os.makedirs(proj_dir, exist_ok=True)

    target = os.path.join(proj_dir, filename)

    if filename == "_project.md":
        # Milestone format: single bullet line
        line = f"- ✅ {timestamp}: {text}\n"
        # Only write if file already exists (per spec)
        if not os.path.exists(target):
            return
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)
    else:
        # decisions.md / findings.md: section header format
        block = f"\n## {timestamp} [{mtype}]\n\n{text}\n"
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(block)


def write_obsidian_milestone(
    project: str, description: str, vault_path: str | None = None
) -> None:
    """Append a milestone line to _project.md.

    Silently does nothing if _project.md does not exist.

    Args:
        project:     Project name.
        description: Human-readable milestone description.
        vault_path:  Override vault root (used in tests). Defaults to DEFAULT_VAULT.
    """
    vault = vault_path or DEFAULT_VAULT
    target = os.path.join(_project_dir(project, vault), "_project.md")

    if not os.path.exists(target):
        return

    timestamp = _now_iso()
    line = f"- ✅ {timestamp}: {description}\n"
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(line)
