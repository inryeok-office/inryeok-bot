#!/usr/bin/env bash
set -u

TAG="v2.11.4"
EXPECTED_COMMIT="e2eee6a7fce366321294c9c2a79f3146891dcbdf"
GOVULNCHECK_VERSION="v1.7.0"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

git clone --depth 1 --branch "$TAG" https://github.com/caddyserver/caddy.git "$ROOT/caddy" >/dev/null 2>&1
actual="$(git -C "$ROOT/caddy" rev-parse HEAD)"
test "$actual" = "$EXPECTED_COMMIT"
cd "$ROOT/caddy"

GOBIN="$ROOT/bin" go install "golang.org/x/vuln/cmd/govulncheck@$GOVULNCHECK_VERSION" >/dev/null 2>&1
mkdir -p "$ROOT/out"
set +e
"$ROOT/bin/govulncheck" -json ./... >"$ROOT/out/source.json"
source_status=$?
"$ROOT/bin/govulncheck" ./... >"$ROOT/out/source.txt" 2>&1
source_text_status=$?
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -buildvcs=false -o "$ROOT/out/caddy" ./cmd/caddy
build_status=$?
if [ "$build_status" -eq 0 ]; then
  "$ROOT/bin/govulncheck" -json -mode=binary "$ROOT/out/caddy" >"$ROOT/out/binary.json"
  binary_status=$?
  "$ROOT/bin/govulncheck" -mode=binary "$ROOT/out/caddy" >"$ROOT/out/binary.txt" 2>&1
  binary_text_status=$?
else
  binary_status=1
  binary_text_status=1
fi
set -e

summary="$GITHUB_WORKSPACE/caddy-vuln-summary.md"
{
  echo "# Caddy source vulnerability check"
  echo
  echo "- tag: $TAG"
  echo "- peeled commit: $actual"
  echo "- govulncheck: $GOVULNCHECK_VERSION"
  echo "- build: $([ "$build_status" -eq 0 ] && echo PASS || echo FAIL)"
  echo "- source scan: $([ "$source_text_status" -eq 0 ] && echo PASS || echo FINDINGS_OR_ERROR)"
  echo "- binary scan: $([ "$binary_text_status" -eq 0 ] && echo PASS || echo FINDINGS_OR_ERROR)"
  echo
  echo "## Advisory identifiers"
  { grep -hoE 'GO-[0-9]{4}-[0-9]+' "$ROOT/out/source.txt" "$ROOT/out/binary.txt" 2>/dev/null || true; } | sort -u | sed 's/^/- /'
  echo
  echo "## Safe finding metadata"
  grep -E '^(Vulnerability #[0-9]+|  More info:|  Module:|    Found in:|    Fixed in:|    Vulnerable symbols found:|      [[:alnum:]_.]+\.)' "$ROOT/out/source.txt" "$ROOT/out/binary.txt" 2>/dev/null | head -n 160 || true
  echo
  echo "The full source tree and module cache are intentionally not retained."
} >"$summary"
cat "$summary" >>"$GITHUB_STEP_SUMMARY"

if [ "$source_status" -ne 0 ] || [ "$source_text_status" -ne 0 ] || [ "$build_status" -ne 0 ] || [ "$binary_status" -ne 0 ] || [ "$binary_text_status" -ne 0 ]; then
  exit 1
fi
