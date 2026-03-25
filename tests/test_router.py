from router import classify_file

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
    assert classify_file("/Users/jiajun-agent/pony/ponylabASMS/iterative-reports/round1.md") == "iterative_report"

def test_pdf_is_paper():
    assert classify_file("/Users/jiajun-agent/pony/some-paper.pdf") == "paper"

def test_docx_is_document():
    assert classify_file("/Users/jiajun-agent/pony/report.docx") == "document"

def test_generic_md_is_document():
    assert classify_file("/Users/jiajun-agent/pony/notes.md") == "document"

def test_debug_ignored():
    assert classify_file("/Users/jiajun-agent/pony/output_debug_log.txt") == "ignore"

def test_ponywriterx_output_is_paper():
    assert classify_file("/Users/jiajun-agent/pony/ponywriterX/output/paper.docx") == "paper"

def test_task_plan_ignored():
    assert classify_file("/Users/jiajun-agent/pony/ponymemory/task_plan.md") == "ignore"

def test_findings_ignored():
    assert classify_file("/Users/jiajun-agent/pony/ponymemory/findings.md") == "ignore"
