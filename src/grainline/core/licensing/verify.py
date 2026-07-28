"""Offline Ed25519 licence verification.

Signature verification needs only the public key, so a machine that will never
see the internet again after commissioning can still prove a licence is
genuine. That matters here more than in most products: CNC networks are
deliberately air-gapped, and a licensing scheme that phones home is a scheme
these customers cannot buy.

What this design does and does not achieve:

* It **does** make licences unforgeable without the private key, which never
  leaves the build machine and is never shipped in any build.
* It **does not** stop a determined user from patching the binary. Nothing
  client-side can. The goal is to keep honest customers honest and to make
  casual key-sharing fail, not to win an arms race against someone who has
  already decided not to pay.

The public key below is a compile-time constant on purpose. Reading it from an
environment variable would let anyone substitute their own keypair and mint
themselves a Pro licence, which would make the whole scheme decorative.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .tier import Tier
from .token import (
    License,
    LicenseError,
    decode_token,
    encode_payload,
)

__all__ = [
    "LicenseVerifier",
    "RELEASE_PUBLIC_KEY_HEX",
    "active_license",
    "active_tier",
    "clear_cache",
    "license_search_paths",
    "ENV_LICENSE",
    "ENV_HOME",
]

#: Ed25519 public key for release licences, as raw 32-byte hex. The matching
#: private key lives only on the build machine and is excluded from version
#: control by .gitignore.
RELEASE_PUBLIC_KEY_HEX = (
    "d8572895f63cdf0dc92c2e680a2347a6cabdca01452396452b540bcc93b7e7ec"
)

#: Environment variable holding either a licence token or a path to one.
ENV_LICENSE = "GRAINLINE_LICENSE"

#: Environment variable overriding where GRAINLINE keeps its configuration.
ENV_HOME = "GRAINLINE_HOME"

#: Filename looked for inside the GRAINLINE home directory.
_LICENSE_FILENAME = "license.key"


def grainline_home() -> Path:
    """Directory holding per-machine configuration and the licence file."""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "grainline"

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "grainline"
    return Path.home() / ".config" / "grainline"


def license_search_paths() -> list[Path]:
    """Every location searched for a licence file, in priority order.

    Exposed so the CLI can print the list when no licence is found. "Licence not
    found" without saying where it looked is a guaranteed support ticket.
    """
    return [
        Path.cwd() / _LICENSE_FILENAME,
        Path.cwd() / ".grainline" / _LICENSE_FILENAME,
        grainline_home() / _LICENSE_FILENAME,
    ]


@dataclass(frozen=True, slots=True)
class LicenseVerifier:
    """Verifies licence tokens against a specific public key.

    Instantiated with an explicit key rather than reading the module constant so
    that tests can exercise the full signing path with their own throwaway
    keypair. Production always uses :meth:`release`.
    """

    public_key_hex: str

    @classmethod
    def release(cls) -> LicenseVerifier:
        """The verifier for officially issued licences."""
        return cls(RELEASE_PUBLIC_KEY_HEX)

    def verify(self, token: str) -> License:
        """Verify a token's signature and return the licence it asserts.

        Raises:
            LicenseError: The token is malformed, or its signature does not
                match this public key.
        """
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        payload_bytes, signature = decode_token(token)

        try:
            key = Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(self.public_key_hex)
            )
        except ValueError as exc:  # pragma: no cover - constant is well-formed
            raise LicenseError(f"licence public key is malformed: {exc}") from exc

        try:
            key.verify(signature, payload_bytes)
        except InvalidSignature as exc:
            raise LicenseError(
                "licence signature is not valid - the key was altered, "
                "truncated, or was not issued by GRAINLINE"
            ) from exc

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LicenseError(f"licence payload is not valid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise LicenseError("licence payload must be a JSON object")

        # Re-encoding must reproduce the signed bytes exactly. Without this a
        # token could carry unsigned extra fields alongside the signed ones and
        # a future reader that started honouring them would be trusting
        # unauthenticated data.
        licence = License.from_payload(payload)
        if encode_payload(payload) != payload_bytes:
            raise LicenseError(
                "licence payload is not in canonical form; it may have been "
                "edited after signing"
            )

        return licence


class _EnvironmentMisconfigured(LicenseError):
    """Raised when GRAINLINE_LICENSE is set but cannot be resolved."""


def _read_token_from_environment() -> str | None:
    """Token supplied via ``GRAINLINE_LICENSE``, inline or by path.

    Raises:
        _EnvironmentMisconfigured: The variable is set but names a path that
            does not exist or cannot be read. Falling through to the disk search
            here would report "no licence installed" to an operator who
            explicitly configured one - the classic mistyped Docker volume
            mount, invisible and infuriating.
    """
    raw = os.environ.get(ENV_LICENSE)
    if not raw:
        return None

    value = raw.strip()
    if value.startswith("GL1-"):
        return value

    # Anything else is treated as a path, which is how a Docker deployment or a
    # shop's provisioning script usually supplies it.
    path = Path(value).expanduser()
    if not path.is_file():
        raise _EnvironmentMisconfigured(
            f"{ENV_LICENSE} is set to {value!r}, which is neither a licence "
            f"token (those start with 'GL1-') nor a readable file"
        )
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise _EnvironmentMisconfigured(
            f"{ENV_LICENSE} points at {path}, which could not be read: {exc}"
        ) from exc

    if not text:
        raise _EnvironmentMisconfigured(f"{ENV_LICENSE} points at {path}, which is empty")
    return text


def _read_token_from_disk() -> str | None:
    """First licence file found in the search path."""
    for candidate in license_search_paths():
        try:
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except OSError:
            # An unreadable candidate must not abort the search; the next
            # location may hold a perfectly good licence.
            continue
    return None


@lru_cache(maxsize=1)
def _resolve() -> tuple[License, str]:
    """Resolve the active licence once per process. Returns (licence, note)."""
    try:
        token = _read_token_from_environment() or _read_token_from_disk()
    except _EnvironmentMisconfigured as exc:
        return License(), str(exc)

    if not token:
        return License(), ""

    try:
        return LicenseVerifier.release().verify(token), ""
    except LicenseError as exc:
        # Falling back to Free rather than raising is deliberate: a corrupt
        # licence must degrade the product, never break it. The reason travels
        # with the result so the CLI can show it.
        return License(), str(exc)


def active_license() -> License:
    """The licence installed on this machine, or a Free licence if none is."""
    return _resolve()[0]


def active_license_problem() -> str:
    """Why an installed licence was rejected, or an empty string."""
    return _resolve()[1]


def active_tier() -> Tier:
    """The tier this machine is entitled to right now."""
    return active_license().effective_tier


def clear_cache() -> None:
    """Forget the resolved licence. Used by tests and by ``grainline licence set``."""
    _resolve.cache_clear()
