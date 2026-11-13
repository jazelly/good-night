#!/bin/sh
# One-time install: same as `./bin/idle-shutdown setup`.
# Run it from the terminal app you will later run `watch` from.
exec "$(cd "$(dirname "$0")/.." && pwd)/bin/idle-shutdown" setup "$@"
