#!/usr/bin/env bash
# Back up or restore a working-tree file without touching git.
# Backups live in <git-dir>/mutate-backups, per repo, never committed.
# usage: mutate.sh backup <file> | mutate.sh restore <file>
set -euo pipefail
usage() { echo "usage: $0 backup|restore <file>" >&2; exit 2; }
[ $# -eq 2 ] || usage
dir="$(git rev-parse --absolute-git-dir)/mutate-backups"
mkdir -p "$dir"
key=$(realpath "$2" | sha1sum | cut -c1-40)
case "$1" in
  backup)  cp -- "$2" "$dir/$key" ;;
  restore) [ -f "$dir/$key" ] || { echo "mutate.sh: no backup for $2" >&2; exit 1; }
           cp -- "$dir/$key" "$2" ;;
  *) usage ;;
esac
