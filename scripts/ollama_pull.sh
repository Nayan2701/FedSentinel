#!/bin/sh
set -eu

HOST="${OLLAMA_HOST:-ollama}"
PORT="${OLLAMA_PORT:-11434}"
MODEL="${OLLAMA_MODEL:-tinyllama}"

BASE_URL="http://${HOST}:${PORT}"

echo "[ollama-pull] waiting for ollama at ${BASE_URL} ..."
until curl -sf "${BASE_URL}/api/tags" >/dev/null; do
  sleep 2
done

echo "[ollama-pull] pulling model: ${MODEL}"
curl -sS "${BASE_URL}/api/pull" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"${MODEL}\"}"

echo "[ollama-pull] done"
