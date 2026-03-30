"""Generate adversarial dental office documents for stress testing.

Creates the nastiest, most realistic synthetic dental data imaginable:
- Dentrix-style pipe-delimited CSV with mixed date formats
- Clinical notes with real dental abbreviations and tooth numbering
- Insurance EOB text with simulated OCR artifacts
- Patient intake forms with structured PHI
- Referral letters between providers
- Multi-format mixed content (JSON clinical records, XML HL7-style)

All data is 100% synthetic. No real patient data is used.

Usage:
    python tests/generate_dental_stress.py --output tests/fixtures/stress/
"""

from __future__ import annotations

import json
import random
import string
import textwrap
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Name pools — adversarial variety with Unicode, accents, hyphens
# ---------------------------------------------------------------------------

FIRST_NAMES_UNICODE = [
    "Jose\u0301",           # Jose with accent (composed)
    "Jos\u00e9",            # Jose with accent (precomposed)
    "Mar\u00eda",           # Maria with accent
    "Ren\u00e9e",           # Renee with accent
    "M\u00fcller",          # Muller with umlaut (used as first name edge case)
    "S\u00e9bastien",
    "Bj\u00f6rk",
    "Ng\u0169gi\u0303",    # Ngugi with tilde
    "A\u00efda",
    "Zo\u00eb",
]

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
    "Linda", "Wei", "Yuki", "Raj", "Fatima", "Alejandro", "Olga",
    "Kwame", "Priya", "Dmitri", "Aisha", "Xiomara", "Tuan",
    "Muhammad", "Svetlana", "Kenji", "Guadalupe", "Saoirse",
] + FIRST_NAMES_UNICODE

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Garcia", "Martinez", "O'Brien",
    "O'Brien-Smith", "Hernandez-Lopez", "Al-Rashid", "De La Cruz",
    "Van Der Berg", "Mc Donald", "St. Claire", "Fitzgerald-O'Malley",
    "Garc\u00eda", "M\u00fcller", "Nakamura-Tanaka", "Nguyen",
    "Kowalski", "Bhattacharya", "Christodoulou", "Papadopoulos",
    "Kim", "Lee", "Chen-Wu", "Del Bosque",
]

MIDDLE_NAMES = [
    "Michael", "Ann", "Marie", "James", "Lynn", "Lee", "Ray", "Jean",
    "Elizabeth", "Jose\u0301", "Mar\u00eda", "Alejandro",
    None, None, None, None, None, None, None,  # ~58% no middle name
]

# ---------------------------------------------------------------------------
# Address pools — includes PO Box, military APO, various formats
# ---------------------------------------------------------------------------

STREETS = [
    "Main Street", "Oak Avenue", "Elm Drive", "Maple Lane", "Cedar Road",
    "Pine Court", "Birch Boulevard", "Martin Luther King Jr. Blvd",
    "1st Avenue", "42nd Street", "Route 66 Highway",
]

APARTMENTS = [
    "Apt 4B", "Suite 200", "#312", "Unit 7", "Apt. 12-A",
    "Ste. 1500", "Floor 3", None, None, None, None, None,
]

CITIES_STATES_ZIPS = [
    ("Springfield", "IL", "62704"),
    ("Naperville", "IL", "60540"),
    ("Miami", "FL", "33101"),
    ("Austin", "TX", "78701"),
    ("Phoenix", "AZ", "85001"),
    ("Denver", "CO", "80201"),
    ("Seattle", "WA", "98101"),
    ("Boston", "MA", "02101"),
    ("Atlanta", "GA", "30301"),
    ("Nashville", "TN", "37201"),
    ("Honolulu", "HI", "96801"),
    ("Juneau", "AK", "99801"),
    # Small population ZIP prefixes (Safe Harbor restricted)
    ("Rutland", "VT", "05901"),
    ("Hyannis", "NE", "69201"),
]

PO_BOXES = [
    "P.O. Box 1234",
    "PO Box 567",
    "P.O. Box 89012",
    "PO Box 3",
]

MILITARY_ADDRESSES = [
    ("APO", "AE", "09001"),
    ("FPO", "AP", "96601"),
    ("DPO", "AA", "34001"),
]

# ---------------------------------------------------------------------------
# Dental-specific data pools
# ---------------------------------------------------------------------------

PROVIDERS = [
    ("Sarah Johnson", "DDS", "1234567890", "BJ1234567", "DDS-IL-12345"),
    ("Roberto Chen-Garcia", "DMD", "2345678901", "AC2345678", "DMD-TX-67890"),
    ("Michelle O'Connor-Park", "DDS", "3456789012", "MP3456789", "DDS-FL-11111"),
    ("David Kim", "DMD", "4567890123", "DK4567890", "DMD-CA-22222"),
    ("Priya Bhattacharya", "DDS", "5678901234", "PB5678901", "DDS-NY-33333"),
]

STAFF = [
    ("Maria Garcia", "RDA"), ("Tuan Nguyen", "RDH"),
    ("Aisha Williams", "CDA"), ("James O'Brien", "EFDA"),
    ("Svetlana Kowalski", "DA"), ("Kenji Nakamura", "RDH"),
]

PRACTICES = [
    "Family Dentistry", "Dental Associates", "Smile Center",
    "Oral Surgery Specialists", "Pediatric Dental Group",
    "Endodontic Excellence", "Periodontal Health Center",
]

INSURANCE_COMPANIES = [
    ("Delta Dental", "DDI"), ("Cigna Dental", "CIG"),
    ("MetLife Dental", "MLF"), ("Aetna Dental", "AET"),
    ("Guardian Dental", "GRD"), ("United Concordia", "UNC"),
    ("Humana Dental", "HUM"), ("Blue Cross Dental", "BCD"),
]

# CDT procedure codes with fees
CDT_PROCEDURES = [
    ("D0120", "Periodic oral evaluation", 55),
    ("D0150", "Comprehensive oral evaluation", 95),
    ("D0210", "Full mouth series (FMX)", 175),
    ("D0220", "Periapical first film", 35),
    ("D0274", "Bitewings - four films (BWX)", 75),
    ("D0330", "Panoramic radiograph (pano)", 125),
    ("D1110", "Prophylaxis - adult (prophy)", 115),
    ("D1120", "Prophylaxis - child (prophy)", 85),
    ("D2140", "Amalgam - one surface, primary", 175),
    ("D2150", "Amalgam - two surfaces, primary (MO/DO)", 225),
    ("D2330", "Resin composite - one surface, anterior", 195),
    ("D2391", "Resin composite - one surface, posterior", 215),
    ("D2392", "Resin composite - two surfaces, posterior (MOD)", 275),
    ("D2740", "Crown - porcelain/ceramic substrate", 1250),
    ("D2750", "Crown - porcelain fused to high noble metal (PFM)", 1150),
    ("D2950", "Core buildup, including any pins", 325),
    ("D3310", "Root canal therapy (RCT) - anterior", 850),
    ("D3320", "Root canal therapy (RCT) - premolar", 950),
    ("D3330", "Root canal therapy (RCT) - molar", 1150),
    ("D4341", "Periodontal SRP, per quadrant", 275),
    ("D4910", "Periodontal maintenance", 165),
    ("D5110", "Complete denture - maxillary", 1850),
    ("D5120", "Complete denture - mandibular", 1850),
    ("D6010", "Surgical implant placement - endosteal", 2500),
    ("D7140", "Extraction, erupted tooth or exposed root (EXT)", 225),
    ("D7210", "Surgical extraction - soft tissue (SURG EXT)", 375),
    ("D7240", "Surgical extraction of impacted tooth", 475),
    ("D9215", "Local anesthesia", 55),
    ("D9230", "Nitrous oxide analgesia (N2O)", 75),
]

