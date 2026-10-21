#!/usr/bin/env bash
# Runs on a pre-provisioned Linux Docker host. Secrets stay only in /srv/bor/.env.
set -Eeuo pipefail

sha="${1:-}"
if [[ ! "$sha" =~ ^[a-f0-9]{40}$ ]]; then
  echo 'Invalid release SHA' >&2
  exit 2
fi

root=/srv/bor
release="$root/releases/$sha"
test -f "$root/.env" || { echo 'Server /srv/bor/.env is missing' >&2; exit 2; }
test -r "$release/bor-image.tar.gz"
test -r "$release/compose.prod.yml"

# Deployments must be serialized, also if a workflow is retried.
exec 9> "$root/deploy.lock"
flock -x 9
previous=""
if test -f "$root/current-release"; then
  previous="$(cat "$root/current-release")"
fi

# The image was built and tested in GitHub Actions. Keep the release image for rollback.
gzip -dc "$release/bor-image.tar.gz" | docker load > /dev/null

run_release() {
  local version="$1"
  export BOR_IMAGE="bor-bot:$version"
  export BOR_BUILD_SHA="$version"
  docker compose --project-name bor --env-file "$root/.env" \
    -f "$root/releases/$version/compose.prod.yml" \
    up -d --no-build --force-recreate --remove-orphans
}

wait_ready() {
  local status running
  for _ in $(seq 1 30); do
    running="$(docker inspect --format '{{.State.Running}}' bor-bot 2>/dev/null || true)"
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' bor-bot 2>/dev/null || true)"
    if [[ "$running" == true && "$status" == healthy ]]; then
      return 0
    fi
    if [[ "$running" != true || "$status" == unhealthy ]]; then
      return 1
    fi
    sleep 5
  done
  return 1
}

if run_release "$sha" && wait_ready; then
  printf '%s\n' "$sha" > "$root/current-release.tmp"
  mv "$root/current-release.tmp" "$root/current-release"
  echo "Deployment successful: $sha"
  exit 0
fi

echo "New release failed health checks: $sha" >&2
if [[ "$previous" =~ ^[a-f0-9]{40}$ && "$previous" != "$sha" ]] \
   && test -f "$root/releases/$previous/compose.prod.yml"; then
  echo "Restoring previous release: $previous" >&2
  # Reload the saved image: the local Docker cache may have been pruned.
  if test -r "$root/releases/$previous/bor-image.tar.gz" \
    && gzip -dc "$root/releases/$previous/bor-image.tar.gz" | docker load > /dev/null \
    && run_release "$previous" && wait_ready; then
    echo "Previous release restored: $previous" >&2
  else
    echo 'Rollback could not be confirmed healthy; intervention required' >&2
  fi
else
  echo 'No previous release available for rollback; intervention required' >&2
  # Do not let an unhealthy first deployment continue running.
  export BOR_IMAGE="bor-bot:$sha"
  export BOR_BUILD_SHA="$sha"
  docker compose --project-name bor --env-file "$root/.env" \
    -f "$release/compose.prod.yml" stop bot || true
fi
exit 1
