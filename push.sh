#!/usr/bin/env bash
# Create the repo, commit, and push.
#
#   ./push.sh <github-username> [repo-name]
#   VISIBILITY=public ./push.sh <github-username>     # default is private
#
# Uses the gh CLI if present (creates the remote for you), otherwise prints the
# one command you need to run after making the repo on github.com.

set -euo pipefail

USERNAME="${1:-}"
REPO="${2:-anteater}"
VISIBILITY="${VISIBILITY:-private}"

if [[ -z "$USERNAME" ]]; then
  echo "usage: ./push.sh <github-username> [repo-name]" >&2
  exit 1
fi
if [[ "$VISIBILITY" != "private" && "$VISIBILITY" != "public" ]]; then
  echo "VISIBILITY must be 'private' or 'public'" >&2
  exit 1
fi

cd "$(dirname "$0")"

# --- sensible line endings, since this repo has .sh and .yml ---
if [[ ! -f .gitattributes ]]; then
  cat > .gitattributes <<'ATTR'
* text=auto eol=lf
*.bat text eol=crlf
*.cmd text eol=crlf
*.ps1 text eol=crlf
*.iss text eol=crlf
*.png binary
*.ico binary
*.icns binary
*.db binary
*.kismet binary
config/oui.csv -diff
ATTR
fi

# --- init ---
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  git init -q
  echo ">> initialised repository"
fi

git branch -M main

# --- never commit personal data ---
for f in .env config/anteater.toml config/allowlist.yaml; do
  if [[ -e "$f" ]] && ! git check-ignore -q "$f"; then
    echo "refusing to commit: $f is not gitignored" >&2
    exit 1
  fi
done

git add -A
if git diff --cached --quiet; then
  echo ">> nothing new to commit"
else
  git commit -qm "Anteater: passive RF surveillance-detection suite"
  echo ">> committed"
fi

REMOTE_URL="https://github.com/${USERNAME}/${REPO}.git"

# --- push ---
if command -v gh >/dev/null 2>&1; then
  if gh repo view "${USERNAME}/${REPO}" >/dev/null 2>&1; then
    echo ">> remote exists, pushing"
    git remote remove origin 2>/dev/null || true
    git remote add origin "$REMOTE_URL"
    git push -u origin main
  else
    echo ">> creating ${USERNAME}/${REPO} (${VISIBILITY}) and pushing"
    # gh adds the origin remote itself, so make sure it isn't there already
    git remote remove origin 2>/dev/null || true
    gh repo create "${USERNAME}/${REPO}" "--${VISIBILITY}" --source=. --remote=origin --push
  fi
  echo
  echo ">> done. cut a release (CI builds the executables) with:"
  echo "   git tag v0.1.0 && git push --tags"
else
  git remote remove origin 2>/dev/null || true
  git remote add origin "$REMOTE_URL"
  echo
  echo ">> gh CLI not found. Do this:"
  echo "   1. open https://github.com/new and create a ${VISIBILITY} repo named '${REPO}'"
  echo "   2. git push -u origin main"
fi
