#!/usr/bin/env bash
# Back up or restore a working-tree file without touching git.
# Backups live in <common git dir>/mutate-backups. restore consumes the backup;
# backup refuses to overwrite an existing one.
# usage: mutate.sh backup <file> | mutate.sh restore <file>
set -euo pipefail
usage() { echo "usage: $0 backup|restore <file>" >&2; exit 2; }
[ $# -eq 2 ] || usage
common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || { echo "mutate.sh: not inside a git repository" >&2; exit 2; }
dir="$common/mutate-backups"
mkdir -p "$dir"
key=$(realpath -m -- "$2" | sha1sum | cut -c1-40)
case "$1" in
  backup)  [ -f "$2" ] || { echo "mutate.sh: $2 is not a file" >&2; exit 2; }
           [ ! -e "$dir/$key" ] || { echo "mutate.sh: a backup for $2 already exists; restore it first" >&2; exit 1; }
           cp -- "$2" "$dir/$key" ;;
  restore) [ -f "$dir/$key" ] || { echo "mutate.sh: no backup for $2" >&2; exit 1; }
           cp -- "$dir/$key" "$2" && rm -f -- "$dir/$key" ;;
  *) usage ;;
esac
