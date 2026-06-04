#!/usr/bin/env bash
# Copy shared/ utilities into an agent folder so Replit can resolve imports.
# Run before pushing a deploy branch, e.g.: bash build_repl.sh agents/arv-mao
#
# Why: Replit imports a single subfolder from GitHub; it cannot reach sibling
# directories. Copying shared/ into the agent folder is the cleanest portable
# solution (Section 8.2 of master-context-v1.4.md).

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash build_repl.sh agents/<agent-name>" >&2
  exit 1
fi

TARGET="$1"

if [[ ! -d "$TARGET" ]]; then
  echo "ERROR: directory '$TARGET' does not exist." >&2
  exit 1
fi

echo "Copying shared/ → $TARGET/shared/ …"
cp -r shared/ "$TARGET/shared/"
echo "Done. Commit $TARGET/ and push to trigger Replit import."