ICD10_CODES = [
    "K02.9", "K02.51", "K02.52", "K02.61",  # Caries
    "K05.10", "K05.11", "K05.20", "K05.30",  # Gingivitis/periodontitis
    "K04.0", "K04.1", "K04.4",                # Pulpitis
    "K08.1", "K08.3",                          # Tooth loss
    "M26.10", "M26.4",                         # Malocclusion
    "K12.1",                                    # Stomatitis
]

ALLERGIES = [
    "Penicillin", "Sulfa drugs", "Latex", "Codeine", "Ibuprofen",
    "Aspirin", "Lidocaine", "Erythromycin", "NKDA (No Known Drug Allergies)",
    "Amoxicillin", "Tetracycline", "Shellfish", "Iodine",
]

MEDICATIONS = [
    "Lisinopril 10mg daily", "Metformin 500mg BID", "Atorvastatin 20mg daily",
    "Amlodipine 5mg daily", "Metoprolol 25mg BID", "Omeprazole 20mg daily",
    "Levothyroxine 50mcg daily", "Aspirin 81mg daily",
    "Warfarin 5mg daily", "Prednisone 10mg PRN",
    "Fluoxetine 20mg daily", "Alprazolam 0.5mg PRN",
    "Albuterol inhaler PRN", "Insulin glargine 20 units QHS",
]

MEDICAL_CONDITIONS = [
    "Hypertension", "Type 2 Diabetes Mellitus", "Hyperlipidemia",
    "Hypothyroidism", "Asthma", "GERD", "Osteoporosis",
    "Atrial fibrillation", "Rheumatoid arthritis", "Depression",
    "Seizure disorder", "Mitral valve prolapse", "Hip replacement (2019)",
]

# Dental abbreviations used in clinical notes
DENTAL_FINDINGS = [
    "Pt presents c/o pain UR quad x 2 wks. Hx of RCT #3 approx 5 yrs ago.",
    "Perio charting: gen 4-5mm PD w/ BOP in post sextants. CAL 3-4mm.",
    "FMX reviewed. Caries noted #14 MOD, #19 DO, #30 MO. Rec tx plan discussed.",
    "Pt reports sensitivity #19 to cold, lingering >10 sec. Pulp test: delayed response.",
    "Ext #1, #16, #17, #32 (3rd molars). All partially erupted/impacted.",
    "Impl site #8 healing well. 4mo post-op. ISQ 72. Ready for impression.",
    "Crown prep #30 PFM. Shade A2. Tmp crown placed w/ TempBond.",
    "SRP UR quad completed. Arestin placed #3, #4 MB, #5 DB pockets.",
    "PA radiograph #19 shows periapical radiolucency ~3mm. Rec RCT.",
    "Pt c/o loose lower partial. Adjusted clasp #21, #28. Reline rec'd.",
    "BW x4 taken. Incipient interproximal caries #4D, #5M. Watch/remineralize.",
    "Ortho consult: Class II div 1 malocclusion. OJ 7mm, OB 5mm. Rec Invisalign.",
]

# Palmer notation examples
PALMER_TEETH = [
    "UR1", "UR2", "UR3", "UR4", "UR5", "UR6", "UR7", "UR8",
    "UL1", "UL2", "UL3", "UL4", "UL5", "UL6", "UL7", "UL8",
    "LR1", "LR2", "LR3", "LR4", "LR5", "LR6", "LR7", "LR8",
    "LL1", "LL2", "LL3", "LL4", "LL5", "LL6", "LL7", "LL8",
]

# Spanish dental terms for bilingual documents
SPANISH_DENTAL = {
    "dolor": "pain",
    "muela": "molar",
    "enc\u00eda": "gum",
    "sangrado": "bleeding",
    "caries": "cavities",
    "corona": "crown",
    "puente": "bridge",
    "limpieza": "cleaning",
    "radiograf\u00eda": "x-ray",
    "extracci\u00f3n": "extraction",
    "empaste": "filling",
    "nervio": "nerve",
    "ra\u00edz": "root",
    "inflamaci\u00f3n": "swelling",
}


# ---------------------------------------------------------------------------
# Random data generators
# ---------------------------------------------------------------------------

def _random_ssn() -> str:
    area = random.randint(1, 899)
    while area in (0, 666) or area >= 900:
        area = random.randint(1, 899)
    group = random.randint(1, 99)
    serial = random.randint(1, 9999)
    return f"{area:03d}-{group:02d}-{serial:04d}"


def _random_ssn_nodash() -> str:
    """SSN without dashes — harder to detect."""
    ssn = _random_ssn()
    return ssn.replace("-", "")


def _random_ssn_last4() -> str:
    """Partial SSN — last 4 only."""
    return f"XXX-XX-{random.randint(1000, 9999)}"


def _random_phone(fmt: str = "random") -> str:
    """Generate phone in various formats."""
    area = random.randint(200, 999)
    exch = random.randint(200, 999)
    num = random.randint(1000, 9999)
    ext = random.randint(100, 9999)

    formats = {
        "parens": f"({area}) {exch}-{num}",
        "dashed": f"{area}-{exch}-{num}",
        "dotted": f"{area}.{exch}.{num}",
        "country": f"+1 {area}-{exch}-{num}",
        "country_parens": f"+1 ({area}) {exch}-{num}",
        "ext": f"({area}) {exch}-{num} ext. {ext}",
        "ext_x": f"{area}-{exch}-{num} x{ext}",
        "spaces": f"{area} {exch} {num}",
        "nopunct": f"{area}{exch}{num}",
    }
    if fmt == "random":
        fmt = random.choice(list(formats.keys()))
    return formats.get(fmt, formats["parens"])


def _random_email(first: str, last: str) -> str:
    # Normalize unicode for email — strip ALL non-ASCII to produce valid emails
    import unicodedata
    clean_first = unicodedata.normalize("NFKD", first.lower())
    clean_first = "".join(c for c in clean_first if c.isascii() and c.isalnum())
    clean_last = unicodedata.normalize("NFKD", last.lower())
    clean_last = "".join(c for c in clean_last if c.isascii() and c.isalnum())

    # Ensure we have at least something
    if not clean_first:
        clean_first = "user"
    if not clean_last:
        clean_last = "patient"

    domains = [
        "gmail.com", "yahoo.com", "outlook.com", "icloud.com",
        "hotmail.com", "aol.com", "protonmail.com",
    ]
    sep = random.choice([".", "_", "", "-"])
    num = random.choice(["", str(random.randint(1, 99)), str(random.randint(1970, 2005))])
    return f"{clean_first}{sep}{clean_last}{num}@{random.choice(domains)}"


def _random_mrn() -> str:
    styles = [
        f"MR-{random.randint(2018, 2025)}-{random.randint(100000, 9999999):07d}",
        f"MRN-{random.randint(10000000, 99999999)}",
        f"Chart #{random.choice(string.ascii_uppercase)}{random.randint(10000, 99999)}",
    ]
    return random.choice(styles)


def _random_npi() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(10))


def _random_dea() -> str:
    """Generate a DEA number (2 letters + 7 digits)."""
    letters = random.choice("ABCFM") + random.choice(string.ascii_uppercase)
    digits = "".join(str(random.randint(0, 9)) for _ in range(7))
    return f"{letters}{digits}"


