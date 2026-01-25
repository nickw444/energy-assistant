#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-hass-energy-fixtures:local}"
UV_ENV_DIR="${UV_ENV_DIR:-/opt/venv}"
UV_CACHE_DIR="${UV_CACHE_DIR:-/repo/.cache/uv}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found in PATH." >&2
  exit 1
fi

echo "Building ${IMAGE_NAME} (docker cache enabled)..."
docker build -f Dockerfile.fixtures -t "${IMAGE_NAME}" .

docker run --rm \
  -e UV_PROJECT_ENVIRONMENT="${UV_ENV_DIR}" \
  -e UV_CACHE_DIR="${UV_CACHE_DIR}" \
  -v "${PWD}:/repo" \
  -w /repo \
  "${IMAGE_NAME}" \
  sh -lc "uv sync --dev --frozen && uv run hass-energy ems refresh-baseline \"\$@\"" -- "$@"
