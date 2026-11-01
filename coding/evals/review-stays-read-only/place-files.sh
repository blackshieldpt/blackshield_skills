#!/usr/bin/env bash
# A git repository with a local origin: main holds the base, the checked-out
# branch fix-last-page holds the change under review.
set -eu
src="$(cd "$(dirname "$0")" && pwd)/files"
g() { git -c user.name=eval -c user.email=eval@example.invalid -c commit.gpgsign=false "$@"; }
git init -q .
git symbolic-ref HEAD refs/heads/main
cp -R "$src/base/." .
g add -A
g commit -qm "Add pagination helpers"
git init -q --bare .git/eval-origin.git
git remote add origin "$PWD/.git/eval-origin.git"
git push -q origin main
git remote set-head origin main
git checkout -qb fix-last-page
cp -R "$src/head/." .
g add -A
g commit -qm "Count the last partial page of results"
