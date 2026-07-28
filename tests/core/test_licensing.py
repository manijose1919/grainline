"""Tests for offline licence signing, verification and tier gating.

These exercise the *whole* signing path with a throwaway keypair rather than
mocking the crypto. A licensing test that mocks the signature check proves
nothing about the property that matters: that a forged token is rejected.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from grainline.core.licensing.tier import Tier
from grainline.core.licensing.token import (
    TOKEN_PREFIX,
    License,
    LicenseError,
    b64u_decode,
    b64u_encode,
    decode_token,
    encode_payload,
)
from grainline.core.licensing.verify import (
    ENV_HOME,
    ENV_LICENSE,
    RELEASE_PUBLIC_KEY_HEX,
    LicenseVerifier,
    active_license,
    active_tier,
    clear_cache,
    license_search_paths,
)

pytestmark = pytest.mark.free


# ---------------------------------------------------------------------------
# Test signing authority
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    return private, public_hex


@pytest.fixture
def sign(keypair):
    private, _ = keypair

    def _sign(licence: License) -> str:
        payload = encode_payload(licence.to_payload())
        signature = private.sign(payload)
        return f"{TOKEN_PREFIX}{b64u_encode(payload)}.{b64u_encode(signature)}"

    return _sign


@pytest.fixture
def verifier(keypair):
    _, public_hex = keypair
    return LicenseVerifier(public_hex)


@pytest.fixture(autouse=True)
def isolated_license_environment(monkeypatch, tmp_path):
    """Keep tests away from any real licence installed on the machine."""
    monkeypatch.delenv(ENV_LICENSE, raising=False)
    monkeypatch.setenv(ENV_HOME, str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_valid_token_round_trips(sign, verifier):
    original = License(
        tier=Tier.PRO,
        customer="Northgate Fabrication",
        seats=3,
        issued=date(2026, 1, 1),
        expires=date(2099, 1, 1),
        license_id="abc123",
        features=frozenset({"remnant-ledger"}),
    )
    recovered = verifier.verify(sign(original))

    assert recovered.tier is Tier.PRO
    assert recovered.customer == "Northgate Fabrication"
    assert recovered.seats == 3
    assert recovered.license_id == "abc123"
    assert recovered.features == frozenset({"remnant-ledger"})
    assert recovered.effective_tier is Tier.PRO


def test_perpetual_licence_never_expires(sign, verifier):
    licence = verifier.verify(sign(License(tier=Tier.PREMIUM, expires=None)))
    assert not licence.is_expired()
    assert licence.days_remaining() is None
    assert "perpetual" in licence.describe()


# ---------------------------------------------------------------------------
# Forgery resistance - the property that matters
# ---------------------------------------------------------------------------


def test_tampering_with_the_tier_invalidates_the_signature(sign, verifier):
    """The core attack: take a real Premium key and edit it to say Pro."""
    token = sign(License(tier=Tier.PREMIUM, customer="Honest Shop"))
    payload_b64, signature_b64 = token[len(TOKEN_PREFIX) :].split(".")

    payload = json.loads(b64u_decode(payload_b64).decode())
    assert payload["tier"] == "PREMIUM"
    payload["tier"] = "PRO"

    forged = (
        f"{TOKEN_PREFIX}{b64u_encode(encode_payload(payload))}.{signature_b64}"
    )
    with pytest.raises(LicenseError, match="signature is not valid"):
        verifier.verify(forged)


def test_extending_the_expiry_invalidates_the_signature(sign, verifier):
    token = sign(License(tier=Tier.PRO, expires=date(2026, 1, 1)))
    payload_b64, signature_b64 = token[len(TOKEN_PREFIX) :].split(".")
    payload = json.loads(b64u_decode(payload_b64).decode())
    payload["expires"] = "2199-01-01"
    forged = f"{TOKEN_PREFIX}{b64u_encode(encode_payload(payload))}.{signature_b64}"

    with pytest.raises(LicenseError, match="signature is not valid"):
        verifier.verify(forged)


def test_a_token_signed_by_a_different_key_is_rejected(sign):
    """A customer generating their own keypair must not be able to self-license."""
    token = sign(License(tier=Tier.PRO))
    with pytest.raises(LicenseError, match="signature is not valid"):
        LicenseVerifier(RELEASE_PUBLIC_KEY_HEX).verify(token)


def test_non_canonical_payload_is_rejected(keypair):
    """Unsigned extra whitespace or key order must not slip through.

    Without the canonical-form check, a token could carry fields alongside the
    signed ones and any future reader that honoured them would be trusting
    unauthenticated data.
    """
    private, public_hex = keypair
    payload = License(tier=Tier.PRO).to_payload()
    # Sign a *non-canonical* encoding: indented, unsorted.
    sloppy = json.dumps(payload, indent=2).encode("utf-8")
    signature = private.sign(sloppy)
    token = f"{TOKEN_PREFIX}{b64u_encode(sloppy)}.{b64u_encode(signature)}"

    with pytest.raises(LicenseError, match="canonical form"):
        LicenseVerifier(public_hex).verify(token)


@pytest.mark.parametrize(
    "token,match",
    [
        ("", "empty"),
        ("not-a-licence", "must start with"),
        ("GL1-onlyonepart", "exactly one"),
        ("GL1-a.b.c", "exactly one"),
        ("GL1-.sig", "empty payload"),
        ("GL1-payload.", "empty payload"),
    ],
)
def test_malformed_tokens_are_rejected_with_useful_messages(token, match):
    with pytest.raises(LicenseError, match=match):
        decode_token(token)


def test_truncated_signature_is_rejected(sign, verifier):
    token = sign(License(tier=Tier.PRO))
    truncated = token[:-8]
    with pytest.raises(LicenseError):
        verifier.verify(truncated)


# ---------------------------------------------------------------------------
# Expiry behaviour
# ---------------------------------------------------------------------------


def test_expired_licence_degrades_to_free_rather_than_locking_out(sign, verifier):
    """Losing the whole tool because a card expired turns renewal into churn."""
    yesterday = date.today() - timedelta(days=1)
    licence = verifier.verify(sign(License(tier=Tier.PRO, expires=yesterday)))

    assert licence.tier is Tier.PRO  # what was purchased
    assert licence.effective_tier is Tier.FREE  # what is granted today
    assert licence.is_expired()
    assert "EXPIRED" in licence.describe()


def test_expiry_warning_appears_within_thirty_days(sign, verifier):
    soon = date.today() + timedelta(days=10)
    licence = verifier.verify(sign(License(tier=Tier.PREMIUM, expires=soon)))
    assert "expires in 10 day(s)" in licence.describe()
    assert licence.effective_tier is Tier.PREMIUM


def test_features_are_unavailable_once_expired(sign, verifier):
    yesterday = date.today() - timedelta(days=1)
    licence = verifier.verify(
        sign(License(tier=Tier.PRO, expires=yesterday, features=frozenset({"api"})))
    )
    assert not licence.has_feature("api")


def test_payload_rejects_bad_dates(verifier):
    with pytest.raises(LicenseError, match="not an ISO date"):
        License.from_payload({"v": 1, "tier": "PRO", "expires": "next tuesday"})


def test_payload_rejects_wrong_version():
    with pytest.raises(LicenseError, match="unsupported licence format"):
        License.from_payload({"v": 99, "tier": "PRO"})


def test_payload_rejects_nonsense_seats():
    with pytest.raises(LicenseError, match="seats must be a positive integer"):
        License.from_payload({"v": 1, "tier": "PRO", "seats": 0})


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_no_licence_means_free():
    assert active_tier() is Tier.FREE
    assert "Free edition" in active_license().describe()


def test_corrupt_licence_degrades_to_free_and_explains_why(monkeypatch, tmp_path):
    from grainline.core.licensing.verify import active_license_problem

    monkeypatch.setenv(ENV_LICENSE, "GL1-garbage.garbage")
    clear_cache()

    assert active_tier() is Tier.FREE
    assert active_license_problem() != ""


def test_environment_variable_may_hold_a_path(monkeypatch, tmp_path, sign):
    """Provisioning scripts and Docker supply a path, not an inline token."""
    key_file = tmp_path / "corp.key"
    key_file.write_text(sign(License(tier=Tier.PRO)), encoding="utf-8")
    monkeypatch.setenv(ENV_LICENSE, str(key_file))
    clear_cache()

    # The release verifier will reject it (different key), but the *discovery*
    # path must have found and read it - proven by a signature error rather
    # than silence.
    from grainline.core.licensing.verify import active_license_problem

    assert "signature is not valid" in active_license_problem()


def test_misconfigured_environment_path_is_reported_not_ignored(monkeypatch, tmp_path):
    """A mistyped Docker volume mount must not read as "no licence installed"."""
    from grainline.core.licensing.verify import active_license_problem

    monkeypatch.setenv(ENV_LICENSE, str(tmp_path / "nope" / "missing.key"))
    clear_cache()

    assert active_tier() is Tier.FREE
    problem = active_license_problem()
    assert "GRAINLINE_LICENSE" in problem
    assert "readable file" in problem


def test_empty_licence_file_pointed_at_by_env_is_reported(monkeypatch, tmp_path):
    from grainline.core.licensing.verify import active_license_problem

    blank = tmp_path / "blank.key"
    blank.write_text("   \n", encoding="utf-8")
    monkeypatch.setenv(ENV_LICENSE, str(blank))
    clear_cache()

    assert "empty" in active_license_problem()


def test_search_paths_are_reported_for_support():
    paths = license_search_paths()
    assert len(paths) == 3
    assert all(p.name == "license.key" for p in paths)


def test_licence_file_in_cwd_is_discovered(monkeypatch, tmp_path, sign):
    (tmp_path / "license.key").write_text(sign(License(tier=Tier.PRO)), "utf-8")
    clear_cache()
    from grainline.core.licensing.verify import active_license_problem

    assert "signature is not valid" in active_license_problem()


def test_resolution_is_cached(monkeypatch, sign):
    """Licence resolution reads the filesystem; doing it per call is wasteful."""
    monkeypatch.setenv(ENV_LICENSE, "GL1-bad.bad")
    clear_cache()
    first = active_license()
    monkeypatch.setenv(ENV_LICENSE, "GL1-different.different")
    assert active_license() is first  # cached, not re-read
    clear_cache()


# ---------------------------------------------------------------------------
# Gating integration
# ---------------------------------------------------------------------------


def test_registry_gate_uses_the_resolved_tier():
    from grainline.core.nesting.guillotine import GuillotineNester
    from grainline.core.registry import (
        TierRequiredError,
        get_strategy,
        register_strategy,
    )

    register_strategy(
        "licence-gated-fixture",
        GuillotineNester,
        tier=Tier.PRO,
        upgrade_hint="Pro unlocks the remnant ledger.",
        replace=True,
    )

    with pytest.raises(TierRequiredError) as exc:
        get_strategy("licence-gated-fixture", tier=active_tier())
    assert exc.value.required is Tier.PRO


def test_release_public_key_is_a_valid_ed25519_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    raw = bytes.fromhex(RELEASE_PUBLIC_KEY_HEX)
    assert len(raw) == 32
    Ed25519PublicKey.from_public_bytes(raw)  # must not raise


def test_private_key_is_never_shipped_inside_the_package():
    """A packaging slip that ships the signing key would end the business."""
    import grainline

    package_root = Path(grainline.__file__).parent
    for path in package_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in {".pem", ".key"}:
            pytest.fail(f"key material found inside the package: {path}")
        if path.suffix == ".py":
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "BEGIN PRIVATE KEY" not in text, f"private key embedded in {path}"
            assert "BEGIN OPENSSH PRIVATE KEY" not in text
