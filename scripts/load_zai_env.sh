#!/usr/bin/env bash
# Load Z.AI / GLM Coding Plan key for Pi without echoing it
set -euo pipefail
KEY_FILE="${ZAI_KEY_FILE:-/home/box/secrets/zai/api_key}"
if [[ ! -f "$KEY_FILE" ]]; then
  echo "Missing key file: $KEY_FILE" >&2
  echo "Write the API key into that file (one line), then re-run." >&2
  return 1 2>/dev/null || exit 1
fi
# Strip CR/LF/spaces
KEY="$(tr -d '\r\n[:space:]' < "$KEY_FILE")"
if [[ -z "$KEY" ]]; then
  echo "Key file is empty: $KEY_FILE" >&2
  return 1 2>/dev/null || exit 1
fi
export ZAI_API_KEY="$KEY"
# Also export CN var so either region works if user uses China plan
export ZAI_CODING_CN_API_KEY="$KEY"
echo "ZAI_API_KEY / ZAI_CODING_CN_API_KEY loaded from $KEY_FILE (length=${#KEY})"
