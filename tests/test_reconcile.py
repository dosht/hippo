"""Unit tests for scripts/reconcile.py — clustering and entry parsing only.

QMD calls and git diff are not exercised here (they're integration concerns
and validated by running the script on the real corpus in S4.2 validation).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from scripts.reconcile import DSU, build_query_text, cluster, parse_entry


# ---------------------------------------------------------------------------
# Union-find clustering
# ---------------------------------------------------------------------------

def test_dsu_basic_union():
    dsu = DSU()
    dsu.union("a", "b")
    dsu.union("b", "c")
    assert dsu.find("a") == dsu.find("c")
    assert dsu.find("a") != dsu.find("d")
    dsu.find("d")  # touch d so it appears
    assert dsu.find("d") == "d"


def test_cluster_isolated_new_entries_appear():
    """A new entry with no neighbors must still produce a singleton cluster."""
    clusters = cluster({"new1": [], "new2": []})
    members = {frozenset(c) for c in clusters}
    assert frozenset(["new1"]) in members
    assert frozenset(["new2"]) in members


def test_cluster_disjoint_neighbor_sets_stay_separate():
    clusters = cluster({
        "a": [("b", 0.8), ("c", 0.7)],
        "x": [("y", 0.8), ("z", 0.7)],
    })
    members = {frozenset(c) for c in clusters}
    assert frozenset(["a", "b", "c"]) in members
    assert frozenset(["x", "y", "z"]) in members
    assert len(members) == 2


def test_cluster_overlapping_neighbor_sets_merge():
    """Two new entries that share even one neighbor collapse to one cluster.
    This is the key correctness property: 4-way duplicates must be one
    cluster, not two pair-judgments."""
    clusters = cluster({
        "new1": [("shared", 0.9), ("only1", 0.7)],
        "new2": [("shared", 0.85), ("only2", 0.7)],
    })
    members = {frozenset(c) for c in clusters}
    assert frozenset(["new1", "new2", "shared", "only1", "only2"]) in members


def test_cluster_chain_of_overlap_merges_transitively():
    """A -> B, B -> C should collapse {A, B, C} even though A and C never
    directly share a neighbor."""
    clusters = cluster({
        "a": [("b", 0.8)],
        "b": [("c", 0.8)],
    })
    members = {frozenset(c) for c in clusters}
    assert frozenset(["a", "b", "c"]) in members
    assert len(members) == 1


# ---------------------------------------------------------------------------
# Entry parsing
# ---------------------------------------------------------------------------

def _write_entry(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body).lstrip())
    return p


def test_parse_entry_extracts_frontmatter_and_title(tmp_path):
    p = _write_entry(tmp_path, "mem-foo.md", """
        ---
        id: mem-foo
        type: gotcha
        topics: [postgres, triggers]
        agents: [developer, tech-lead]
        ---

        # Postgres triggers cannot read env vars

        Body text here with VARIANT_ID and a path scripts/foo.py:42.
    """)
    e = parse_entry(p)
    assert e["id"] == "mem-foo"
    assert e["title"] == "Postgres triggers cannot read env vars"
    assert e["topics"] == ["postgres", "triggers"]
    assert e["type"] == "gotcha"
    assert e["agents"] == ["developer", "tech-lead"]
    assert "VARIANT_ID" in e["body"]


def test_build_query_text_single_line_with_signals_in_order(tmp_path):
    """QMD's structured-query mode triggers on newlines and requires per-line
    prefixes (lex:/vec:/hyde:/intent:). Single-line queries get auto-expanded
    into hybrid sub-queries by QMD, which is what we want — so the function
    must produce a single line."""
    entry = {
        "id": "mem-foo",
        "title": "T",
        "topics": ["alpha", "beta"],
        "type": "gotcha",
        "agents": ["dev"],
        "body": "BODY-WITH-IDENTIFIER" * 500,  # ensure cap kicks in
    }
    q = build_query_text(entry)
    assert "\n" not in q
    # title comes first, then topics, type, agents, body
    assert q.startswith("T alpha beta gotcha dev BODY-WITH-IDENTIFIER")
    assert "BODY-WITH-IDENTIFIER" in q


def test_parse_entry_handles_missing_frontmatter(tmp_path):
    p = _write_entry(tmp_path, "mem-bare.md", "# Just a title\n\nBody.\n")
    e = parse_entry(p)
    assert e["id"] == "mem-bare"
    assert e["title"] == ""  # no frontmatter, regex fall-through
    assert e["topics"] == []
    assert "Body." in e["body"]


# ---------------------------------------------------------------------------
# Summary field (S4.8)
# ---------------------------------------------------------------------------

def test_parse_entry_extracts_summary(tmp_path):
    p = _write_entry(tmp_path, "mem-foo.md", """
        ---
        id: mem-foo
        type: gotcha
        topics: [postgres]
        agents: [developer]
        summary: Triggers can't read process.env; pass values via payload.
        ---

        # Postgres triggers cannot read env vars

        Body.
    """)
    e = parse_entry(p)
    assert e["summary"] == "Triggers can't read process.env; pass values via payload."


def test_parse_entry_summary_empty_when_absent(tmp_path):
    p = _write_entry(tmp_path, "mem-bar.md", """
        ---
        id: mem-bar
        type: pattern
        topics: [stripe]
        agents: [developer]
        ---

        # Stripe webhook idempotency

        Body.
    """)
    e = parse_entry(p)
    assert e["summary"] == ""


def test_build_query_text_prefers_summary_over_body():
    entry = {
        "id": "mem-foo",
        "title": "T",
        "topics": ["alpha"],
        "type": "gotcha",
        "agents": ["dev"],
        "summary": "Concise insight here.",
        "body": "BODY-TEXT-SHOULD-NOT-APPEAR" * 50,
    }
    q = build_query_text(entry)
    assert "Concise insight here." in q
    assert "BODY-TEXT-SHOULD-NOT-APPEAR" not in q


def test_build_query_text_falls_back_to_body_when_summary_missing():
    entry = {
        "id": "mem-foo",
        "title": "T",
        "topics": ["alpha"],
        "type": "gotcha",
        "agents": ["dev"],
        "summary": "",
        "body": "FALLBACK-BODY-TOKEN content",
    }
    q = build_query_text(entry)
    assert "FALLBACK-BODY-TOKEN" in q


# ---------------------------------------------------------------------------
# Judge step (S4.3)
# ---------------------------------------------------------------------------

import argparse as _argparse
from unittest.mock import patch

import pytest

from scripts.reconcile import (
    build_cluster_block,
    build_judge_prompt,
    call_claude_judge,
    estimate_tokens,
    load_clusters,
    load_entry_content,
    main,
    parse_proposals,
    run_judge,
)


@pytest.fixture()
def judge_gold_dir(tmp_path):
    d = tmp_path / "gold" / "entries"
    d.mkdir(parents=True)
    (d / "mem-foo.md").write_text("---\nid: mem-foo\ntype: operational\n---\n# Foo\nSome content.")
    (d / "mem-bar.md").write_text("---\nid: mem-bar\ntype: operational\n---\n# Bar\nOther content.")
    return d


@pytest.fixture()
def judge_clusters_path(tmp_path):
    p = tmp_path / "reconcile-clusters.jsonl"
    p.write_text(json.dumps({
        "cluster_id": "c-0001",
        "members": ["mem-foo", "mem-bar"],
        "scores": [0.88],
    }) + "\n")
    return p


@pytest.fixture()
def judge_prompt_path(tmp_path):
    p = tmp_path / "reconcile.md"
    p.write_text("You are a judge. Output JSONL proposals.")
    return p


def test_load_clusters_valid(judge_clusters_path):
    clusters = load_clusters(judge_clusters_path)
    assert len(clusters) == 1
    assert clusters[0]["cluster_id"] == "c-0001"


def test_load_clusters_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_clusters(tmp_path / "nope.jsonl")


def test_load_clusters_invalid_json(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text("not-json\n")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_clusters(p)


def test_load_entry_content_existing(judge_gold_dir):
    assert "# Foo" in load_entry_content("mem-foo", judge_gold_dir)


def test_load_entry_content_missing(judge_gold_dir):
    assert load_entry_content("mem-nope", judge_gold_dir) is None


def test_build_cluster_block_renders(judge_gold_dir):
    cluster = {"cluster_id": "c-0001", "members": ["mem-foo"], "scores": [0.9]}
    block = build_cluster_block(cluster, judge_gold_dir)
    assert "=== CLUSTER c-0001 ===" in block
    assert "# Foo" in block


def test_build_cluster_block_handles_dict_scores(judge_gold_dir):
    cluster = {"cluster_id": "c1", "members": ["mem-foo"], "scores": {"mem-foo->mem-bar": 0.7}}
    block = build_cluster_block(cluster, judge_gold_dir)
    assert "mem-foo->mem-bar=0.7" in block


def test_build_cluster_block_missing_entry(judge_gold_dir):
    cluster = {"cluster_id": "c1", "members": ["mem-missing"], "scores": []}
    block = build_cluster_block(cluster, judge_gold_dir)
    assert "entry file not found" in block


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("x" * 1000) == 250


def _proposal(**ov):
    base = {"cluster_id": "c-0001", "action": "duplicate",
            "primary": "mem-foo", "others": ["mem-bar"],
            "rationale": "Same thing."}
    base.update(ov)
    return base


def test_parse_proposals_valid_single():
    out = parse_proposals(json.dumps(_proposal()))
    assert len(out) == 1 and out[0]["action"] == "duplicate"


def test_parse_proposals_skips_blank_and_narration():
    text = "preamble narration here\n\n" + json.dumps(_proposal()) + "\n"
    out = parse_proposals(text)
    assert len(out) == 1


def test_parse_proposals_missing_field_raises():
    with pytest.raises(ValueError, match="missing required fields"):
        parse_proposals(json.dumps({"cluster_id": "c1", "action": "duplicate"}))


def test_parse_proposals_invalid_action_raises():
    with pytest.raises(ValueError, match="invalid action"):
        parse_proposals(json.dumps(_proposal(action="delete")))


def test_parse_proposals_all_valid_actions_accepted():
    for action in ("duplicate", "contradiction", "related", "unrelated", "subagent-needed"):
        out = parse_proposals(json.dumps(_proposal(action=action)))
        assert out[0]["action"] == action


def test_build_judge_prompt_contains_template_and_clusters(judge_gold_dir):
    template = "You are a judge."
    clusters = [{"cluster_id": "c-0001", "members": ["mem-foo"], "scores": [0.9]}]
    p = build_judge_prompt(template, clusters, judge_gold_dir)
    assert "You are a judge." in p
    assert "c-0001" in p
    assert "# Foo" in p


def test_call_claude_judge_dry_run_returns_empty():
    assert call_claude_judge("anything", dry_run=True) == ""


def test_call_claude_judge_dry_run_does_not_subprocess():
    with patch("scripts.reconcile.subprocess.run") as m:
        call_claude_judge("p", dry_run=True)
        m.assert_not_called()


def test_run_judge_dry_run_prints_preview(tmp_path, judge_clusters_path,
                                           judge_gold_dir, judge_prompt_path, capsys):
    args = _argparse.Namespace(
        clusters=judge_clusters_path, gold_dir=judge_gold_dir,
        output=tmp_path / "proposals.jsonl", prompt=judge_prompt_path, dry_run=True,
    )
    rc = run_judge(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out and "Clusters: 1" in out and "Estimated tokens:" in out


def test_run_judge_missing_prompt_returns_error(tmp_path, judge_clusters_path, judge_gold_dir):
    args = _argparse.Namespace(
        clusters=judge_clusters_path, gold_dir=judge_gold_dir,
        output=tmp_path / "p.jsonl", prompt=tmp_path / "missing.md", dry_run=True,
    )
    assert run_judge(args) == 1


def test_main_judge_subcommand_dry_run(tmp_path, judge_clusters_path, judge_gold_dir,
                                        judge_prompt_path, capsys):
    rc = main([
        "judge", "--clusters", str(judge_clusters_path),
        "--gold-dir", str(judge_gold_dir),
        "--output", str(tmp_path / "p.jsonl"),
        "--prompt", str(judge_prompt_path), "--dry-run",
    ])
    assert rc == 0
    assert "[dry-run]" in capsys.readouterr().out


def test_main_no_subcommand_exits():
    with pytest.raises(SystemExit):
        main([])


# ---------------------------------------------------------------------------
# Apply step (S4.4)
# ---------------------------------------------------------------------------

from scripts.reconcile import (
    apply_proposal,
    filter_approved,
    load_proposals,
    merge_entries,
    retire_entry,
    run_apply,
    serialize_frontmatter,
    split_frontmatter,
)


def _make_gold(d: Path, name: str, body: str = "Body.\n") -> Path:
    p = d / f"{name}.md"
    p.write_text(textwrap.dedent(body).lstrip())
    return p


def test_split_and_serialize_frontmatter_round_trip():
    text = "---\nid: mem-foo\ntopics: [a, b]\n---\nbody here\n"
    fm, body, lines = split_frontmatter(text)
    assert fm["id"] == "mem-foo"
    assert body == "body here\n"
    new_fm = serialize_frontmatter(lines, {"topics": "[a, b, c]"})
    assert "topics: [a, b, c]" in new_fm
    assert "id: mem-foo" in new_fm


def test_serialize_frontmatter_appends_new_keys():
    lines = ["id: mem-foo", "topics: [a]"]
    out = serialize_frontmatter(lines, {"superseded_by": "mem-bar"})
    assert out.endswith("superseded_by: mem-bar")


def test_merge_entries_unions_topics_and_appends_evidence(tmp_path):
    d = tmp_path / "gold"
    d.mkdir()
    primary = _make_gold(d, "mem-primary", """
        ---
        id: mem-primary
        topics: [a, b]
        source_sessions: [s1]
        ---
        # Primary

        Primary body content.
    """)
    other = _make_gold(d, "mem-other", """
        ---
        id: mem-other
        topics: [b, c]
        source_sessions: [s2]
        ---
        # Other

        Other body.
    """)
    merged = merge_entries(primary, [other])
    # union of topics
    assert "topics: [a, b, c]" in merged
    # union of sessions
    assert "source_sessions: [s1, s2]" in merged
    # primary body preserved
    assert "Primary body content." in merged
    # evidence section appended
    assert "## Evidence (merged from)" in merged
    assert "mem-other" in merged


def test_retire_entry_moves_file_and_adds_frontmatter(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    retired = tmp_path / "retired"
    p = _make_gold(gold, "mem-old", """
        ---
        id: mem-old
        topics: [x]
        ---
        Body.
    """)
    dest = retire_entry(p, retired, superseded_by="mem-new", reason="duplicate of mem-new")
    assert not p.exists()
    assert dest.exists()
    text = dest.read_text()
    assert "superseded_by: mem-new" in text
    assert "retired_reason: duplicate of mem-new" in text
    assert "retired_at:" in text


def test_filter_approved_keeps_only_actionable_and_approved():
    props = [
        {"cluster_id": "c1", "action": "duplicate", "primary": "a", "others": ["b"], "rationale": "x"},
        {"cluster_id": "c2", "action": "related", "primary": None, "others": [], "rationale": "x"},
        {"cluster_id": "c3", "action": "contradiction", "primary": "a", "others": ["b"], "rationale": "x"},
        {"cluster_id": "c4", "action": "unrelated", "primary": None, "others": [], "rationale": "x"},
    ]
    # approve_all
    out = filter_approved(props, None, approve_all=True)
    assert {p["cluster_id"] for p in out} == {"c1", "c3"}
    # selective
    out = filter_approved(props, {"c1"}, approve_all=False)
    assert {p["cluster_id"] for p in out} == {"c1"}


def test_apply_proposal_duplicate_writes_merged_and_retires(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    retired = tmp_path / "retired"
    primary = _make_gold(gold, "mem-primary", """
        ---
        id: mem-primary
        topics: [a]
        source_sessions: [s1]
        ---
        # Primary
        Primary body.
    """)
    other = _make_gold(gold, "mem-other", """
        ---
        id: mem-other
        topics: [b]
        source_sessions: [s2]
        ---
        # Other
        Other body.
    """)
    proposal = {"cluster_id": "c1", "action": "duplicate",
                "primary": "mem-primary", "others": ["mem-other"], "rationale": "same"}
    merges, retirements = apply_proposal(proposal, gold, retired, dry_run=False)
    assert merges == 1 and retirements == 1
    assert not other.exists()
    assert (retired / "mem-other.md").exists()
    merged_text = primary.read_text()
    assert "topics: [a, b]" in merged_text
    assert "## Evidence (merged from)" in merged_text


def test_apply_proposal_contradiction_only_retires(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    retired = tmp_path / "retired"
    new = _make_gold(gold, "mem-new", """
        ---
        id: mem-new
        topics: [a]
        ---
        # New port is 9224.
    """)
    old = _make_gold(gold, "mem-old", """
        ---
        id: mem-old
        topics: [a]
        ---
        # Old port was 9222.
    """)
    proposal = {"cluster_id": "c1", "action": "contradiction",
                "primary": "mem-new", "others": ["mem-old"], "rationale": "port changed"}
    merges, retirements = apply_proposal(proposal, gold, retired, dry_run=False)
    assert merges == 0 and retirements == 1
    # Primary body NOT modified for contradiction (newer wins as-is)
    assert "## Evidence (merged from)" not in new.read_text()
    assert (retired / "mem-old.md").exists()


def test_apply_proposal_dry_run_no_mutation(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    retired = tmp_path / "retired"
    primary = _make_gold(gold, "mem-primary", "---\nid: mem-primary\n---\nBody.\n")
    other = _make_gold(gold, "mem-other", "---\nid: mem-other\n---\nBody.\n")
    proposal = {"cluster_id": "c1", "action": "duplicate",
                "primary": "mem-primary", "others": ["mem-other"], "rationale": "x"}
    apply_proposal(proposal, gold, retired, dry_run=True)
    assert primary.exists() and other.exists()
    assert not retired.exists() or not list(retired.glob("*"))


def test_apply_proposal_missing_primary_skips(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    retired = tmp_path / "retired"
    proposal = {"cluster_id": "c1", "action": "duplicate",
                "primary": "mem-nope", "others": [], "rationale": "x"}
    m, r = apply_proposal(proposal, gold, retired, dry_run=False)
    assert m == 0 and r == 0


def test_run_apply_idempotent_on_empty_proposals(tmp_path):
    proposals = tmp_path / "p.jsonl"
    proposals.write_text("")
    args = _argparse.Namespace(
        proposals=proposals, gold_dir=tmp_path / "gold",
        retired_dir=tmp_path / "retired", approve=None, approve_all=True, dry_run=False,
    )
    assert run_apply(args) == 0
