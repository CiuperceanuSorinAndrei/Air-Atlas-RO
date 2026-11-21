#!/usr/bin/env bash
set -euo pipefail
run="$1"
initialize="${2:-false}"
manifest="${ATLAS_REGISTRY_MANIFEST:-data/eea/registries/v1/manifest.json}"
branch=refresh-state
verify=(uv run --locked python -m scripts.publish_controlled_run --registry-manifest "$manifest" --run "$run")
parent=()
status=0
git ls-remote --exit-code --heads origin "$branch" >/dev/null || status=$?
if [ "$status" -eq 0 ]; then
  git fetch --no-tags origin "$branch"
  parent=(-p "$(git rev-parse FETCH_HEAD)")
elif [ "$status" -ne 2 ] || [ "$initialize" != true ]; then
  echo 'No readable persisted controlled state; initialize it explicitly.'
  exit 1
fi
previous="$(mktemp -d)"
index="$(mktemp -u)"
trap 'rm -rf "$previous" "$index"' EXIT
if [ "$initialize" != true ]; then
  git archive FETCH_HEAD run | tar -x -C "$previous"
  verify+=(--previous-run "$previous/run")
fi
"${verify[@]}"
for file in "$run"/*; do
  blob="$(git hash-object -w "$file")"
  GIT_INDEX_FILE="$index" git update-index --add --cacheinfo "100644,$blob,run/$(basename "$file")"
done
tree="$(GIT_INDEX_FILE="$index" git write-tree)"
commit="$(
  git -c user.name='github-actions[bot]' -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
    commit-tree "$tree" ${parent[@]+"${parent[@]}"} -m "state: controlled refresh ${GITHUB_RUN_ID:-local}"
)"
git push origin "$commit:refs/heads/$branch"
