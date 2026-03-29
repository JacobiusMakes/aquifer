"""Rule-based PHI pattern detectors using regex.

Each detector returns a list of PHIMatch objects with the detected span,
PHI type, confidence score, and the matched text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class PHIType(str, Enum):
    NAME = "NAME"
    DATE = "DATE"
    SSN = "SSN"
    PHONE = "PHONE"
    FAX = "FAX"
    EMAIL = "EMAIL"
    ADDRESS = "ADDRESS"
    MRN = "MRN"
    ACCOUNT = "ACCOUNT"
    LICENSE = "LICENSE"
    VEHICLE = "VEHICLE"
    DEVICE = "DEVICE"
    URL = "URL"
    IP = "IP"
    BIOMETRIC = "BIOMETRIC"
    PHOTO = "PHOTO"
    NPI = "NPI"
    AGE = "AGE"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class PHIMatch:
    start: int
    end: int
    phi_type: PHIType
    text: str
    confidence: float = 1.0
    source: str = "regex"


class PatternDetector(Protocol):
    def detect(self, text: str) -> list[PHIMatch]: ...


# ---------------------------------------------------------------------------
# Individual pattern detectors
# ---------------------------------------------------------------------------

class SSNDetector:
    """Detects Social Security Numbers (XXX-XX-XXXX and variants)."""
    _PATTERN = re.compile(
        r'\b(?!000|666|9\d{2})([0-8]\d{2})'
        r'[-\s]?'
        r'(?!00)(\d{2})'
        r'[-\s]?'
        r'(?!0000)(\d{4})\b'
    )

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        for m in self._PATTERN.finditer(text):
            full = m.group(0)
            digits = re.sub(r'\D', '', full)
            if len(digits) != 9:
                continue
            # Must have at least one separator to distinguish from other 9-digit numbers
            # OR appear in SSN context
            has_separator = '-' in full or ' ' in full.strip()
            pre_ctx = text[max(0, m.start() - 30):m.start()].lower()
            in_ssn_context = any(k in pre_ctx for k in ['ssn', 'social security', 'ss#', 'ss #'])
            if not has_separator and not in_ssn_context:
                continue
            matches.append(PHIMatch(
                start=m.start(), end=m.end(),
                phi_type=PHIType.SSN, text=full,
            ))
        return matches


class PhoneDetector:
    """Detects US phone/fax numbers in various formats."""
    _PATTERN = re.compile(
        r'(?:(?:\+?1[-.\s]?)?'
        r'(?:\(?\d{3}\)?[-.\s]?)'
        r'\d{3}[-.\s]?\d{4})'
        r'(?:\s*(?:ext|x|ext\.)\s*\d+)?',
        re.IGNORECASE
    )
    # NPI label patterns — used to exclude NPI numbers from phone matches
    _NPI_CONTEXT = re.compile(r'NPI\s*[:=]?\s*$', re.IGNORECASE)

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        for m in self._PATTERN.finditer(text):
            full = m.group(0)
            digits = re.sub(r'\D', '', full)
            if len(digits) < 10 or len(digits) > 15:
                continue

            # Check if this is actually an NPI number (preceded by "NPI" label)
            pre_ctx = text[max(0, m.start() - 20):m.start()]
            if self._NPI_CONTEXT.search(pre_ctx):
                continue  # Skip — this is an NPI, not a phone number

            # Check if preceded by "phone", "fax", "call", "tel", etc. for higher confidence
            pre_lower = pre_ctx.lower()
            phi_type = PHIType.FAX if 'fax' in pre_lower else PHIType.PHONE

            # A bare 10-digit number without separators and without phone context
            # is probably not a phone number
            has_separator = any(c in full for c in '(-.)')
            in_phone_context = any(k in pre_lower for k in
                                   ['phone', 'tel', 'call', 'fax', 'cell', 'mobile', 'contact'])
            if not has_separator and not in_phone_context:
                continue

            matches.append(PHIMatch(
                start=m.start(), end=m.end(),
                phi_type=phi_type, text=full,
            ))
        return matches


class EmailDetector:
    """Detects email addresses."""
    _PATTERN = re.compile(
        r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b'
    )

    def detect(self, text: str) -> list[PHIMatch]:
        return [
            PHIMatch(start=m.start(), end=m.end(),
                     phi_type=PHIType.EMAIL, text=m.group(0))
            for m in self._PATTERN.finditer(text)
        ]


class URLDetector:
    """Detects web URLs."""
    _PATTERN = re.compile(
        r'https?://[^\s<>\"\']+|'
        r'www\.[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}[^\s<>\"\']*',
        re.IGNORECASE
    )

    def detect(self, text: str) -> list[PHIMatch]:
        return [
            PHIMatch(start=m.start(), end=m.end(),
                     phi_type=PHIType.URL, text=m.group(0))
            for m in self._PATTERN.finditer(text)
        ]


class IPDetector:
    """Detects IPv4 and IPv6 addresses."""
    _IPV4 = re.compile(
        r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}'
        r'(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
    )
    _IPV6 = re.compile(
        r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b|'
        r'\b(?:[0-9a-fA-F]{1,4}:){1,7}:\b|'
        r'\b::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}\b'
    )

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        for m in self._IPV4.finditer(text):
            matches.append(PHIMatch(
                start=m.start(), end=m.end(),
                phi_type=PHIType.IP, text=m.group(0)))
        for m in self._IPV6.finditer(text):
            matches.append(PHIMatch(
                start=m.start(), end=m.end(),
                phi_type=PHIType.IP, text=m.group(0)))
        return matches


class DateDetector:
    """Detects dates in many formats.

    Per HIPAA Safe Harbor, all dates (except year) related to an individual
    must be removed. This detector aggressively matches date patterns.
    """
    _PATTERNS = [
        # MM/DD/YYYY, MM-DD-YYYY, MM.DD.YYYY
        re.compile(
            r'\b(0?[1-9]|1[0-2])[/\-\.](0?[1-9]|[12]\d|3[01])[/\-\.]'
            r'((?:19|20)\d{2})\b'
        ),
        # YYYY-MM-DD (ISO)
        re.compile(
            r'\b((?:19|20)\d{2})[/\-\.](0?[1-9]|1[0-2])[/\-\.](0?[1-9]|[12]\d|3[01])\b'
        ),
        # Month DD, YYYY / Month DD YYYY (full and abbreviated month names)
        re.compile(
            r'\b(?:January|February|March|April|May|June|July|August|'
            r'September|October|November|December|'
            r'Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?'
            r'\s+\d{1,2}(?:st|nd|rd|th)?,?\s*(?:19|20)\d{2}\b',
            re.IGNORECASE
        ),
        # DD Month YYYY
        re.compile(
            r'\b\d{1,2}(?:st|nd|rd|th)?\s+'
            r'(?:January|February|March|April|May|June|July|August|'
            r'September|October|November|December|'
            r'Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?'
            r',?\s*(?:19|20)\d{2}\b',
            re.IGNORECASE
        ),
        # MM/DD/YY (two-digit year — common in medical forms)
        re.compile(
            r'\b(0?[1-9]|1[0-2])[/\-](0?[1-9]|[12]\d|3[01])[/\-]'
            r'(\d{2})\b'
        ),
        # "DOB: MM/DD" without year (still PHI — day+month is identifying)
        # Only match if NOT followed by /YYYY or /YY (those are caught by full date patterns)
        re.compile(
            r'(?:DOB|Date of Birth|Birth\s*Date)\s*[:=]?\s*'
            r'(0?[1-9]|1[0-2])[/\-]([12]\d|3[01]|0?[1-9])'  # greedy: try 2-digit day first
            r'(?![/\-\d])',  # not followed by / or - or another digit
            re.IGNORECASE
        ),
    ]

    # Time patterns associated with appointments (PHI when linked to patient)
    _TIME_PATTERN = re.compile(
        r'\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm|a\.m\.|p\.m\.)\b'
    )

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        seen: set[tuple[int, int]] = set()
        for pattern in self._PATTERNS:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                if span not in seen:
                    seen.add(span)
                    matches.append(PHIMatch(
                        start=m.start(), end=m.end(),
                        phi_type=PHIType.DATE, text=m.group(0),
                    ))

        # Detect times when they appear near date context
        for m in self._TIME_PATTERN.finditer(text):
            # Check if within 100 chars of a date-related keyword
            ctx = text[max(0, m.start() - 100):m.end() + 50].lower()
            if any(k in ctx for k in ['appointment', 'next', 'scheduled', 'follow', 'visit']):
                span = (m.start(), m.end())
                if span not in seen:
                    seen.add(span)
                    matches.append(PHIMatch(
                        start=m.start(), end=m.end(),
                        phi_type=PHIType.DATE, text=m.group(0),
                        confidence=0.8,
                    ))
        return matches


class AgeDetector:
    """Detects ages over 89 (Safe Harbor requirement).

    Per Safe Harbor §164.514(b)(2)(i)(C), all ages over 89 and all elements
    of dates (including year) indicative of such age must be aggregated into
    a single category of age 90 or older.
    """
    _PATTERNS = [
        # "age: 92", "age 95", "aged 91"
        re.compile(r'\bage[d]?\s*[:=]?\s*(\d{2,3})\b', re.IGNORECASE),
        # "92 years old", "91-year-old", "95 y/o", "93yo"
        re.compile(r'\b(\d{2,3})\s*[-\s]?\s*(?:years?\s*old|y/?o|yr|yrs)\b', re.IGNORECASE),
        # "DOB" with age calculation context — we flag the age, the DOB date is caught by DateDetector
    ]

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        for pattern in self._PATTERNS:
            for m in pattern.finditer(text):
                try:
                    age = int(m.group(1))
                except (ValueError, IndexError):
                    continue
                if age > 89:
                    matches.append(PHIMatch(
                        start=m.start(), end=m.end(),
                        phi_type=PHIType.AGE, text=m.group(0),
                        confidence=0.95,
                    ))
        return matches


class MRNDetector:
    """Detects Medical Record Numbers in common formats."""
    _PATTERNS = [
        # MR-YYYY-XXXXXXX or MRN-XXXXXXX or MR: XXXXXXX
        re.compile(r'\bMR[N]?[-:\s]?\d{4}[-]?\d{4,10}\b', re.IGNORECASE),
        # Generic MRN label followed by alphanumeric ID
        re.compile(
            r'(?:MRN|Medical\s+Record\s+(?:Number|No\.?)|Med\s*Rec\s*#?)'
            r'\s*[:=]?\s*([A-Z0-9][\w\-]{4,20})',
            re.IGNORECASE
        ),
        # Chart# or Chart Number
        re.compile(
            r'(?:Chart\s*(?:#|No\.?|Number))\s*[:=]?\s*([A-Z0-9][\w\-]{4,15})',
            re.IGNORECASE
        ),
    ]

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        seen: set[tuple[int, int]] = set()
        for pattern in self._PATTERNS:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                if span not in seen:
                    seen.add(span)
                    matches.append(PHIMatch(
                        start=m.start(), end=m.end(),
                        phi_type=PHIType.MRN, text=m.group(0),
                    ))
        return matches


class NPIDetector:
    """Detects National Provider Identifier numbers (10 digits with label context)."""
    _LABELED = re.compile(r'NPI\s*[:=]?\s*(\d{10})\b', re.IGNORECASE)

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        for m in self._LABELED.finditer(text):
            matches.append(PHIMatch(
                start=m.start(), end=m.end(),
                phi_type=PHIType.NPI, text=m.group(0),
                confidence=0.9,
            ))
        return matches


class AccountDetector:
    """Detects insurance member IDs, account numbers, and health plan beneficiary numbers."""
    _PATTERNS = [
        re.compile(
            r'(?:Member\s*ID|Member\s*#|Account\s*(?:No\.?|Number|#)|'
            r'Policy\s*(?:No\.?|Number|#)|Group\s*(?:No\.?|Number|#)|'
            r'Subscriber\s*(?:ID|No\.?|Number|#)|'
            r'Beneficiary\s*(?:ID|No\.?|Number|#)|'
            r'Claim\s*(?:ID|No\.?|Number|#)|'
            r'Authorization\s*(?:No\.?|Number|#)|'
            r'Certificate\s*(?:No\.?|Number|#)|'
            r'Plan\s*ID)\s*[:=]?\s*([A-Z0-9][\w\-]{4,20})',
            re.IGNORECASE
        ),
    ]

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        for pattern in self._PATTERNS:
            for m in pattern.finditer(text):
                matches.append(PHIMatch(
                    start=m.start(), end=m.end(),
                    phi_type=PHIType.ACCOUNT, text=m.group(0),
                    confidence=0.85,
                ))
        return matches


class AddressDetector:
    """Detects US street addresses in various formats."""
    _PATTERNS = [
        # Standard: 123 Main Street, City, ST 12345
        # Require at least 2-char street name word before the suffix
        re.compile(
            r'\b\d{1,5}\s+'
            r'(?:[A-Z][a-zA-Z]{2,}\.?\s+){1,5}'  # min 3-char words (avoids short clinical fragments)
            r'(?:Street|Avenue|Boulevard|Drive|'
            r'Lane|Road|Way|Court|Place|'
            r'Circle|Terrace|Trail|Parkway|'
            r'Highway|Route|Pike|Alley|Loop|'
            r'St|Ave|Blvd|Dr|Ln|Rd|Ct|Pl|Cir|Ter|Trl|Pkwy|Hwy|Rt)\.?'
            r'(?:\s*,\s*[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)*'
            r'(?:\s*,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)?)?'
            r'(?=[\s,\.\n]|$)',  # must end at whitespace, punctuation, or end
        ),
        # PO Box
        re.compile(
            r'\bP\.?O\.?\s*Box\s+\d+\b',
            re.IGNORECASE
        ),
        # "Address: <anything until next label or double newline>"
        re.compile(
            r'(?:Address|ADDR)\s*[:=]\s*(.+?)(?=\n[A-Z]|\n\n|$)',
            re.IGNORECASE
        ),
    ]

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        seen: set[tuple[int, int]] = set()
        for pattern in self._PATTERNS:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                if span not in seen:
                    seen.add(span)
                    matches.append(PHIMatch(
                        start=m.start(), end=m.end(),
                        phi_type=PHIType.ADDRESS, text=m.group(0).strip(),
                        confidence=0.9,
                    ))
        return matches


class ZIPCodeDetector:
    """Detects ZIP codes that need generalization per Safe Harbor.

    Per Safe Harbor, geographic subdivisions smaller than a state must be
    removed. For initial 3-digit ZIP codes, those with populations < 20,000
    must be replaced with 000.
    """
    _PATTERN = re.compile(r'\b(\d{5})(?:-(\d{4}))?\b')

    # 3-digit ZIP prefixes with populations under 20,000 (per Census Bureau)
    # These must be zeroed out per Safe Harbor
    _RESTRICTED_PREFIXES = frozenset({
        "036", "059", "063", "102", "203", "556",
        "692", "790", "821", "823", "830", "831",
        "878", "879", "884", "890", "893",
    })

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        for m in self._PATTERN.finditer(text):
            pre = text[max(0, m.start() - 80):m.start()].lower()
            # Only flag ZIPs that appear in address context
            if any(kw in pre for kw in [',', 'zip', 'address', 'state',
                                         'city', 'street', 'ave', 'blvd',
                                         'road', 'drive', 'lane']):
                zip5 = m.group(1)
                prefix = zip5[:3]
                is_restricted = prefix in self._RESTRICTED_PREFIXES
                matches.append(PHIMatch(
                    start=m.start(), end=m.end(),
                    phi_type=PHIType.ADDRESS, text=m.group(0),
                    confidence=0.9 if is_restricted else 0.7,
                ))
        return matches


class LicenseDetector:
    """Detects driver's license and professional license numbers."""
    _PATTERNS = [
        re.compile(
            r'(?:License|Lic|DL|Driver.?s?\s*License)\s*(?:#|No\.?|Number)?\s*[:=]?\s*'
            r'([A-Z0-9][\w\-]{5,15})',
            re.IGNORECASE
        ),
        re.compile(
            r'(?:DEA|DEA\s*#|DEA\s*Number)\s*[:=]?\s*([A-Z]{2}\d{7})',
            re.IGNORECASE
        ),
    ]

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        for pattern in self._PATTERNS:
            for m in pattern.finditer(text):
                matches.append(PHIMatch(
                    start=m.start(), end=m.end(),
                    phi_type=PHIType.LICENSE, text=m.group(0),
                    confidence=0.85,
                ))
        return matches


