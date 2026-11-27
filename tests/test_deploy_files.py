"""Static checks of deployment files (the stack itself is not run on the dev laptop)."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
S = compose["services"]


def test_services_present():
    assert set(S) == {"mcp-server", "postgres", "collector", "caddy"}


def test_only_caddy_publishes_ports():
    assert "ports" in S["caddy"]
    for name in ("mcp-server", "postgres", "collector"):
        assert "ports" not in S[name], name
    published = {p.split(":")[-1] for p in S["caddy"]["ports"]}
    assert published == {"80", "443"}


def test_restart_policy_and_healthchecks():
    for name, svc in S.items():
        assert svc["restart"] == "unless-stopped", name
    assert "healthcheck" in S["postgres"] and "healthcheck" in S["mcp-server"]
    assert S["collector"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert "pgdata:/var/lib/postgresql/data" in S["postgres"]["volumes"]


def test_no_literal_secrets_in_compose_or_caddyfile():
    text = (ROOT / "docker-compose.yml").read_text() + (ROOT / "Caddyfile").read_text()
    assert not re.search(r"EAA[A-Za-z0-9]{10,}", text)
    assert "${POSTGRES_PASSWORD" in text and "{$PUBLIC_HOST}" in text
    assert S["mcp-server"]["env_file"] == ".env"


def test_dockerfile_nonroot_and_ffmpeg_only_in_media_target():
    d = (ROOT / "Dockerfile").read_text()
    stages = re.split(r"(?m)^FROM ", d)[1:]
    by_name = {re.search(r"AS (\w+)", s).group(1): s for s in stages}
    assert {"builder", "runtime", "media"} <= set(by_name)
    assert "ffmpeg" in by_name["media"] and "ffmpeg" not in by_name["runtime"] + by_name["builder"]
    assert re.search(r"(?m)^USER app", by_name["runtime"])


def test_gitignore_covers_secrets_and_data():
    lines = set((ROOT / ".gitignore").read_text().split())
    assert {".env", "*.env", "*.pem", "*.key", "data/", "media/", "__pycache__/", "pgdata/"} <= lines


def test_env_example_has_all_vars_and_no_values_for_secrets():
    kv = dict(l.split("=", 1) for l in (ROOT / ".env.example").read_text().splitlines()
              if "=" in l and not l.startswith("#"))
    need = {"IG_ACCESS_TOKEN", "IG_USER_ID", "FB_PAGE_ID", "META_APP_ID", "META_APP_SECRET",
            "GRAPH_API_VERSION", "MODE", "MCP_API_KEY", "POSTGRES_PASSWORD", "PUBLIC_HOST",
            "TRANSCRIPTION_PROVIDER", "TRANSCRIPTION_API_KEY", "NOTIFY_CHANNEL"}
    assert need <= set(kv)
    for secret in ("IG_ACCESS_TOKEN", "META_APP_SECRET", "MCP_API_KEY", "POSTGRES_PASSWORD",
                   "TRANSCRIPTION_API_KEY"):
        assert kv[secret] == ""


def test_env_example_names_are_read_by_settings(monkeypatch):
    from flashly.config import Settings
    for k, v in {"IG_ACCESS_TOKEN": "tok-abcdefgh", "MCP_API_KEY": "k" * 40, "FB_PAGE_ID": "7",
                 "MODE": "live", "GRAPH_API_VERSION": "v99.0", "IG_USER_ID": "5"}.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.mode == "live" and s.facebook_page_id == "7" and s.graph_api_version == "v99.0"
    assert s.mcp_auth_token.get_secret_value() == "k" * 40 and s.ig_user_id == "5"
    assert s.token() == "tok-abcdefgh"


async def test_no_secret_in_any_tool_output(settings, fast_sleep):
    """Run every tool and make sure no configured secret appears in the output."""
    import json

    from flashly.config import Settings
    from flashly.server import create_server
    from flashly.service import build_service
    s = Settings(mode="mock", ig_access_token="EAAsecretvalue1234567", meta_app_secret="appsecretvalue999",
                 mcp_auth_token="m" * 40, database_url="postgresql+psycopg://u:dbpassvalue123@h/d", _env_file=None)
    srv = create_server(build_service(s, sleep=fast_sleep))
    calls = [("get_profile", {}), ("list_media", {}), ("get_media_details", {"media_id": "18000000000000001"}),
             ("get_media_insights", {"media_id": "18000000000000001"}), ("get_account_insights", {}),
             ("get_audience_insights", {"metric": "online_followers"}), ("get_token_status", {})]
    blob = ""
    for name, args in calls:
        blob += json.dumps([c.text for c in await srv.call_tool(name, args)])
    for secret in ("EAAsecretvalue1234567", "appsecretvalue999", "m" * 40, "dbpassvalue123", "mock-token"):
        assert secret not in blob
    assert "access_token" not in blob


def test_caddyfile_masks_key_path_in_all_logs():
    c = (ROOT / "Caddyfile").read_text()
    # default (error) logger and the site access log both mask /mcp/<key>
    assert c.count('request>uri regexp "^/mcp/[^/?]+" "/mcp/***"') == 2
    assert "log default" in c and "{$PUBLIC_HOST}" in c and "flush_interval -1" in c
