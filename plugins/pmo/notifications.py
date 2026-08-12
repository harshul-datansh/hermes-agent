"""Read-only notification egress policy for PM-OS.

This module cannot create messages, comments, or agent wakes.  It accepts
already-durable inbox projections and can only call an outbound sender.  The
caller records successful delivery through the separate PMO coordination edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import hashlib
import hmac
import json
from typing import Awaitable, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class QuietHours:
    start: time
    end: time
    timezone: str = "Asia/Kolkata"


def _inside_quiet_hours(now: datetime, quiet: QuietHours) -> bool:
    local = now.astimezone(ZoneInfo(quiet.timezone)).time().replace(tzinfo=None)
    if quiet.start == quiet.end:
        return False
    if quiet.start < quiet.end:
        return quiet.start <= local < quiet.end
    return local >= quiet.start or local < quiet.end


def should_egress(
    notification: Mapping[str, object], *, now: datetime, quiet: QuietHours
) -> bool:
    """High urgency always passes; normal urgency observes quiet hours."""

    if bool(notification.get("egressed")):
        return False
    if str(notification.get("urgency", "normal")) == "high":
        return True
    return not _inside_quiet_hours(now, quiet)


OutboundSender = Callable[[Mapping[str, object]], None]


def deliver_outbound(
    notifications: Iterable[Mapping[str, object]],
    sender: OutboundSender,
    *,
    now: datetime,
    quiet: QuietHours,
    limit: int = 100,
) -> list[int]:
    """Send a bounded egress-only batch and return successful durable ids."""

    sent: list[int] = []
    for item in notifications:
        if len(sent) >= max(0, min(int(limit), 1000)):
            break
        if not should_egress(item, now=now, quiet=quiet):
            continue
        sender(item)
        sent.append(int(item["id"]))
    return sent


@dataclass(frozen=True)
class NotificationTarget:
    """One operator-configured egress target; never an inbound authority."""

    platform: str
    chat_id: str
    thread_id: str | None = None


@dataclass(frozen=True)
class EgressReceipt:
    notification_id: int
    platform: str
    delivered: bool
    message_id: str | None = None
    error: str | None = None


def render_notification(notification: Mapping[str, object]) -> str:
    """Render a compact alert whose only action is a dashboard deep link."""

    title = str(notification.get("title") or "PM-OS notification").strip()
    body = str(notification.get("body") or "").strip()
    link = str(notification.get("link") or "").strip()
    parts = [title]
    if body:
        parts.append(body)
    if link:
        parts.append(f"Open PM-OS: {link}")
    return "\n\n".join(parts)


AsyncPlatformSender = Callable[
    [object, object, str, str, str | None], Awaitable[Mapping[str, object]]
]


async def deliver_configured_channels(
    notifications: Iterable[Mapping[str, object]],
    targets: Sequence[NotificationTarget],
    *,
    now: datetime,
    quiet: QuietHours,
    gateway_config=None,
    sender: AsyncPlatformSender | None = None,
    limit: int = 100,
) -> list[EgressReceipt]:
    """Project durable notifications through configured Hermes channels.

    This function has no board, comment, conversation, or wake handle.  It can
    only call Hermes' existing outbound adapter seam and return receipts for a
    separate coordinator to persist.  Channel replies therefore cannot become
    project instructions accidentally.
    """

    if gateway_config is None:
        from gateway.config import load_gateway_config

        gateway_config = load_gateway_config()
    if sender is None:
        from tools.send_message_tool import _send_via_adapter

        async def sender(platform, platform_config, chat_id, content, thread_id):
            return await _send_via_adapter(
                platform,
                platform_config,
                chat_id,
                content,
                thread_id=thread_id,
            )

    from gateway.config import Platform

    selected = [
        item
        for item in notifications
        if should_egress(item, now=now, quiet=quiet)
    ][: max(0, min(int(limit), 1000))]
    receipts: list[EgressReceipt] = []
    seen: set[tuple[int, str, str, str | None]] = set()
    for item in selected:
        notification_id = int(item["id"])
        content = render_notification(item)
        for target in targets:
            key = (notification_id, target.platform, target.chat_id, target.thread_id)
            if key in seen:
                continue
            seen.add(key)
            try:
                platform = Platform(target.platform)
            except ValueError:
                receipts.append(
                    EgressReceipt(
                        notification_id,
                        target.platform,
                        False,
                        error="unknown Hermes gateway platform",
                    )
                )
                continue
            platform_config = gateway_config.platforms.get(platform)
            if platform_config is None or not platform_config.enabled:
                receipts.append(
                    EgressReceipt(
                        notification_id,
                        target.platform,
                        False,
                        error="gateway platform is not configured and enabled",
                    )
                )
                continue
            try:
                result = await sender(
                    platform,
                    platform_config,
                    target.chat_id,
                    content,
                    target.thread_id,
                )
            except Exception as exc:
                result = {"error": str(exc)}
            delivered = bool(result.get("success"))
            receipts.append(
                EgressReceipt(
                    notification_id=notification_id,
                    platform=target.platform,
                    delivered=delivered,
                    message_id=(str(result.get("message_id")) if result.get("message_id") else None),
                    error=(str(result.get("error")) if result.get("error") else None),
                )
            )
    return receipts


def signed_webhook_body(
    notification: Mapping[str, object], *, secret: str
) -> tuple[bytes, str]:
    """Return canonical JSON and its SHA-256 HMAC for generic webhook egress."""

    if not secret:
        raise ValueError("webhook secret is required")
    payload = {
        "schema": "datansh-pmo-notification.v1",
        "id": int(notification["id"]),
        "kind": str(notification.get("kind") or "notification"),
        "urgency": str(notification.get("urgency") or "normal"),
        "title": str(notification.get("title") or ""),
        "body": str(notification.get("body") or ""),
        "link": str(notification.get("link") or ""),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, f"sha256={signature}"
