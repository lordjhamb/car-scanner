#!/usr/bin/env bash
# Syntax-check every browser ES module with Node (no bundler needed).
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v node >/dev/null 2>&1; then
  echo "node not found - skipping JavaScript syntax check"; exit 0
fi
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fail=0; n=0
while IFS= read -r f; do
  n=$((n+1))
  cp "$f" "$tmp/check.mjs"
  if ! out=$(node --check "$tmp/check.mjs" 2>&1); then
    echo "FAIL $f"; echo "$out" | sed 's/^/    /'; fail=1
  fi
done < <(find web/js -name '*.js' | sort)
[ $fail -eq 0 ] && echo "JavaScript syntax OK ($n modules)"
exit $fail
