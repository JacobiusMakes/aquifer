"""Aquifer CLI — Click-based command-line interface."""

from __future__ import annotations

import getpass
import logging
import sys
from pathlib import Path

import click


@click.group()
@click.version_option(version="0.1.0", prog_name="aquifer")
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
