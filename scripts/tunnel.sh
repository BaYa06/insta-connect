#!/usr/bin/env bash
# Public HTTPS URL for the local server via a Cloudflare quick tunnel (no account needed).
# Prints a https://<random>.trycloudflare.com URL; the URL changes on every start.
command -v cloudflared >/dev/null || { echo "Install first: brew install cloudflared"; exit 1; }
exec cloudflared tunnel --url "http://127.0.0.1:${PORT:-8000}"