def _random_state_license() -> str:
    """State dental license number."""
    state = random.choice(["IL", "TX", "CA", "NY", "FL", "WA", "CO"])
    prefix = random.choice(["DDS", "DMD", "DEN", "DN"])
    num = random.randint(10000, 99999)
    return f"{prefix}-{state}-{num}"


def _random_member_id(prefix: str) -> str:
    return f"{prefix}-{random.randint(10000000, 99999999)}"


def _random_ip() -> str:
    return f"{random.choice(['10', '172.16', '192.168'])}.{random.randint(0, 255)}.{random.randint(1, 254)}"


def _random_date(start_year: int = 1940, end_year: int = 2005) -> date:
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def _format_date(d: date, fmt: str = "random") -> str:
    """Format a date in many possible ways."""
    formats = {
        "mm/dd/yyyy": d.strftime("%m/%d/%Y"),
        "mm-dd-yyyy": d.strftime("%m-%d-%Y"),
        "mm/dd/yy": d.strftime("%m/%d/%y"),
        "yyyy-mm-dd": d.strftime("%Y-%m-%d"),
        "yyyymmdd": d.strftime("%Y%m%d"),
        "dd-mon-yyyy": d.strftime("%d-%b-%Y"),
        "month_dd_yyyy": d.strftime("%B %d, %Y"),
        "mon_dd_yyyy": d.strftime("%b %d, %Y"),
        "dd_month_yyyy": d.strftime("%d %B %Y"),
        "mm.dd.yyyy": d.strftime("%m.%d.%Y"),
        "m/d/yy": f"{d.month}/{d.day}/{d.strftime('%y')}",
        "m/d/yyyy": f"{d.month}/{d.day}/{d.year}",
    }
    if fmt == "random":
        fmt = random.choice(list(formats.keys()))
    return formats.get(fmt, formats["mm/dd/yyyy"])


def _random_address(include_apt: bool = True) -> str:
    """Generate a random street address."""
    num = random.randint(1, 9999)
    street = random.choice(STREETS)
    apt = random.choice(APARTMENTS) if include_apt else None
    city, state, zipcode = random.choice(CITIES_STATES_ZIPS)

    addr = f"{num} {street}"
    if apt:
        addr += f" {apt}"
    addr += f", {city}, {state} {zipcode}"
    return addr


def _random_po_box_address() -> str:
    box = random.choice(PO_BOXES)
    city, state, zipcode = random.choice(CITIES_STATES_ZIPS)
    return f"{box}, {city}, {state} {zipcode}"


def _random_military_address() -> str:
    first = random.choice(FIRST_NAMES[:15])
    last = random.choice(LAST_NAMES[:10])
    unit = f"Unit {random.randint(1000, 9999)}"
    city, state, zipcode = random.choice(MILITARY_ADDRESSES)
    return f"{first} {last}\n{unit}\n{city}, {state} {zipcode}"


def _random_tooth() -> int:
    return random.randint(1, 32)


def _random_age_over_89() -> int:
    return random.randint(90, 105)


def _random_age_boundary() -> int:
    """Ages near the HIPAA 89/90 threshold."""
    return random.choice([88, 89, 90, 91, 92])


# ---------------------------------------------------------------------------
# OCR artifact simulation
# ---------------------------------------------------------------------------

def _add_ocr_artifacts(text: str, intensity: float = 0.3) -> str:
    """Simulate OCR scanning artifacts on text."""
    result = []
    for ch in text:
        r = random.random()
        if r < intensity * 0.05:
            # Character substitution (common OCR errors)
            subs = {
                "0": "O", "O": "0", "1": "l", "l": "1",
                "I": "l", "5": "S", "S": "5", "8": "B",
                "B": "8", "6": "G", "G": "6", "2": "Z",
                "rn": "m", "m": "rn", "c": "(", "e": "c",
            }
            result.append(subs.get(ch, ch))
        elif r < intensity * 0.03:
            # Double space
            result.append(ch)
            result.append(" ")
        elif r < intensity * 0.02:
            # Line break mid-word
            result.append(ch)
            result.append("\n")
        elif r < intensity * 0.01:
            # Garbled character
            result.append(random.choice("@#$%&*"))
        else:
            result.append(ch)
    return "".join(result)


# ---------------------------------------------------------------------------
# Document generators
# ---------------------------------------------------------------------------

