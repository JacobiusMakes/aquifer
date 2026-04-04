"""Patient form scanner and auto-fill.

Takes a scanned intake form image, identifies form fields via OCR,
and fills them from the patient's stored Aquifer data.

Flow:
1. Patient photographs paper intake form
2. OCR extracts text + field positions (labels like "Name:", "DOB:", etc.)
3. Match labels to patient's stored PHI types
4. Generate a filled version (text report or data dict)
5. Patient can email/share the filled data with the practice
"""

from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Label → PHI type mapping
# ---------------------------------------------------------------------------

# Each entry: (compiled regex pattern, canonical field_type string)
_LABEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Name variants
    (re.compile(r"\b(patient\s+name|full\s+name|name)\s*[:/]", re.IGNORECASE), "NAME"),
    # Date of birth variants
    (re.compile(r"\b(date\s+of\s+birth|dob|birth\s+date|birthdate)\s*[:/]", re.IGNORECASE), "DATE"),
    # SSN
    (re.compile(r"\b(social\s+security\s*(number|#|no)?|ssn|ss\s*#)\s*[:/]", re.IGNORECASE), "SSN"),
    # Phone
    (re.compile(r"\b(cell(\s+phone)?|mobile|telephone|phone\s*(number|#|no)?|home\s+phone|work\s+phone)\s*[:/]", re.IGNORECASE), "PHONE"),
    # Address
    (re.compile(r"\b(street\s+address|mailing\s+address|home\s+address|address)\s*[:/]", re.IGNORECASE), "ADDRESS"),
    # Email
    (re.compile(r"\b(e[\-\s]?mail(\s+address)?)\s*[:/]", re.IGNORECASE), "EMAIL"),
    # Insurance / payer / member ID / group number
    (re.compile(r"\b(insurance\s+carrier|insurance\s+provider|insurance\s+company|insurance|carrier|payer|member\s+id|member\s+#|group\s+(number|#|no)?|policy\s+(number|#|no)?)\s*[:/]", re.IGNORECASE), "ACCOUNT"),
    # Emergency contact (secondary name)
    (re.compile(r"\b(emergency\s+contact(\s+name)?)\s*[:/]", re.IGNORECASE), "EMERGENCY_CONTACT"),
    # Allergies
    (re.compile(r"\b(drug\s+allergies|known\s+allergies|allergies|allergy)\s*[:/]", re.IGNORECASE), "ALLERGIES"),
    # Medications
    (re.compile(r"\b(current\s+medications?|medications?|prescriptions?|drugs?)\s*[:/]", re.IGNORECASE), "MEDICATIONS"),
]

# Maps PHI token types (from vault) to canonical field_type keys used above
_PHI_TYPE_MAP: dict[str, str] = {
    "NAME": "NAME",
    "DATE": "DATE",
    "SSN": "SSN",
    "PHONE": "PHONE",
    "FAX": "PHONE",
    "EMAIL": "EMAIL",
    "ADDRESS": "ADDRESS",
    "ACCOUNT": "ACCOUNT",
    "MRN": "ACCOUNT",
}

# Maps field_type to a human-readable label for the summary
_FIELD_LABELS: dict[str, str] = {
    "NAME": "Name",
    "DATE": "Date of Birth",
    "SSN": "Social Security Number",
    "PHONE": "Phone",
    "EMAIL": "Email",
    "ADDRESS": "Address",
    "ACCOUNT": "Insurance / Account",
    "EMERGENCY_CONTACT": "Emergency Contact",
    "ALLERGIES": "Allergies",
    "MEDICATIONS": "Medications",
}

# Blank placeholder patterns that follow a label (underscores, dotted lines, empty space)
_BLANK_PATTERN = re.compile(r"[_\-\.]{3,}|\[\s*\]|\(\s*\)")


