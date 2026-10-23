#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