class DentalStressGenerator:
    """Generates adversarial dental office documents for stress testing.

    Each generate_* method returns a dict with:
      - "text": the raw document text
      - "phi": dict of all PHI values embedded in the document
      - "doc_type": string label for the document type
    """

    def __init__(self, seed: Optional[int] = None):
        if seed is not None:
            random.seed(seed)

    def _make_patient(self, age_override: Optional[int] = None) -> dict:
        """Create a synthetic patient identity with all PHI."""
        first = random.choice(FIRST_NAMES)
        middle = random.choice(MIDDLE_NAMES)
        last = random.choice(LAST_NAMES)
        full_name = f"{first} {middle} {last}" if middle else f"{first} {last}"

        if age_override is not None:
            # Generate DOB for specific age
            today = date(2025, 1, 1)
            dob = today.replace(year=today.year - age_override)
        else:
            dob = _random_date(1930, 2010)

        ssn = _random_ssn()
        phone = _random_phone()
        email = _random_email(first, last)
        address = _random_address()
        mrn = _random_mrn()
        insurance, ins_prefix = random.choice(INSURANCE_COMPANIES)
        member_id = _random_member_id(ins_prefix)
        ip = _random_ip()

        return {
            "first": first,
            "middle": middle,
            "last": last,
            "full_name": full_name,
            "dob": dob,
            "ssn": ssn,
            "ssn_nodash": ssn.replace("-", ""),
            "phone": phone,
            "email": email,
            "address": address,
            "mrn": mrn,
            "insurance": insurance,
            "ins_prefix": ins_prefix,
            "member_id": member_id,
            "ip": ip,
        }

    def _make_provider(self) -> dict:
        name, title, npi, dea, lic = random.choice(PROVIDERS)
        return {
            "name": name, "title": title,
            "npi": npi, "dea": dea, "license": lic,
        }

    def generate_dentrix_csv(self, num_rows: int = 50) -> dict:
        """Generate Dentrix-style pipe-delimited patient export.

        Dentrix uses pipe delimiters, mixed date formats, and weird column names.
        """
        # Dentrix-style headers
        header = (
            "PatName|PatSSN|PatDOB|PatPhone|PatAddr1|PatAddr2|PatCity"
            "|PatSt|PatZip|PatEmail|PatMRN|PriInsID|PriInsGrp"
            "|PatGender|PatAge|LastVisit|NextAppt|Balance|ProvName|ProvNPI"
        )
        rows = [header]
        patients = []

        date_formats = [
            "mm/dd/yyyy", "mm/dd/yy", "yyyymmdd", "yyyy-mm-dd",
            "mm-dd-yyyy", "m/d/yy", "m/d/yyyy",
        ]

        for i in range(num_rows):
            age_override = None
            if i < 3:
                age_override = _random_age_boundary()  # HIPAA threshold ages

            pat = self._make_patient(age_override=age_override)
            prov = self._make_provider()
            patients.append(pat)

            # Mix date formats within the same CSV (real Dentrix exports do this)
            dob_fmt = random.choice(date_formats)
            visit_fmt = random.choice(date_formats)
            appt_fmt = random.choice(date_formats)

            last_visit = _random_date(2024, 2025)
            next_appt = last_visit + timedelta(days=random.randint(7, 180))
            age = (date(2025, 1, 1) - pat["dob"]).days // 365

            # Sometimes SSN has dashes, sometimes not
            ssn_val = pat["ssn"] if random.random() > 0.3 else pat["ssn_nodash"]

            city, state, zipcode = random.choice(CITIES_STATES_ZIPS)
            street = f"{random.randint(1, 9999)} {random.choice(STREETS)}"
            apt = random.choice(APARTMENTS)
            addr2 = apt if apt else ""

            gender = random.choice(["M", "F", "O", "U"])
            balance = round(random.uniform(0, 2500), 2)

            row = (
                f"{pat['full_name']}|{ssn_val}"
                f"|{_format_date(pat['dob'], dob_fmt)}"
                f"|{pat['phone']}|{street}|{addr2}|{city}|{state}|{zipcode}"
                f"|{pat['email']}|{pat['mrn']}|{pat['member_id']}"
                f"|GRP-{random.randint(10000, 99999)}"
                f"|{gender}|{age}"
                f"|{_format_date(last_visit, visit_fmt)}"
                f"|{_format_date(next_appt, appt_fmt)}"
                f"|{balance:.2f}|Dr. {prov['name']}|{prov['npi']}"
            )
            rows.append(row)

        text = "\n".join(rows)

        phi = {
            "ssns": [p["ssn"] for p in patients],
            "emails": [p["email"] for p in patients],
            "phones": [p["phone"] for p in patients],
            "names": [p["full_name"] for p in patients],
            "mrns": [p["mrn"] for p in patients],
            "member_ids": [p["member_id"] for p in patients],
        }

        return {"text": text, "phi": phi, "doc_type": "dentrix_csv"}

    def generate_clinical_note(self, with_palmer: bool = False) -> dict:
        """Generate clinical note with real dental abbreviations.

        Includes tooth numbering (universal + optional Palmer), procedure
        narratives, dental shorthand, and full PHI.
        """
        pat = self._make_patient()
        prov = self._make_provider()
        staff_first, staff_last = random.choice(FIRST_NAMES[:15]), random.choice(LAST_NAMES[:10])
        staff_title = random.choice(["RDA", "RDH", "CDA", "EFDA"])

        dos = _random_date(2024, 2025)
        followup = dos + timedelta(days=random.randint(7, 90))

        procedures = random.sample(CDT_PROCEDURES, k=random.randint(1, 5))
        findings = random.sample(DENTAL_FINDINGS, k=random.randint(1, 3))
        teeth = [_random_tooth() for _ in range(random.randint(1, 4))]

        allergies = random.sample(ALLERGIES, k=random.randint(1, 3))
        medications = random.sample(MEDICATIONS, k=random.randint(0, 4))
        conditions = random.sample(MEDICAL_CONDITIONS, k=random.randint(0, 3))

        # Build the note with dental abbreviations
        note = f"""PATIENT: {pat['full_name']}
DOB: {_format_date(pat['dob'])}
SSN: {pat['ssn']}
MRN: {pat['mrn']}
Phone: {pat['phone']}
Email: {pat['email']}
Address: {pat['address']}

PROVIDER: Dr. {prov['name']}, {prov['title']}
NPI: {prov['npi']}
DEA: {prov['dea']}
License: {prov['license']}
Practice: {random.choice(CITIES_STATES_ZIPS)[0]} {random.choice(PRACTICES)}

DATE OF SERVICE: {_format_date(dos)}

MEDICAL HISTORY:
Allergies: {', '.join(allergies)}
Medications: {'; '.join(medications) if medications else 'None reported'}
Medical Conditions: {'; '.join(conditions) if conditions else 'None reported'}

CHIEF COMPLAINT:
Pt presents c/o discomfort #{teeth[0]}. Hx of tx #{teeth[0]} approx 2 yrs ago.
{"Palmer notation: " + random.choice(PALMER_TEETH) + " involved" if with_palmer else ""}

CLINICAL EXAMINATION:
"""
        for finding in findings:
            note += f"{finding}\n"

        note += f"""
VITALS: BP 120/80, HR 72, Temp 98.6F, SpO2 99%

RADIOGRAPHIC FINDINGS:
PA radiograph #{teeth[0]} shows {"periapical radiolucency ~3mm" if random.random() > 0.5 else "no significant findings"}.
{"BW x4: incipient caries noted #" + str(teeth[-1]) + "M" if len(teeth) > 1 else ""}

DIAGNOSIS:
"""
        for icd in random.sample(ICD10_CODES, k=random.randint(1, 3)):
            note += f"- {icd}\n"

        note += f"""
TREATMENT PLAN:
"""
        total = 0
        for code, desc, base_fee in procedures:
            fee = base_fee + random.randint(-20, 50)
            total += fee
            tooth_ref = f"#{random.choice(teeth)}" if random.random() > 0.3 else ""
            note += f"- {code} {desc} {tooth_ref} (${fee})\n"

        note += f"""
Insurance: {pat['insurance']} (Member ID: {pat['member_id']})
Group #: GRP-{random.randint(10000, 99999)}
Estimated patient responsibility: ${random.randint(50, 500)}
Total estimated fee: ${total}

ANESTHESIA: Lidocaine 2% w/ 1:100k epi, {random.randint(1, 3)} carp(s) administered
PROCEDURE NOTES:
Patient {pat['first']} {pat['last']} tolerated procedure well.
{"Hemostasis achieved. Sutures placed." if random.random() > 0.5 else "No complications."}
Post-op instructions reviewed and pt verbalized understanding.

POST-OP INSTRUCTIONS:
- Soft diet x 24-48 hrs
- Ibuprofen 600mg q6h PRN pain
- {"Amoxicillin 500mg TID x 7 days" if random.random() > 0.5 else "No antibiotics indicated"}
- Call office if symptoms worsen

NEXT APPOINTMENT: {_format_date(followup)} at {random.randint(8, 16)}:{random.choice(['00', '15', '30', '45'])} {'AM' if random.random() > 0.5 else 'PM'}

Notes entered by: {staff_first} {staff_last}, {staff_title}
Reviewed by: Dr. {prov['name']}, {prov['title']}
IP: {pat['ip']}
"""

        phi = {
            "patient_name": pat["full_name"],
            "ssn": pat["ssn"],
            "email": pat["email"],
            "phone": pat["phone"],
            "address": pat["address"],
            "mrn": pat["mrn"],
            "member_id": pat["member_id"],
            "dob": pat["dob"].isoformat(),
            "npi": prov["npi"],
            "dea": prov["dea"],
            "license": prov["license"],
            "ip": pat["ip"],
            "staff_name": f"{staff_first} {staff_last}",
            "provider_name": prov["name"],
        }

        return {"text": note, "phi": phi, "doc_type": "clinical_note"}

    def generate_insurance_eob(self, with_ocr_artifacts: bool = True) -> dict:
        """Generate insurance EOB text with optional OCR artifacts.

        Simulates a scanned EOB document with garbled characters,
        broken lines, and common OCR misreads.
        """
        pat = self._make_patient()
        prov = self._make_provider()
        dos = _random_date(2024, 2025)
        procedures = random.sample(CDT_PROCEDURES, k=random.randint(2, 6))
        insurance, ins_prefix = random.choice(INSURANCE_COMPANIES)
        claim_id = f"CLM-{dos.year}-{random.randint(100000, 999999)}"

        total_submitted = 0
        total_allowed = 0
        total_paid = 0
        proc_lines = []
        for code, desc, base_fee in procedures:
            submitted = base_fee + random.randint(-10, 30)
            allowed = int(submitted * random.uniform(0.6, 0.95))
            paid = int(allowed * random.uniform(0.5, 0.8))
            total_submitted += submitted
            total_allowed += allowed
            total_paid += paid
            tooth = _random_tooth()
            proc_lines.append(
                f"  {code}    {desc:<45}  #{tooth:>2}"
                f"   ${submitted:>8.2f}   ${allowed:>8.2f}   ${paid:>8.2f}"
            )

        patient_resp = total_submitted - total_paid

        eob = f"""
EXPLANATION OF BENEFITS
=======================

{insurance}
Claims Processing Center
P.O. Box {random.randint(10000, 99999)}
{random.choice(CITIES_STATES_ZIPS)[0]}, {random.choice(CITIES_STATES_ZIPS)[1]} {random.choice(CITIES_STATES_ZIPS)[2]}

THIS IS NOT A BILL

Claim ID: {claim_id}
Date Processed: {_format_date(dos + timedelta(days=random.randint(14, 45)))}

SUBSCRIBER INFORMATION:
  Name: {pat['full_name']}
  Member ID: {pat['member_id']}
  Group #: GRP-{random.randint(10000, 99999)}
  SSN: ***-**-{pat['ssn'][-4:]}
  DOB: {_format_date(pat['dob'])}

PATIENT INFORMATION:
  Patient Name: {pat['full_name']}
  Relationship: Self
  Patient ID: {pat['mrn']}

PROVIDER INFORMATION:
  Provider: Dr. {prov['name']}, {prov['title']}
  NPI: {prov['npi']}
  Tax ID: {random.randint(10, 99)}-{random.randint(1000000, 9999999)}

DATE OF SERVICE: {_format_date(dos)}

PROCEDURE DETAILS:
  Code    Description                                    Tooth  Submitted   Allowed      Paid
  ----    -----------                                    -----  ---------   -------      ----
{chr(10).join(proc_lines)}

                                                         TOTALS: ${total_submitted:>8.2f}   ${total_allowed:>8.2f}   ${total_paid:>8.2f}

PATIENT RESPONSIBILITY: ${patient_resp:.2f}

REMARKS:
- Benefits applied to annual maximum. Remaining maximum: ${random.randint(100, 1500):.2f}
- Waiting period satisfied for major services.
- Claim processed per plan {ins_prefix}-PLAN-{dos.year}

For questions call: {_random_phone("dashed")}
Member portal: https://www.{insurance.lower().replace(' ', '')}.com/members
Address correspondence to: {pat['full_name']}, {pat['address']}
"""

        phi = {
            "patient_name": pat["full_name"],
            "ssn_last4": pat["ssn"][-4:],
            "ssn": pat["ssn"],
            "email": pat["email"],
            "phone": pat["phone"],
            "address": pat["address"],
            "mrn": pat["mrn"],
            "member_id": pat["member_id"],
            "dob": pat["dob"].isoformat(),
            "npi": prov["npi"],
            "provider_name": prov["name"],
            "claim_id": claim_id,
        }

        if with_ocr_artifacts:
            eob = _add_ocr_artifacts(eob, intensity=0.2)

        return {"text": eob, "phi": phi, "doc_type": "insurance_eob"}

    def generate_intake_form(self) -> dict:
        """Generate a patient intake/registration form."""
        pat = self._make_patient()

        allergies = random.sample(ALLERGIES, k=random.randint(1, 4))
        medications = random.sample(MEDICATIONS, k=random.randint(0, 5))
        conditions = random.sample(MEDICAL_CONDITIONS, k=random.randint(0, 4))

        emergency_first = random.choice(FIRST_NAMES[:15])
        emergency_last = random.choice(LAST_NAMES[:10])
        emergency_phone = _random_phone()

        form = f"""
{'=' * 60}
        PATIENT REGISTRATION FORM
{'=' * 60}

PATIENT INFORMATION:
  Last Name: {pat['last']}
  First Name: {pat['first']}
  Middle Name: {pat['middle'] or 'N/A'}
  Preferred Name: {pat['first']}
  Date of Birth: {_format_date(pat['dob'])}
  Social Security #: {pat['ssn']}
  Gender: {random.choice(['Male', 'Female', 'Non-binary', 'Prefer not to say'])}
  Marital Status: {random.choice(['Single', 'Married', 'Divorced', 'Widowed'])}

CONTACT INFORMATION:
  Home Address: {pat['address']}
  Mailing Address: {_random_po_box_address() if random.random() > 0.7 else 'Same as above'}
  Home Phone: {pat['phone']}
  Cell Phone: {_random_phone()}
  Work Phone: {_random_phone('ext') if random.random() > 0.5 else 'N/A'}
  Email: {pat['email']}

EMPLOYMENT INFORMATION:
  Employer: {random.choice(['Self-employed', 'Retired', 'ABC Corporation', 'City of ' + random.choice(CITIES_STATES_ZIPS)[0]])}
  Occupation: {random.choice(['Teacher', 'Engineer', 'Retired', 'Homemaker', 'Manager', 'Nurse'])}

EMERGENCY CONTACT:
  Name: {emergency_first} {emergency_last}
  Relationship: {random.choice(['Spouse', 'Parent', 'Sibling', 'Friend', 'Child'])}
  Phone: {emergency_phone}

DENTAL INSURANCE (PRIMARY):
  Insurance Company: {pat['insurance']}
  Member ID: {pat['member_id']}
  Group #: GRP-{random.randint(10000, 99999)}
  Subscriber Name: {pat['full_name']}
  Subscriber SSN: {pat['ssn']}
  Subscriber DOB: {_format_date(pat['dob'])}

MEDICAL HISTORY:
  Allergies: {', '.join(allergies)}
  Current Medications:
{chr(10).join('    - ' + m for m in medications) if medications else '    None'}
  Medical Conditions:
{chr(10).join('    - ' + c for c in conditions) if conditions else '    None'}

  Are you currently under a physician's care? {random.choice(['Yes', 'No'])}
  Physician Name: Dr. {random.choice(FIRST_NAMES[:10])} {random.choice(LAST_NAMES[:10])}
  Physician Phone: {_random_phone()}

DENTAL HISTORY:
  Last dental visit: {_format_date(_random_date(2023, 2025))}
  Reason for today's visit: {random.choice(['Routine checkup', 'Toothache', 'Broken tooth', 'Cleaning', 'Second opinion'])}
  Have you had any dental problems? {random.choice(['Yes', 'No'])}

PATIENT SIGNATURE: ____________________  Date: {_format_date(_random_date(2025, 2025))}
"""

        phi = {
            "patient_name": pat["full_name"],
            "first_name": pat["first"],
            "last_name": pat["last"],
            "ssn": pat["ssn"],
            "email": pat["email"],
            "phone": pat["phone"],
            "address": pat["address"],
            "mrn": pat["mrn"],
            "member_id": pat["member_id"],
            "dob": pat["dob"].isoformat(),
            "emergency_contact": f"{emergency_first} {emergency_last}",
            "emergency_phone": emergency_phone,
        }

        return {"text": form, "phi": phi, "doc_type": "intake_form"}

    def generate_referral_letter(self) -> dict:
        """Generate a referral letter between dentists/specialists."""
        pat = self._make_patient()
        referring = self._make_provider()
        specialist = self._make_provider()

        dos = _random_date(2024, 2025)
        teeth = [_random_tooth() for _ in range(random.randint(1, 3))]

        letter = f"""
{random.choice(CITIES_STATES_ZIPS)[0]} {random.choice(PRACTICES)}
{_random_address(include_apt=False)}
Phone: {_random_phone()}  |  Fax: {_random_phone()}

{_format_date(dos, "month_dd_yyyy")}

{random.choice(["Endodontic", "Periodontal", "Oral Surgery", "Orthodontic", "Prosthodontic"])} Associates
{_random_address(include_apt=False)}

RE: Patient Referral — {pat['full_name']}
    DOB: {_format_date(pat['dob'])}
    SSN: {pat['ssn']}
    Patient Phone: {pat['phone']}
    Insurance: {pat['insurance']} (Member ID: {pat['member_id']})

Dear Dr. {specialist['name']},

I am referring my patient, {pat['first']} {pat['last']}, for evaluation and
possible treatment of {"tooth #" + str(teeth[0])}.

CLINICAL SUMMARY:
Patient {pat['full_name']} presented on {_format_date(dos)} with
{random.choice(["acute pulpitis", "periapical abscess", "advanced periodontal disease", "impacted wisdom teeth", "fractured cusp"])}.
{"Periapical radiograph shows radiolucency ~" + str(random.randint(2, 5)) + "mm at apex of #" + str(teeth[0]) + "." if random.random() > 0.3 else ""}

RELEVANT HISTORY:
- {random.choice(DENTAL_FINDINGS)}
- Previous tx: {random.choice(["RCT #" + str(teeth[0]) + " (2019)", "Crown #" + str(teeth[0]) + " (2020)", "SRP all quads (2023)", "No prior treatment"])}
- Medical: {random.choice(MEDICAL_CONDITIONS)}
- Medications: {random.choice(MEDICATIONS)}
- Allergies: {random.choice(ALLERGIES)}

Current radiographs {"are enclosed" if random.random() > 0.5 else "will be sent electronically"}.

Please contact our office at {_random_phone()} if you need additional information.

Thank you for your prompt attention to this matter.

Sincerely,

Dr. {referring['name']}, {referring['title']}
NPI: {referring['npi']}
DEA: {referring['dea']}
License: {referring['license']}
"""

        phi = {
            "patient_name": pat["full_name"],
            "ssn": pat["ssn"],
            "phone": pat["phone"],
            "member_id": pat["member_id"],
            "dob": pat["dob"].isoformat(),
            "referring_npi": referring["npi"],
            "referring_dea": referring["dea"],
            "referring_name": referring["name"],
            "specialist_name": specialist["name"],
        }

        return {"text": letter, "phi": phi, "doc_type": "referral_letter"}

    def generate_json_clinical_record(self) -> dict:
        """Generate a JSON clinical record (EHR export format)."""
        pat = self._make_patient()
        prov = self._make_provider()
        dos = _random_date(2024, 2025)
        procedures = random.sample(CDT_PROCEDURES, k=random.randint(1, 4))
        teeth = [_random_tooth() for _ in range(random.randint(1, 3))]

        record = {
            "encounter_id": f"ENC-{dos.year}-{random.randint(100000, 999999)}",
            "patient": {
                "name": {"first": pat["first"], "middle": pat["middle"], "last": pat["last"]},
                "dob": pat["dob"].isoformat(),
                "ssn": pat["ssn"],
                "mrn": pat["mrn"],
                "contact": {
                    "phone": pat["phone"],
                    "email": pat["email"],
                    "address": pat["address"],
                },
                "insurance": {
                    "payer": pat["insurance"],
                    "member_id": pat["member_id"],
                    "group": f"GRP-{random.randint(10000, 99999)}",
                },
            },
            "provider": {
                "name": f"Dr. {prov['name']}",
                "npi": prov["npi"],
                "dea": prov["dea"],
                "license": prov["license"],
            },
            "encounter": {
                "date": dos.isoformat(),
                "type": random.choice(["exam", "treatment", "emergency", "follow-up"]),
                "chief_complaint": f"Pain tooth #{teeth[0]}",
                "findings": [random.choice(DENTAL_FINDINGS) for _ in range(random.randint(1, 2))],
                "diagnoses": random.sample(ICD10_CODES, k=random.randint(1, 3)),
                "procedures": [
                    {
                        "code": code,
                        "description": desc,
                        "tooth": random.choice(teeth),
                        "fee": base + random.randint(-10, 30),
                        "surface": random.choice(["M", "O", "D", "B", "L", "MO", "DO", "MOD", None]),
                    }
                    for code, desc, base in procedures
                ],
            },
            "notes": f"Patient {pat['first']} {pat['last']} tolerated procedure well.",
            "metadata": {
                "created_by": f"{random.choice(FIRST_NAMES[:10])} {random.choice(LAST_NAMES[:10])}",
                "ip_address": pat["ip"],
                "system": "Dentrix G7",
            },
        }

        text = json.dumps(record, indent=2, ensure_ascii=False)

        phi = {
            "patient_name": pat["full_name"],
            "ssn": pat["ssn"],
            "email": pat["email"],
            "phone": pat["phone"],
            "address": pat["address"],
            "mrn": pat["mrn"],
            "member_id": pat["member_id"],
            "dob": pat["dob"].isoformat(),
            "npi": prov["npi"],
            "dea": prov["dea"],
            "ip": pat["ip"],
        }

        return {"text": text, "phi": phi, "doc_type": "json_clinical"}

    def generate_xml_hl7_message(self) -> dict:
        """Generate an HL7-style XML message (simplified dental ADT/claim)."""
        pat = self._make_patient()
        prov = self._make_provider()
        dos = _random_date(2024, 2025)
        procedures = random.sample(CDT_PROCEDURES, k=random.randint(1, 3))

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3" xmlns:sdtc="urn:hl7-org:sdtc">
  <id root="2.16.840.1.113883.3.1" extension="{random.randint(100000, 999999)}"/>
  <effectiveTime value="{dos.strftime('%Y%m%d')}"/>
  <recordTarget>
    <patientRole>
      <id extension="{pat['mrn']}" root="2.16.840.1.113883.3.1"/>
      <addr>
        <streetAddressLine>{random.randint(1, 9999)} {random.choice(STREETS)}</streetAddressLine>
        <city>{random.choice(CITIES_STATES_ZIPS)[0]}</city>
        <state>{random.choice(CITIES_STATES_ZIPS)[1]}</state>
        <postalCode>{random.choice(CITIES_STATES_ZIPS)[2]}</postalCode>
      </addr>
      <telecom value="tel:{pat['phone']}" use="HP"/>
      <telecom value="mailto:{pat['email']}" use="HP"/>
      <patient>
        <name>
          <given>{pat['first']}</given>
          {"<given>" + pat['middle'] + "</given>" if pat['middle'] else ""}
          <family>{pat['last']}</family>
        </name>
        <birthTime value="{pat['dob'].strftime('%Y%m%d')}"/>
        <administrativeGenderCode code="{random.choice(['M', 'F', 'UN'])}" codeSystem="2.16.840.1.113883.5.1"/>
        <sdtc:id extension="{pat['ssn']}" root="2.16.840.1.113883.4.1"/>
      </patient>
    </patientRole>
  </recordTarget>
  <author>
    <assignedAuthor>
      <id extension="{prov['npi']}" root="2.16.840.1.113883.4.6"/>
      <assignedPerson>
        <name>
          <prefix>Dr.</prefix>
          <given>{prov['name'].split()[0]}</given>
          <family>{prov['name'].split()[-1]}</family>
          <suffix>{prov['title']}</suffix>
        </name>
      </assignedPerson>
    </assignedAuthor>
  </author>
  <component>
    <structuredBody>
      <component>
        <section>
          <code code="11450-4" displayName="Problem List"/>
          <text>
            Patient {pat['full_name']} presents with dental concerns.
            Member ID: {pat['member_id']}
            Insurance: {pat['insurance']}
          </text>
        </section>
      </component>
      <component>
        <section>
          <code code="29545-1" displayName="Procedures"/>
          <text>
