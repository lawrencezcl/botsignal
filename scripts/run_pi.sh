#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/load_zai_env.sh"
cd "$ROOT"
# Default to GLM Coding Plan model; override with MODEL=...
exec pi --provider zai --model "${MODEL:-glm-4.6}" "$@"
