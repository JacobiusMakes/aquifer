"""Aquifer configuration file support.

Looks for aquifer.toml in the current directory or home directory.

Example aquifer.toml:
    [vault]
    path = "./my_vault.aqv"

    [output]
    directory = "./deidentified"

    [detection]
    use_ner = true
    confidence_threshold = 0.5

    [dashboard]
    host = "127.0.0.1"
    port = 8080
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class AquiferConfig:
    vault_path: Optional[str] = None
    output_directory: Optional[str] = None
    use_ner: bool = True
    confidence_threshold: float = 0.5
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080


def load_config(config_path: Path | None = None) -> AquiferConfig:
    """Load configuration from aquifer.toml.

    Search order:
    1. Explicit config_path argument
    2. ./aquifer.toml (current directory)
    3. ~/.aquifer.toml (home directory)
    4. Default values
    """
    search_paths = []
    if config_path:
        search_paths.append(config_path)
    search_paths.extend([
        Path("aquifer.toml"),
        Path.home() / ".aquifer.toml",
        Path.home() / ".config" / "aquifer" / "config.toml",
    ])

    for p in search_paths:
        if p.exists():
            return _parse_config(p)

    return AquiferConfig()


def _parse_config(path: Path) -> AquiferConfig:
    """Parse an aquifer.toml file."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    config = AquiferConfig()

    vault = data.get("vault", {})
    if "path" in vault:
        config.vault_path = vault["path"]

    output = data.get("output", {})
    if "directory" in output:
        config.output_directory = output["directory"]

    detection = data.get("detection", {})
    if "use_ner" in detection:
        config.use_ner = detection["use_ner"]
    if "confidence_threshold" in detection:
        config.confidence_threshold = detection["confidence_threshold"]

    dashboard = data.get("dashboard", {})
    if "host" in dashboard:
        config.dashboard_host = dashboard["host"]
    if "port" in dashboard:
        config.dashboard_port = dashboard["port"]

    return config
