#!/bin/zsh
# Frame Atlas — weekly fingerprint autopilot.
# Runs the CLIP fingerprint script; if any new images got fingerprinted,
# commits the updated seed file and pushes it (which redeploys the site).
# Scheduled by macOS launchd — see ~/Library/LaunchAgents/com.frameatlas.fingerprints.plist
# Log of every run: scripts/fingerprints.log

cd "$(dirname "$0")/.." || exit 1

echo ""
echo "=== Fingerprint autopilot run: $(date) ==="

scripts/.venv/bin/python scripts/generate_embeddings.py || { echo "Fingerprint script failed."; exit 1; }

if [[ -n "$(git status --porcelain backend/embeddings_seed.json.gz)" ]]; then
    git add backend/embeddings_seed.json.gz
    git commit -m "Auto-update CLIP fingerprints (weekly autopilot)" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
    git push origin main
    echo "Pushed updated fingerprints — Railway will redeploy in ~3 minutes."
else
    echo "Seed file unchanged — nothing to push."
fi
