"""Tests for the watchfolder daemon.

Tests init, scan logic, file processing, and archiving without
actually running the blocking start() loop.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aquifer.watchfolder import WatchFolder


@pytest.fixture
def watch_dirs(tmp_path):
    inbox = tmp_path / "inbox"
    output = tmp_path / "output"
    inbox.mkdir()
    output.mkdir()
    return inbox, output


@pytest.fixture
def mock_vault():
    return MagicMock()


@pytest.fixture
def watcher(watch_dirs, mock_vault):
    inbox, output = watch_dirs
    return WatchFolder(
        inbox=inbox,
        output_dir=output,
        vault=mock_vault,
        poll_interval=0.1,
    )


class TestWatchFolderInit:
    def test_creates_with_defaults(self, watch_dirs, mock_vault):
        inbox, output = watch_dirs
        wf = WatchFolder(inbox, output, mock_vault)
        assert wf.inbox == inbox
        assert wf.output_dir == output
        assert wf.poll_interval == 5.0
        assert wf.archive_originals is True
        assert wf.archive_dir == inbox / ".aquifer_originals"
        assert wf._running is False

    def test_custom_archive_dir(self, watch_dirs, mock_vault, tmp_path):
        inbox, output = watch_dirs
        custom_archive = tmp_path / "custom_archive"
        wf = WatchFolder(inbox, output, mock_vault, archive_dir=custom_archive)
        assert wf.archive_dir == custom_archive

    def test_no_archive(self, watch_dirs, mock_vault):
        inbox, output = watch_dirs
        wf = WatchFolder(inbox, output, mock_vault, archive_originals=False)
        assert wf.archive_originals is False


class TestScanAndProcess:
    def test_skips_unsupported_extensions(self, watcher, watch_dirs):
        inbox, _ = watch_dirs
        (inbox / "readme.md").write_text("not a medical file")
        (inbox / "script.py").write_text("print('hello')")

        with patch.object(watcher, '_process_file') as mock_process:
            watcher._scan_and_process()
            mock_process.assert_not_called()

    def test_skips_hidden_files(self, watcher, watch_dirs):
        inbox, _ = watch_dirs
        (inbox / ".hidden.txt").write_text("hidden file")

        with patch.object(watcher, '_process_file') as mock_process:
            watcher._scan_and_process()
            mock_process.assert_not_called()

    def test_skips_directories(self, watcher, watch_dirs):
        inbox, _ = watch_dirs
        (inbox / "subdir").mkdir()

        with patch.object(watcher, '_process_file') as mock_process:
            watcher._scan_and_process()
            mock_process.assert_not_called()

    def test_processes_supported_files(self, watcher, watch_dirs):
        inbox, _ = watch_dirs
        (inbox / "intake.txt").write_text("Patient Name: John Doe")
        (inbox / "form.pdf").write_bytes(b"%PDF-1.4 fake")

        with patch.object(watcher, '_process_file') as mock_process:
            watcher._scan_and_process()
            assert mock_process.call_count == 2

    def test_deduplicates_already_processed(self, watcher, watch_dirs):
        inbox, _ = watch_dirs
        f = inbox / "intake.txt"
        f.write_text("test content")

        with patch.object(watcher, '_process_file') as mock_process:
            watcher._scan_and_process()
            watcher._scan_and_process()  # Second scan
            assert mock_process.call_count == 1  # Only processed once


class TestProcessFile:
    @patch("aquifer.watchfolder.process_file")
    def test_process_file_success(self, mock_pipeline, watcher, watch_dirs):
        inbox, output = watch_dirs
        f = inbox / "intake.txt"
        f.write_text("Patient Name: Jane Doe, DOB: 01/01/1980")

        mock_result = MagicMock()
        mock_result.errors = []
        mock_result.token_count = 3
        mock_pipeline.return_value = mock_result

        watcher._process_file(f)

        mock_pipeline.assert_called_once()
        call_args = mock_pipeline.call_args
        assert call_args[0][0] == f  # input path
        assert str(call_args[0][1]).endswith(".aqf")  # output path

    @patch("aquifer.watchfolder.process_file")
    def test_process_file_archives_original(self, mock_pipeline, watcher, watch_dirs):
        inbox, output = watch_dirs
        f = inbox / "intake.txt"
        f.write_text("test")

        # Need archive dir to exist
        watcher.archive_dir.mkdir(parents=True, exist_ok=True)

        mock_result = MagicMock()
        mock_result.errors = []
        mock_result.token_count = 1
        mock_pipeline.return_value = mock_result

        watcher._process_file(f)

        # Original should be moved to archive
        assert not f.exists()
        assert (watcher.archive_dir / "intake.txt").exists()

    @patch("aquifer.watchfolder.process_file")
    def test_process_file_handles_errors(self, mock_pipeline, watcher, watch_dirs):
        inbox, _ = watch_dirs
        f = inbox / "bad.txt"
        f.write_text("broken content")

        mock_result = MagicMock()
        mock_result.errors = ["extraction failed"]
        mock_pipeline.return_value = mock_result

        # Should not raise
        watcher._process_file(f)
        # Original should NOT be archived on error
        assert f.exists()

    @patch("aquifer.watchfolder.process_file")
    def test_process_file_handles_exception(self, mock_pipeline, watcher, watch_dirs):
        inbox, _ = watch_dirs
        f = inbox / "crash.txt"
        f.write_text("will crash")

        mock_pipeline.side_effect = RuntimeError("pipeline exploded")

        # Should not raise
        watcher._process_file(f)


class TestStartStop:
    def test_stop_sets_flag(self, watcher):
        watcher._running = True
        watcher.stop()
        assert watcher._running is False