"""
        for code, desc, fee in procedures:
            xml += f"            {code}: {desc} (${fee})\n"

        xml += f"""          </text>
        </section>
      </component>
    </structuredBody>
  </component>
</ClinicalDocument>"""

        phi = {
            "patient_name": pat["full_name"],
            "ssn": pat["ssn"],
            "email": pat["email"],
            "phone": pat["phone"],
            "mrn": pat["mrn"],
            "member_id": pat["member_id"],
            "dob": pat["dob"].isoformat(),
            "npi": prov["npi"],
        }

        return {"text": xml, "phi": phi, "doc_type": "xml_hl7"}

    def generate_bilingual_note(self) -> dict:
        """Generate a clinical note mixing English and Spanish.

        Common in dental practices serving Hispanic communities.
        """
        pat = self._make_patient()
        prov = self._make_provider()
        dos = _random_date(2024, 2025)
        tooth = _random_tooth()

        # Pick some Spanish terms
        complaints = random.sample(list(SPANISH_DENTAL.items()), k=3)

        note = f"""PATIENT: {pat['full_name']}
DOB: {_format_date(pat['dob'])}
SSN: {pat['ssn']}
Phone: {pat['phone']}
Email: {pat['email']}

PROVIDER: Dr. {prov['name']}, {prov['title']}
NPI: {prov['npi']}
DATE: {_format_date(dos)}

