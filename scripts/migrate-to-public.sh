#!/usr/bin/env bash
# scripts/migrate-to-public.sh -- One-time migration to the BYOG layout.
#
# Splits ~/src/hippo into:
#   ~/src/hippo-public          (clean public framework, no personal data)
#   ~/Documents/hippo-data      (your private gold + silver + manifest)
#
# After this runs, ~/src/hippo is renamed to ~/src/hippo-OLD-BACKUP.
# Verify the new layout, then delete the backup whenever you're confident.
#
# This script does NOT push anywhere. It just rearranges your local filesystem
# and creates two fresh local git repos. You add remotes and push manually.
#
# Idempotent? NO — run this exactly once.

set -euo pipefail

SRC="$HOME/src/hippo"
PUBLIC="$HOME/src/hippo-public"
DATA="$HOME/Documents/hippo-data"

echo "=== Hippo migration ==="
echo "  source:   $SRC"
echo "  public:   $PUBLIC  (will be created)"
echo "  data:     $DATA     (will be created)"
echo

if [[ -d "$PUBLIC" || -d "$DATA" ]]; then
  echo "ERROR: target dir(s) already exist. Aborting to avoid clobbering."
  echo "  hippo-public exists?  $([[ -d $PUBLIC ]] && echo yes || echo no)"
  echo "  hippo-data exists?    $([[ -d $DATA ]] && echo yes || echo no)"
  exit 1
fi

if [[ ! -d "$SRC/.git" ]]; then
  echo "ERROR: $SRC is not a git repo."
  exit 1
fi

read -r -p "Proceed? [y/N] " ans
[[ "$ans" =~ ^[Yy]$ ]] || { echo "aborted"; exit 0; }

# ---------------------------------------------------------------------------
# Step 1: create public repo (rsync framework only, no data)
# ---------------------------------------------------------------------------
echo
echo "==> [1/4] creating $PUBLIC (framework only)"
mkdir -p "$PUBLIC"
rsync -a \
  --exclude='.git' \
  --exclude='gold/entries/' \
  --exclude='gold/_retired/' \
  --exclude='gold/suggestions/' \
  --exclude='silver/' \
  --exclude='bronze/' \
  --exclude='manifest.jsonl' \
  --exclude='reconcile-clusters.jsonl' \
  --exclude='reconcile-proposals.jsonl' \
  --exclude='.qmd/' \
  --exclude='.cache/' \
  --exclude='.claude/scheduled_tasks.lock' \
  --exclude='__pycache__/' \
  --exclude='.venv/' \
  "$SRC/" "$PUBLIC/"

# Recreate empty gold/entries/ with .gitkeep
mkdir -p "$PUBLIC/gold/entries"
touch "$PUBLIC/gold/entries/.gitkeep"

cd "$PUBLIC"
git init -q
git add -A
git commit -q -m "Initial commit: hippo experiential memory framework"
echo "    initialized git, 1 commit"

# ---------------------------------------------------------------------------
# Step 2: create data repo (move personal data out of $SRC)
# ---------------------------------------------------------------------------
echo
echo "==> [2/4] creating $DATA (your private gold + silver + manifest)"
mkdir -p "$DATA/gold"

[[ -d "$SRC/gold/entries"  ]] && cp -R "$SRC/gold/entries"  "$DATA/gold/entries"
[[ -d "$SRC/gold/_retired" ]] && cp -R "$SRC/gold/_retired" "$DATA/gold/_retired"
[[ -d "$SRC/silver"        ]] && cp -R "$SRC/silver"        "$DATA/silver"
[[ -f "$SRC/manifest.jsonl" ]] && cp    "$SRC/manifest.jsonl" "$DATA/manifest.jsonl"

cat > "$DATA/README.md" <<'EOF'
# hippo-data — private

This is the personal data side of Hippo (https://github.com/<you>/hippo).

Contents:
- `gold/entries/` — extracted heuristics from your sessions
- `gold/_retired/` — soft-retired entries (audit trail for reconcile)
- `silver/` — compacted session trajectories (regeneratable from bronze)
- `manifest.jsonl` — pipeline state

This repo is private. Do not push to a public remote.
EOF

cd "$DATA"
git init -q
git add -A
git commit -q -m "Initial: hippo data, imported from MVP-1 run"
echo "    initialized git, 1 commit"
echo "    contents: $(ls $DATA/gold/entries 2>/dev/null | wc -l | tr -d ' ') gold entries, $(ls $DATA/silver 2>/dev/null | wc -l | tr -d ' ') silver files"

# ---------------------------------------------------------------------------
# Step 3: symlink data into public workspace (so pipeline runs in-place)
# ---------------------------------------------------------------------------
echo
echo "==> [3/4] symlinking data dirs into $PUBLIC"
rm -rf "$PUBLIC/gold/entries"   # empty placeholder
rm -rf "$PUBLIC/gold/_retired" 2>/dev/null

ln -s "$DATA/gold/entries"  "$PUBLIC/gold/entries"
[[ -d "$DATA/gold/_retired" ]] && ln -s "$DATA/gold/_retired" "$PUBLIC/gold/_retired"
[[ -d "$DATA/silver"        ]] && ln -s "$DATA/silver"        "$PUBLIC/silver"
[[ -f "$DATA/manifest.jsonl" ]] && ln -s "$DATA/manifest.jsonl" "$PUBLIC/manifest.jsonl"

echo "    symlinks created"

# ---------------------------------------------------------------------------
# Step 4: rename old workspace
# ---------------------------------------------------------------------------
echo
echo "==> [4/4] renaming $SRC → ${SRC}-OLD-BACKUP"
mv "$SRC" "${SRC}-OLD-BACKUP"

echo
echo "=== DONE ==="
echo
echo "Next steps:"
echo "  1. Re-install launchd jobs to point at the new path:"
echo "     # First update plists in $PUBLIC/scripts/launchd/ to use $PUBLIC instead of $SRC"
echo "     sed -i '' 's|/Users/mu/src/hippo|/Users/mu/src/hippo-public|g' \\"
echo "       $PUBLIC/scripts/launchd/com.mu.hippo.*.plist"
echo "     $PUBLIC/scripts/launchd/install.sh"
echo
echo "  2. Re-register QMD against the new path:"
echo "     qmd collection remove hippo"
echo "     cd $PUBLIC && ./scripts/qmd-setup.sh"
echo
echo "  3. Update the global hippo-remember skill symlink:"
echo "     rm ~/.claude/skills/hippo-remember"
echo "     ln -s $PUBLIC/.claude/skills/hippo-remember ~/.claude/skills/hippo-remember"
echo
echo "  4. Add remotes & push:"
echo "     cd $PUBLIC"
echo "     gh repo create <you>/hippo --public --source=. --remote=origin"
echo "     git push -u origin main"
echo
echo "     cd $DATA"
echo "     gh repo create <you>/hippo-data --private --source=. --remote=origin"
echo "     git push -u origin main"
echo
echo "  5. Verify everything works (memory query, scheduled run), then:"
echo "     rm -rf ${SRC}-OLD-BACKUP"
