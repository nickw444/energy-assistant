import asyncio
import sys
from collections.abc import Callable, Coroutine
from functools import wraps
from pathlib import Path
from typing import Any

import click

from .config import Config, load_config
from .ha_client import HomeAssistantWebSocketClient


def sync(func: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Any]:
    """Decorator that runs async click commands with asyncio.run."""
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(func(*args, **kwargs))

    return wrapper


@click.group()
@click.option(
    "--config",
    "config_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to hass-energy YAML config.",
)
@click.pass_context
def cli(ctx: click.Context, config_path: Path) -> None:
    """Entry point for the hass-energy CLI."""
    ctx.ensure_object(dict)
    try:
        ctx.obj["config"] = load_config(config_path)
        ctx.obj["config_path"] = config_path
    except (ValueError, FileNotFoundError) as err:
        click.echo(f"Config error: {err}", err=True)
        sys.exit(1)


@cli.command("validate-config")
@click.pass_context
def validate_config(ctx: click.Context) -> None:
    """Validate a YAML configuration file."""
    config: Config = ctx.obj["config"]
    click.echo(f"Config OK for base_url={config.home_assistant.base_url}")


@cli.command("test-connection")
@click.option(
    "--timeout",
    default=10.0,
    show_default=True,
    help="Timeout for the connection (seconds).",
)
@click.pass_context
@sync
async def test_connection(ctx: click.Context, timeout: float) -> None:
    """Test connection to Home Assistant via WebSocket API."""
    config: Config = ctx.obj["config"]
    client = HomeAssistantWebSocketClient(
        base_url=config.home_assistant.base_url,
        token=config.home_assistant.token,
        verify_ssl=config.home_assistant.verify_ssl,
        ws_max_size=config.home_assistant.ws_max_size,
    )

    try:
        ha_version = await client.connect(timeout=timeout)
        await client.ping()
        await client.disconnect()
        click.echo(f"Connection successful. Home Assistant version: {ha_version}")
    except (ValueError, PermissionError, FileNotFoundError, TimeoutError, OSError) as err:
        click.echo(f"Connection failed: {err}", err=True)
        sys.exit(1)


@cli.group("hass")
@click.pass_context
def hass_group(ctx: click.Context) -> None:
    """Home Assistant data helpers."""
    ctx.ensure_object(dict)


@hass_group.command("list-entities")
@click.pass_context
@sync
async def list_entities(ctx: click.Context) -> None:
    """List all entity_ids from Home Assistant."""
    config: Config = ctx.obj["config"]
    client = HomeAssistantWebSocketClient(
        base_url=config.home_assistant.base_url,
        token=config.home_assistant.token,
        verify_ssl=config.home_assistant.verify_ssl,
        ws_max_size=config.home_assistant.ws_max_size,
    )

    try:
        await client.connect()
        states = await client.get_states([])
        for entity_id in sorted(states.keys()):
            click.echo(entity_id)
    except (ValueError, PermissionError, FileNotFoundError, TimeoutError, OSError) as err:
        click.echo(f"Failed to list entities: {err}", err=True)
        sys.exit(1)
    finally:
        await client.disconnect()


@hass_group.command("get-states")
@click.argument("entity_id", nargs=-1)
@click.pass_context
@sync
async def get_states(ctx: click.Context, entity_id: tuple[str, ...]) -> None:
    """Fetch state for specified entity IDs (space separated list)."""
    if not entity_id:
        click.echo("Provide at least one entity_id.", err=True)
        sys.exit(1)

    config: Config = ctx.obj["config"]
    client = HomeAssistantWebSocketClient(
        base_url=config.home_assistant.base_url,
        token=config.home_assistant.token,
        verify_ssl=config.home_assistant.verify_ssl,
        ws_max_size=config.home_assistant.ws_max_size,
    )

    try:
        await client.connect()
        states = await client.get_states(list(entity_id))
        for eid, state in states.items():
            click.echo(f"{eid}: {state}")
    except (ValueError, PermissionError, FileNotFoundError, TimeoutError, OSError) as err:
        click.echo(f"Failed to get states: {err}", err=True)
        sys.exit(1)
    finally:
        await client.disconnect()


def main() -> None:
    """Invoke the CLI."""
    cli(prog_name="hass-energy")


if __name__ == "__main__":
    main()
