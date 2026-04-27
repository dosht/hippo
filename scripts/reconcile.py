"""
scripts/reconcile.py -- Gold layer reconciliation (M4).

Two subcommands:

  cluster (S4.2): identify "new" gold entries (git diff against the last
                  `M4/reconcile:` commit, or all with --full), query QMD for
                  top-K neighbors per entry, union-find merge overlapping
                  neighbor sets into clusters, write reconcile-clusters.jsonl.
  judge (S4.3):   read reconcile-clusters.jsonl, call `claude -p` with the
                  reconcile prompt to classify each cluster member as
                  duplicate / contradiction / related / unrelated (or flag
                  for sub-agent re-judging), write reconcile-proposals.jsonl.

Apply step (S4.4) and staleness sweep (S4.5) are pending.

Usage:
  python -m scripts.reconcile cluster [--full] [--gold-dir DIR] [--out PATH]
                                      [--top-k N] [--threshold F] [--dry-run]
  python -m scripts.reconcile judge [--clusters PATH] [--gold-dir DIR]
                                    [--output PATH] [--prompt PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path


EXTRACT_GIT_TAG = "M4/reconcile:"

QMD_COLLECTION = "hippo"

DEFAULT_TOP_K = 5
DEFAULT_SIMILARITY_THRESHOLD = 0.5

QUERY_BODY_CHAR_CAP = 3000

# Judge step (S4.3): claude -p invocation policy mirrors extract.py.
JUDGE_MODEL = "claude-sonnet-4-5"
JUDGE_MAX_TURNS = 1
BACKOFF_BASE_SECONDS = 2
BACKOFF_MAX_RETRIES = 3

DEFAULT_GOLD_DIR = Path(
    os.environ.get("HIPPO_GOLD_DIR")
    or Path(__file__).parent.parent / "gold" / "entries"
)
DEFAULT_OUT = Path(__file__).parent.parent / "reconcile-clusters.jsonl"
DEFAULT_PROPOSALS = Path(__file__).parent.parent / "reconcile-proposals.jsonl"
DEFAULT_RECONCILE_PROMPT = Path(__file__).parent / "prompts" / "reconcile.md"
DEFAULT_RETIRED_DIR = Path(
    os.environ.get("HIPPO_RETIRED_DIR")
    or Path(__file__).parent.parent / "gold" / "_retired"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("reconcile")


# ---------------------------------------------------------------------------
# Step 1: identify new entries
# ---------------------------------------------------------------------------

def last_reconcile_commit(repo_root: Path) -> str | None:
    """Return SHA of the most recent commit whose subject starts with
    `M4/reconcile:`, or None if no such commit exists."""
    try:
        out = subprocess.check_output(
            ["git", "log", "--grep", f"^{re.escape(EXTRACT_GIT_TAG)}", "-n", "1", "--format=%H"],
            cwd=repo_root, text=True,
        ).strip()
        return out or None
    except subprocess.CalledProcessError:
        return None


def find_new_entries(gold_dir: Path, full: bool) -> list[Path]:
    """List gold entries to reconcile. With --full, return everything;
    otherwise diff gold/entries/ against the last reconcile commit (or
    treat all entries as new if there's no prior reconcile commit)."""
    all_entries = sorted(gold_dir.glob("*.md"))
    if full:
        return all_entries

    repo_root = gold_dir.parent.parent
    last = last_reconcile_commit(repo_root)
    if last is None:
        log.info("No prior M4/reconcile: commit found, treating all entries as new.")
        return all_entries

    rel_dir = gold_dir.relative_to(repo_root)
    out = subprocess.check_output(
        ["git", "diff", "--name-only", "--diff-filter=AM", f"{last}..HEAD", "--", str(rel_dir)],
        cwd=repo_root, text=True,
    )
    changed = [repo_root / p for p in out.splitlines() if p.endswith(".md")]
    return [p for p in changed if p.exists()]


# ---------------------------------------------------------------------------
# Step 2: build query text + call QMD
# ---------------------------------------------------------------------------

def parse_entry(path: Path) -> dict:
    """Return {id, title, topics, type, agents, summary, body} from a gold entry.
    `summary` is the empty string when the field is absent (entries pre-S4.8)."""
    text = path.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not fm_match:
        return {"id": path.stem, "title": "", "topics": [], "type": "",
                "agents": [], "summary": "", "body": text.strip()}
    fm_raw, body = fm_match.group(1), fm_match.group(2)

    def get_scalar(key: str) -> str:
        m = re.search(rf"^{key}:\s*(.+)$", fm_raw, re.MULTILINE)
        return m.group(1).strip() if m else ""

    def get_list(key: str) -> list[str]:
        m = re.search(rf"^{key}:\s*\[(.*?)\]", fm_raw, re.MULTILINE)
        if not m:
            return []
        return [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]

    h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    title = h1.group(1).strip() if h1 else get_scalar("id")

    return {
        "id": get_scalar("id") or path.stem,
        "title": title,
        "topics": get_list("topics"),
        "type": get_scalar("type"),
        "agents": get_list("agents"),
        "summary": get_scalar("summary"),
        "body": body.strip(),
    }


def build_query_text(entry: dict) -> str:
    """Concatenate semantic signals for QMD into a single line. QMD auto-
    expands single-line queries into hybrid lex+vec+hyde sub-queries, so
    we just space-join the signals. Newlines would trigger QMD's structured
    multi-line query mode which requires per-line lex:/vec:/hyde: prefixes.
    Title and topics first so BM25 weights them; prefers `summary` (≤140 chars,
    precise) when present, falls back to body-stuffing for entries that predate
    the summary field."""
    summary = entry.get("summary", "")
    if summary:
        body_part = summary
    else:
        body_part = " ".join(entry["body"][:QUERY_BODY_CHAR_CAP].split())
    parts = [
        entry["title"],
        " ".join(entry["topics"]),
        entry["type"],
        " ".join(entry["agents"]),
        body_part,
    ]
    return " ".join(p for p in parts if p)


def qmd_query(text: str, n: int) -> list[dict]:
    """Call `qmd query` and return parsed JSON results, or [] on failure."""
    try:
        out = subprocess.check_output(
            ["qmd", "query", text, "--collection", QMD_COLLECTION, "--json", "-n", str(n)],
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.warning("qmd query failed: %s", exc)
        return []

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        log.warning("qmd returned non-JSON output")
        return []
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, list):
        return data
    return []


def neighbors_for(entry: dict, top_k: int, threshold: float) -> list[tuple[str, float]]:
    """Return [(neighbor_id, score), ...] for the entry, excluding self
    and below-threshold matches."""
    raw = qmd_query(build_query_text(entry), n=top_k + 1)
    out: list[tuple[str, float]] = []
    for r in raw:
        # QMD result shape: {docid, score, file: "qmd://hippo/mem-foo.md", title, snippet}
        rid = r.get("id") or Path(r.get("file", r.get("path", ""))).stem
        if not rid or rid == entry["id"]:
            continue
        score = float(r.get("score", r.get("similarity", 0.0)))
        if score < threshold:
            continue
        out.append((rid, score))
        if len(out) >= top_k:
            break
    return out


# ---------------------------------------------------------------------------
# Step 3: union-find clustering
# ---------------------------------------------------------------------------

class DSU:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def cluster(neighbor_map: dict[str, list[tuple[str, float]]]) -> list[set[str]]:
    """Group entries into clusters: each new-entry's neighbor set is one
    initial group; groups sharing any member are merged."""
    dsu = DSU()
    for new_id, neighbors in neighbor_map.items():
        for nid, _ in neighbors:
            dsu.union(new_id, nid)
        # ensure isolated new entries (no neighbors) still appear
        dsu.find(new_id)

    groups: dict[str, set[str]] = defaultdict(set)
    for member in dsu.parent:
        groups[dsu.find(member)].add(member)
    return list(groups.values())


# ---------------------------------------------------------------------------
# Step 4: write clusters
# ---------------------------------------------------------------------------

def write_clusters(
    clusters: list[set[str]],
    new_ids: set[str],
    score_map: dict[tuple[str, str], float],
    out_path: Path,
) -> int:
    written = 0
    with out_path.open("w") as f:
        for i, members in enumerate(sorted(clusters, key=lambda s: (-len(s), sorted(s)[0])), start=1):
            triggers = sorted(members & new_ids)
            if not triggers:
                continue  # cluster has no new entry — skip (nothing to reconcile)
            scores = {}
            for t in triggers:
                for m in members:
                    if m == t:
                        continue
                    s = score_map.get((t, m)) or score_map.get((m, t))
                    if s is not None:
                        scores[f"{t}->{m}"] = round(s, 3)
            record = {
                "cluster_id": f"c{i:03d}",
                "size": len(members),
                "members": sorted(members),
                "trigger_entries": triggers,
                "scores": scores,
            }
            f.write(json.dumps(record) + "\n")
            written += 1
    return written


# ---------------------------------------------------------------------------
# S4.3 — Judge step: classify clusters via claude -p
# ---------------------------------------------------------------------------

def load_clusters(clusters_path: Path) -> list[dict]:
    if not clusters_path.exists():
        raise FileNotFoundError(f"Clusters file not found: {clusters_path}")
    out = []
    with clusters_path.open() as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {lineno} of {clusters_path}: {exc}") from exc
    return out


def load_entry_content(entry_id: str, gold_dir: Path) -> str | None:
    p = gold_dir / f"{entry_id}.md"
    return p.read_text() if p.exists() else None


def build_cluster_block(cluster: dict, gold_dir: Path) -> str:
    cid = cluster.get("cluster_id", "unknown")
    members = cluster.get("members", [])
    scores = cluster.get("scores", {})
    if isinstance(scores, dict):
        score_str = ", ".join(f"{k}={v}" for k, v in scores.items()) if scores else "n/a"
    else:
        score_str = ", ".join(str(round(s, 3)) for s in scores) if scores else "n/a"
    lines = [f"=== CLUSTER {cid} ===",
             f"Members: {', '.join(members)}",
             f"Similarity scores: {score_str}",
             ""]
    for m in members:
        content = load_entry_content(m, gold_dir)
        lines.append(f"--- ENTRY: {m} ---")
        lines.append(content if content else f"(entry file not found: {gold_dir}/{m}.md)")
        lines.append("")
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def build_judge_prompt(prompt_template: str, clusters: list[dict], gold_dir: Path) -> str:
    blocks = [build_cluster_block(c, gold_dir) for c in clusters]
    return prompt_template.strip() + "\n\n## Clusters to judge\n\n" + "\n".join(blocks)


def parse_proposals(output: str) -> list[dict]:
    required = {"cluster_id", "action", "primary", "others", "rationale"}
    valid_actions = {"duplicate", "contradiction", "related", "unrelated", "subagent-needed"}
    out = []
    for lineno, line in enumerate(output.splitlines(), 1):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            log.warning("Could not parse JSON on output line %d: %r", lineno, line[:80])
            continue
        missing = required - set(obj.keys())
        if missing:
            raise ValueError(f"Proposal on line {lineno} missing required fields: {missing}")
        if obj["action"] not in valid_actions:
            raise ValueError(f"Proposal on line {lineno} has invalid action {obj['action']!r}")
        out.append(obj)
    return out


def call_claude_judge(prompt: str, dry_run: bool) -> str:
    if dry_run:
        return ""
    attempts = 0
    last_error: Exception | None = None
    while attempts <= BACKOFF_MAX_RETRIES:
        if attempts > 0:
            time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)))
        result = subprocess.run(
            ["claude", "-p", "--strict-mcp-config", "--max-turns", str(JUDGE_MAX_TURNS),
             "--model", JUDGE_MODEL, prompt],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            return result.stdout or ""
        stderr = result.stderr or ""
        is_rate_limit = result.returncode == 429 or "rate limit" in stderr.lower() or "429" in stderr.lower()
        is_silent = result.returncode != 0 and stderr.strip() == ""
        if is_rate_limit or is_silent:
            last_error = RuntimeError(f"transient (rc={result.returncode}): {stderr.strip()!r}")
            attempts += 1
            continue
        raise RuntimeError(f"claude -p failed (rc={result.returncode}): {stderr.strip()}")
    raise RuntimeError(f"Exhausted {BACKOFF_MAX_RETRIES} retries. Last error: {last_error}")


def handle_subagent_clusters(subagent_proposals: list[dict], prompt_template: str,
                             gold_dir: Path, all_clusters: list[dict]) -> list[dict]:
    by_id = {c["cluster_id"]: c for c in all_clusters}
    resolved: list[dict] = []
    for proposal in subagent_proposals:
        cid = proposal["cluster_id"]
        cluster = by_id.get(cid)
        if cluster is None:
            log.warning("Cannot find cluster %s for sub-agent re-run; keeping placeholder", cid)
            resolved.append(proposal)
            continue
        log.info("Sub-agent for cluster %s (%d members)", cid, len(cluster.get("members", [])))
        sub_prompt = build_judge_prompt(prompt_template, [cluster], gold_dir)
        try:
            sub_proposals = parse_proposals(call_claude_judge(sub_prompt, dry_run=False))
            resolved.extend(sub_proposals if sub_proposals else [proposal])
        except (RuntimeError, ValueError) as exc:
            log.error("Sub-agent failed for cluster %s: %s; keeping placeholder", cid, exc)
            resolved.append(proposal)
    return resolved


# ---------------------------------------------------------------------------
# Subcommand entry points
# ---------------------------------------------------------------------------

def run_cluster(args: argparse.Namespace) -> int:
    new_paths = find_new_entries(args.gold_dir, full=args.full)
    if not new_paths:
        log.info("No new gold entries to reconcile.")
        return 0
    log.info("Found %d candidate entries (full=%s).", len(new_paths), args.full)

    new_entries = [parse_entry(p) for p in new_paths]
    new_ids = {e["id"] for e in new_entries}

    neighbor_map: dict[str, list[tuple[str, float]]] = {}
    score_map: dict[tuple[str, str], float] = {}
    for e in new_entries:
        nbrs = neighbors_for(e, top_k=args.top_k, threshold=args.threshold)
        neighbor_map[e["id"]] = nbrs
        for nid, score in nbrs:
            score_map[(e["id"], nid)] = score
        log.info("  %s -> %d neighbors", e["id"], len(nbrs))

    clusters = cluster(neighbor_map)
    nontrivial = [c for c in clusters if len(c) > 1]
    log.info("Built %d clusters total (%d non-trivial).", len(clusters), len(nontrivial))

    if args.dry_run:
        for c in sorted(clusters, key=lambda s: -len(s)):
            triggers = sorted(c & new_ids)
            if triggers:
                log.info("  cluster size=%d trigger=%s members=%s", len(c), triggers, sorted(c))
        return 0

    written = write_clusters(clusters, new_ids, score_map, args.out)
    log.info("Wrote %d clusters to %s", written, args.out)
    return 0


def run_judge(args: argparse.Namespace) -> int:
    if not args.prompt.exists():
        log.error("Reconcile prompt not found: %s", args.prompt)
        return 1
    prompt_template = args.prompt.read_text()
    try:
        clusters = load_clusters(args.clusters)
    except (FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    if not clusters:
        log.info("No clusters to judge.")
        return 0
    log.info("Loaded %d clusters from %s", len(clusters), args.clusters)

    full_prompt = build_judge_prompt(prompt_template, clusters, args.gold_dir)
    if args.dry_run:
        print(f"[dry-run] Would send prompt to claude -p ({JUDGE_MODEL})")
        print(f"[dry-run] Clusters: {len(clusters)}")
        print(f"[dry-run] Estimated tokens: {estimate_tokens(full_prompt):,}")
        print(f"[dry-run] Output would be written to: {args.output}")
        print("--- PROMPT PREVIEW (first 2000 chars) ---")
        print(full_prompt[:2000])
        if len(full_prompt) > 2000:
            print(f"... [{len(full_prompt) - 2000} more chars] ...")
        return 0

    log.info("Sending %d clusters to claude -p (~%d tokens)", len(clusters), estimate_tokens(full_prompt))
    try:
        proposals = parse_proposals(call_claude_judge(full_prompt, dry_run=False))
    except (RuntimeError, ValueError) as exc:
        log.error("Judge call failed: %s", exc)
        return 1

    log.info("First pass: %d proposals", len(proposals))
    pending = [p for p in proposals if p["action"] == "subagent-needed"]
    resolved = [p for p in proposals if p["action"] != "subagent-needed"]
    if pending:
        log.info("%d cluster(s) need sub-agent: %s", len(pending), [p["cluster_id"] for p in pending])
        resolved.extend(handle_subagent_clusters(pending, prompt_template, args.gold_dir, clusters))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as f:
        for p in resolved:
            f.write(json.dumps(p) + "\n")
    log.info("Wrote %d proposals to %s", len(resolved), args.output)
    return 0


# ---------------------------------------------------------------------------
# S4.4 — Apply step: mutate gold/ from approved proposals
# ---------------------------------------------------------------------------

def split_frontmatter(text: str) -> tuple[dict, str, list[str]]:
    """Return (frontmatter_dict, body, raw_fm_lines). Preserves field order via raw_fm_lines."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text, []
    fm_raw, body = m.group(1), m.group(2)
    fm_lines = fm_raw.split("\n")
    fm_dict: dict = {}
    for line in fm_lines:
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm_dict[k.strip()] = v.strip()
    return fm_dict, body, fm_lines


def serialize_frontmatter(fm_lines: list[str], updates: dict) -> str:
    """Rewrite an entry's frontmatter line list applying `updates` (key->raw-value).
    Updates existing keys in place; appends new keys at the end."""
    seen = set()
    out: list[str] = []
    for line in fm_lines:
        if ":" not in line:
            out.append(line)
            continue
        k = line.split(":", 1)[0].strip()
        if k in updates:
            out.append(f"{k}: {updates[k]}")
            seen.add(k)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}: {v}")
    return "\n".join(out)


def merge_entries(primary_path: Path, others: list[Path]) -> str:
    """Build merged content: keep primary body verbatim, append an Evidence section
    listing source_sessions from retired entries (union, deduplicated). Frontmatter
    is the primary's; topics/source_sessions get unioned in."""
    p_text = primary_path.read_text()
    p_fm, p_body, p_fm_lines = split_frontmatter(p_text)

    union_topics = set()
    union_sources: list[str] = []
    seen_sources: set[str] = set()

    def add_list(value: str | None, target_set: set | None, target_list: list | None):
        if not value:
            return
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            for v in inner.split(","):
                v = v.strip().strip("'\"")
                if not v:
                    continue
                if target_set is not None:
                    target_set.add(v)
                if target_list is not None and v not in seen_sources:
                    target_list.append(v)
                    seen_sources.add(v)

    add_list(p_fm.get("topics", ""), union_topics, None)
    add_list(p_fm.get("source_sessions", ""), None, union_sources)

    evidence_blocks: list[str] = []
    for op in others:
        o_text = op.read_text()
        o_fm, o_body, _ = split_frontmatter(o_text)
        add_list(o_fm.get("topics", ""), union_topics, None)
        add_list(o_fm.get("source_sessions", ""), None, union_sources)
        evidence_blocks.append(
            f"### From `{op.stem}` (sessions: {o_fm.get('source_sessions', '[]')})\n\n"
            f"_Merged into `{primary_path.stem}` by reconcile._"
        )

    updates: dict[str, str] = {}
    if union_topics:
        updates["topics"] = "[" + ", ".join(sorted(union_topics)) + "]"
    if union_sources:
        updates["source_sessions"] = "[" + ", ".join(union_sources) + "]"
    new_fm = serialize_frontmatter(p_fm_lines, updates)

    body_with_evidence = p_body.rstrip() + "\n\n## Evidence (merged from)\n\n" + "\n\n".join(evidence_blocks) + "\n"
    return f"---\n{new_fm}\n---\n{body_with_evidence}"


def retire_entry(entry_path: Path, retired_dir: Path, superseded_by: str, reason: str) -> Path:
    """Move entry out of gold/entries/ into gold/_retired/, adding superseded_by frontmatter."""
    text = entry_path.read_text()
    _, _, fm_lines = split_frontmatter(text)
    new_fm = serialize_frontmatter(fm_lines, {
        "superseded_by": superseded_by,
        "retired_reason": reason,
        "retired_at": _now_iso(),
    })
    _, body, _ = split_frontmatter(text)
    new_text = f"---\n{new_fm}\n---\n{body}"
    retired_dir.mkdir(parents=True, exist_ok=True)
    dest = retired_dir / entry_path.name
    dest.write_text(new_text)
    entry_path.unlink()
    return dest


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(tz=timezone.utc).isoformat()


def load_proposals(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Proposals file not found: {path}")
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def filter_approved(proposals: list[dict], approve: set[str] | None, approve_all: bool) -> list[dict]:
    """Keep only proposals the user approved + only the actionable ones (duplicate/contradiction)."""
    actionable = {"duplicate", "contradiction"}
    out = []
    for p in proposals:
        if p["action"] not in actionable:
            continue
        if approve_all or (approve and p["cluster_id"] in approve):
            out.append(p)
    return out


def apply_proposal(proposal: dict, gold_dir: Path, retired_dir: Path, dry_run: bool) -> tuple[int, int]:
    """Apply one proposal. Returns (merges, retirements) counts."""
    primary_id = proposal["primary"]
    others_ids = proposal["others"]
    if not primary_id:
        log.warning("Proposal %s has no primary; skipping", proposal.get("cluster_id"))
        return 0, 0

    primary_path = gold_dir / f"{primary_id}.md"
    if not primary_path.exists():
        log.warning("Primary entry %s not found; skipping", primary_id)
        return 0, 0

    other_paths = [gold_dir / f"{oid}.md" for oid in others_ids]
    missing = [p for p in other_paths if not p.exists()]
    if missing:
        log.warning("Missing entries for proposal %s: %s; skipping", proposal["cluster_id"], missing)
        return 0, 0

    action = proposal["action"]
    log.info("[%s] %s: primary=%s others=%s", "DRY" if dry_run else "APPLY", action, primary_id, others_ids)

    if dry_run:
        return (1, len(other_paths))

    if action == "duplicate":
        merged = merge_entries(primary_path, other_paths)
        primary_path.write_text(merged)
    # for contradiction: don't merge bodies (newer wins as-is); just retire the others.

    for op in other_paths:
        retire_entry(op, retired_dir, superseded_by=primary_id,
                     reason=f"{action}: {proposal.get('rationale', '')[:200]}")
    return (1 if action == "duplicate" else 0, len(other_paths))


def run_apply(args: argparse.Namespace) -> int:
    try:
        proposals = load_proposals(args.proposals)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        log.error("%s", exc)
        return 1

    approve_set = set(args.approve.split(",")) if args.approve else None
    approved = filter_approved(proposals, approve_set, args.approve_all)
    log.info("Loaded %d proposals; %d approved + actionable", len(proposals), len(approved))

    if not approved:
        log.info("Nothing to apply.")
        return 0

    total_merges, total_retirements = 0, 0
    for p in approved:
        m, r = apply_proposal(p, args.gold_dir, args.retired_dir, args.dry_run)
        total_merges += m
        total_retirements += r

    log.info("%s: %d merges, %d retirements",
             "DRY-RUN" if args.dry_run else "APPLIED",
             total_merges, total_retirements)
    if args.dry_run:
        log.info("Suggested commit message: M4/reconcile: %d merges, %d retirements",
                 total_merges, total_retirements)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M4 reconcile (cluster + judge + apply).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("cluster", help="S4.2: build candidate clusters from QMD.")
    pc.add_argument("--full", action="store_true",
                    help="S4.6: bypass git diff and reconcile every gold entry. "
                         "Use after bulk imports or for monthly drift sweeps.")
    pc.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR)
    pc.add_argument("--out", type=Path, default=DEFAULT_OUT)
    pc.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    pc.add_argument("--threshold", type=float, default=DEFAULT_SIMILARITY_THRESHOLD)
    pc.add_argument("--dry-run", action="store_true")
    pc.set_defaults(func=run_cluster)

    pj = sub.add_parser("judge", help="S4.3: classify clusters via claude -p.")
    pj.add_argument("--clusters", type=Path, default=DEFAULT_OUT)
    pj.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR)
    pj.add_argument("--output", type=Path, default=DEFAULT_PROPOSALS)
    pj.add_argument("--prompt", type=Path, default=DEFAULT_RECONCILE_PROMPT)
    pj.add_argument("--dry-run", action="store_true")
    pj.set_defaults(func=run_judge)

    pa = sub.add_parser("apply", help="S4.4: apply approved proposals to gold/.")
    pa.add_argument("--proposals", type=Path, default=DEFAULT_PROPOSALS)
    pa.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR)
    pa.add_argument("--retired-dir", type=Path, default=DEFAULT_RETIRED_DIR)
    pa.add_argument("--approve", type=str, default=None,
                    help="Comma-separated cluster IDs to apply (e.g. c001,c003).")
    pa.add_argument("--approve-all", action="store_true",
                    help="Apply every actionable proposal without filtering.")
    pa.add_argument("--dry-run", action="store_true")
    pa.set_defaults(func=run_apply)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
