"""Acceptance reports must not turn pending checks into a passing result."""
import pytest

from macvtap_acceptance import acceptance_status


@pytest.mark.parametrize("checks,expected", [
    ({"network": "pass", "agent_channel": True}, "pass"),
    ({"network": "pass", "agent": "pending"}, "partial"),
    ({"network": "pass", "agent_channel": False}, "partial"),
    ({"network": "failed", "agent": "pending"}, "failed"),
])
def test_report_status_preserves_incomplete_evidence(checks, expected):
    assert acceptance_status(checks) == expected
