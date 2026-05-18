#!/bin/bash
set -e

# Start virtual display so Chromium can run with headless=False (bypasses Akamai)
Xvfb :99 -screen 0 1920x1080x24 -ac &
export DISPLAY=:99
sleep 1

echo "Xvfb started on DISPLAY=:99"

exec uv run uvicorn src.web:app --host 0.0.0.0 --port 8080
