"""Aquifer hosted API client.

Connects the local CLI to the Aquifer cloud service for:
- Denial prediction (requires claims data corpus)
- Appeal generation (requires successful appeal corpus)
- Cross-practice analytics (requires aggregated data)
- Claim status tracking

All data sent to the API is de-identified — only AQ tokens, CDT codes,
payer IDs, and amounts. Zero PHI crosses the wire.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


API_BASE = "https://api.aquifer.health/v1"


@dataclass
class APIConfig:
    api_key: str
    base_url: str = API_BASE
    timeout: int = 30


@dataclass
class PredictionResult:
    risk_score: float
    risk_level: str
    risk_factors: list[str]
    recommended_actions: list[str]
    historical_denial_rate: float


@dataclass
class AppealDraft:
    appeal_text: str
    confidence: float
    similar_appeal_count: int
    estimated_success_rate: float
    template_id: str


@dataclass
class ClaimStatusResult:
    claim_number: str
    status: str
    last_updated: str
    payer_response: Optional[str] = None
    payment_amount: Optional[float] = None
    denial_reason: Optional[str] = None


class AquiferAPI:
    """Client for the Aquifer hosted claims intelligence API.

    All methods send only de-identified data. The API never receives
    patient names, SSNs, or any other PHI — only AQ token references,
    CDT codes, payer identifiers, and financial amounts.
    """

    def __init__(self, config: APIConfig):
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import httpx
                self._client = httpx.Client(
                    base_url=self.config.base_url,
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "aquifer-cli/0.1.0",
                    },
                    timeout=self.config.timeout,
                )
            except ImportError:
                raise ImportError("httpx required for API access: pip install httpx")
        return self._client

    def predict_denial(
        self,
        cdt_codes: list[str],
        payer_id: str,
        doc_completeness_score: float = 1.0,
        charge_amount: float | None = None,
    ) -> PredictionResult:
        """Predict denial risk for a claim before submission.

        Sends only: CDT codes, payer ID, doc score, charge amount.
        No PHI is transmitted.
        """
        client = self._get_client()
        resp = client.post("/claims/predict", json={
            "cdt_codes": cdt_codes,
            "payer_id": payer_id,
            "doc_completeness_score": doc_completeness_score,
            "charge_amount": charge_amount,
        })
        resp.raise_for_status()
        data = resp.json()
        return PredictionResult(
            risk_score=data["risk_score"],
            risk_level=data["risk_level"],
            risk_factors=data["risk_factors"],
            recommended_actions=data["recommended_actions"],
            historical_denial_rate=data["historical_denial_rate"],
        )

    def generate_appeal(
        self,
        carc_code: str,
        cdt_code: str,
        payer_id: str,
        denial_description: str,
        denied_amount: float,
    ) -> AppealDraft:
        """Generate an appeal letter draft for a denial.

        Sends only: denial reason codes, CDT code, payer ID, amount.
        No PHI is transmitted.
        """
        client = self._get_client()
        resp = client.post("/claims/appeal", json={
            "carc_code": carc_code,
            "cdt_code": cdt_code,
            "payer_id": payer_id,
            "denial_description": denial_description,
            "denied_amount": denied_amount,
        })
        resp.raise_for_status()
        data = resp.json()
        return AppealDraft(
            appeal_text=data["appeal_text"],
            confidence=data["confidence"],
            similar_appeal_count=data["similar_appeal_count"],
            estimated_success_rate=data["estimated_success_rate"],
            template_id=data["template_id"],
        )

    def track_claim(
        self,
        claim_number: str,
        payer_id: str,
        cdt_codes: list[str],
        date_of_service: str,
        charge_amount: float,
        patient_token: str,  # AQ token, NOT a real name
    ) -> ClaimStatusResult:
        """Submit a claim for tracking.

        The patient_token is an AQ de-identification token, NOT real PHI.
        """
        client = self._get_client()
        resp = client.post("/claims/track", json={
            "claim_number": claim_number,
            "payer_id": payer_id,
            "cdt_codes": cdt_codes,
            "date_of_service": date_of_service,
            "charge_amount": charge_amount,
            "patient_token": patient_token,
        })
        resp.raise_for_status()
        data = resp.json()
        return ClaimStatusResult(
            claim_number=data["claim_number"],
            status=data["status"],
            last_updated=data["last_updated"],
            payer_response=data.get("payer_response"),
            payment_amount=data.get("payment_amount"),
            denial_reason=data.get("denial_reason"),
        )

    def get_claim_status(self, claim_number: str) -> ClaimStatusResult:
        """Check current status of a tracked claim."""
        client = self._get_client()
        resp = client.get(f"/claims/{claim_number}/status")
        resp.raise_for_status()
        data = resp.json()
        return ClaimStatusResult(
            claim_number=data["claim_number"],
            status=data["status"],
            last_updated=data["last_updated"],
            payer_response=data.get("payer_response"),
            payment_amount=data.get("payment_amount"),
            denial_reason=data.get("denial_reason"),
        )

    def get_payer_analytics(self, payer_id: str) -> dict:
        """Get denial analytics for a specific payer."""
        client = self._get_client()
        resp = client.get(f"/analytics/payer/{payer_id}")
        resp.raise_for_status()
        return resp.json()

    def get_code_analytics(self, cdt_code: str, payer_id: str | None = None) -> dict:
        """Get denial analytics for a CDT code (optionally filtered by payer)."""
        client = self._get_client()
        params = {}
        if payer_id:
            params["payer_id"] = payer_id
        resp = client.get(f"/analytics/code/{cdt_code}", params=params)
        resp.raise_for_status()
        return resp.json()

    def report_outcome(
        self,
        claim_number: str,
        outcome: str,  # approved, denied, partial
        paid_amount: float | None = None,
        denial_carc: str | None = None,
        appeal_text: str | None = None,
        appeal_outcome: str | None = None,
    ) -> dict:
        """Report a claim outcome back to the corpus (feeds the flywheel).

        Every outcome reported makes the prediction model more accurate
        for all practices on the network.
        """
        client = self._get_client()
        resp = client.post(f"/claims/{claim_number}/outcome", json={
            "outcome": outcome,
            "paid_amount": paid_amount,
            "denial_carc": denial_carc,
            "appeal_text": appeal_text,
            "appeal_outcome": appeal_outcome,
        })
        resp.raise_for_status()
        return resp.json()

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
