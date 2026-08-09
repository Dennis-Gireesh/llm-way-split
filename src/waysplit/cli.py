from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from waysplit import __version__
from waysplit.model_gateway import discover_models
from waysplit.repository import Repository
from waysplit.settings import load_settings
from waysplit.web import create_app

app = typer.Typer(
    name="waysplit",
    no_args_is_help=True,
    help="Local-first mobile statement splitting with deterministic safety gates.",
)


@app.command()
def serve(
    config: Annotated[Path | None, typer.Option(help="Optional YAML configuration file.")] = None,
) -> None:
    """Start the browser application."""

    settings = load_settings(config)
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
        proxy_headers=settings.trust_proxy_headers,
        server_header=False,
        date_header=False,
        log_level=settings.log_level.lower(),
    )


@app.command()
def doctor(
    config: Annotated[Path | None, typer.Option(help="Optional YAML configuration file.")] = None,
) -> None:
    """Check local storage, the audit chain, OCR, and configured model endpoints."""

    settings = load_settings(config)
    settings.ensure_directories()
    repository = Repository(settings.database_path)
    try:
        audit = repository.audit.verify()
        model_results = asyncio.run(
            discover_models(
                settings.model_endpoints,
                allow_remote=settings.allow_remote_model_endpoints,
                api_key=(
                    settings.model_api_key.get_secret_value() if settings.model_api_key else None
                ),
            )
        )
        report = {
            "version": __version__,
            "data_directory": str(settings.data_dir.resolve()),
            "audit_chain_valid": audit.valid,
            "audit_entries": audit.entries_checked,
            "model_endpoints": [
                {
                    "endpoint": result.endpoint,
                    "provider": result.provider.value if result.provider else None,
                    "models": [model.name for model in result.models],
                    "error": result.error,
                }
                for result in model_results
            ],
        }
        typer.echo(json.dumps(report, indent=2))
        if not audit.valid:
            raise typer.Exit(code=2)
    finally:
        repository.close()


@app.command("audit-verify")
def audit_verify(
    config: Annotated[Path | None, typer.Option(help="Optional YAML configuration file.")] = None,
) -> None:
    """Verify the local tamper-evident audit hash chain."""

    settings = load_settings(config)
    repository = Repository(settings.database_path)
    try:
        result = repository.audit.verify()
        typer.echo(result.model_dump_json(indent=2))
        if not result.valid:
            raise typer.Exit(code=2)
    finally:
        repository.close()


@app.command()
def version() -> None:
    """Print the installed WaySplit version."""

    typer.echo(__version__)


if __name__ == "__main__":
    app()
