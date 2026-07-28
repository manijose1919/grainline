"""Licence token structure, encoding and decoding.

A licence is a signed statement of fact: *this customer is entitled to this tier
until this date*. It is deliberately small, human-inspectable once decoded, and
carries no secret — everything in it is information the customer already knows.

Format::

    GL1-<base64url(payload_json)>.<base64url(ed25519_signature)>

Base64url without padding keeps the token free of characters that get mangled by
email clients, spreadsheet cells and support tickets, which is how licence keys
actually travel.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .tier import Tier

__all__ = [
    "License",
    "LicenseError",
    "TOKEN_PREFIX",
    "TOKEN_VERSION",
    "encode_payload",
    "decode_token",
    "b64u_encode",
    "b64u_decode",
]

#: Human-recognisable prefix. Support staff can identify a GRAINLINE key on
#: sight in a screenshot, and it version-stamps the format for future changes.
TOKEN_PREFIX = "GL1-"
TOKEN_VERSION = 1


class LicenseError(ValueError):
    """Raised when a licence token is malformed, forged or expired."""


def b64u_encode(data: bytes) -> str:
    """Base64url-encode without padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    """Base64url-decode, restoring stripped padding.

    Raises:
        LicenseError: The text is not valid base64url.
    """
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (ValueError, TypeError) as exc:
        raise LicenseError(f"licence token is not valid base64url: {exc}") from exc


@dataclass(frozen=True, slots=True)
class License:
    """The entitlement a token asserts.

    Attributes:
        tier: The tier granted.
        customer: Who the licence was issued to, shown in the CLI banner so an
            operator can confirm at a glance which licence a machine is running.
        seats: Number of seats purchased. Recorded for the customer's benefit;
            it is not enforced offline, because seat counting without a server
            is trivially defeated and pretending otherwise is dishonest.
        issued: Issue date.
        expires: Expiry date, or ``None`` for a perpetual licence.
        license_id: Unique reference for support and revocation lists.
        features: Optional named add-ons beyond the tier itself.
    """

    tier: Tier = Tier.FREE
    customer: str = ""
    seats: int = 1
    issued: date | None = None
    expires: date | None = None
    license_id: str = ""
    features: frozenset[str] = field(default_factory=frozenset)

    # -- state ------------------------------------------------------------

    def is_expired(self, on: date | None = None) -> bool:
        """True when the licence has lapsed as of ``on`` (default: today, UTC).

        Clock tampering defeats this, and it is meant to. Offline licensing can
        prove *authenticity* cryptographically but cannot prove *time*; a
        customer who sets their clock back has left the honest-customer path
        that offline licensing serves, and the answer to that is commercial,
        not technical.
        """
        if self.expires is None:
            return False
        today = on or datetime.now(timezone.utc).date()
        return today > self.expires

    def days_remaining(self, on: date | None = None) -> int | None:
        """Whole days until expiry, negative once lapsed, ``None`` if perpetual."""
        if self.expires is None:
            return None
        today = on or datetime.now(timezone.utc).date()
        return (self.expires - today).days

    @property
    def effective_tier(self) -> Tier:
        """The tier actually granted right now.

        An expired licence falls back to Free rather than locking the operator
        out. Their historic nests must keep opening and the core must keep
        working; losing access to the whole tool because a card expired turns a
        renewal conversation into a churn event.
        """
        return Tier.FREE if self.is_expired() else self.tier

    def has_feature(self, name: str) -> bool:
        """True when a named add-on is included and the licence is current."""
        return not self.is_expired() and name in self.features

    def describe(self) -> str:
        """One-line human summary for the CLI banner."""
        if self.tier is Tier.FREE:
            return "Free edition (no licence installed)"

        bits = [f"{self.tier.label} edition"]
        if self.customer:
            bits.append(f"licensed to {self.customer}")
        remaining = self.days_remaining()
        if remaining is None:
            bits.append("perpetual")
        elif remaining < 0:
            bits.append(f"EXPIRED {-remaining} day(s) ago - running as Free")
        elif remaining <= 30:
            bits.append(f"expires in {remaining} day(s)")
        else:
            bits.append(f"expires {self.expires.isoformat()}")
        return " - ".join(bits)

    # -- serialisation ----------------------------------------------------

    def to_payload(self) -> dict[str, Any]:
        """The exact dictionary that gets signed."""
        return {
            "v": TOKEN_VERSION,
            "tier": self.tier.name,
            "customer": self.customer,
            "seats": self.seats,
            "issued": self.issued.isoformat() if self.issued else None,
            "expires": self.expires.isoformat() if self.expires else None,
            "id": self.license_id,
            "features": sorted(self.features),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> License:
        """Rebuild a licence from a decoded payload.

        Raises:
            LicenseError: The payload is the wrong version or has bad dates.
        """
        version = payload.get("v")
        if version != TOKEN_VERSION:
            raise LicenseError(
                f"unsupported licence format version {version!r}; "
                f"this build understands version {TOKEN_VERSION}"
            )

        def parse_date(value: Any, field_name: str) -> date | None:
            if value in (None, ""):
                return None
            try:
                return date.fromisoformat(str(value))
            except ValueError as exc:
                raise LicenseError(
                    f"licence field {field_name!r} is not an ISO date: {value!r}"
                ) from exc

        seats = payload.get("seats", 1)
        if not isinstance(seats, int) or isinstance(seats, bool) or seats < 1:
            raise LicenseError(f"licence seats must be a positive integer, got {seats!r}")

        return cls(
            tier=Tier.parse(payload.get("tier")),
            customer=str(payload.get("customer") or ""),
            seats=seats,
            issued=parse_date(payload.get("issued"), "issued"),
            expires=parse_date(payload.get("expires"), "expires"),
            license_id=str(payload.get("id") or ""),
            features=frozenset(payload.get("features") or ()),
        )


def encode_payload(payload: dict[str, Any]) -> bytes:
    """Serialise a payload to the exact bytes that get signed.

    Sorted keys and no whitespace make the encoding canonical. Without that, a
    payload re-serialised by a different JSON library would produce different
    bytes and its signature would fail to verify for no visible reason.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def decode_token(token: str) -> tuple[bytes, bytes]:
    """Split a token into its signed payload bytes and signature bytes.

    Raises:
        LicenseError: The token is structurally invalid.
    """
    text = (token or "").strip()
    if not text:
        raise LicenseError("licence token is empty")

    if not text.startswith(TOKEN_PREFIX):
        raise LicenseError(
            f"licence token must start with {TOKEN_PREFIX!r}; "
            f"check that the whole key was pasted"
        )

    body = text[len(TOKEN_PREFIX) :]
    if body.count(".") != 1:
        raise LicenseError(
            "licence token must contain exactly one '.' separating the payload "
            "from the signature"
        )

    payload_b64, signature_b64 = body.split(".")
    if not payload_b64 or not signature_b64:
        raise LicenseError("licence token has an empty payload or signature")

    return b64u_decode(payload_b64), b64u_decode(signature_b64)
