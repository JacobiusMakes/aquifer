"""Generate synthetic medical/dental records for testing.

Creates realistic but entirely fake patient data using randomized
combinations. No real patient data is ever used.

Usage:
    python tests/generate_synthetic.py --count 10 --output tests/fixtures/synthetic/
"""

from __future__ import annotations

import argparse
import json
import random
import string
from datetime import date, timedelta
from pathlib import Path

# --- Name pools (all fictional) ---
FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
    "Linda", "David", "Elizabeth", "William", "Barbara", "Richard", "Susan",
    "Joseph", "Jessica", "Thomas", "Sarah", "Christopher", "Karen",
    "Daniel", "Lisa", "Matthew", "Nancy", "Anthony", "Betty", "Mark",
    "Margaret", "Maria", "Sandra", "Wei", "Yuki", "Raj", "Fatima",
    "Alejandro", "Olga", "Kwame", "Priya", "Dmitri", "Aisha",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Anderson", "Taylor", "Thomas",
    "Hernandez", "Moore", "Jackson", "Lee", "Perez", "Thompson", "White",
    "Harris", "Clark", "Lewis", "Robinson", "Chen", "Kim", "Patel",
    "Nguyen", "Singh", "Yamamoto", "O'Brien", "Al-Rashid", "Kowalski",
]

MIDDLE_NAMES = [
    "Michael", "Ann", "Marie", "James", "Lynn", "Lee", "Ray", "Jean",
    "Elizabeth", "Joseph", "Grace", "Robert", "Rose", "William", "Mae",
    None, None, None, None, None,  # ~50% chance of no middle name
]

STREETS = [
    "Main Street", "Oak Avenue", "Elm Drive", "Maple Lane", "Cedar Road",
    "Pine Court", "Birch Boulevard", "Walnut Terrace", "Cherry Lane",
    "Willow Way", "Hickory Place", "Spruce Circle", "Ash Parkway",
    "Magnolia Drive", "Cypress Trail", "Sycamore Avenue",
]

CITIES_STATES_ZIPS = [
    ("Springfield", "IL", "62704"), ("Naperville", "IL", "60540"),
    ("Evanston", "IL", "60201"), ("Chicago", "IL", "60601"),
    ("Austin", "TX", "78701"), ("Dallas", "TX", "75201"),
    ("Phoenix", "AZ", "85001"), ("Denver", "CO", "80201"),
    ("Seattle", "WA", "98101"), ("Portland", "OR", "97201"),
    ("Miami", "FL", "33101"), ("Tampa", "FL", "33601"),
    ("Boston", "MA", "02101"), ("Atlanta", "GA", "30301"),
    ("Nashville", "TN", "37201"), ("Charlotte", "NC", "28201"),
]

PROVIDERS = [
    ("Sarah Johnson", "DDS"), ("Robert Chen", "DMD"),
    ("Michelle Park", "DDS"), ("David Kim", "DMD"),
    ("Amanda Lopez", "DDS"), ("James Wilson", "DMD"),
    ("Lisa Patel", "DDS"), ("Brian O'Connor", "DMD"),
]

PRACTICES = [
    "Family Dentistry", "Dental Associates", "Smile Center",
    "Dental Care", "Oral Health Clinic", "Dental Group",
]

INSURANCE_COMPANIES = [
    ("Delta Dental", "DDI"), ("Cigna", "CIG"), ("MetLife Dental", "MLF"),
    ("Aetna Dental", "AET"), ("Guardian", "GRD"), ("United Concordia", "UNC"),
    ("Humana Dental", "HUM"), ("Blue Cross Dental", "BCD"),
]

CDT_PROCEDURES = [
    ("D0120", "Periodic oral evaluation"),
    ("D0150", "Comprehensive oral evaluation"),
    ("D0210", "Full mouth series"),
    ("D0220", "Periapical first film"),
    ("D0274", "Bitewings - four films"),
    ("D0330", "Panoramic radiograph"),
    ("D1110", "Prophylaxis - adult"),
    ("D1120", "Prophylaxis - child"),
    ("D2140", "Amalgam - one surface, primary"),
    ("D2150", "Amalgam - two surfaces, primary"),
    ("D2391", "Resin composite - one surface, posterior"),
    ("D2750", "Crown - porcelain fused to high noble metal"),
    ("D3330", "Root canal therapy, molar"),
    ("D4341", "Periodontal scaling and root planing, per quadrant"),
    ("D7140", "Extraction, erupted tooth"),
    ("D7240", "Surgical extraction of impacted tooth"),
]

