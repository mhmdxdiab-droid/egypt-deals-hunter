#!/data/data/com.termux/files/usr/bin/sh
set -eu
cd "$(dirname "$0")"
termux-wake-lock
trap 'termux-wake-unlock' EXIT INT TERM
python direct.py run --limit 2
