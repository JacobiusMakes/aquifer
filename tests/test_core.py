"""Tests for core constants, data domains, and practice type defaults."""

from __future__ import annotations

import pytest

from aquifer.core import (
    SUPPORTED_EXTENSIONS,
    FILE_TYPE_MAP,
    DataDomain,
    PRACTICE_TYPE_DEFAULTS,
    AquiferError,
    ExtractionError,
    DetectionError,
    VaultError,
    FormatError,
)


class TestSupportedExtensions:
    def test_includes_pdf(self):
        assert ".pdf" in SUPPORTED_EXTENSIONS

    def test_includes_docx(self):
        assert ".docx" in SUPPORTED_EXTENSIONS

    def test_includes_images(self):
        for ext in [".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"]:
            assert ext in SUPPORTED_EXTENSIONS

    def test_includes_structured(self):
        for ext in [".csv", ".json", ".xml"]:
            assert ext in SUPPORTED_EXTENSIONS

    def test_no_executable_types(self):
        for ext in [".exe", ".sh", ".bat", ".py", ".js"]:
            assert ext not in SUPPORTED_EXTENSIONS


class TestFileTypeMap:
    def test_maps_all_extensions(self):
        for ext in SUPPORTED_EXTENSIONS:
            assert ext in FILE_TYPE_MAP, f"{ext} not in FILE_TYPE_MAP"

    def test_image_types(self):
        for ext in [".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"]:
            assert FILE_TYPE_MAP[ext] == "image"

    def test_doc_aliases(self):
        assert FILE_TYPE_MAP[".doc"] == "docx"
        assert FILE_TYPE_MAP[".docx"] == "docx"


class TestDataDomain:
    def test_all_domains_have_values(self):
        for domain in DataDomain:
            assert isinstance(domain.value, str)
            assert len(domain.value) > 0

    def test_universal_domains(self):
        assert DataDomain.DEMOGRAPHICS.value == "demographics"
        assert DataDomain.INSURANCE.value == "insurance"
        assert DataDomain.MEDICATIONS.value == "medications"
        assert DataDomain.ALLERGIES.value == "allergies"

    def test_specialty_domains(self):
        assert DataDomain.DENTAL.value == "dental"
        assert DataDomain.VISION.value == "vision"
        assert DataDomain.BEHAVIORAL.value == "behavioral"
        assert DataDomain.SURGICAL.value == "surgical"


class TestPracticeTypeDefaults:
    def test_all_types_include_demographics(self):
        for ptype, domains in PRACTICE_TYPE_DEFAULTS.items():
            assert DataDomain.DEMOGRAPHICS in domains, f"{ptype} missing demographics"

    def test_all_types_include_insurance(self):
        for ptype, domains in PRACTICE_TYPE_DEFAULTS.items():
            assert DataDomain.INSURANCE in domains, f"{ptype} missing insurance"

    def test_all_types_include_medications(self):
        for ptype, domains in PRACTICE_TYPE_DEFAULTS.items():
            assert DataDomain.MEDICATIONS in domains, f"{ptype} missing medications"

    def test_all_types_include_allergies(self):
        for ptype, domains in PRACTICE_TYPE_DEFAULTS.items():
            assert DataDomain.ALLERGIES in domains, f"{ptype} missing allergies"

    def test_dental_includes_dental(self):
        assert DataDomain.DENTAL in PRACTICE_TYPE_DEFAULTS["dental"]

    def test_oral_surgery_includes_surgical(self):
        assert DataDomain.SURGICAL in PRACTICE_TYPE_DEFAULTS["oral_surgery"]

    def test_specialist_includes_referrals(self):
        assert DataDomain.REFERRALS in PRACTICE_TYPE_DEFAULTS["specialist"]

    def test_known_practice_types(self):
        expected = {"dental", "medical", "oral_surgery", "orthodontics", "specialist"}
        assert set(PRACTICE_TYPE_DEFAULTS.keys()) == expected


class TestExceptionHierarchy:
    def test_all_exceptions_inherit_from_aquifer_error(self):
        for exc_class in [ExtractionError, DetectionError, VaultError, FormatError]:
            assert issubclass(exc_class, AquiferError)

    def test_aquifer_error_is_exception(self):
        assert issubclass(AquiferError, Exception)

    def test_raise_and_catch(self):
        with pytest.raises(AquiferError):
            raise VaultError("test")
