#!/bin/sh
set -eu
devops_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec uv run --project "$devops_dir/server" python "$devops_dir/scripts/deploy.py" "$@"
