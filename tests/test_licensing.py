"""Tests for license key generation, validation, and feature gating."""

from datetime import date, timedelta

import pytest

from aquifer.licensing import (
    Tier, License, TIER_FEATURES, TIER_FILE_LIMITS,
    generate_license_key, validate_license_key,
    require_feature, LicenseError,
)


class TestLicenseKeyGeneration:
    def test_generate_valid_key(self):
        key = generate_license_key(
            Tier.PROFESSIONAL,
            "practice-001",
            date.today() + timedelta(days=365),
        )
        assert key.startswith("AQ-PROF-")
        assert len(key.split("-")) == 4

    def test_generate_different_tiers(self):
        for tier, prefix in [
            (Tier.STARTER, "AQ-STAR-"),
            (Tier.PROFESSIONAL, "AQ-PROF-"),
            (Tier.ENTERPRISE, "AQ-ENTE-"),
        ]:
            key = generate_license_key(tier, "p1", date.today() + timedelta(days=30))
            assert key.startswith(prefix)


class TestLicenseValidation:
    def test_valid_key(self):
        key = generate_license_key(
            Tier.PROFESSIONAL,
            "practice-001",
            date.today() + timedelta(days=365),
        )
        lic = validate_license_key(key)
        assert lic.is_valid
        assert lic.tier == Tier.PROFESSIONAL
        assert lic.practice_id == "practice-001"

    def test_expired_key(self):
        key = generate_license_key(
            Tier.STARTER,
            "practice-001",
            date.today() - timedelta(days=1),  # expired yesterday
        )
        lic = validate_license_key(key)
        assert not lic.is_valid
        assert "expired" in lic.error.lower()

    def test_tampered_key(self):
        key = generate_license_key(
            Tier.ENTERPRISE,
            "practice-001",
            date.today() + timedelta(days=365),
        )
        # Tamper with the signature
        parts = key.rsplit("-", 1)
        tampered = parts[0] + "-" + "0" * 16
        lic = validate_license_key(tampered)
        assert not lic.is_valid
        assert "signature" in lic.error.lower()

    def test_invalid_format(self):
        lic = validate_license_key("not-a-real-key")
        assert not lic.is_valid

    def test_empty_key(self):
        lic = validate_license_key("")
        assert not lic.is_valid

    def test_wrong_signing_secret(self):
        key = generate_license_key(
            Tier.PROFESSIONAL,
            "p1",
            date.today() + timedelta(days=30),
            signing_secret=b"secret-1",
        )
        lic = validate_license_key(key, signing_secret=b"secret-2")
        assert not lic.is_valid


class TestTierFeatures:
    def test_community_has_basic_features(self):
        features = TIER_FEATURES[Tier.COMMUNITY]
        assert "deid" in features
        assert "aqf_read" in features
        assert "vault_local" in features
        assert "denial_prediction" not in features
        assert "appeal_generation" not in features

    def test_starter_has_claims_tracking(self):
        features = TIER_FEATURES[Tier.STARTER]
        assert "claims_tracking" in features
        assert "unlimited_files" in features
        assert "denial_prediction" not in features

    def test_professional_has_predictions(self):
        features = TIER_FEATURES[Tier.PROFESSIONAL]
        assert "denial_prediction" in features
        assert "appeal_generation" in features
        assert "qc_dashboard" in features
        assert "multi_location" not in features

    def test_enterprise_has_everything(self):
        features = TIER_FEATURES[Tier.ENTERPRISE]
        assert "multi_location" in features
        assert "cross_practice_analytics" in features
        assert "custom_ner" in features
        assert "api_access" in features

    def test_each_tier_is_superset_of_previous(self):
        tiers = [Tier.COMMUNITY, Tier.STARTER, Tier.PROFESSIONAL, Tier.ENTERPRISE]
        for i in range(1, len(tiers)):
            assert TIER_FEATURES[tiers[i - 1]].issubset(TIER_FEATURES[tiers[i]]), \
                f"{tiers[i].value} should be superset of {tiers[i-1].value}"


class TestFileLimits:
    def test_community_has_limit(self):
        assert TIER_FILE_LIMITS[Tier.COMMUNITY] == 100

    def test_paid_tiers_unlimited(self):
        assert TIER_FILE_LIMITS[Tier.STARTER] is None
        assert TIER_FILE_LIMITS[Tier.PROFESSIONAL] is None
        assert TIER_FILE_LIMITS[Tier.ENTERPRISE] is None


class TestFeatureGating:
    def test_has_feature(self):
        key = generate_license_key(
            Tier.PROFESSIONAL, "p1", date.today() + timedelta(days=30),
        )
        lic = validate_license_key(key)
        assert lic.has_feature("denial_prediction")
        assert lic.has_feature("deid")
        assert not lic.has_feature("multi_location")

    def test_expired_license_has_no_features(self):
        key = generate_license_key(
            Tier.ENTERPRISE, "p1", date.today() - timedelta(days=1),
        )
        lic = validate_license_key(key)
        assert not lic.has_feature("deid")
        assert not lic.has_feature("denial_prediction")
