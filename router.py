import fnmatch

ROUTE_RULES = [
    ("*/plans/_archived/*", "spec"),
    ("*/plans/*", "ignore"),
    ("/tmp/*", "ignore"),
    ("*_debug*", "ignore"),
    ("*_temp*", "ignore"),
    ("*HANDOFF.md", "ignore"),
    ("*task_plan.md", "ignore"),
    ("*progress.md", "ignore"),
    ("*findings.md", "ignore"),
    ("*/docs/superpowers/specs/*", "spec"),
    ("*/docs/superpowers/plans/*", "spec"),
    ("*/iterative-reports/*", "iterative_report"),
    ("*/ponywriterX/output/*", "paper"),
    ("*.pdf", "paper"),
    ("*.docx", "document"),
    ("*.md", "document"),
]


def classify_file(file_path):
    """Classify a file path into a route category.

    Returns one of: 'ignore', 'spec', 'iterative_report', 'paper', 'document'
    Rules are evaluated in order, first match wins.
    """
    for pattern, route in ROUTE_RULES:
        if fnmatch.fnmatch(file_path, pattern):
            return route
    return "document"  # default fallback