CLINICAL_FINDINGS = [
    "Periapical radiograph shows periapical radiolucency consistent with chronic apical periodontitis.",
    "Bitewing radiographs reveal interproximal caries on tooth #{tooth} mesial.",
    "Patient reports intermittent pain for {weeks} weeks.",
    "Periodontal charting shows generalized {depth}mm probing depths.",
    "Clinical examination reveals moderate plaque accumulation.",
    "Intraoral examination shows generalized mild gingivitis.",
    "Tooth #{tooth} presents with extensive decay involving the pulp.",
    "Panoramic radiograph reveals impacted third molars.",
    "Patient reports sensitivity to cold on tooth #{tooth}.",
    "Fractured cusp noted on tooth #{tooth}, distal-lingual.",
]

STAFF_TITLES = ["RDA", "RDH", "CDA", "EFDA", "DA"]


def _random_ssn() -> str:
    area = random.randint(1, 899)
    while area in (0, 666) or area >= 900:
        area = random.randint(1, 899)
    group = random.randint(1, 99)
    serial = random.randint(1, 9999)
    return f"{area:03d}-{group:02d}-{serial:04d}"


def _random_phone() -> str:
    return f"({random.randint(200,999)}) {random.randint(200,999)}-{random.randint(1000,9999)}"


def _random_email(first: str, last: str) -> str:
    domains = ["gmail.com", "yahoo.com", "outlook.com", "icloud.com", "hotmail.com"]
    sep = random.choice([".", "_", ""])
    return f"{first.lower()}{sep}{last.lower()}@{random.choice(domains)}"


def _random_mrn() -> str:
    year = random.randint(2020, 2024)
    num = random.randint(100000, 9999999)
    return f"MR-{year}-{num:07d}"


def _random_npi() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(10))


def _random_member_id(prefix: str) -> str:
    num = random.randint(10000000, 99999999)
    return f"{prefix}-{num}"


def _random_ip() -> str:
    return f"{random.choice(['10', '172.16', '192.168'])}.{random.randint(0,255)}.{random.randint(1,254)}"


def _random_date(start_year: int = 1940, end_year: int = 2005) -> date:
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def generate_clinical_note(patient_id: int = 1) -> dict:
    """Generate a single synthetic clinical note."""
    first = random.choice(FIRST_NAMES)
    middle = random.choice(MIDDLE_NAMES)
    last = random.choice(LAST_NAMES)
    full_name = f"{first} {middle} {last}" if middle else f"{first} {last}"

    dob = _random_date(1940, 2005)
    dos = _random_date(2023, 2024)
    followup = dos + timedelta(days=random.randint(7, 30))

    city, state, zip_code = random.choice(CITIES_STATES_ZIPS)
    street_num = random.randint(100, 9999)
    street = random.choice(STREETS)

    provider_name, provider_title = random.choice(PROVIDERS)
    practice_city = random.choice(CITIES_STATES_ZIPS)[0]
    practice = f"{practice_city} {random.choice(PRACTICES)}"

    insurance, ins_prefix = random.choice(INSURANCE_COMPANIES)
    procedures = random.sample(CDT_PROCEDURES, k=random.randint(1, 4))

    tooth = random.choice([2, 3, 4, 5, 12, 13, 14, 15, 18, 19, 20, 21, 28, 29, 30, 31])
    finding = random.choice(CLINICAL_FINDINGS).format(
        tooth=tooth, weeks=random.randint(1, 8), depth=random.randint(3, 6)
    )

    staff_first = random.choice(FIRST_NAMES)
    staff_last = random.choice(LAST_NAMES)
    staff_title = random.choice(STAFF_TITLES)

    note = f"""PATIENT: {full_name}
DOB: {dob.strftime('%m/%d/%Y')}
SSN: {_random_ssn()}
MRN: {_random_mrn()}
Phone: {_random_phone()}
Email: {_random_email(first, last)}
Address: {street_num} {street}, {city}, {state} {zip_code}

PROVIDER: Dr. {provider_name}, {provider_title}
NPI: {_random_npi()}
Practice: {practice}

DATE OF SERVICE: {dos.strftime('%m/%d/%Y')}

CHIEF COMPLAINT: Patient presents with concern regarding tooth #{tooth}.

CLINICAL NOTES:
Patient {first} {last} {finding}

TREATMENT PLAN:
"""
    total_fee = 0
    for code, desc in procedures:
        fee = random.randint(75, 1500)
        total_fee += fee
        note += f"- {code} {desc} (${fee})\n"

    note += f"""
Insurance: {insurance} (Member ID: {_random_member_id(ins_prefix)})
Estimated total: ${total_fee}

Patient agreed to treatment plan. Consent form signed.
Next appointment: {followup.strftime('%m/%d/%Y')} at {random.randint(8,16)}:{random.choice(['00','15','30','45'])} {'AM' if random.random() > 0.5 else 'PM'}

Notes entered by: {staff_first} {staff_last}, {staff_title}
IP: {_random_ip()}
"""
    return {
        "text": note,
        "patient_name": full_name,
        "ssn": note.split("SSN: ")[1].split("\n")[0],
        "email": note.split("Email: ")[1].split("\n")[0],
        "phone": note.split("Phone: ")[1].split("\n")[0],
        "dob": dob.strftime('%m/%d/%Y'),
        "procedures": [p[0] for p in procedures],
    }


