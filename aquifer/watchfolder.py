"""Watchfolder daemon — monitors a directory and auto-processes new files.

Designed for dental/medical offices: staff saves files to an inbox folder
(scanner output, downloads, etc.) and Aquifer automatically de-identifies
them, storing output in a clean folder with non-PHI filenames.

Usage:
    aquifer watch /path/to/inbox --output /path/to/clean --vault practice.aqv
"""

import logging
import os
import time
import uuid
from pathlib import Path

from aquifer.core import SUPPORTED_EXTENSIONS
from aquifer.engine.pipeline import process_file
from aquifer.vault.store import TokenVault

logger = logging.getLogger(__name__)


class WatchFolder:
    """Watches a directory for new files and auto-processes them."""

    def __init__(
        self,
        inbox: Path,
        output_dir: Path,
        vault: TokenVault,
        *,
        use_ner: bool = True,
        poll_interval: float = 5.0,
        archive_originals: bool = True,
        archive_dir: Path | None = None,
    ):
        self.inbox = inbox
        self.output_dir = output_dir
        self.vault = vault
        self.use_ner = use_ner
        self.poll_interval = poll_interval
        self.archive_originals = archive_originals
        self.archive_dir = archive_dir or inbox / ".aquifer_originals"
        self._processed: set[str] = set()  # Track processed file hashes
        self._running = False

    def start(self) -> None:
        """Start watching the inbox folder. Blocks until stopped."""
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.archive_originals:
            self.archive_dir.mkdir(parents=True, exist_ok=True)

        self._running = True
        logger.info(f"Watching {self.inbox} for new files (polling every {self.poll_interval}s)")
        logger.info(f"De-identified output → {self.output_dir}")
        if self.archive_originals:
            logger.info(f"Originals archived → {self.archive_dir}")

        while self._running:
            self._scan_and_process()
            time.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False

    def _scan_and_process(self) -> None:
        """Scan inbox for new supported files and process them."""
        for path in sorted(self.inbox.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if path.name.startswith("."):
                continue  # Skip hidden files

            # Skip if we already processed this file (by name + size + mtime)
            stat = path.stat()
            file_key = f"{path.name}:{stat.st_size}:{stat.st_mtime}"
            if file_key in self._processed:
                continue

            self._process_file(path)
            self._processed.add(file_key)

    def _process_file(self, path: Path) -> None:
        """Process a single file: de-identify, save with UUID name, archive original."""
        file_id = str(uuid.uuid4())
        output_path = self.output_dir / f"{file_id}.aqf"

        logger.info(f"Processing: {path.name} → {file_id}.aqf")

        try:
            result = process_file(
                path, output_path, self.vault,
                use_ner=self.use_ner, verbose=False,
            )

            if result.errors:
                logger.error(f"Failed: {path.name} — {result.errors[0]}")
                return

            logger.info(
                f"Done: {path.name} → {file_id}.aqf "
                f"({result.token_count} tokens)"
            )

            # Archive the original (move out of inbox)
            if self.archive_originals:
                archive_path = self.archive_dir / path.name
                # Handle name collisions
                if archive_path.exists():
                    archive_path = self.archive_dir / f"{path.stem}_{file_id[:8]}{path.suffix}"
                path.rename(archive_path)
                logger.debug(f"Archived: {path.name} → {archive_path}")

        except Exception as e:
            logger.error(f"Error processing {path.name}: {e}")
