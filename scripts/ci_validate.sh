#!/usr/bin/env bash
# Run CI-safe PHOS validation while preserving every result for reporting.
set -uo pipefail

mkdir -p artifacts
status=0

run() {
  local name="$1"
  shift
  set +e
  "$@" 2>&1 | tee "artifacts/${name}.txt"
  local result=${PIPESTATUS[0]}
  set -e
  printf '%s\n' "$result" > "artifacts/${name}.exit-code"
  if [ "$result" -ne 0 ]; then status=1; fi
}

set -e
run pytest python -m pytest -q --tb=short -rs --junitxml=artifacts/junit.xml
run compileall python -m compileall -q src
run docs mkdocs build --strict

printf '%s\n' "$status" > artifacts/validation.exit-code
exit "$status"
