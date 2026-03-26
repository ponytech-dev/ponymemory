"""Tests for obsidian_writer.py — TDD, written before implementation."""
import os
import tempfile
import pytest

from obsidian_writer import write_obsidian_entry, write_obsidian_milestone


def make_fact(memory_type: str, text: str) -> dict:
    return {"memory_type": memory_type, "text": text}


class TestWriteDecision:
    def test_write_decision(self):
        with tempfile.TemporaryDirectory() as vault:
            fact = make_fact("decision", "Use FastAPI over Flask")
            write_obsidian_entry("TestProject", fact, vault_path=vault)

            target = os.path.join(vault, "01-Projects", "TestProject", "decisions.md")
            assert os.path.exists(target), "decisions.md should be created"
            content = open(target).read()
            assert "[decision]" in content
            assert "Use FastAPI over Flask" in content


class TestWriteFinding:
    def test_write_finding(self):
        with tempfile.TemporaryDirectory() as vault:
            fact = make_fact("finding", "Memory leak in qdrant client v1.2")
            write_obsidian_entry("TestProject", fact, vault_path=vault)

            target = os.path.join(vault, "01-Projects", "TestProject", "findings.md")
            assert os.path.exists(target), "findings.md should be created"
            content = open(target).read()
            assert "[finding]" in content
            assert "Memory leak in qdrant client v1.2" in content


class TestWriteMilestone:
    def test_write_milestone(self):
        with tempfile.TemporaryDirectory() as vault:
            # Pre-create _project.md as required by spec
            project_dir = os.path.join(vault, "01-Projects", "TestProject")
            os.makedirs(project_dir, exist_ok=True)
            project_file = os.path.join(project_dir, "_project.md")
            open(project_file, "w").write("# TestProject\n")

            fact = make_fact("milestone", "v1.0 shipped to production")
            write_obsidian_entry("TestProject", fact, vault_path=vault)

            content = open(project_file).read()
            assert "v1.0 shipped to production" in content
            assert "✅" in content


class TestWriteCorrection:
    def test_write_correction(self):
        with tempfile.TemporaryDirectory() as vault:
            fact = make_fact("correction", "Qdrant host should be localhost not 127.0.0.1")
            write_obsidian_entry("TestProject", fact, vault_path=vault)

            target = os.path.join(vault, "01-Projects", "TestProject", "decisions.md")
            assert os.path.exists(target), "decisions.md should be created for correction"
            content = open(target).read()
            assert "[correction]" in content
            assert "Qdrant host should be localhost not 127.0.0.1" in content


class TestCreatesDirectories:
    def test_creates_directories(self):
        with tempfile.TemporaryDirectory() as vault:
            # Project directory does NOT exist yet
            project_dir = os.path.join(vault, "01-Projects", "BrandNewProject")
            assert not os.path.exists(project_dir)

            fact = make_fact("preference", "Always use black formatter")
            write_obsidian_entry("BrandNewProject", fact, vault_path=vault)

            assert os.path.isdir(project_dir), "Project dir should be auto-created"
            target = os.path.join(project_dir, "decisions.md")
            assert os.path.exists(target)


class TestWriteObsidianMilestone:
    def test_write_obsidian_milestone_appends(self):
        with tempfile.TemporaryDirectory() as vault:
            project_dir = os.path.join(vault, "01-Projects", "MyProject")
            os.makedirs(project_dir, exist_ok=True)
            project_file = os.path.join(project_dir, "_project.md")
            open(project_file, "w").write("# MyProject\n")

            write_obsidian_milestone("MyProject", "Phase 2 complete", vault_path=vault)

            content = open(project_file).read()
            assert "Phase 2 complete" in content
            assert "✅" in content

    def test_write_obsidian_milestone_no_file_no_op(self):
        """write_obsidian_milestone should silently skip if _project.md doesn't exist."""
        with tempfile.TemporaryDirectory() as vault:
            # No _project.md created
            write_obsidian_milestone("Ghost", "Should not crash", vault_path=vault)
            target = os.path.join(vault, "01-Projects", "Ghost", "_project.md")
            assert not os.path.exists(target), "_project.md must NOT be created by milestone writer"
