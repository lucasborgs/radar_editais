#!/usr/bin/env bash
# Execute Docker Compose with an environment-specific, immutable identity.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/compose.sh <production|staging-local> <docker compose args...>

Examples:
  scripts/compose.sh production ps
  scripts/compose.sh production up -d
  scripts/compose.sh staging-local up -d app worker

Production `down` is blocked by default. For an intentional teardown:
  ALLOW_PRODUCTION_DOWN=radar-production scripts/compose.sh production down
EOF
}

target="${1:-}"
if [[ -z "$target" ]]; then
  usage >&2
  exit 2
fi
shift

if [[ $# -eq 0 ]]; then
  usage >&2
  exit 2
fi

case "$target" in
  production)
    project_name="radar-production"
    env_file=".env"
    expected_environment="production"
    ;;
  staging-local)
    project_name="radar-staging-local"
    env_file=".env.staging-local"
    expected_environment="test"
    ;;
  *)
    echo "Unknown Compose target: $target" >&2
    usage >&2
    exit 2
    ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
cd "$repo_root"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file for target $target." >&2
  exit 1
fi

actual_environment="$(awk -F= '$1 == "ENVIRONMENT" { sub(/\r$/, "", $2); print $2; exit }' "$env_file")"
if [[ "$actual_environment" != "$expected_environment" ]]; then
  echo "$env_file must declare ENVIRONMENT=$expected_environment (found: ${actual_environment:-missing})." >&2
  exit 1
fi

for arg in "$@"; do
  case "$arg" in
    --project-name|--project-name=*|-p|-p=*|--env-file|--env-file=*)
      echo "Project name and env file are fixed by the selected target." >&2
      exit 2
      ;;
  esac
done

if [[ "$target" == "production" ]]; then
  for arg in "$@"; do
    if [[ "$arg" == "down" && "${ALLOW_PRODUCTION_DOWN:-}" != "$project_name" ]]; then
      echo "Production down is blocked." >&2
      echo "Set ALLOW_PRODUCTION_DOWN=$project_name only for an intentional teardown." >&2
      exit 1
    fi
  done
fi

exec docker compose \
  --project-name "$project_name" \
  --env-file "$env_file" \
  "$@"
