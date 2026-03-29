"""Ensemble reconciliation: merge detection results from all stages.

Union all detected PHI spans. When spans overlap, keep the one with
the highest confidence. Merge adjacent spans of the same type.
"""

from __future__ import annotations

from aquifer.engine.detectors.patterns import PHIMatch


def reconcile(matches: list[PHIMatch]) -> list[PHIMatch]:
    """Merge and deduplicate PHI matches from multiple detection stages.

    Strategy: Union of all detections (maximize recall).
    For overlapping spans, keep the highest-confidence match.
    """
    if not matches:
        return []

    # Sort by start position, then by confidence (descending)
    sorted_matches = sorted(matches, key=lambda m: (m.start, -m.confidence))

    merged: list[PHIMatch] = []
    current = sorted_matches[0]

    for m in sorted_matches[1:]:
        if m.start < current.end:
            # Overlapping span — keep the one with higher confidence,
            # but extend the span to cover both
            if m.confidence > current.confidence:
                # Take the higher-confidence match but extend span
                current = PHIMatch(
                    start=min(current.start, m.start),
                    end=max(current.end, m.end),
                    phi_type=m.phi_type,
                    text=m.text if m.confidence > current.confidence else current.text,
                    confidence=max(current.confidence, m.confidence),
                    source=m.source if m.confidence > current.confidence else current.source,
                )
            else:
                # Keep current but extend span
                current = PHIMatch(
                    start=min(current.start, m.start),
                    end=max(current.end, m.end),
                    phi_type=current.phi_type,
                    text=current.text,
                    confidence=current.confidence,
                    source=current.source,
                )
        else:
            merged.append(current)
            current = m

    merged.append(current)
    return merged


def filter_by_confidence(matches: list[PHIMatch], threshold: float = 0.0) -> list[PHIMatch]:
    """Filter matches below a confidence threshold."""
    return [m for m in matches if m.confidence >= threshold]


def flag_low_confidence(matches: list[PHIMatch], threshold: float = 0.7) -> list[PHIMatch]:
    """Return matches that are below the confidence threshold for human review."""
    return [m for m in matches if m.confidence < threshold]
