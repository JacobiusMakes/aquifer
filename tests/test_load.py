"""Load and concurrency tests for the Aquifer vault and pipeline."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest

from aquifer.vault.store import TokenVault


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    v = TokenVault(tmp_path / "load_test.aqv", "load-test-password")
    v.init()
    yield v
    v.close()


# ---------------------------------------------------------------------------
# Concurrent vault access
# ---------------------------------------------------------------------------

class TestConcurrentVaultAccess:
    def test_concurrent_reads_and_writes(self, tmp_path):
        """Five threads simultaneously writing to the same vault must not corrupt it."""
        vault_path = tmp_path / "concurrent.aqv"

        # Seed the vault with a known token before spawning threads
        seed_vault = TokenVault(vault_path, "concurrent-password")
        seed_vault.init()
        seed_vault.store_token(
            token_id="seed-tok",
            phi_type="SSN",
            phi_value="000-00-0000",
            source_file_hash="seed-hash",
        )
        seed_vault.close()

        errors: list[str] = []
        written_ids: list[str] = []
        lock = threading.Lock()

        def worker(thread_index: int) -> None:
            tok_id = f"tok-thread-{thread_index}"
            v = TokenVault(vault_path, "concurrent-password")
            try:
                v.open()
                v.store_token(
                    token_id=tok_id,
                    phi_type="EMAIL",
                    phi_value=f"user{thread_index}@example.com",
                    source_file_hash=f"hash-{thread_index}",
                )
                with lock:
                    written_ids.append(tok_id)
            except Exception as exc:
                with lock:
                    errors.append(f"thread {thread_index}: {exc}")
            finally:
                v.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent vault errors: {errors}"
        assert len(written_ids) == 5

        # Verify all written tokens are readable
        verify = TokenVault(vault_path, "concurrent-password")
        verify.open()
        for tok_id in written_ids:
            tok = verify.get_token(tok_id)
            assert tok is not None, f"Token {tok_id} missing after concurrent writes"
        # Seed token must still be intact
        seed = verify.get_token("seed-tok")
        assert seed is not None
        assert seed.phi_value == "000-00-0000"
        verify.close()

    def test_concurrent_read_does_not_block(self, tmp_path):
        """Multiple threads reading from the vault simultaneously must all succeed."""
        vault_path = tmp_path / "read_concurrent.aqv"

        setup = TokenVault(vault_path, "pass")
        setup.init()
        for i in range(10):
            setup.store_token(
                token_id=f"read-tok-{i}",
                phi_type="DATE",
                phi_value=f"2024-01-{i + 1:02d}",
                source_file_hash="read-hash",
            )
        setup.close()

        errors: list[str] = []
        lock = threading.Lock()

        def reader(thread_index: int) -> None:
            v = TokenVault(vault_path, "pass")
            try:
                v.open()
                tok = v.get_token(f"read-tok-{thread_index}")
                assert tok is not None
            except Exception as exc:
                with lock:
                    errors.append(str(exc))
            finally:
                v.close()

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent read errors: {errors}"


# ---------------------------------------------------------------------------
# Batch pipeline processing
# ---------------------------------------------------------------------------

class TestBatchProcessing:
    def test_process_20_synthetic_files_in_sequence(self, tmp_path):
        """Process 20 synthetic text files sequentially; all should succeed."""
        from aquifer.engine.pipeline import process_file

        vault_path = tmp_path / "batch.aqv"
        v = TokenVault(vault_path, "batch-password")
        v.init()

        results = []
        for i in range(20):
            content = (
                f"Patient: John Doe {i}\n"
                f"SSN: {100 + i:03d}-{20 + i:02d}-{3000 + i:04d}\n"
                f"Email: patient{i}@clinic.com\n"
                f"DOB: 01/{i + 1:02d}/1985\n"
                f"Procedure: D{1000 + i}\n"
            )
            input_file = tmp_path / f"note_{i}.txt"
            input_file.write_text(content)
            output_file = tmp_path / f"note_{i}.aqf"

            result = process_file(input_file, output_file, v, use_ner=False)
            results.append(result)

        v.close()

        succeeded = [r for r in results if not r.errors]
        failed = [r for r in results if r.errors]

        assert len(succeeded) == 20, (
            f"{len(failed)} file(s) failed: "
            + "; ".join(f"{r.source_path}: {r.errors}" for r in failed)
        )


# ---------------------------------------------------------------------------
# Vault performance with many tokens
# ---------------------------------------------------------------------------

class TestVaultPerformance:
    def test_vault_with_1000_tokens_performs_basic_ops_quickly(self, tmp_path):
        """A vault holding 1000+ tokens should complete read/write in under 1 second."""
        vault_path = tmp_path / "perf.aqv"
        v = TokenVault(vault_path, "perf-password")
        v.init()

        # Insert 1000 tokens in batches of 100
        for batch_start in range(0, 1000, 100):
            batch = [
                (
                    f"perf-tok-{batch_start + j}",
                    "SSN",
                    f"{batch_start + j:03d}-{j:02d}-{j * 7:04d}",
                    f"hash-{batch_start}",
                    None,
                    1.0,
                )
                for j in range(100)
            ]
            v.store_tokens_batch(batch)

        stats = v.get_stats()
        assert stats["total_tokens"] == 1000

        # Time a get_stats call (aggregation over all tokens)
        start = time.monotonic()
        stats2 = v.get_stats()
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"get_stats took {elapsed:.3f}s on 1000 tokens"
        assert stats2["total_tokens"] == 1000

        # Time a point lookup
        start = time.monotonic()
        tok = v.get_token("perf-tok-500")
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"get_token took {elapsed:.3f}s"
        assert tok is not None

        v.close()

    def test_store_tokens_batch_faster_than_individual(self, tmp_path):
        """store_tokens_batch must be faster than 200 individual store_token calls."""
        vault_path = tmp_path / "speed.aqv"
        v = TokenVault(vault_path, "speed-password")
        v.init()

        tokens = [
            (f"batch-{i}", "EMAIL", f"user{i}@example.com", "speed-hash", None, 1.0)
            for i in range(200)
        ]

        start = time.monotonic()
        v.store_tokens_batch(tokens)
        batch_time = time.monotonic() - start

        # Batch insert of 200 tokens should finish in under 1 second
        assert batch_time < 1.0, f"store_tokens_batch took {batch_time:.3f}s for 200 tokens"

        v.close()
