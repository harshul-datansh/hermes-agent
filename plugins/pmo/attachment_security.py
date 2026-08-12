"""PMO attachment content sniffing and generated storage names."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass


SNIFF_BYTES = 8192


@dataclass(frozen=True)
class SniffedType:
    media_type: str
    suffix: str


def sniff_attachment(data: bytes) -> SniffedType:
    """Return an allowlisted content type based on bytes, never extension."""

    sample = bytes(data[:SNIFF_BYTES])
    if sample.startswith(b"%PDF-"):
        return SniffedType("application/pdf", ".pdf")
    if sample.startswith(b"\x89PNG\r\n\x1a\n"):
        return SniffedType("image/png", ".png")
    if sample.startswith(b"\xff\xd8\xff"):
        return SniffedType("image/jpeg", ".jpg")
    if sample.startswith((b"GIF87a", b"GIF89a")):
        return SniffedType("image/gif", ".gif")
    if sample.startswith(b"PK\x03\x04"):
        return SniffedType("application/zip", ".zip")
    if not sample:
        return SniffedType("text/plain", ".txt")
    if b"\x00" in sample:
        raise ValueError("attachment content type is not allowlisted")
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("attachment content type is not allowlisted") from exc
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            return SniffedType("application/json", ".json")
    return SniffedType("text/plain", ".txt")


def generated_storage_name(sniffed: SniffedType) -> str:
    return f"pmo_{uuid.uuid4().hex}{sniffed.suffix}"


def validate_attachment_filename(value: str) -> str:
    """Reject traversal, absolute, and NTFS alternate-stream names."""

    name = str(value or "").strip()
    if not name:
        raise ValueError("attachment filename is required")
    if name.startswith(("/", "\\")) or "/" in name or "\\" in name or ":" in name:
        raise ValueError("attachment filename must be a plain leaf name")
    if name in {".", ".."} or ".." in name:
        raise ValueError("attachment filename must not contain traversal components")
    return name
