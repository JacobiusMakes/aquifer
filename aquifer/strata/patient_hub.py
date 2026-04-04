"""Patient data portability — cross-practice record sharing.

Enables patients to authorize secure transfer of their intake data
between practices. PHI is re-encrypted between vaults server-side
and never exists in plaintext on the wire or disk.

Flow:
1. Patient registers a portable identity (email + OTP verification)
2. Practice A de-identifies patient's intake forms (existing pipeline)
3. Patient authorizes Practice B to receive their data
4. Aquifer rehydrates from A's vault, re-encrypts into B's vault
5. Practice B receives pre-filled intake data
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pydantic import BaseModel

from aquifer.vault.encryption import decrypt_value, encrypt_value


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class PatientIdentity(BaseModel):
    patient_id: str
    email: str
    phone: str | None
    created_at: str
    share_key: str | None = None


class ConsentRecord(BaseModel):
    consent_id: str
    patient_id: str
    source_practice_id: str
    target_practice_id: str
    scope: str
    status: str
    authorized_at: str | None
    expires_at: str
    created_at: str


class TransferRecord(BaseModel):
    transfer_id: str
    consent_id: str
    token_count: int
    status: str
    created_at: str


# ---------------------------------------------------------------------------
# PatientHub
# ---------------------------------------------------------------------------

class PatientHub:
    """Orchestrates patient identity, consent, and cross-practice data transfer."""

    # OTPs expire after 15 minutes
    _OTP_TTL_MINUTES = 15
    # Consents expire 24 hours after authorization
    _CONSENT_TTL_HOURS = 24
    # Rate limiting: max failed attempts per window
    _MAX_OTP_ATTEMPTS = 5
    _OTP_ATTEMPT_WINDOW_SECONDS = 15 * 60

    def __init__(self, db, vault_manager, config):
        self.db = db
        self.vault_manager = vault_manager
        self.config = config
        self._otp_attempts: dict[str, list[float]] = {}

    # --- Patient Identity ---

    # Share key alphabet — no O/0/I/1/L to avoid ambiguity
    _SHARE_KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

    @staticmethod
    def _generate_share_key() -> str:
        """Generate a unique share key in AQ-XXXX-XXXX format.

        Uses an unambiguous alphabet (no O, 0, I, 1, L) so patients can
        read and type the key reliably from a card or screen.
        """
        alphabet = PatientHub._SHARE_KEY_ALPHABET
        segment = lambda: "".join(secrets.choice(alphabet) for _ in range(4))
        return f"AQ-{segment()}-{segment()}"

    def register_patient(self, email: str, phone: str | None = None) -> PatientIdentity:
        """Create a new portable patient identity.

        Returns the new PatientIdentity. Raises ValueError if the email is
        already registered.
        """
        email = email.lower().strip()
        existing = self.db.get_patient_by_email(email)
        if existing:
            raise ValueError(f"Email already registered: {email}")

        patient_id = str(uuid.uuid4())
        share_key = self._generate_share_key()
        row = self.db.create_patient(patient_id, email, phone)
        self.db.set_patient_share_key(patient_id, share_key)
        row = self.db.get_patient(patient_id)
        return PatientIdentity(
            patient_id=row["id"],
            email=row["email"],
            phone=row["phone"],
            created_at=str(row["created_at"]),
            share_key=row["share_key"],
        )

    def verify_patient(self, patient_id: str, otp: str) -> bool:
        """Verify a one-time password for a patient.

        Checks the stored OTP hash and expiry. Marks the patient's email as
        verified on success. Returns True on success, False otherwise.

        Note: OTP generation and delivery (Phase B email integration) is
        handled by generate_otp() below.
        """
        # Rate limit: max 5 failed attempts per 15-minute window
        now = time.monotonic()
        cutoff = now - self._OTP_ATTEMPT_WINDOW_SECONDS
        attempts = [t for t in self._otp_attempts.get(patient_id, []) if t > cutoff]
        if len(attempts) >= self._MAX_OTP_ATTEMPTS:
            return False

        patient = self.db.get_patient(patient_id)
        if not patient or not patient["otp_hash"] or not patient["otp_expires_at"]:
            attempts.append(now)
            self._otp_attempts[patient_id] = attempts
            return False

        # Check expiry
        try:
            expires_at = datetime.fromisoformat(str(patient["otp_expires_at"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                attempts.append(now)
                self._otp_attempts[patient_id] = attempts
                return False
        except (ValueError, TypeError):
            attempts.append(now)
            self._otp_attempts[patient_id] = attempts
            return False

        # Constant-time OTP hash comparison
        provided_hash = self._hash_otp(otp)
        if not secrets.compare_digest(provided_hash, patient["otp_hash"]):
            attempts.append(now)
            self._otp_attempts[patient_id] = attempts
            return False

        # Success — reset the attempt counter
        self._otp_attempts.pop(patient_id, None)
        self.db.verify_patient_email(patient_id)
        return True

    def generate_otp(self, patient_id: str, email_config=None) -> tuple[str, bool]:
        """Generate and store a new OTP for a patient.

        Returns a (otp, email_sent) tuple. otp is the plaintext code; email_sent
        is True if the code was delivered via email, False otherwise.

        If email_config is provided and enabled, the OTP is sent directly to the
        patient's registered email address.
        """
        patient = self.db.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient not found: {patient_id}")

        otp = f"{secrets.randbelow(1_000_000):06d}"
        otp_hash = self._hash_otp(otp)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self._OTP_TTL_MINUTES)
        self.db.update_patient_otp(patient_id, otp_hash, expires_at.isoformat())

        email_sent = False
        if email_config is not None and email_config.enabled:
            from aquifer.strata.notifications import send_notification
            body = (
                f"Your Aquifer verification code is: {otp}\n\n"
                f"This code expires in 15 minutes.\n\n"
                f"If you didn't request this code, please ignore this email."
            )
            email_sent = send_notification(
                email_config,
                to=patient["email"],
                subject="Aquifer \u2014 Your verification code",
                body=body,
            )

        return otp, email_sent

    def link_patient_to_practice(
        self, patient_id: str, practice_id: str, source_file_hashes: str = ""
    ) -> None:
        """Associate a patient with a practice.

        Called by the de-identification pipeline after processing a patient's
        file. source_file_hashes is a comma-separated list of the source file
        hashes that belong to this patient, used to locate vault tokens during
        transfer.
        """
        patient = self.db.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient not found: {patient_id}")
        practice = self.db.get_practice(practice_id)
        if not practice:
            raise ValueError(f"Practice not found: {practice_id}")

        self.db.link_patient_to_practice(patient_id, practice_id, source_file_hashes)

    # --- Health Data Import ---

    def _health_encryption_key(self) -> bytes:
        """Derive a stable Fernet key from the server master key for health record encryption.

        Uses a fixed salt so the same master key always produces the same derived key,
        making stored records decryptable across server restarts.
        """
        master_key = self.config.master_key
        salt = b"aquifer-health-data-v1"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        return base64.urlsafe_b64encode(kdf.derive(master_key.encode()))

    def import_health_records(
        self,
        patient_id: str,
        records: list,
    ) -> int:
        """Store imported health records for a patient.

        Records are stored in a patient-owned vault (separate from practice vaults).
        The value field is encrypted with a key derived from the server master key.
        Returns count of records stored.
        """
        from aquifer.patient_app.health_import import HealthRecord

        patient = self.db.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient not found: {patient_id}")

        key = self._health_encryption_key()
        f = Fernet(key)
        count = 0

        for record in records:
            if not isinstance(record, HealthRecord):
                continue
            record_id = str(uuid.uuid4())
            value_encrypted = f.encrypt(record.value.encode()).decode()
            self.db.store_health_record(
                id=record_id,
                patient_id=patient_id,
                domain=record.domain,
                field_type=record.field_type,
                label=record.label,
                value_encrypted=value_encrypted,
                recorded_date=record.date,
                source=record.source,
                source_system=record.source_system,
            )
            count += 1

        return count

    def get_health_records(
        self,
        patient_id: str,
        domain: str | None = None,
        decrypt: bool = True,
    ) -> list[dict]:
        """Retrieve stored health records for a patient.

        When decrypt=True (the default), value_encrypted is decrypted and
        returned as 'value'. When decrypt=False the raw encrypted blob is returned.
        """
        rows = self.db.get_patient_health_records(patient_id, domain=domain)
        if not decrypt:
            return rows

        key = self._health_encryption_key()
        f = Fernet(key)
        result = []
        for row in rows:
            r = dict(row)
            try:
                r["value"] = f.decrypt(r["value_encrypted"].encode()).decode()
            except Exception:
                r["value"] = ""
            del r["value_encrypted"]
            result.append(r)
        return result

    def pull_records(
        self,
        share_key: str,
        target_practice_id: str,
        target_practice_type: str | None = None,
    ) -> list[TransferRecord]:
        """Instant record pull using a patient share key.

        The share key IS the authorization — patient presenting it at check-in
        constitutes consent. For each source practice that holds this patient's
        data, this method auto-creates a scoped consent, authorizes it, and
        executes the transfer into the target vault.

        Returns a list of TransferRecords (one per source practice).
        """
        from aquifer.core import PRACTICE_TYPE_DEFAULTS

        patient = self.db.get_patient_by_share_key(share_key)
        if not patient:
            raise ValueError("Invalid share key")

        patient_id = patient["id"]
        target = self.db.get_practice(target_practice_id)
        if not target:
            raise ValueError(f"Target practice not found: {target_practice_id}")

        # Resolve scope from practice type
        scope = "all"
        if target_practice_type:
            defaults = PRACTICE_TYPE_DEFAULTS.get(target_practice_type.lower())
            if defaults is not None:
                scope = ",".join(sorted(str(d.value) for d in defaults))

        # All practices this patient is linked to, except the target
        links = self.db.get_patient_practices(patient_id)
        source_practice_ids = [
            l["practice_id"] for l in links
            if l["practice_id"] != target_practice_id
        ]

        records: list[TransferRecord] = []
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(hours=self._CONSENT_TTL_HOURS)).isoformat()

        for source_practice_id in source_practice_ids:
            source = self.db.get_practice(source_practice_id)
            if not source:
                continue

            consent_id = str(uuid.uuid4())
            self.db.create_consent(
                id=consent_id,
                patient_id=patient_id,
                source_practice_id=source_practice_id,
                target_practice_id=target_practice_id,
                scope=scope,
                expires_at=expires_at,
            )
            self.db.update_consent_status(
                consent_id,
                status="authorized",
                authorized_at=now.isoformat(),
                expires_at=expires_at,
            )

            try:
                record = self.execute_transfer(consent_id)
                records.append(record)
            except Exception:
                # If a single source fails, continue with the rest
                pass

        # Link the patient to the target practice so future pulls work
        self.db.link_patient_to_practice(patient_id, target_practice_id)

        return records

    # --- Consent ---

    def create_consent(
        self,
        patient_id: str,
        source_practice_id: str,
        target_practice_id: str,
        scope: str = "all",
    ) -> ConsentRecord:
        """Create a consent record authorizing data sharing from A to B.

        The consent starts in 'pending' status. Call authorize_consent() or
        set status to 'authorized' once the patient confirms.

        For Phase A the practice creates consent on behalf of the patient;
        Phase B will add patient-facing OTP confirmation.
        """
        patient = self.db.get_patient(patient_id)
        if not patient:
            raise ValueError(f"Patient not found: {patient_id}")

        source = self.db.get_practice(source_practice_id)
        if not source:
            raise ValueError(f"Source practice not found: {source_practice_id}")

        target = self.db.get_practice(target_practice_id)
        if not target:
            raise ValueError(f"Target practice not found: {target_practice_id}")

        if source_practice_id == target_practice_id:
            raise ValueError("Source and target practice must differ")

        consent_id = str(uuid.uuid4())
        # expires_at is set relative to when authorization actually happens, but
        # we pre-populate it as a ceiling from now so the DB constraint is satisfied.
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=self._CONSENT_TTL_HOURS)
        ).isoformat()

        row = self.db.create_consent(
            id=consent_id,
            patient_id=patient_id,
            source_practice_id=source_practice_id,
            target_practice_id=target_practice_id,
            scope=scope,
            expires_at=expires_at,
        )
        return self._consent_from_row(row)

    def authorize_consent(self, consent_id: str, patient_id: str) -> ConsentRecord:
        """Mark a consent as authorized by the patient.

        Resets the expiry window from the moment of authorization.
        """
        consent = self.db.get_consent(consent_id)
        if not consent:
            raise ValueError(f"Consent not found: {consent_id}")
        if consent["patient_id"] != patient_id:
            raise ValueError("Consent does not belong to this patient")
        if consent["status"] != "pending":
            raise ValueError(f"Cannot authorize consent in status '{consent['status']}'")

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=self._CONSENT_TTL_HOURS)
        self.db.update_consent_status(
            consent_id,
            status="authorized",
            authorized_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        updated = self.db.get_consent(consent_id)
        if not updated:
            raise ValueError(f"Consent not found after update: {consent_id}")
        return self._consent_from_row(updated)

    def revoke_consent(self, consent_id: str, patient_id: str) -> bool:
        """Revoke a consent. Patients can always revoke, regardless of status.

        Returns True if the consent was found and revoked.
        """
        consent = self.db.get_consent(consent_id)
        if not consent:
            return False
        if consent["patient_id"] != patient_id:
            return False

        return self.db.update_consent_status(consent_id, status="revoked")

    # --- Transfer ---

    def execute_transfer(self, consent_id: str) -> TransferRecord:
        """Execute a vault-to-vault PHI transfer under a valid consent.

        Steps:
        1. Verify consent is authorized and not expired.
        2. Find the source practice's vault tokens linked to the patient.
        3. Decrypt each token from the source vault (in-memory only).
        4. Re-encrypt each token into the target vault.
        5. Log the transfer in the audit trail.

        PHI is never written to disk in plaintext — it decrypts and re-encrypts
        entirely in memory following the same pattern as vault.rekey().
        """
        consent = self.db.get_consent(consent_id)
        if not consent:
            raise ValueError(f"Consent not found: {consent_id}")
        if consent["status"] != "authorized":
            raise ValueError(
                f"Transfer requires authorized consent, got '{consent['status']}'"
            )

        # Check expiry
        try:
            expires_at = datetime.fromisoformat(str(consent["expires_at"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                self.db.update_consent_status(consent_id, status="expired")
                raise ValueError("Consent has expired")
        except ValueError:
            raise

        patient_id = consent["patient_id"]
        source_practice_id = consent["source_practice_id"]
        target_practice_id = consent["target_practice_id"]
        scope = consent["scope"]

        transfer_id = str(uuid.uuid4())
        try:
            token_count = self._transfer_tokens(
                patient_id=patient_id,
                source_practice_id=source_practice_id,
                target_practice_id=target_practice_id,
                scope=scope,
            )

            self.db.log_transfer(
                id=transfer_id,
                consent_id=consent_id,
                source_practice_id=source_practice_id,
                target_practice_id=target_practice_id,
                token_count=token_count,
                status="completed",
            )
            self.db.log_audit(
                practice_id=source_practice_id,
                action="patient_transfer.completed",
                resource_type="consent",
                resource_id=consent_id,
                detail=(
                    f"patient={patient_id} target={target_practice_id} "
                    f"tokens={token_count} scope={scope}"
                ),
            )
        except Exception as exc:
            error_msg = str(exc)
            self.db.log_transfer(
                id=transfer_id,
                consent_id=consent_id,
                source_practice_id=source_practice_id,
                target_practice_id=target_practice_id,
                token_count=0,
                status="failed",
                error_message=error_msg,
            )
            self.db.log_audit(
                practice_id=source_practice_id,
                action="patient_transfer.failed",
                resource_type="consent",
                resource_id=consent_id,
                detail=f"patient={patient_id} error={error_msg}",
            )
            raise

        created_at = datetime.now(timezone.utc).isoformat()
        return TransferRecord(
            transfer_id=transfer_id,
            consent_id=consent_id,
            token_count=token_count,
            status="completed",
            created_at=created_at,
        )

    # --- Internal helpers ---

    def _transfer_tokens(
        self,
        patient_id: str,
        source_practice_id: str,
        target_practice_id: str,
        scope: str,
    ) -> int:
        """Decrypt tokens from source vault and re-encrypt into target vault.

        Returns the number of tokens transferred. PHI never touches disk in
        plaintext — the decrypt/re-encrypt happens entirely in memory.
        """
        source_practice = self.db.get_practice(source_practice_id)
        target_practice = self.db.get_practice(target_practice_id)

        # Open both vaults via the vault manager (uses cached connections where
        # possible, decrypts vault keys server-side with master key)
        source_vault = self.vault_manager.open_vault(
            source_practice_id, source_practice["vault_key_encrypted"], db=self.db
        )
        target_vault = self.vault_manager.open_vault(
            target_practice_id, target_practice["vault_key_encrypted"], db=self.db
        )

        # Collect file hashes for this patient at the source practice
        links = self.db.get_patient_practices(patient_id)
        source_file_hashes: list[str] = []
        for link in links:
            if link["practice_id"] == source_practice_id and link.get("source_file_hashes"):
                for fhash in link["source_file_hashes"].split(","):
                    fhash = fhash.strip()
                    if fhash:
                        source_file_hashes.append(fhash)

        if not source_file_hashes:
            return 0

        # When scope is not "all", build a set of allowed domains and filter
        # files by their stored data_domain before collecting tokens.
        allowed_domains: set[str] | None = None
        if scope != "all":
            allowed_domains = {s.strip().lower() for s in scope.split(",")}

        # Collect tokens, filtering by data domain when a scoped transfer
        skipped_domains: set[str] = set()
        included_domains: set[str] = set()
        all_tokens = []
        for fhash in source_file_hashes:
            if allowed_domains is not None:
                file_record = self.db.get_file_record_by_hash(source_practice_id, fhash)
                file_domain = (file_record or {}).get("data_domain")
                if file_domain and file_domain not in allowed_domains:
                    skipped_domains.add(file_domain)
                    continue
                if file_domain:
                    included_domains.add(file_domain)
            tokens = source_vault.get_tokens_for_file(fhash)
            all_tokens.extend(tokens)

        if not all_tokens:
            return 0

        # Secondary filter: also apply PHI-type-level scope check for fine-grained control
        if scope != "all" and allowed_domains is not None:
            all_tokens = [
                t for t in all_tokens
                if self._phi_type_in_scope(t.phi_type, allowed_domains)
            ]

        if skipped_domains or included_domains:
            import logging as _logging
            _log = _logging.getLogger(__name__)
            _log.info(
                "patient_transfer scope filtering: included_domains=%s skipped_domains=%s",
                sorted(included_domains), sorted(skipped_domains),
            )

        # Decrypt with source key, re-encrypt with target key — never plaintext on disk.
        # Follow the same pattern as TokenVault.rekey().
        source_key = source_vault.encryption_key
        target_key = target_vault.encryption_key

        reencrypted_batch: list[tuple[str, str, str, str, str | None, float]] = []
        for token in all_tokens:
            # token.phi_value is already decrypted by get_tokens_for_file()
            reencrypted_value = encrypt_value(token.phi_value, target_key)
            # import_token_raw expects an already-encrypted value
            target_vault.import_token_raw(
                token_id=token.token_id,
                phi_type=token.phi_type,
                phi_value_encrypted=reencrypted_value,
                source_file_hash=token.source_file_hash,
                aqf_file_hash=token.aqf_file_hash,
                confidence=token.confidence,
            )

        return len(all_tokens)

    def _phi_type_in_scope(self, phi_type: str, allowed_scopes: set[str]) -> bool:
        """Map a PHI type string to a portability scope bucket.

        Scope names align with DataDomain enum values. A PHI type passes
        if it matches any keyword in any allowed scope bucket. When "all"
        is in allowed_scopes, everything passes.
        """
        if "all" in allowed_scopes:
            return True
        phi_lower = phi_type.lower()
        scope_map = {
            "demographics": {"name", "dob", "date_of_birth", "address", "phone", "email", "gender", "ssn", "age"},
            "insurance": {"insurance", "policy", "group", "member_id", "payer", "account", "subscriber", "coverage"},
            "medications": {"medication", "prescription", "rx", "drug", "dosage"},
            "allergies": {"allergy", "allergic", "reaction", "anaphylaxis"},
            "medical_history": {"diagnosis", "condition", "surgery", "hospital", "icd", "procedure"},
            "dental": {"tooth", "dental", "periodontal", "ortho", "caries", "crown", "filling", "extraction", "implant"},
            "vision": {"vision", "optical", "eye", "ophthalmol", "optometr"},
            "behavioral": {"behavioral", "mental", "psychiatric", "psycholog"},
            "surgical": {"surgical", "anesthesia", "operative", "biopsy"},
            "consent_forms": {"consent", "hipaa", "authorization", "acknowledgment"},
            "referrals": {"referral", "referred", "specialist"},
        }
        for scope_name, keywords in scope_map.items():
            if scope_name in allowed_scopes:
                if any(kw in phi_lower for kw in keywords):
                    return True
        return False

    def is_otp_rate_limited(self, patient_id: str) -> bool:
        """Return True if this patient has exceeded the OTP attempt rate limit."""
        now = time.monotonic()
        cutoff = now - self._OTP_ATTEMPT_WINDOW_SECONDS
        attempts = [t for t in self._otp_attempts.get(patient_id, []) if t > cutoff]
        return len(attempts) >= self._MAX_OTP_ATTEMPTS

    @staticmethod
    def _hash_otp(otp: str) -> str:
        """SHA-256 hash of an OTP for storage."""
        return hashlib.sha256(otp.encode()).hexdigest()

    # --- Patient data summary ---

    def get_patient_data_summary(self, patient_id: str) -> dict[str, str]:
        """Get all stored data for a patient across all linked practices.

        Returns a flat dict mapping canonical field_type keys to values, e.g.:
        {"NAME": "Maria Garcia", "SSN": "287-65-4321", "PHONE": "(512) 555-0147", ...}

        Merges data from all linked practices; most recent practice (by linked_at)
        wins when the same PHI type appears more than once. PHI is decrypted
        server-side and returned only to the authenticated patient.
        """
        links = self.db.get_patient_practices(patient_id)
        if not links:
            return {}

        summary: dict[str, str] = {}

        # links are ordered by linked_at DESC — iterate so that the most-recent
        # practice's tokens overwrite older ones for the same PHI type.
        for link in reversed(links):
            practice_id = link["practice_id"]
            practice = self.db.get_practice(practice_id)
            if not practice:
                continue

            try:
                vault = self.vault_manager.open_vault(
                    practice_id, practice["vault_key_encrypted"], db=self.db
                )
            except Exception:
                continue

            source_file_hashes: list[str] = []
            if link.get("source_file_hashes"):
                for fhash in link["source_file_hashes"].split(","):
                    fhash = fhash.strip()
                    if fhash:
                        source_file_hashes.append(fhash)

            for fhash in source_file_hashes:
                try:
                    tokens = vault.get_tokens_for_file(fhash)
                except Exception:
                    continue
                for token in tokens:
                    phi_key = token.phi_type.upper() if token.phi_type else ""
                    if phi_key and token.phi_value:
                        summary[phi_key] = token.phi_value

        return summary

    # --- Convenience accessors ---

    def _consent_from_row(self, row: dict) -> ConsentRecord:
        return ConsentRecord(
            consent_id=row["id"],
            patient_id=row["patient_id"],
            source_practice_id=row["source_practice_id"],
            target_practice_id=row["target_practice_id"],
            scope=row["scope"],
            status=row["status"],
            authorized_at=str(row["authorized_at"]) if row.get("authorized_at") else None,
            expires_at=str(row["expires_at"]),
            created_at=str(row["created_at"]),
        )