CHIEF COMPLAINT / QUEJA PRINCIPAL:
Pt reports "{complaints[0][0]}" ({complaints[0][1]}) in #{tooth} area.
Patient states: "Tengo mucho {complaints[0][0]} en la {complaints[1][0]}."
"Mi {complaints[2][0]} esta muy mal."
Translation: Patient reports significant {complaints[0][1]} in the {complaints[1][1]}.
The {complaints[2][1]} is very bad.

EXAM / EXAMEN:
Tooth #{tooth} presents with extensive caries (caries extensas).
Radiografia (radiograph) shows periapical involvement.
Encía (gum) tissue is inflamed and tender to palpation.

PLAN DE TRATAMIENTO / TREATMENT PLAN:
1. Extracci\u00f3n (extraction) #{tooth} if non-restorable
2. Corona (crown) #{tooth} if RCT successful
3. Limpieza (cleaning) - prophy D1110

Patient {pat['first']} {pat['last']} verbalized understanding in Spanish.
Interpreter used: {random.choice(['Yes - staff interpreter', 'No - bilingual provider', 'Yes - phone interpreter'])}

Insurance: {pat['insurance']} (Member ID: {pat['member_id']})
Address: {pat['address']}
"""

        phi = {
            "patient_name": pat["full_name"],
            "ssn": pat["ssn"],
            "email": pat["email"],
            "phone": pat["phone"],
            "address": pat["address"],
            "member_id": pat["member_id"],
            "dob": pat["dob"].isoformat(),
            "npi": prov["npi"],
            "provider_name": prov["name"],
        }

        return {"text": note, "phi": phi, "doc_type": "bilingual_note"}

    def generate_long_document(self, page_count: int = 12) -> dict:
        """Generate a very long document (~page_count pages of clinical text).

        Tests performance with large inputs.
        """
        pat = self._make_patient()
        prov = self._make_provider()

        sections = []

        # Header with PHI
        header = f"""
