"""
Hippo pipeline scripts package.

Entry points:
  scripts/ingest.py   -- bronze layer: copy raw JSONL sessions from ~/.claude/projects/
  scripts/compact.py  -- silver layer: compact bronze sessions via claude -p
  scripts/extract.py  -- gold layer:   extract knowledge entries from silver summaries
  scripts/manifest.py -- shared I/O helpers for manifest.jsonl (used by all three)

Run each script from the project root:
  python scripts/ingest.py [--dry-run]
  python scripts/compact.py [--dry-run]
  python scripts/extract.py [--dry-run]
"""
