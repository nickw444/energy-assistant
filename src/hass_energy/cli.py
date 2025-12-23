from __future__ import annotations

import asyncio
import inspect
import json
import logging
import signal
from dataclasses import asdict
from pathlib import Path
from threading import Event

import click
import uvicorn

from hass_energy.api.server import create_app
from hass_energy.config import load_app_config
from hass_energy.lib.home_assistant import HomeAssistantClient
from hass_energy.lib.source_resolver.hass_provider import HassDataProvider
from hass_energy.lib.source_resolver.resolver import ValueResolver
from hass_energy.milp_v2 import MilpCompiler, MilpExecutor, MilpPlanner
from hass_energy.plotting import plot_plan
from hass_energy.worker import Worker

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _common_options(func: click.Command) -> click.Command:
    func = click.option(
        "--config",
        type=click.Path(path_type=Path, dir_okay=False),
        default=Path("config.yaml"),
        show_default=True,
        help="Path to YAML config.",
    )(func)
    func = click.option(
        "--log-level",
        type=click.Choice(LOG_LEVELS, case_sensitive=False),
        default="INFO",
        show_default=True,
        help="Logging level.",
    )(func)
    return func


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, invoke_without_command=True)
@_common_options
@click.pass_context
def cli(ctx: click.Context, config: Path, log_level: str) -> int | None:
    if ctx.invoked_subcommand:
        ctx.ensure_object(dict)
        ctx.obj["config"] = config
        ctx.obj["log_level"] = log_level
        return None

    _configure_logging(log_level)

    app_config = load_app_config(config)

    hass_client = HomeAssistantClient(config=app_config.homeassistant)
    hass_data_provider = HassDataProvider(hass_client=hass_client)

    resolver = ValueResolver(hass_data_provider=hass_data_provider)
    resolver.mark_for_hydration(app_config)
    resolver.hydrate()

    worker = Worker(app_config=app_config, home_assistant_client=hass_client)
    shutdown_event = Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        logging.info("Received signal %s, shutting down", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    app = create_app(app_config=app_config, worker=worker)
    worker.start()

    server = uvicorn.Server(
        config=uvicorn.Config(
            app,
            host=app_config.server.host,
            port=app_config.server.port,
            reload=False,
            log_level="info",
        )
    )

    server_task = server.serve()
    try:
        if inspect.iscoroutine(server_task):
            asyncio.run(server_task)
        else:
            _ = server_task
    finally:
        shutdown_event.set()
        if worker:
            worker.stop()
    return 0


@cli.command()
@click.option("--plot/--no-plot", default=False, help="Show an interactive plot of the plan.")
@click.pass_context
def milp(ctx: click.Context, plot: bool) -> None:
    ctx.ensure_object(dict)
    config_path = ctx.obj["config"]
    log_level = ctx.obj["log_level"]
    _configure_logging(log_level)

    app_config = load_app_config(config_path)
    hass_client = HomeAssistantClient(config=app_config.homeassistant)
    hass_data_provider = HassDataProvider(hass_client=hass_client)
    resolver = ValueResolver(hass_data_provider=hass_data_provider)
    resolver.mark_for_hydration(app_config)
    resolver.hydrate()

    planner = MilpPlanner(
        compiler=MilpCompiler(),
        executor=MilpExecutor(),
    )
    try:
        plan = planner.generate_plan(
            ems=app_config.ems,
            plant=app_config.plant,
            loads=app_config.loads,
            value_resolver=resolver,
        )
    except NotImplementedError as exc:
        raise click.ClickException(str(exc)) from exc

    if plot:
        try:
            plot_plan(plan)
        except ImportError as exc:
            raise click.ClickException("matplotlib is required for --plot") from exc
    click.echo(json.dumps(asdict(plan), indent=2))


def _parse_log_level(level_str: str) -> int:
    normalized = level_str.strip().upper()
    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    if normalized in mapping:
        return mapping[normalized]
    raise ValueError(f"Invalid log level: {level_str}")


def _configure_logging(level_str: str) -> None:
    log_level = _parse_log_level(level_str)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("hass_energy").setLevel(log_level)




if __name__ == "__main__":
    cli()
