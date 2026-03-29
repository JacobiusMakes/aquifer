"""Rehydration engine: .aqf + vault → original content with PHI restored.

Never persists re-hydrated output to disk by default.
Requires vault password for access control.
"""

from __future__ import annotations

import re
from pathlib import Path

from aquifer.format.reader import read_aqf, AQFFile
from aquifer.vault.store import TokenVault


# Pattern to match AQ tokens in text: [AQ:TYPE:UUID]
_TOKEN_PATTERN = re.compile(r'\[AQ:(\w+):([0-9a-f\-]{36})\]')


def rehydrate(aqf_path: Path, vault: TokenVault) -> str:
    """Rehydrate an .aqf file by replacing tokens with original PHI.

    Args:
        aqf_path: Path to the .aqf file.
        vault: An opened TokenVault instance.

    Returns:
        The rehydrated text with PHI restored.

    Raises:
        ValueError: If tokens cannot be resolved.
    """
    aqf = read_aqf(aqf_path)
    return rehydrate_text(aqf.text_content, vault)


def rehydrate_text(text: str, vault: TokenVault) -> str:
    """Replace all AQ tokens in text with their original PHI values.

    Args:
        text: De-identified text containing [AQ:TYPE:UUID] tokens.
        vault: An opened TokenVault instance.

    Returns:
        Text with tokens replaced by original PHI values.
    """
    def replace_token(match: re.Match) -> str:
        token_id = match.group(2)
        token = vault.get_token(token_id)
        if token is None:
            # Token not found in vault — leave as-is
            return match.group(0)
        return token.phi_value

    return _TOKEN_PATTERN.sub(replace_token, text)


def rehydrate_to_stream(aqf_path: Path, vault: TokenVault):
    """Generator that yields rehydrated content line by line.

    This avoids holding the entire rehydrated document in memory.
    """
    aqf = read_aqf(aqf_path)
    for line in aqf.text_content.split("\n"):
        yield _TOKEN_PATTERN.sub(
            lambda m: (vault.get_token(m.group(2)) or type('', (), {'phi_value': m.group(0)})()).phi_value
            if vault.get_token(m.group(2))
            else m.group(0),
            line,
        )


def rehydrate_to_stream_simple(aqf_path: Path, vault: TokenVault):
    """Simpler streaming rehydration — yields lines."""
    aqf = read_aqf(aqf_path)
    for line in aqf.text_content.split("\n"):
        result_line = line
        for m in _TOKEN_PATTERN.finditer(line):
            token_id = m.group(2)
            token = vault.get_token(token_id)
            if token:
                result_line = result_line.replace(m.group(0), token.phi_value)
        yield result_line