class VehicleDetector:
    """Detects vehicle identification numbers (VINs)."""
    # Standard VIN: 17 alphanumeric chars (no I, O, Q)
    _VIN = re.compile(
        r'(?:VIN|Vehicle\s*ID)\s*[:=]?\s*([A-HJ-NPR-Z0-9]{17})\b',
        re.IGNORECASE
    )

    def detect(self, text: str) -> list[PHIMatch]:
        return [
            PHIMatch(start=m.start(), end=m.end(),
                     phi_type=PHIType.VEHICLE, text=m.group(0),
                     confidence=0.9)
            for m in self._VIN.finditer(text)
        ]


class DeviceDetector:
    """Detects medical device identifiers and serial numbers."""
    _PATTERNS = [
        re.compile(
            r'(?:Serial\s*(?:#|No\.?|Number)|Device\s*ID|UDI)\s*[:=]?\s*'
            r'([A-Z0-9][\w\-]{5,25})',
            re.IGNORECASE
        ),
    ]

    def detect(self, text: str) -> list[PHIMatch]:
        matches = []
        for pattern in self._PATTERNS:
            for m in pattern.finditer(text):
                matches.append(PHIMatch(
                    start=m.start(), end=m.end(),
                    phi_type=PHIType.DEVICE, text=m.group(0),
                    confidence=0.85,
                ))
        return matches


# ---------------------------------------------------------------------------
# Composite detector: runs all pattern detectors
# ---------------------------------------------------------------------------

ALL_PATTERN_DETECTORS: list[PatternDetector] = [
    SSNDetector(),
    PhoneDetector(),
    EmailDetector(),
    URLDetector(),
    IPDetector(),
    DateDetector(),
    AgeDetector(),
    MRNDetector(),
    NPIDetector(),
    AccountDetector(),
    AddressDetector(),
    ZIPCodeDetector(),
    LicenseDetector(),
    VehicleDetector(),
    DeviceDetector(),
]


def detect_patterns(text: str) -> list[PHIMatch]:
    """Run all regex-based pattern detectors on the given text."""
    all_matches: list[PHIMatch] = []
    for detector in ALL_PATTERN_DETECTORS:
        all_matches.extend(detector.detect(text))
    return all_matches
