#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
"$ROOT/scripts/local_pc/start-backend.sh" & BACK=$!
sleep 6
"$ROOT/scripts/local_pc/start-frontend.sh" & FRONT=$!
trap "kill $BACK $FRONT" INT TERM
wait