class FormFiller:
    def __init__(self, patient_data: dict[str, str]):
        """patient_data maps field_type keys to values.

        e.g. {"NAME": "Maria Garcia", "DATE": "07/22/1985"}
        """
        self.data = patient_data

    @classmethod
    def from_vault_tokens(cls, tokens: list) -> "FormFiller":
        """Build a FormFiller from vault tokens (VaultToken objects).

        Maps PHI types to canonical field_type keys. When multiple tokens
        share the same PHI type, the first one wins (highest confidence
        tokens should be sorted first by the caller).
        """
        data: dict[str, str] = {}
        for token in tokens:
            phi_type = token.phi_type.upper() if token.phi_type else ""
            field_type = _PHI_TYPE_MAP.get(phi_type)
            if field_type and field_type not in data:
                data[field_type] = token.phi_value
        return cls(data)

    def identify_fields(self, form_text: str) -> list[dict]:
        """Given OCR'd text from a form, identify fillable fields.

        Looks for patterns like:
        - "Name: ___________" or "Name:" followed by blank/whitespace
        - "Date of Birth:" / "DOB:"
        - "Phone:" / "Telephone:"
        - "Address:"
        - "SSN:" / "Social Security:"
        - "Insurance Carrier:" / "Member ID:"
        - "Email:"
        - "Emergency Contact:"

        Returns list of {label, field_type, value} where value is
        filled from patient_data if available, None otherwise.
        """
        found: list[dict] = []
        seen_types: set[str] = set()

        lines = form_text.splitlines()
        for line in lines:
            for pattern, field_type in _LABEL_PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue

                label = m.group(0).rstrip(":/ \t")

                # For EMERGENCY_CONTACT and free-text fields there may be
                # no stored value, so we skip the dedup guard to always surface them.
                if field_type in seen_types and field_type not in ("EMERGENCY_CONTACT",):
                    break

                value = self.data.get(field_type)
                found.append({
                    "label": label,
                    "field_type": field_type,
                    "value": value,
                })
                seen_types.add(field_type)
                break  # one match per line

        return found

    def fill_form(self, form_text: str) -> str:
        """Fill a form's blank fields with patient data.

        Replaces blank placeholders (underscores, dotted lines, empty brackets)
        that immediately follow a recognised label with the patient's value.
        Lines with no matching patient data are left unchanged.

        Returns the form text with blanks replaced by patient data.
        """
        output_lines: list[str] = []
        for line in form_text.splitlines():
            filled = False
            for pattern, field_type in _LABEL_PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue
                value = self.data.get(field_type)
                if value is None:
                    break
                # Replace any blank placeholder on the same line after the label
                after_label = line[m.end():]
                replaced, n = _BLANK_PATTERN.subn(value, after_label, count=1)
                if n:
                    output_lines.append(line[:m.end()] + replaced)
                else:
                    # No blank placeholder — append value after the label
                    output_lines.append(line.rstrip() + " " + value)
                filled = True
                break
            if not filled:
                output_lines.append(line)

        return "\n".join(output_lines)

    def to_summary(self) -> str:
        """Generate a clean text summary of patient data for emailing.

        Format:
        Patient Information Summary (Aquifer)
        ======================================
        Name: Maria Garcia
        Date of Birth: 07/22/1985
        ...

        This information was securely provided via Aquifer (aquifer.health).
        """
        lines: list[str] = [
            "Patient Information Summary (Aquifer)",
            "=" * 38,
        ]

        # Demographics section
        demographic_keys = ["NAME", "DATE", "SSN", "PHONE", "EMAIL", "ADDRESS"]
        demographics = {k: self.data[k] for k in demographic_keys if k in self.data}
        if demographics:
            for key, value in demographics.items():
                lines.append(f"{_FIELD_LABELS[key]}: {value}")

        # Insurance section
        insurance_keys = ["ACCOUNT"]
        insurance = {k: self.data[k] for k in insurance_keys if k in self.data}
        if insurance:
            lines.append("")
            lines.append("Insurance:")
            for key, value in insurance.items():
                lines.append(f"  {_FIELD_LABELS[key]}: {value}")

        # Medical history section
        medical_keys = ["ALLERGIES", "MEDICATIONS"]
        medical = {k: self.data[k] for k in medical_keys if k in self.data}
        if medical:
            lines.append("")
            lines.append("Medical History:")
            for key, value in medical.items():
                lines.append(f"  {_FIELD_LABELS[key]}: {value}")

        # Catch any remaining fields not covered above
        covered = set(demographic_keys + insurance_keys + medical_keys)
        extras = {k: v for k, v in self.data.items() if k not in covered}
        if extras:
            lines.append("")
            lines.append("Additional Information:")
            for key, value in extras.items():
                label = _FIELD_LABELS.get(key, key.replace("_", " ").title())
                lines.append(f"  {label}: {value}")

        lines.append("")
        lines.append("This information was securely provided via Aquifer (aquifer.health).")

        return "\n".join(lines)
