#!/bin/sh
# One-time install: same as `./bin/good-night setup`.
# Run it from the terminal app you will later run `watch` from.
exec "$(cd "$(dirname "$0")/.." && pwd)/bin/good-night" setup "$@"
