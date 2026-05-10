#!/usr/bin/env bash
set -euo pipefail

# Remove Python cache artifacts
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f -name "*.pyc" -delete

# Remove local OS/editor temp files if present
find . -type f \( -name ".DS_Store" -o -name "Thumbs.db" \) -delete

echo "Repository cleanup complete."