COMPREHENSIVE TREATMENT RECORD
Patient: {pat['full_name']}
DOB: {_format_date(pat['dob'])}
SSN: {pat['ssn']}
MRN: {pat['mrn']}
Phone: {pat['phone']}
Email: {pat['email']}
Address: {pat['address']}
Insurance: {pat['insurance']} (Member ID: {pat['member_id']})
Provider: Dr. {prov['name']}, {prov['title']}
NPI: {prov['npi']}
"""
        sections.append(header)

        # Generate many visit entries to fill pages
        # ~500 chars per visit entry, ~3000 chars per page, so ~6 entries per page
        entries_needed = page_count * 6
        for i in range(entries_needed):
            visit_date = _random_date(2020, 2025)
            procedures = random.sample(CDT_PROCEDURES, k=random.randint(1, 3))
            teeth = [_random_tooth() for _ in range(random.randint(1, 2))]
            finding = random.choice(DENTAL_FINDINGS)

            entry = f"""
--- Visit {i + 1}: {_format_date(visit_date)} ---
Provider: Dr. {prov['name']}
CC: Pt c/o #{teeth[0]}. {finding}
Tx:
"""
            for code, desc, fee in procedures:
                entry += f"  {code} {desc} #{random.choice(teeth)} ${fee}\n"

            entry += f"  Patient {pat['first']} {pat['last']} tolerated procedure well.\n"
            sections.append(entry)

        text = "\n".join(sections)

        phi = {
            "patient_name": pat["full_name"],
            "ssn": pat["ssn"],
            "email": pat["email"],
            "phone": pat["phone"],
            "address": pat["address"],
            "mrn": pat["mrn"],
            "member_id": pat["member_id"],
            "dob": pat["dob"].isoformat(),
            "npi": prov["npi"],
            "provider_name": prov["name"],
        }

        return {"text": text, "phi": phi, "doc_type": "long_document"}

    def generate_empty_document(self) -> dict:
        """Generate an empty/minimal document."""
        return {"text": "", "phi": {}, "doc_type": "empty"}

    def generate_minimal_document(self) -> dict:
        """Generate a document with minimal content."""
        return {
            "text": "This is a dental office note with no patient information.",
            "phi": {},
            "doc_type": "minimal",
        }

    def generate_no_phi_document(self) -> dict:
        """Generate a document with clinical content but zero PHI.

        Should produce clean output with no redactions.
        """
        text = """
DENTAL OFFICE POLICIES AND PROCEDURES

Infection Control Protocol:
All instruments are sterilized using autoclave at 270 degrees F for 30 minutes.
Surfaces are disinfected between patients using EPA-registered disinfectant.
Staff must wear appropriate PPE including gloves, masks, and eye protection.

Radiograph Protocol:
Bitewing radiographs are taken annually for caries-active patients.
Full mouth series (FMX) every 3-5 years as indicated.
Panoramic radiograph for new patients and as needed for treatment planning.
All radiographs stored digitally in DICOM format.

Common CDT Codes Used in This Office:
D0120 - Periodic oral evaluation
D0150 - Comprehensive oral evaluation
D0210 - Full mouth series
D0274 - Bitewings (4 films)
D1110 - Adult prophylaxis
D2391 - Resin composite, 1 surface posterior
D2750 - Crown, PFM
D3330 - Root canal, molar
D7140 - Extraction, erupted tooth

Appointment Scheduling Guidelines:
- New patient exams: 60 minutes
- Recall/prophy: 45 minutes
- Single crown prep: 90 minutes
- Root canal (anterior): 60 minutes
- Root canal (molar): 90-120 minutes
- Surgical extraction: 45-60 minutes

