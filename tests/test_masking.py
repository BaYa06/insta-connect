import logging

from flashly.errors import GraphAPIError
from flashly.masking import MaskingFormatter, mask, register_secret


def test_masks_query_token():
    out = mask("GET https://graph.facebook.com/v27.0/1?fields=id&access_token=EAAB12345678901234&x=1")
    assert "EAAB1234" not in out and "access_token=***" in out and "x=1" in out


def test_masks_all_param_names():
    for k in ("client_secret", "input_token", "fb_exchange_token", "appsecret_proof"):
        assert mask(f"{k}=abcdef123456") == f"{k}=***"


def test_masks_json_bearer_app_token_and_meta_token():
    assert "abc" not in mask('{"access_token": "abcdefghij"}')
    assert mask("Authorization: Bearer abc.def-123") == "Authorization: Bearer ***"
    assert mask("1503417951637249|SuperSecretValue123") == "***"
    assert mask("tok EAAGm0PX4ZCpsBAKabcdefghij end") == "tok *** end"


def test_masks_registered_literal():
    register_secret("literal-secret-value")
    assert "literal-secret-value" not in mask("oops literal-secret-value here")


def test_exception_message_is_masked():
    e = GraphAPIError("fail access_token=EAAXXXXXXXXXXXXXXXX", code=1)
    assert "EAAXXXX" not in str(e) and "***" in str(e)


def test_formatter_masks_traceback():
    register_secret("trace-secret-123456")
    try:
        raise RuntimeError("bad trace-secret-123456")
    except RuntimeError:
        import sys
        rec = logging.LogRecord("t", logging.ERROR, "f", 1, "msg access_token=ZZZZZZZZZZ", None, sys.exc_info())
    text = MaskingFormatter("%(message)s").format(rec)
    assert "trace-secret-123456" not in text and "ZZZZZZZZZZ" not in text
