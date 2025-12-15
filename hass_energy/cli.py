import asyncio
import json
import sys
from collections.abc import Callable, Coroutine
from functools import wraps
from pathlib import Path
from typing import Any

import click

from .config import Config, load_config
from .datalogger import DataLogger
from .ha_client import HomeAssistantWebSocketClient
from .mapper import load_mapper
from .optimizer import load_optimizer


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
    except Exception as err:
        click.echo(f"Connection failed: {err}", err=True)
        sys.exit(1)


@cli.command("datalogger")
@click.option(
    "--entity",
    "entities",
    multiple=True,
    help="Entity IDs to log when a trigger fires.",
)
@click.option(
    "--trigger",
    "triggers",
    multiple=True,
    help="Entity IDs whose state changes trigger logging.",
)
@click.option(
    "--output-dir",
    required=True,
    type=click.Path(path_type=Path, file_okay=False),
    help="Directory to write JSON snapshots.",
)
@click.option(
    "--debounce",
    "debounce_seconds",
    type=click.FloatRange(min=0.0),
    default=2.0,
    show_default=True,
    help="Seconds to wait after the last trigger change before logging.",
)
@click.pass_context
@sync
async def datalogger(
    ctx: click.Context,
    entities: tuple[str, ...],
    triggers: tuple[str, ...],
    output_dir: Path,
    debounce_seconds: float,
) -> None:
    """Continuously log entity states when trigger entities change."""
    tracked_entities = list(dict.fromkeys(entities))
    trigger_entities = list(dict.fromkeys(triggers))

    if not trigger_entities:
        config: Config = ctx.obj["config"]
        trigger_entities = (
            list(dict.fromkeys(config.datalogger.triggers))
            if config.datalogger and config.datalogger.triggers
            else []
        )
    if not tracked_entities:
        config: Config = ctx.obj["config"]
        try:
            mapper = load_mapper(config.mapper)
            tracked_entities = list(dict.fromkeys(mapper.required_entities()))
        except Exception as err:
            click.echo(f"Failed to load mapper for entities: {err}", err=True)
            sys.exit(1)
        if not tracked_entities:
            click.echo(
                "Provide at least one entity via --entity or configure a mapper with "
                "required entities.",
                err=True,
            )
            sys.exit(1)
    if not trigger_entities:
        click.echo("Provide at least one trigger via --trigger.", err=True)
        sys.exit(1)

    output_dir_path = output_dir.resolve()

    config: Config = ctx.obj["config"]
    client = HomeAssistantWebSocketClient(
        base_url=config.home_assistant.base_url,
        token=config.home_assistant.token,
        verify_ssl=config.home_assistant.verify_ssl,
        ws_max_size=config.home_assistant.ws_max_size,
    )

    logger = DataLogger(
        client=client,
        entities=tracked_entities,
        triggers=trigger_entities,
        output_dir=output_dir_path,
        debounce_seconds=debounce_seconds,
        on_snapshot=lambda path: click.echo(f"Wrote snapshot to {path}"),
        on_error=lambda msg: click.echo(msg, err=True),
    )

    click.echo(
        "Datalogger running. "
        f"Triggers: {', '.join(trigger_entities)} | "
        f"Logging: {', '.join(tracked_entities)} | "
        f"Output: {output_dir_path}"
    )
    click.echo("Press Ctrl+C to stop.")

    try:
        started = await logger.run()
        if not started:
            sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\nStopping datalogger.")
    finally:
        await logger.stop()


@cli.command("run-mapper")
@click.pass_context
@sync
async def run_mapper(ctx: click.Context) -> None:
    """Run the configured mapper once and print mapped output."""
    config: Config = ctx.obj["config"]
    try:
        mapper = load_mapper(config.mapper)
    except Exception as err:
        click.echo(f"Failed to load mapper: {err}", err=True)
        sys.exit(1)

    required_entities = mapper.required_entities()
    if not required_entities:
        click.echo("Mapper has no required entities.", err=True)
        sys.exit(1)

    client = HomeAssistantWebSocketClient(
        base_url=config.home_assistant.base_url,
        token=config.home_assistant.token,
        verify_ssl=config.home_assistant.verify_ssl,
        ws_max_size=config.home_assistant.ws_max_size,
    )

    try:
        await client.connect()
        states = await client.get_states(required_entities)
        mapped = mapper.map(states)
    except Exception as err:
        click.echo(f"Failed to run mapper: {err}", err=True)
        sys.exit(1)
    finally:
        await client.disconnect()

    click.echo(json.dumps(mapped, indent=2))


@cli.command("run-optimizer")
@click.pass_context
@sync
async def run_optimizer(ctx: click.Context) -> None:
    """Run the configured mapper and optimizer once and print the decision output."""
    config: Config = ctx.obj["config"]
    if not config.optimizer:
        click.echo("No optimizer configured; add an optimizer section to the config.", err=True)
        sys.exit(1)

    try:
        mapper = load_mapper(config.mapper)
    except Exception as err:
        click.echo(f"Failed to load mapper: {err}", err=True)
        sys.exit(1)

    try:
        optimizer = load_optimizer(config.optimizer)
    except Exception as err:
        click.echo(f"Failed to load optimizer: {err}", err=True)
        sys.exit(1)

    required_entities = mapper.required_entities()
    if not required_entities:
        click.echo("Mapper has no required entities.", err=True)
        sys.exit(1)
    optimizer_entities = optimizer.required_entities()

    client = HomeAssistantWebSocketClient(
        base_url=config.home_assistant.base_url,
        token=config.home_assistant.token,
        verify_ssl=config.home_assistant.verify_ssl,
        ws_max_size=config.home_assistant.ws_max_size,
    )

    try:
        await client.connect()
        all_entities = list(dict.fromkeys([*required_entities, *optimizer_entities]))
        states = await client.get_states(all_entities)
        mapped = mapper.map(states)
        missing_knobs = [entity for entity in optimizer_entities if entity not in states]
        if missing_knobs:
            click.echo(
                "Missing optimizer knob entities from Home Assistant: "
                f"{', '.join(sorted(missing_knobs))}",
                err=True,
            )
            sys.exit(1)
        knob_states = {entity: states[entity] for entity in optimizer_entities}
        decision = optimizer.decide(mapped, knob_states)
    except Exception as err:
        click.echo(f"Failed to run optimizer: {err}", err=True)
        sys.exit(1)
    finally:
        await client.disconnect()

    click.echo(json.dumps(decision, indent=2))


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
    except Exception as err:
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
    except Exception as err:
        click.echo(f"Failed to get states: {err}", err=True)
        sys.exit(1)
    finally:
        await client.disconnect()



def main() -> None:
    """Invoke the CLI."""
    cli(prog_name="hass-energy")


if __name__ == "__main__":
    main()