Payment Policy:
Insurance claims filed on behalf of all patients.
Patient responsibility due at time of service.
Payment plans available for treatment over $500.
"""
        return {"text": text, "phi": {}, "doc_type": "no_phi"}

    def generate_age_boundary_document(self) -> dict:
        """Generate a document with ages near the HIPAA 89/90 threshold."""
        patients = []
        lines = ["PATIENT AGE REPORT\n"]

        for age in [85, 87, 88, 89, 90, 91, 92, 95, 100, 103]:
            pat = self._make_patient(age_override=age)
            patients.append(pat)
            lines.append(
                f"Patient: {pat['full_name']}, Age: {age}, "
                f"{age} years old, DOB: {_format_date(pat['dob'])}, "
                f"SSN: {pat['ssn']}, Phone: {pat['phone']}"
            )
            if age >= 90:
                lines.append(f"  ** Note: {pat['first']} is a {age} y/o patient **")

        text = "\n".join(lines)

        phi = {
            "names": [p["full_name"] for p in patients],
            "ssns": [p["ssn"] for p in patients],
            "phones": [p["phone"] for p in patients],
            "ages_over_89": [a for a in [85, 87, 88, 89, 90, 91, 92, 95, 100, 103] if a > 89],
        }

        return {"text": text, "phi": phi, "doc_type": "age_boundary"}

    def generate_all_phone_formats(self) -> dict:
        """Generate a document with phone numbers in every format."""
        pat = self._make_patient()

        formats = [
            "parens", "dashed", "dotted", "country", "country_parens",
            "ext", "ext_x", "spaces",
        ]

        lines = [f"PATIENT: {pat['full_name']}\nSSN: {pat['ssn']}\n\nCONTACT NUMBERS:"]
        phones = []
        for fmt in formats:
            phone = _random_phone(fmt)
            phones.append(phone)
            label = random.choice(["Phone", "Tel", "Contact", "Cell", "Mobile", "Home"])
            lines.append(f"  {label}: {phone}")

        lines.append(f"\n  Fax: {_random_phone('dashed')}")
        fax_phone = _random_phone("dashed")
        phones.append(fax_phone)

        text = "\n".join(lines)
        phi = {
            "patient_name": pat["full_name"],
            "ssn": pat["ssn"],
            "phones": phones,
        }

        return {"text": text, "phi": phi, "doc_type": "phone_formats"}

    def generate_all_date_formats(self) -> dict:
        """Generate a document with dates in 12+ formats."""
        pat = self._make_patient()
        dob = pat["dob"]
        dos = _random_date(2024, 2025)

        lines = [
            f"PATIENT: {pat['full_name']}",
            f"SSN: {pat['ssn']}",
            "",
            "DATE OF BIRTH IN VARIOUS FORMATS:",
        ]

        all_formats = [
            "mm/dd/yyyy", "mm-dd-yyyy", "mm/dd/yy", "yyyy-mm-dd",
            "yyyymmdd", "dd-mon-yyyy", "month_dd_yyyy", "mon_dd_yyyy",
            "dd_month_yyyy", "mm.dd.yyyy", "m/d/yy", "m/d/yyyy",
        ]

        dates_used = []
        for fmt in all_formats:
            formatted = _format_date(dob, fmt)
            dates_used.append(formatted)
            lines.append(f"  DOB ({fmt}): {formatted}")

        lines.append(f"\nDATE OF SERVICE: {_format_date(dos)}")
        lines.append(f"Next appointment: {_format_date(dos + timedelta(days=14))}")

        text = "\n".join(lines)
        phi = {
            "patient_name": pat["full_name"],
            "ssn": pat["ssn"],
            "dates": dates_used,
            "dob": dob.isoformat(),
        }

        return {"text": text, "phi": phi, "doc_type": "date_formats"}

    def generate_ssn_variants(self) -> dict:
        """Generate a document with SSN in various formats."""
        pat = self._make_patient()
        ssn = pat["ssn"]
        ssn_nodash = ssn.replace("-", "")
        ssn_spaced = ssn.replace("-", " ")
        ssn_last4 = f"XXX-XX-{ssn[-4:]}"

        text = f"""PATIENT: {pat['full_name']}
Phone: {pat['phone']}
Email: {pat['email']}

SSN FORMATS:
  Standard: SSN: {ssn}
  No dashes: Social Security: {ssn_nodash}
  Spaced: SS# {ssn_spaced}
  Last 4 only: SSN: {ssn_last4}
  With label: Social Security Number: {ssn}
"""

        phi = {
            "patient_name": pat["full_name"],
            "ssn": ssn,
            "ssn_nodash": ssn_nodash,
            "ssn_spaced": ssn_spaced,
            "email": pat["email"],
            "phone": pat["phone"],
        }

        return {"text": text, "phi": phi, "doc_type": "ssn_variants"}

    def generate_dental_identifiers(self) -> dict:
        """Generate a document with dental-specific identifiers: NPI, DEA, licenses."""
        providers = [self._make_provider() for _ in range(3)]
        pat = self._make_patient()

        lines = [
            f"PATIENT: {pat['full_name']}",
            f"SSN: {pat['ssn']}",
            f"Phone: {pat['phone']}",
            "",
            "PROVIDER DIRECTORY:",
        ]

        for prov in providers:
            lines.extend([
                f"  Dr. {prov['name']}, {prov['title']}",
                f"    NPI: {prov['npi']}",
                f"    DEA: {prov['dea']}",
                f"    State License: {prov['license']}",
                "",
            ])

        text = "\n".join(lines)
        phi = {
            "patient_name": pat["full_name"],
            "ssn": pat["ssn"],
            "phone": pat["phone"],
            "npis": [p["npi"] for p in providers],
            "deas": [p["dea"] for p in providers],
            "licenses": [p["license"] for p in providers],
            "provider_names": [p["name"] for p in providers],
        }

        return {"text": text, "phi": phi, "doc_type": "dental_identifiers"}


# ---------------------------------------------------------------------------
# Convenience function to generate all document types
# ---------------------------------------------------------------------------

def generate_all_stress_docs(seed: int = 42) -> list[dict]:
    """Generate one of each document type for testing."""
    gen = DentalStressGenerator(seed=seed)
    return [
        gen.generate_dentrix_csv(num_rows=20),
        gen.generate_clinical_note(),
        gen.generate_clinical_note(with_palmer=True),
        gen.generate_insurance_eob(with_ocr_artifacts=False),
        gen.generate_insurance_eob(with_ocr_artifacts=True),
        gen.generate_intake_form(),
        gen.generate_referral_letter(),
        gen.generate_json_clinical_record(),
        gen.generate_xml_hl7_message(),
        gen.generate_bilingual_note(),
        gen.generate_long_document(page_count=12),
        gen.generate_empty_document(),
        gen.generate_minimal_document(),
        gen.generate_no_phi_document(),
        gen.generate_age_boundary_document(),
        gen.generate_all_phone_formats(),
        gen.generate_all_date_formats(),
        gen.generate_ssn_variants(),
        gen.generate_dental_identifiers(),
    ]


def main():
    """Generate stress test fixtures to disk."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate dental stress test data")
    parser.add_argument("--output", type=str, default="tests/fixtures/stress",
                        help="Output directory")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    docs = generate_all_stress_docs(seed=args.seed)

    for i, doc in enumerate(docs):
        doc_type = doc["doc_type"]
        ext = ".json" if "json" in doc_type else ".xml" if "xml" in doc_type else ".txt"
        if doc_type == "dentrix_csv":
            ext = ".csv"
        filepath = out / f"{doc_type}_{i:03d}{ext}"
        filepath.write_text(doc["text"], encoding="utf-8")

        # Also write PHI manifest for verification
        phi_path = out / f"{doc_type}_{i:03d}_phi.json"
        phi_path.write_text(json.dumps(doc["phi"], indent=2, default=str), encoding="utf-8")

    print(f"Generated {len(docs)} stress test documents in {out}/")


if __name__ == "__main__":
    main()
