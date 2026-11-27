import json

import httpx
import pytest

from flashly.config import Settings
from flashly.graph_client import GraphClient
from flashly.masking import register_secret
from flashly.service import InstagramService, build_service

FAKE_TOKEN = "EAATESTTOKEN1234567890abcdef"
FAKE_SECRET = "s3cr3tappsecretvalue99"


@pytest.fixture
def settings():
    return Settings(mode="mock", ig_access_token=FAKE_TOKEN, meta_app_secret=FAKE_SECRET,
                    max_retries=3, retry_base_delay_seconds=1.0, _env_file=None)


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def fast_sleep(sleeps):
    async def _s(d):
        sleeps.append(d)
    return _s


@pytest.fixture
def svc(settings, fast_sleep):
    """Service on the fixture-backed mock transport."""
    return build_service(settings, sleep=fast_sleep)


def client_with(handler, settings, sleep) -> GraphClient:
    register_secret(FAKE_TOKEN)
    register_secret(FAKE_SECRET)
    return GraphClient(settings, transport=httpx.MockTransport(handler), sleep=sleep)


def err_body(code, message="boom", sub=None):
    e = {"message": message, "type": "OAuthException", "code": code}
    if sub:
        e["error_subcode"] = sub
    return {"error": e}


def make_service(handler, settings, sleep) -> InstagramService:
    return InstagramService(client_with(handler, settings, sleep), settings)
