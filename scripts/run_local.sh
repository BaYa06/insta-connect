#!/usr/bin/env bash
# Start the MCP server on 127.0.0.1:8000 (auth key is read from .env).
cd "$(dirname "$0")/.." && exec .venv/bin/python -m flashly.server --port "${PORT:-8000}"
