#!/usr/bin/env bash
# Launch bird_brain. Setup is INSTALL.md; this script only runs what's already
# installed, and fails loudly if it isn't there.
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
    echo "run.sh: no .venv/bin/python here — see INSTALL.md §2" >&2
    exit 1
fi

if [[ ! -f .env ]]; then
    echo "run.sh: no .env here — cp .env.example .env and fill it in (INSTALL.md §3)" >&2
    exit 1
fi

# -u: transcript lines and lane output are watched live, so stdout must not sit
# in a block buffer when it is piped to a log or a pager.
exec .venv/bin/python -u -m src.main "$@"
