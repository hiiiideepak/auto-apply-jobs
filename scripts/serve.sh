#!/bin/zsh
cd "$(dirname "$0")/.."
source .venv/bin/activate
export PYTHONPATH=src
exec python -m autoapply serve
