"""Aquifer CLI — Click-based command-line interface."""

from __future__ import annotations

import getpass
import logging
import sys
from pathlib import Path

import click


@click.group()
@click.version_option(package_name="aquifer", prog_name="aquifer")
def cli():
    """Aquifer — HIPAA De-Identification Engine."""
    pass


@cli.command()
@click.argument("input_path", type=click.Path(exists=True))
@click.option("-o", "--output", "output_path", type=click.Path(), default=None,
              help="Output .aqf file path (default: <input>.aqf)")
@click.option("--vault", "vault_path", type=click.Path(), required=True,
              help="Path to vault database file (.aqv)")
@click.option("--password", "password", default=None,
              help="Vault password (will prompt if not given)")
@click.option("--verbose", is_flag=True, help="Show detailed detection output")
@click.option("--no-ner", is_flag=True, help="Disable NER detection (faster, regex only)")
def deid(input_path: str, output_path: str | None, vault_path: str,
         password: str | None, verbose: bool, no_ner: bool):
    """De-identify a file or directory of files."""
    from aquifer.engine.pipeline import process_file
    from aquifer.vault.store import TokenVault

    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    input_p = Path(input_path)
    vault_p = Path(vault_path)

    if password is None:
        password = getpass.getpass("Vault password: ")

    vault = TokenVault(vault_p, password)
    if not vault_p.exists():
        click.echo(f"Initializing new vault at {vault_p}")
        vault.init()
    else:
        vault.open()

    try:
        if input_p.is_dir():
            _process_directory(input_p, output_path, vault, not no_ner, verbose)
        else:
            if output_path is None:
                output_path = str(input_p.with_suffix(".aqf"))
            result = process_file(
                input_p, Path(output_path), vault,
                use_ner=not no_ner, verbose=verbose,
            )
            _print_result(result, verbose)
    finally:
        vault.close()


