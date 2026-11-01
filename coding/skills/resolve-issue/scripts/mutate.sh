#!/usr/bin/env bash
# Back up or restore a working-tree file without touching git.
# Backups live in <common git dir>/mutate-backups. restore consumes the backup;
# backup refuses to overwrite an existing one.
# usage: mutate.sh [-C <dir>] backup|restore <file>
#   -C  run as if started in <dir>; <file> is then relative to <dir>
set -euo pipefail
usage() { echo "usage: $0 [-C <dir>] backup|restore <file>" >&2; exit 2; }
# realpath -m is GNU-only; macOS/BSD fall back to python3 (same semantics: symlinks
# resolved, missing components kept).
abspath() {
  local out
  if out=$(realpath -m -- "$1" 2>/dev/null); then printf '%s\n' "$out"
  else python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1"; fi
}
sha1() { if command -v sha1sum >/dev/null 2>&1; then sha1sum; else shasum -a 1; fi; }
if [ "${1:-}" = -C ]; then
  [ $# -ge 2 ] || usage
  cd "$2" 2>/dev/null || { echo "mutate.sh: cannot enter $2" >&2; exit 2; }
  shift 2
fi
[ $# -eq 2 ] || usage
common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || { echo "mutate.sh: not inside a git repository" >&2; exit 2; }
dir="$common/mutate-backups"
mkdir -p "$dir"
key=$(abspath "$2" | sha1 | cut -c1-40)
case "$1" in
  backup)  [ -f "$2" ] || { echo "mutate.sh: $2 is not a file" >&2; exit 2; }
           [ ! -e "$dir/$key" ] || { echo "mutate.sh: a backup for $2 already exists; restore it first" >&2; exit 1; }
           cp -- "$2" "$dir/$key" ;;
  restore) [ -f "$dir/$key" ] || { echo "mutate.sh: no backup for $2" >&2; exit 1; }
           cp -- "$dir/$key" "$2" && rm -f -- "$dir/$key" ;;
  *) usage ;;
esac