def generate_claim_json(patient_id: int = 1) -> dict:
    """Generate a synthetic insurance claim JSON."""
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    dob = _random_date(1940, 2005)
    dos = _random_date(2023, 2024)
    city, state, zip_code = random.choice(CITIES_STATES_ZIPS)
    insurance, ins_prefix = random.choice(INSURANCE_COMPANIES)
    provider_name, provider_title = random.choice(PROVIDERS)
    procedures = random.sample(CDT_PROCEDURES, k=random.randint(1, 3))

    return {
        "claim_id": f"CLM-{dos.year}-{random.randint(100000, 999999):06d}",
        "patient": {
            "first_name": first,
            "last_name": last,
            "dob": dob.isoformat(),
            "ssn": _random_ssn(),
            "member_id": _random_member_id(ins_prefix),
            "phone": _random_phone(),
            "email": _random_email(first, last),
            "address": {
                "street": f"{random.randint(100,9999)} {random.choice(STREETS)}",
                "city": city, "state": state, "zip": zip_code,
            },
        },
        "provider": {
            "name": f"Dr. {provider_name}",
            "npi": _random_npi(),
        },
        "date_of_service": dos.isoformat(),
        "procedures": [
            {"code": code, "description": desc,
             "tooth": random.randint(1, 32),
             "fee": round(random.uniform(75, 1500), 2)}
            for code, desc in procedures
        ],
        "insurance": {
            "payer": insurance,
            "plan_id": f"{ins_prefix}-PLAN-{dos.year}",
            "group_number": f"GRP-{random.randint(10000, 99999)}",
        },
        "total_fee": sum(random.uniform(75, 1500) for _ in procedures),
        "status": random.choice(["submitted", "pending", "approved", "denied"]),
    }


def generate_patient_csv(count: int = 20) -> str:
    """Generate a synthetic patient roster CSV."""
    rows = ["Patient Name,DOB,SSN,Phone,Email,Address,MRN,Insurance Member ID"]
    for _ in range(count):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        dob = _random_date(1940, 2005)
        city, state, zip_code = random.choice(CITIES_STATES_ZIPS)
        _, ins_prefix = random.choice(INSURANCE_COMPANIES)
        rows.append(
            f"{first} {last},"
            f"{dob.strftime('%m/%d/%Y')},"
            f"{_random_ssn()},"
            f"{_random_phone()},"
            f"{_random_email(first, last)},"
            f"\"{random.randint(100,9999)} {random.choice(STREETS)}, {city}, {state} {zip_code}\","
            f"{_random_mrn()},"
            f"{_random_member_id(ins_prefix)}"
        )
    return "\n".join(rows)


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic dental records")
    parser.add_argument("--count", type=int, default=10, help="Number of records")
    parser.add_argument("--output", type=str, default="tests/fixtures/synthetic",
                        help="Output directory")
    args = parser.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    # Generate clinical notes
    for i in range(args.count):
        record = generate_clinical_note(i)
        (out / f"clinical_note_{i+1:03d}.txt").write_text(record["text"])

    # Generate claims
    for i in range(args.count):
        claim = generate_claim_json(i)
        (out / f"claim_{i+1:03d}.json").write_text(
            json.dumps(claim, indent=2)
        )

    # Generate patient roster
    csv_data = generate_patient_csv(args.count * 3)
    (out / "patient_roster.csv").write_text(csv_data)

    print(f"Generated {args.count} clinical notes, {args.count} claims, "
          f"and 1 patient roster ({args.count * 3} patients) in {out}/")


if __name__ == "__main__":
    main()
