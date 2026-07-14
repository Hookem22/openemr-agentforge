#!/usr/bin/env bash
# Installs the Clinical Co-Pilot's pre-push eval gate (Gauntlet/Week 2/W2_ARCHITECTURE.md Section 6).
# Git hooks aren't tracked by git itself, so this install step + a README pointer
# (agent/eval/README.md) is the reproducible path for every contributor/grader.
#
# Usage: ./scripts/install-hooks.sh   (run once, from anywhere inside the repo)
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
HOOK_PATH="$HOOKS_DIR/pre-push"

if [ -e "$HOOK_PATH" ] && ! grep -q "run_eval_gate.py" "$HOOK_PATH" 2>/dev/null; then
    echo "A pre-push hook already exists at $HOOK_PATH and it isn't this one -- refusing to overwrite it." >&2
    echo "Back it up or merge manually, then re-run this script." >&2
    exit 1
fi

cat > "$HOOK_PATH" <<'HOOK'
#!/usr/bin/env bash
# Installed by scripts/install-hooks.sh -- runs the Clinical Co-Pilot's 50-case golden-set eval
# gate before every push, per Gauntlet/Week 2/W2_ARCHITECTURE.md Section 6. Blocks the push (exit
# 1) on a category regression >5% or below its 80% floor. Makes real Anthropic + Voyage API calls
# (and needs a local OpenEMR + DEV_BEARER_TOKEN for the chat-based cases) -- see agent/eval/README.md.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT/agent"

if [ ! -d venv ]; then
    echo "pre-push: agent/venv not found -- skipping the eval gate (set up the venv per agent/README.md to enable it)." >&2
    exit 0
fi

source venv/bin/activate
python eval/run_eval_gate.py
HOOK

chmod +x "$HOOK_PATH"
echo "Installed pre-push eval gate at $HOOK_PATH"
