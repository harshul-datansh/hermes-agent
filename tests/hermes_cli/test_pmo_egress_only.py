"""One-way Hermes channel delivery for PMO notifications."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
import hashlib
import hmac
from types import SimpleNamespace

from gateway.config import Platform
from plugins.pmo.notifications import (
    NotificationTarget,
    QuietHours,
    deliver_configured_channels,
    signed_webhook_body,
)


def test_configured_channel_delivery_is_egress_only_and_deduplicated():
    calls = []

    async def send(platform, platform_config, chat_id, content, thread_id):
        calls.append((platform, platform_config, chat_id, content, thread_id))
        return {"success": True, "message_id": "sent-1"}

    config = SimpleNamespace(
        platforms={Platform.SLACK: SimpleNamespace(enabled=True)}
    )
    notification = {
        "id": 7,
        "kind": "approval",
        "urgency": "high",
        "title": "Approval required",
        "body": "Release spend exceeds the threshold.",
        "link": "https://pmo.local/pmo?view=approvals&id=a7",
        "egressed": False,
    }
    target = NotificationTarget("slack", "C123")

    receipts = asyncio.run(
        deliver_configured_channels(
            [notification],
            [target, target],
            now=datetime(2026, 8, 5, 22, tzinfo=timezone.utc),
            quiet=QuietHours(time(20), time(8), "UTC"),
            gateway_config=config,
            sender=send,
        )
    )

    assert len(calls) == 1
    assert calls[0][0] is Platform.SLACK
    assert "Open PM-OS:" in calls[0][3]
    assert [(item.delivered, item.message_id) for item in receipts] == [(True, "sent-1")]
    # No message/comment/wake callback is accepted by the API; the durable id
    # only comes back as a receipt for the coordinator to mark separately.
    assert receipts[0].notification_id == 7


def test_delivery_failure_remains_retryable():
    async def fail(*_args):
        return {"error": "temporary gateway outage"}

    config = SimpleNamespace(
        platforms={Platform.EMAIL: SimpleNamespace(enabled=True)}
    )
    receipts = asyncio.run(
        deliver_configured_channels(
            [{"id": 8, "urgency": "high", "title": "Blocked", "egressed": False}],
            [NotificationTarget("email", "owner@example.test")],
            now=datetime.now(timezone.utc),
            quiet=QuietHours(time(0), time(0), "UTC"),
            gateway_config=config,
            sender=fail,
        )
    )

    assert receipts[0].delivered is False
    assert receipts[0].error == "temporary gateway outage"


def test_webhook_payload_is_canonical_and_signed():
    body, signature = signed_webhook_body(
        {"id": 9, "kind": "mention", "urgency": "normal", "title": "Mention"},
        secret="shared-secret",
    )
    expected = hmac.new(b"shared-secret", body, hashlib.sha256).hexdigest()

    assert signature == f"sha256={expected}"
    assert body.startswith(b'{"body":""')