def _process_directory(input_dir: Path, output_dir: str | None,
                       vault, use_ner: bool, verbose: bool):
    from aquifer.engine.pipeline import process_file

    out_dir = Path(output_dir) if output_dir else input_dir / "deidentified"
    out_dir.mkdir(parents=True, exist_ok=True)

    supported = {".pdf", ".docx", ".doc", ".txt", ".csv", ".json", ".xml",
                 ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"}
    files = [f for f in input_dir.iterdir()
             if f.is_file() and f.suffix.lower() in supported
             and not f.name.startswith('.')]

    if not files:
        click.echo("No supported files found")
        return

    total_tokens = 0
    errors = 0
    with click.progressbar(files, label=f"Processing {len(files)} files",
                           show_pos=True) as bar:
        for f in bar:
            out = out_dir / f.with_suffix(".aqf").name
            result = process_file(f, out, vault, use_ner=use_ner, verbose=verbose)
            total_tokens += result.token_count
            if result.errors:
                errors += 1
                click.echo(f"\n  ERROR: {f.name}: {result.errors[0]}")

    click.echo(f"\nDone: {len(files)} files, {total_tokens} tokens, {errors} errors")


def _print_result(result, verbose):
    if result.errors:
        click.echo(f"Error: {result.errors[0]}", err=True)
        sys.exit(1)

    click.echo(f"De-identified: {result.source_path}")
    click.echo(f"  Output: {result.aqf_path}")
    click.echo(f"  Tokens: {result.token_count}")
    click.echo(f"  Source hash: {result.source_hash[:16]}...")
    click.echo(f"  AQF hash: {result.aqf_hash[:16]}...")

    if verbose and result.detections:
        click.echo(f"\n  Detections ({len(result.detections)}):")
        for d in result.detections:
            click.echo(f"    [{d.phi_type.value}] \"{d.text}\" "
                        f"(confidence={d.confidence:.2f}, source={d.source})")

    if result.low_confidence:
        click.echo(f"\n  Low-confidence detections for review ({len(result.low_confidence)}):")
        for d in result.low_confidence:
            click.echo(f"    [{d.phi_type.value}] \"{d.text}\" "
                        f"(confidence={d.confidence:.2f})")


@cli.command()
@click.argument("aqf_path", type=click.Path(exists=True))
@click.option("--vault", "vault_path", type=click.Path(exists=True), required=True,
              help="Path to vault database file")
@click.option("--password", "password", default=None,
              help="Vault password (will prompt if not given)")
def rehydrate(aqf_path: str, vault_path: str, password: str | None):
    """Rehydrate an .aqf file (restore PHI from vault)."""
    from aquifer.rehydrate.engine import rehydrate as do_rehydrate
    from aquifer.vault.store import TokenVault

    if password is None:
        password = getpass.getpass("Vault password: ")

    vault = TokenVault(Path(vault_path), password)
    vault.open()

    try:
        text = do_rehydrate(Path(aqf_path), vault)
        click.echo(text)
    finally:
        vault.close()


@cli.command()
@click.argument("aqf_path", type=click.Path(exists=True))
def inspect(aqf_path: str):
    """Inspect an .aqf file (show metadata, no PHI)."""
    from aquifer.format.reader import read_aqf, verify_integrity

    aqf = read_aqf(Path(aqf_path))
    valid, errors = verify_integrity(Path(aqf_path))

    click.echo(f"AQF File: {aqf_path}")
    click.echo(f"  Version: {aqf.manifest.version}")
    click.echo(f"  Source type: {aqf.manifest.source_type}")
    click.echo(f"  Source hash: {aqf.manifest.source_hash[:16]}...")
    click.echo(f"  Created: {aqf.manifest.creation_time}")
    click.echo(f"  Tokens: {aqf.token_count}")
    click.echo(f"  Compression: {aqf.manifest.compression}")
    click.echo(f"  Integrity: {'VALID' if valid else 'INVALID'}")
    if errors:
        for e in errors:
            click.echo(f"    Error: {e}")

    click.echo(f"\n  Token types:")
    type_counts: dict[str, int] = {}
    for t in aqf.tokens:
        type_counts[t.phi_type] = type_counts.get(t.phi_type, 0) + 1
    for phi_type, count in sorted(type_counts.items()):
        click.echo(f"    {phi_type}: {count}")


@cli.group()
def vault():
    """Vault management commands."""
    pass


@vault.command("init")
@click.argument("vault_path", type=click.Path())
@click.option("--password", "password", default=None,
              help="Vault password (will prompt if not given)")
def vault_init(vault_path: str, password: str | None):
    """Initialize a new token vault."""
    from aquifer.vault.store import TokenVault

    vp = Path(vault_path)
    if vp.exists():
        click.echo(f"Vault already exists at {vault_path}", err=True)
        sys.exit(1)

    if password is None:
        password = getpass.getpass("New vault password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            click.echo("Passwords do not match", err=True)
            sys.exit(1)

    v = TokenVault(vp, password)
    v.init()
    v.close()
    click.echo(f"Vault initialized at {vault_path}")


@vault.command("stats")
@click.argument("vault_path", type=click.Path(exists=True))
@click.option("--password", "password", default=None,
              help="Vault password (will prompt if not given)")
def vault_stats(vault_path: str, password: str | None):
    """Show vault statistics."""
    from aquifer.vault.store import TokenVault

    if password is None:
        password = getpass.getpass("Vault password: ")

    v = TokenVault(Path(vault_path), password)
    v.open()
    try:
        stats = v.get_stats()
        click.echo(f"Vault: {vault_path}")
        click.echo(f"  Total tokens: {stats['total_tokens']}")
        click.echo(f"  Total files: {stats['total_files']}")
        if stats['tokens_by_type']:
            click.echo(f"  Tokens by type:")
            for t, c in sorted(stats['tokens_by_type'].items()):
                click.echo(f"    {t}: {c}")
    finally:
        v.close()


# ---------------------------------------------------------------------------
# Vault sync commands
# ---------------------------------------------------------------------------

_DEFAULT_SERVER_URL = "https://api.aquifer.health"


def _get_server_url(server: str | None) -> str:
    """Resolve server URL from argument, env var, or default."""
    import os
    if server:
        return server
    return os.environ.get("AQUIFER_SERVER_URL", _DEFAULT_SERVER_URL)


def _sync_progress(step: str, current: int, total: int) -> None:
    """Print sync progress to stderr."""
    messages = {
        "generating_manifest": "Generating token manifest...",
        "computing_diff": f"Computing diff ({total} local tokens)...",
        "pushing": f"Pushing tokens... ({current}/{total})",
        "push_complete": f"Push complete: {current} tokens pushed.",
        "pulling": f"Pulling tokens... ({current}/{total})",
        "pull_complete": f"Pull complete: {current} tokens pulled.",
        "sync_complete": f"Sync complete: {current} tokens transferred.",
    }
    msg = messages.get(step, f"{step}: {current}/{total}")
    click.echo(f"  {msg}")


@vault.command("sync")
@click.argument("vault_path", type=click.Path(exists=True))
@click.option("--password", "password", default=None,
              help="Vault password (will prompt if not given)")
@click.option("--api-key", envvar="AQUIFER_API_KEY", required=True,
              help="API key for Strata server (or set AQUIFER_API_KEY)")
@click.option("--server", "server_url", envvar="AQUIFER_SERVER_URL", default=None,
              help="Strata server URL (or set AQUIFER_SERVER_URL)")
def vault_sync(vault_path: str, password: str | None, api_key: str, server_url: str | None):
    """Bidirectional sync between local vault and cloud."""
    from aquifer.vault.store import TokenVault
    from aquifer.vault.sync_client import VaultSyncClient

    if password is None:
        password = getpass.getpass("Vault password: ")

    url = _get_server_url(server_url)
    v = TokenVault(Path(vault_path), password)
    v.open()

    try:
        click.echo(f"Syncing vault with {url}...")
        client = VaultSyncClient(url, api_key)
        result = client.sync(v, progress=_sync_progress)

        if result.status == "completed":
            click.echo(f"\nSync completed successfully:")
            click.echo(f"  Pushed: {result.pushed} tokens")
            click.echo(f"  Pulled: {result.pulled} tokens")
            if result.conflicts:
                click.echo(f"  Conflicts resolved: {result.conflicts} (last-write-wins)")
        else:
            click.echo(f"\nSync failed: {result.error}", err=True)
            sys.exit(1)
    finally:
        v.close()


@vault.command("push")
@click.argument("vault_path", type=click.Path(exists=True))
@click.option("--password", "password", default=None,
              help="Vault password (will prompt if not given)")
@click.option("--api-key", envvar="AQUIFER_API_KEY", required=True,
              help="API key for Strata server (or set AQUIFER_API_KEY)")
@click.option("--server", "server_url", envvar="AQUIFER_SERVER_URL", default=None,
              help="Strata server URL (or set AQUIFER_SERVER_URL)")
def vault_push(vault_path: str, password: str | None, api_key: str, server_url: str | None):
    """Push local vault tokens to cloud."""
    from aquifer.vault.store import TokenVault
    from aquifer.vault.sync_client import VaultSyncClient

    if password is None:
        password = getpass.getpass("Vault password: ")

    url = _get_server_url(server_url)
    v = TokenVault(Path(vault_path), password)
    v.open()

    try:
        click.echo(f"Pushing to {url}...")
        client = VaultSyncClient(url, api_key)
        result = client.push(v, progress=_sync_progress)

        if result.status == "completed":
            click.echo(f"\nPush completed: {result.pushed} tokens pushed.")
            if result.conflicts:
                click.echo(f"  Conflicts: {result.conflicts} (resolved by last-write-wins)")
        else:
            click.echo(f"\nPush failed: {result.error}", err=True)
            sys.exit(1)
    finally:
        v.close()


@vault.command("pull")
@click.argument("vault_path", type=click.Path(exists=True))
@click.option("--password", "password", default=None,
              help="Vault password (will prompt if not given)")
@click.option("--api-key", envvar="AQUIFER_API_KEY", required=True,
              help="API key for Strata server (or set AQUIFER_API_KEY)")
@click.option("--server", "server_url", envvar="AQUIFER_SERVER_URL", default=None,
              help="Strata server URL (or set AQUIFER_SERVER_URL)")
def vault_pull(vault_path: str, password: str | None, api_key: str, server_url: str | None):
    """Pull cloud vault tokens to local."""
    from aquifer.vault.store import TokenVault
    from aquifer.vault.sync_client import VaultSyncClient

    if password is None:
        password = getpass.getpass("Vault password: ")

    url = _get_server_url(server_url)
    v = TokenVault(Path(vault_path), password)
    v.open()

    try:
        click.echo(f"Pulling from {url}...")
        client = VaultSyncClient(url, api_key)
        result = client.pull(v, progress=_sync_progress)

        if result.status == "completed":
            click.echo(f"\nPull completed: {result.pulled} tokens pulled.")
            if result.conflicts:
                click.echo(f"  Conflicts: {result.conflicts} (resolved by last-write-wins)")
        else:
            click.echo(f"\nPull failed: {result.error}", err=True)
            sys.exit(1)
    finally:
        v.close()


@vault.command("sync-status")
@click.argument("vault_path", type=click.Path(exists=True))
@click.option("--password", "password", default=None,
              help="Vault password (will prompt if not given)")
@click.option("--api-key", envvar="AQUIFER_API_KEY", required=True,
              help="API key for Strata server (or set AQUIFER_API_KEY)")
@click.option("--server", "server_url", envvar="AQUIFER_SERVER_URL", default=None,
              help="Strata server URL (or set AQUIFER_SERVER_URL)")
def vault_sync_status(vault_path: str, password: str | None, api_key: str, server_url: str | None):
    """Show sync status (local + cloud)."""
    from aquifer.vault.store import TokenVault
    from aquifer.vault.sync_client import VaultSyncClient

    if password is None:
        password = getpass.getpass("Vault password: ")

    url = _get_server_url(server_url)
    v = TokenVault(Path(vault_path), password)
    v.open()

    try:
        v.ensure_sync_schema()

        # Local sync history
        local_stats = v.get_stats()
        last_sync = v.get_last_sync(url)
        sync_history = v.get_sync_history(limit=5)

        click.echo(f"Local vault: {vault_path}")
        click.echo(f"  Tokens: {local_stats['total_tokens']}")
        click.echo(f"  Files: {local_stats['total_files']}")

        if last_sync:
            click.echo(f"\nLast sync with {url}:")
            click.echo(f"  Direction: {last_sync['direction']}")
            click.echo(f"  Tokens: {last_sync['token_count']}")
            click.echo(f"  Conflicts: {last_sync['conflict_count']}")
            click.echo(f"  Status: {last_sync['status']}")
            click.echo(f"  Completed: {last_sync['completed_at']}")
        else:
            click.echo(f"\nNo sync history with {url}")

        if sync_history:
            click.echo(f"\nRecent sync log:")
            for entry in sync_history:
                click.echo(
                    f"  [{entry['completed_at'] or entry['started_at']}] "
                    f"{entry['direction']}: {entry['token_count']} tokens, "
                    f"{entry['status']}"
                )

        # Server-side status
        try:
            client = VaultSyncClient(url, api_key)
            server_status = client.get_status()
            click.echo(f"\nCloud vault ({url}):")
            click.echo(f"  Tokens: {server_status['total_tokens']}")
            click.echo(f"  Files: {server_status['total_files']}")
            if server_status.get("last_sync"):
                ls = server_status["last_sync"]
                click.echo(f"  Last sync: {ls['direction']} ({ls['token_count']} tokens, "
                           f"{ls['completed_at']})")
        except Exception as e:
            click.echo(f"\nCould not reach server: {e}")

    finally:
        v.close()


@cli.command()
@click.option("--vault", "vault_path", type=click.Path(exists=True), required=True,
              help="Path to vault database file")
@click.option("--password", "password", default=None,
              help="Vault password (will prompt if not given)")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8080, type=int, help="Port to listen on")
def dashboard(vault_path: str, password: str | None, host: str, port: int):
    """Launch the QC dashboard web UI."""
    from aquifer.dashboard.app import run

    if password is None:
        password = getpass.getpass("Vault password: ")

    click.echo(f"Starting Aquifer dashboard at http://{host}:{port}")
    run(vault_path, password, host=host, port=port)


# ---------------------------------------------------------------------------
# License management
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("license_key")
def activate(license_key: str):
    """Activate a license key to unlock paid features."""
    from aquifer.licensing import activate_license

    license = activate_license(license_key)
    if license.is_valid:
        click.echo(f"License activated successfully!")
        click.echo(f"  Tier: {license.tier.value}")
        click.echo(f"  Practice: {license.practice_id}")
        click.echo(f"  Expires: {license.expires}")
        click.echo(f"  Features: {', '.join(sorted(license.features))}")
    else:
        click.echo(f"License validation failed: {license.error}", err=True)
        sys.exit(1)


@cli.command()
def license():
    """Show current license status."""
    from aquifer.licensing import get_current_license

    lic = get_current_license()
    click.echo(f"Tier: {lic.tier.value}")
    click.echo(f"Valid: {lic.is_valid}")
    if lic.practice_id:
        click.echo(f"Practice: {lic.practice_id}")
    click.echo(f"Expires: {lic.expires}")
    if lic.file_limit:
        click.echo(f"File limit: {lic.file_limit}/month")
    else:
        click.echo(f"File limit: unlimited")
    click.echo(f"Features: {', '.join(sorted(lic.features))}")


# ---------------------------------------------------------------------------
# Strata server
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--host", default=None, help="Host to bind to (default: 0.0.0.0)")
@click.option("--port", default=None, type=int, help="Port (default: 8443)")
@click.option("--debug", is_flag=True, help="Debug mode (auto-generates secrets, verbose logging)")
@click.option("--data-dir", default=None, type=click.Path(),
              help="Data directory for vaults and files")
def server(host: str | None, port: int | None, debug: bool, data_dir: str | None):
    """Start the Aquifer Strata API server (hosted mode)."""
    import os
    import uvicorn
    from aquifer.strata.config import StrataConfig

    if debug:
        os.environ.setdefault("AQUIFER_DEBUG", "1")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if data_dir:
        os.environ["AQUIFER_DATA_DIR"] = data_dir
    if host:
        os.environ["AQUIFER_HOST"] = host
    if port:
        os.environ["AQUIFER_PORT"] = str(port)

    config = StrataConfig.from_env()
    run_host = host or config.host
    run_port = port or config.port

    click.echo(f"Starting Aquifer Strata server")
    click.echo(f"  Bind: {run_host}:{run_port}")
    click.echo(f"  Data: {config.data_dir}")
    click.echo(f"  Debug: {config.debug}")
    if config.debug:
        click.echo(f"  Docs: http://{run_host}:{run_port}/docs")

    from aquifer.strata.server import create_app
    app = create_app(config)
    uvicorn.run(app, host=run_host, port=run_port)


# ---------------------------------------------------------------------------
# Claims intelligence (requires Starter+ license or API key)
# ---------------------------------------------------------------------------

@cli.group()
def claims():
    """Claims intelligence commands (requires paid license or API key)."""
    pass


@claims.command()
@click.argument("cdt_codes", nargs=-1, required=True)
@click.option("--payer", required=True, help="Payer ID (e.g., delta_dental)")
@click.option("--doc-score", default=1.0, type=float,
              help="Documentation completeness score (0.0-1.0)")
@click.option("--api-key", envvar="AQUIFER_API_KEY", default=None,
              help="API key for hosted service (or set AQUIFER_API_KEY)")
def predict(cdt_codes: tuple[str], payer: str, doc_score: float, api_key: str | None):
    """Predict denial risk before submitting a claim.

    Example: aquifer claims predict D3330 D2750 --payer delta_dental
    """
    if api_key:
        # Use hosted API
        from aquifer.api_client import AquiferAPI, APIConfig
        with AquiferAPI(APIConfig(api_key=api_key)) as api:
            result = api.predict_denial(list(cdt_codes), payer, doc_score)
    else:
        # Check license for local prediction
        from aquifer.licensing import require_feature, LicenseError
        try:
            require_feature("denial_prediction")
        except LicenseError as e:
            click.echo(str(e), err=True)
            click.echo("\nAlternatively, use --api-key for hosted predictions.", err=True)
            sys.exit(1)

        click.echo("Local prediction requires the aquifer-claims module.", err=True)
        click.echo("Use --api-key for hosted predictions, or install aquifer-claims.", err=True)
        sys.exit(1)

    # Display results
    color = {"low": "green", "medium": "yellow", "high": "red", "critical": "red"}
    click.echo(f"\nDenial Risk Assessment")
    click.echo(f"  CDT Codes: {', '.join(cdt_codes)}")
    click.echo(f"  Payer: {payer}")
    click.secho(f"  Risk Score: {result.risk_score:.0%} ({result.risk_level.upper()})",
                fg=color.get(result.risk_level, "white"), bold=True)
    click.echo(f"  Historical denial rate: {result.historical_denial_rate:.0%}")

    if result.risk_factors:
        click.echo(f"\n  Risk Factors:")
        for f in result.risk_factors:
            click.echo(f"    - {f}")

    if result.recommended_actions:
        click.echo(f"\n  Recommended Actions:")
        for a in result.recommended_actions:
            click.echo(f"    - {a}")


@claims.command()
@click.option("--carc", required=True, help="CARC denial reason code")
@click.option("--cdt", required=True, help="Primary CDT code")
@click.option("--payer", required=True, help="Payer ID")
@click.option("--amount", required=True, type=float, help="Denied amount")
@click.option("--description", default="", help="Denial description")
@click.option("--api-key", envvar="AQUIFER_API_KEY", default=None,
              help="API key for hosted service")
@click.option("-o", "--output", "output_file", default=None,
              help="Save appeal draft to file")
def appeal(carc: str, cdt: str, payer: str, amount: float,
           description: str, api_key: str | None, output_file: str | None):
    """Generate an appeal letter draft for a denied claim.

    Example: aquifer claims appeal --carc 16 --cdt D3330 --payer delta_dental --amount 950
    """
    if api_key:
        from aquifer.api_client import AquiferAPI, APIConfig
        with AquiferAPI(APIConfig(api_key=api_key)) as api:
            result = api.generate_appeal(carc, cdt, payer, description, amount)
    else:
        from aquifer.licensing import require_feature, LicenseError
        try:
            require_feature("appeal_generation")
        except LicenseError as e:
            click.echo(str(e), err=True)
            click.echo("\nUse --api-key for hosted appeal generation.", err=True)
            sys.exit(1)

        click.echo("Local appeal generation requires the aquifer-claims module.", err=True)
        sys.exit(1)

    click.echo(f"\nAppeal Draft (confidence: {result.confidence:.0%})")
    click.echo(f"Based on {result.similar_appeal_count} similar appeals "
               f"({result.estimated_success_rate:.0%} success rate)")
    click.echo(f"Template: {result.template_id}")
    click.echo(f"\n{'='*60}\n")
    click.echo(result.appeal_text)
    click.echo(f"\n{'='*60}")

    if output_file:
        Path(output_file).write_text(result.appeal_text)
        click.echo(f"\nSaved to {output_file}")


@claims.command("status")
@click.argument("claim_number")
@click.option("--api-key", envvar="AQUIFER_API_KEY", default=None)
def claim_status(claim_number: str, api_key: str | None):
    """Check the status of a tracked claim."""
    if not api_key:
        click.echo("Claim tracking requires an API key. Set AQUIFER_API_KEY or use --api-key.", err=True)
        sys.exit(1)

    from aquifer.api_client import AquiferAPI, APIConfig
    with AquiferAPI(APIConfig(api_key=api_key)) as api:
        result = api.get_claim_status(claim_number)

    click.echo(f"Claim: {result.claim_number}")
    click.echo(f"  Status: {result.status}")
    click.echo(f"  Last updated: {result.last_updated}")
    if result.payment_amount is not None:
        click.echo(f"  Payment: ${result.payment_amount:.2f}")
    if result.denial_reason:
        click.echo(f"  Denial reason: {result.denial_reason}")


if __name__ == "__main__":
    cli()
