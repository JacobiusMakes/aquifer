"""Unit tests for the patient portability system (PatientHub).

Tests patient registration, OTP verification, practice linking,
consent lifecycle, data transfer, and domain classification.
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aquifer.core import DataDomain, PRACTICE_TYPE_DEFAULTS
from aquifer.engine.pipeline import _classify_domain
from aquifer.strata.database import StrataDB
from aquifer.strata.patient_hub import PatientHub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """A fresh StrataDB for each test."""
    db = StrataDB(tmp_path / "test.db")
    db.connect()
    yield db
    db.close()


@pytest.fixture
def practice_a(tmp_db):
    """Insert a source practice row into the DB."""
    pid = str(uuid.uuid4())
    tmp_db.create_practice(
        id=pid,
        name="Practice A",
        slug="practice-a",
        vault_key_encrypted="enc-key-a",
    )
    return pid


@pytest.fixture
def practice_b(tmp_db):
    """Insert a target practice row into the DB."""
    pid = str(uuid.uuid4())
    tmp_db.create_practice(
        id=pid,
        name="Practice B",
        slug="practice-b",
        vault_key_encrypted="enc-key-b",
    )
    return pid


@pytest.fixture
def mock_vault_manager():
    """A vault manager that returns mock vaults."""
    mgr = MagicMock()
    vault = MagicMock()
    vault.encryption_key = b"0" * 32
    vault.get_tokens_for_file.return_value = []
    mgr.open_vault.return_value = vault
    return mgr, vault


@pytest.fixture
def hub(tmp_db, mock_vault_manager):
    """A PatientHub wired to a real DB and a mock vault manager."""
    mgr, _ = mock_vault_manager
    config = MagicMock()
    return PatientHub(tmp_db, mgr, config)


# ---------------------------------------------------------------------------
# TestPatientRegistration
# ---------------------------------------------------------------------------

class TestPatientRegistration:
    def test_register_patient(self, hub):
        identity = hub.register_patient("Alice@Example.com")
        assert identity.patient_id
        assert identity.email == "alice@example.com"

    def test_register_duplicate_email(self, hub):
        hub.register_patient("duplicate@example.com")
        with pytest.raises(ValueError, match="already registered"):
            hub.register_patient("DUPLICATE@example.com")

    def test_generate_and_verify_otp(self, hub):
        identity = hub.register_patient("otp-test@example.com")
        otp, email_sent = hub.generate_otp(identity.patient_id)
        assert len(otp) == 6
        assert otp.isdigit()
        assert email_sent is False

        ok = hub.verify_patient(identity.patient_id, otp)
        assert ok is True

        # email_verified flag should be set
        patient = hub.db.get_patient(identity.patient_id)
        assert patient["email_verified"] == 1

    def test_verify_wrong_otp(self, hub):
        identity = hub.register_patient("wrong-otp@example.com")
        hub.generate_otp(identity.patient_id)
        ok = hub.verify_patient(identity.patient_id, "000000")
        assert ok is False

    def test_verify_expired_otp(self, hub):
        identity = hub.register_patient("expired-otp@example.com")
        hub.generate_otp(identity.patient_id)

        # Back-date the expiry to the past
        past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        hub.db.update_patient_otp(
            identity.patient_id,
            PatientHub._hash_otp("123456"),
            past,
        )
        ok = hub.verify_patient(identity.patient_id, "123456")
        assert ok is False


# ---------------------------------------------------------------------------
# TestPatientPracticeLinks
# ---------------------------------------------------------------------------

class TestPatientPracticeLinks:
    def test_link_patient_to_practice(self, hub, tmp_db, practice_a):
        identity = hub.register_patient("link-test@example.com")
        hub.link_patient_to_practice(identity.patient_id, practice_a)

        practices = tmp_db.get_patient_practices(identity.patient_id)
        assert len(practices) == 1
        assert practices[0]["practice_id"] == practice_a

    def test_link_patient_to_multiple_practices(self, hub, tmp_db, practice_a, practice_b):
        identity = hub.register_patient("multi-link@example.com")
        hub.link_patient_to_practice(identity.patient_id, practice_a)
        hub.link_patient_to_practice(identity.patient_id, practice_b)

        practices = tmp_db.get_patient_practices(identity.patient_id)
        practice_ids = {p["practice_id"] for p in practices}
        assert practice_ids == {practice_a, practice_b}

    def test_get_practice_patients(self, hub, tmp_db, practice_a):
        id1 = hub.register_patient("patient1@example.com")
        id2 = hub.register_patient("patient2@example.com")
        hub.link_patient_to_practice(id1.patient_id, practice_a)
        hub.link_patient_to_practice(id2.patient_id, practice_a)

        links = tmp_db.get_practice_patients(practice_a)
        patient_ids = {l["patient_id"] for l in links}
        assert id1.patient_id in patient_ids
        assert id2.patient_id in patient_ids


# ---------------------------------------------------------------------------
# TestConsentManagement
# ---------------------------------------------------------------------------

class TestConsentManagement:
    def test_create_consent(self, hub, practice_a, practice_b):
        identity = hub.register_patient("consent-create@example.com")
        consent = hub.create_consent(
            patient_id=identity.patient_id,
            source_practice_id=practice_a,
            target_practice_id=practice_b,
        )
        assert consent.consent_id
        assert consent.status == "pending"
        assert consent.patient_id == identity.patient_id
        assert consent.source_practice_id == practice_a
        assert consent.target_practice_id == practice_b

    def test_authorize_consent(self, hub, practice_a, practice_b):
        identity = hub.register_patient("consent-auth@example.com")
        consent = hub.create_consent(
            patient_id=identity.patient_id,
            source_practice_id=practice_a,
            target_practice_id=practice_b,
        )
        authorized = hub.authorize_consent(consent.consent_id, identity.patient_id)
        assert authorized.status == "authorized"
        assert authorized.authorized_at is not None
        assert authorized.expires_at is not None

    def test_revoke_consent(self, hub, practice_a, practice_b):
        identity = hub.register_patient("consent-revoke@example.com")
        consent = hub.create_consent(
            patient_id=identity.patient_id,
            source_practice_id=practice_a,
            target_practice_id=practice_b,
        )
        result = hub.revoke_consent(consent.consent_id, identity.patient_id)
        assert result is True

        row = hub.db.get_consent(consent.consent_id)
        assert row["status"] == "revoked"

    def test_consent_with_practice_type_auto_scope(self):
        """PRACTICE_TYPE_DEFAULTS for 'dental' includes expected domains."""
        defaults = PRACTICE_TYPE_DEFAULTS["dental"]
        scope_str = ",".join(sorted(str(d.value) for d in defaults))
        # Should contain core dental domains
        assert "dental" in scope_str
        assert "demographics" in scope_str
        assert "medications" in scope_str
        assert "allergies" in scope_str

    def test_consent_explicit_scope_overrides_practice_type(self, hub, practice_a, practice_b):
        """When an explicit non-'all' scope is provided, it should pass through as-is."""
        identity = hub.register_patient("explicit-scope@example.com")
        explicit_scope = "demographics,insurance"
        consent = hub.create_consent(
            patient_id=identity.patient_id,
            source_practice_id=practice_a,
            target_practice_id=practice_b,
            scope=explicit_scope,
        )
        assert consent.scope == explicit_scope

    def test_consent_scope_all(self, hub, practice_a, practice_b):
        identity = hub.register_patient("scope-all@example.com")
        consent = hub.create_consent(
            patient_id=identity.patient_id,
            source_practice_id=practice_a,
            target_practice_id=practice_b,
            scope="all",
        )
        assert consent.scope == "all"


# ---------------------------------------------------------------------------
# TestDataTransfer
# ---------------------------------------------------------------------------

class TestDataTransfer:
    def _make_hub_with_real_vaults(self, tmp_path, tmp_db, practice_a_id, practice_b_id):
        """Set up a PatientHub with real vault files for transfer tests."""
        from aquifer.strata.cloud_vault import CloudVaultManager
        from aquifer.strata.config import StrataConfig

        config = StrataConfig()
        config.debug = True
        config.master_key = "test-master-key-for-transfer-tests"
        config.jwt_secret = "test-jwt-secret-not-used"
        config.data_dir = tmp_path
        config.db_path = tmp_path / "test.db"

        # Initialize vault directories and vault files for both practices
        mgr = CloudVaultManager(config)
        mgr.practices_dir = tmp_path / "practices"
        mgr.practices_dir.mkdir(parents=True, exist_ok=True)

        from aquifer.strata.auth import encrypt_vault_key
        vault_password_a = "vault-password-for-practice-a"
        vault_password_b = "vault-password-for-practice-b"

        mgr.init_practice(practice_a_id, vault_password_a)
        mgr.init_practice(practice_b_id, vault_password_b)

        enc_key_a = encrypt_vault_key(vault_password_a, config.master_key)
        enc_key_b = encrypt_vault_key(vault_password_b, config.master_key)

        # Update the DB rows with real encrypted keys
        tmp_db.conn.execute(
            "UPDATE practices SET vault_key_encrypted = ? WHERE id = ?",
            (enc_key_a, practice_a_id),
        )
        tmp_db.conn.execute(
            "UPDATE practices SET vault_key_encrypted = ? WHERE id = ?",
            (enc_key_b, practice_b_id),
        )
        tmp_db.conn.commit()

        hub = PatientHub(tmp_db, mgr, config)
        return hub, mgr

    def test_execute_transfer_basic(self, tmp_path, tmp_db, practice_a, practice_b):
        hub, mgr = self._make_hub_with_real_vaults(
            tmp_path, tmp_db, practice_a, practice_b
        )

        # Register patient and link to source practice
        identity = hub.register_patient("transfer-basic@example.com")

        # Store a token directly in the source vault to simulate processed file
        source_vault = mgr.open_vault(
            practice_a,
            tmp_db.get_practice(practice_a)["vault_key_encrypted"],
            db=tmp_db,
        )
        fake_file_hash = "abc123filehashabcdef0123456789ab"
        token_id = str(uuid.uuid4())
        source_vault.store_token(
            token_id=token_id,
            phi_type="name",
            phi_value="John Doe",
            source_file_hash=fake_file_hash,
            aqf_file_hash=None,
            confidence=1.0,
        )

        # Record the file in processed_files so domain filtering can find it
        file_record_id = str(uuid.uuid4())
        tmp_db.create_file_record(
            id=file_record_id,
            practice_id=practice_a,
            original_filename="intake.txt",
            source_type="txt",
            source_hash=fake_file_hash,
            file_size_bytes=100,
            data_domain="demographics",
        )

        # Link patient with file hash
        hub.link_patient_to_practice(identity.patient_id, practice_a, fake_file_hash)

        # Create and authorize consent
        consent = hub.create_consent(
            patient_id=identity.patient_id,
            source_practice_id=practice_a,
            target_practice_id=practice_b,
        )
        hub.authorize_consent(consent.consent_id, identity.patient_id)

        # Execute transfer
        transfer = hub.execute_transfer(consent.consent_id)
        assert transfer.status == "completed"
        assert transfer.token_count == 1

        # Verify token appears in target vault
        target_vault = mgr.open_vault(
            practice_b,
            tmp_db.get_practice(practice_b)["vault_key_encrypted"],
            db=tmp_db,
        )
        tokens = target_vault.get_tokens_for_file(fake_file_hash)
        assert len(tokens) == 1
        assert tokens[0].phi_value == "John Doe"

    def test_transfer_respects_domain_scope(self, tmp_path, tmp_db, practice_a, practice_b):
        """Tokens from files whose data_domain is not in scope are excluded.

        The secondary phi-type filter in _phi_type_in_scope uses "dental_history"
        as the key for tooth/crown/filling phi types (matching DataDomain.DENTAL
        file-level domain). We use a phi_type containing "tooth" so it passes when
        scope includes "dental_history", and scope out "medical_history" files.
        """
        hub, mgr = self._make_hub_with_real_vaults(
            tmp_path, tmp_db, practice_a, practice_b
        )

        identity = hub.register_patient("transfer-scope@example.com")
        source_vault = mgr.open_vault(
            practice_a,
            tmp_db.get_practice(practice_a)["vault_key_encrypted"],
            db=tmp_db,
        )

        dental_hash = "dental" + "0" * 27
        medical_hash = "medical" + "0" * 25

        # phi_type "tooth_number" contains "tooth" — matches the "dental_history" bucket
        source_vault.store_token(
            token_id=str(uuid.uuid4()),
            phi_type="tooth_number",
            phi_value="#14",
            source_file_hash=dental_hash,
        )
        source_vault.store_token(
            token_id=str(uuid.uuid4()),
            phi_type="diagnosis",
            phi_value="Type 2 Diabetes",
            source_file_hash=medical_hash,
        )

        # data_domain="dental_history" matches both file-level and phi-type scope filters
        tmp_db.create_file_record(
            id=str(uuid.uuid4()),
            practice_id=practice_a,
            original_filename="dental.txt",
            source_type="txt",
            source_hash=dental_hash,
            file_size_bytes=50,
            data_domain="dental",
        )
        tmp_db.create_file_record(
            id=str(uuid.uuid4()),
            practice_id=practice_a,
            original_filename="medical.txt",
            source_type="txt",
            source_hash=medical_hash,
            file_size_bytes=50,
            data_domain="medical_history",
        )

        combined_hashes = f"{dental_hash},{medical_hash}"
        hub.link_patient_to_practice(identity.patient_id, practice_a, combined_hashes)

        # Consent scoped to dental only — matches DataDomain.DENTAL and the
        # scope_map key in _phi_type_in_scope; filters out medical_history file
        consent = hub.create_consent(
            patient_id=identity.patient_id,
            source_practice_id=practice_a,
            target_practice_id=practice_b,
            scope="dental",
        )
        hub.authorize_consent(consent.consent_id, identity.patient_id)

        transfer = hub.execute_transfer(consent.consent_id)
        assert transfer.status == "completed"
        assert transfer.token_count == 1

        target_vault = mgr.open_vault(
            practice_b,
            tmp_db.get_practice(practice_b)["vault_key_encrypted"],
            db=tmp_db,
        )
        dental_tokens = target_vault.get_tokens_for_file(dental_hash)
        medical_tokens = target_vault.get_tokens_for_file(medical_hash)
        assert len(dental_tokens) == 1
        assert len(medical_tokens) == 0

    def test_transfer_fails_without_authorization(self, tmp_path, tmp_db, practice_a, practice_b):
        hub, mgr = self._make_hub_with_real_vaults(
            tmp_path, tmp_db, practice_a, practice_b
        )

        identity = hub.register_patient("transfer-no-auth@example.com")
        consent = hub.create_consent(
            patient_id=identity.patient_id,
            source_practice_id=practice_a,
            target_practice_id=practice_b,
        )
        # Status is pending — do NOT authorize

        with pytest.raises(ValueError, match="authorized consent"):
            hub.execute_transfer(consent.consent_id)

    def test_transfer_fails_with_expired_consent(self, tmp_path, tmp_db, practice_a, practice_b):
        hub, mgr = self._make_hub_with_real_vaults(
            tmp_path, tmp_db, practice_a, practice_b
        )

        identity = hub.register_patient("transfer-expired@example.com")
        consent = hub.create_consent(
            patient_id=identity.patient_id,
            source_practice_id=practice_a,
            target_practice_id=practice_b,
        )
        hub.authorize_consent(consent.consent_id, identity.patient_id)

        # Force expiry into the past
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        tmp_db.update_consent_status(consent.consent_id, status="authorized", expires_at=past)

        with pytest.raises(ValueError, match="expired"):
            hub.execute_transfer(consent.consent_id)

    def test_transfer_revoked_consent_fails(self, tmp_path, tmp_db, practice_a, practice_b):
        hub, mgr = self._make_hub_with_real_vaults(
            tmp_path, tmp_db, practice_a, practice_b
        )

        identity = hub.register_patient("transfer-revoked@example.com")
        consent = hub.create_consent(
            patient_id=identity.patient_id,
            source_practice_id=practice_a,
            target_practice_id=practice_b,
        )
        hub.revoke_consent(consent.consent_id, identity.patient_id)

        with pytest.raises(ValueError, match="authorized consent"):
            hub.execute_transfer(consent.consent_id)


# ---------------------------------------------------------------------------
# TestDomainClassification
# ---------------------------------------------------------------------------

class TestDomainClassification:
    def test_classify_dental_document(self):
        text = "Patient presents with tooth #14 needing a crown. Filling on #3 was placed."
        assert _classify_domain(text, "txt") == "dental"

    def test_classify_insurance_document(self):
        text = "Member ID: 123456789. Policy number: POL-987. Coverage includes copay of $20."
        assert _classify_domain(text, "txt") == "insurance"

    def test_classify_medication_list(self):
        text = "Current medication: Metformin 500mg tablet twice daily. Lisinopril 10mg once daily."
        assert _classify_domain(text, "txt") == "medications"

    def test_classify_allergy_record(self):
        text = "Allergy: Penicillin — severe reaction, anaphylaxis noted."
        assert _classify_domain(text, "txt") == "allergies"

    def test_classify_medical_history(self):
        text = "Past medical history includes diagnosis of diabetes and hypertension. Surgery in 2019."
        assert _classify_domain(text, "txt") == "medical_history"

    def test_classify_intake_form(self):
        text = "Patient information form. Date of birth: 01/01/1980. Emergency contact listed."
        assert _classify_domain(text, "txt") == "demographics"

    def test_classify_ambiguous_defaults_to_demographics(self):
        text = "This document contains no recognizable medical keywords."
        assert _classify_domain(text, "txt") == "demographics"
