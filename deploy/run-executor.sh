#!/bin/sh
set -eu

socket=/run/inryeok-bot/executor.sock
rm -f "$socket"
/opt/inryeok-bot/executor/venv/bin/uvicorn app.codex.executor:app --uds "$socket" &
child=$!
trap 'kill -TERM "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; exit 143' TERM INT

ready=0
for _ in $(seq 1 100); do
    if [ -S "$socket" ]; then
        chmod 0660 "$socket"
        ready=1
        break
    fi
    sleep 0.1
done
if [ "$ready" -ne 1 ]; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
    exit 1
fi
wait "$child"
