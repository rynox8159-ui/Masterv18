#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" && exec npm --prefix dependencies/frontend run dev
