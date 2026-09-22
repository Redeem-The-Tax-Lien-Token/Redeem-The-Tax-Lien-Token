"""
Unit tests for the Twilio startup self-check in agents/outreach/twilio_utils.py.

Tests:
  - Check passes when the Messaging Service fetch succeeds
  - Check fails closed (raises RuntimeError) when the fetch raises an error
  - send_sms uses messaging_service_sid= (not from_=)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Insert agent path so twilio_utils can be imported
_agent_dir  = Path(__file__).parent.parent / "agents" / "outreach"
_repo_root  = Path(__file__).parent.parent
for _p in [str(_agent_dir), str(_repo_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_twilio_utils(mock_sid="MG_TEST_SID"):
    """Import twilio_utils with mocked env vars and Twilio client."""
    env = {
        "TWILIO_ACCOUNT_SID":           "AC_TEST",
        "TWILIO_AUTH_TOKEN":            "token_TEST",
        "TWILIO_MESSAGING_SERVICE_SID": mock_sid,
    }
    with patch.dict("os.environ", env):
        with patch("twilio.rest.Client") as MockClient:
            import twilio_utils as tu
            return tu, MockClient


def test_startup_check_passes_on_successful_fetch():
    env = {
        "TWILIO_ACCOUNT_SID":           "AC_TEST",
        "TWILIO_AUTH_TOKEN":            "token_TEST",
        "TWILIO_MESSAGING_SERVICE_SID": "MG_TEST",
    }
    with patch.dict("os.environ", env, clear=False):
        with patch("twilio.rest.Client") as MockClient:
            mock_svc = MagicMock()
            mock_svc.sid = "MG_TEST"
            mock_svc.friendly_name = "REDEEM 10DLC"
            MockClient.return_value.messaging.v1.services.return_value.fetch.return_value = mock_svc

            import importlib
            import twilio_utils
            importlib.reload(twilio_utils)
            twilio_utils.twilio_startup_check()   # should not raise


def test_startup_check_raises_on_fetch_failure():
    env = {
        "TWILIO_ACCOUNT_SID":           "AC_TEST",
        "TWILIO_AUTH_TOKEN":            "bad_token",
        "TWILIO_MESSAGING_SERVICE_SID": "MG_INVALID",
    }
    with patch.dict("os.environ", env, clear=False):
        with patch("twilio.rest.Client") as MockClient:
            MockClient.return_value.messaging.v1.services.return_value.fetch.side_effect = (
                Exception("HTTP 401: Unauthorized")
            )
            import importlib
            import twilio_utils
            importlib.reload(twilio_utils)
            with pytest.raises(RuntimeError, match="self-check FAILED"):
                twilio_utils.twilio_startup_check()


def test_send_sms_uses_messaging_service_sid():
    env = {
        "TWILIO_ACCOUNT_SID":           "AC_TEST",
        "TWILIO_AUTH_TOKEN":            "token_TEST",
        "TWILIO_MESSAGING_SERVICE_SID": "MG_TEST",
    }
    with patch.dict("os.environ", env, clear=False):
        with patch("twilio.rest.Client") as MockClient:
            mock_msg = MagicMock()
            mock_msg.sid = "SM_RETURNED"
            MockClient.return_value.messages.create.return_value = mock_msg

            import importlib
            import twilio_utils
            importlib.reload(twilio_utils)

            sid = twilio_utils.send_sms("+13175550001", "Test message")
            assert sid == "SM_RETURNED"

            call_kwargs = MockClient.return_value.messages.create.call_args.kwargs
            assert "messaging_service_sid" in call_kwargs
            assert "from_" not in call_kwargs
            assert call_kwargs["messaging_service_sid"] == "MG_TEST"
