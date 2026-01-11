from __future__ import annotations

import asyncio
import inspect
import json
import logging
import signal
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Event

import click
import uvicorn

from hass_energy.api.server import create_app
from hass_energy.config import load_app_config
from hass_energy.ems.planner import EmsMilpPlanner
from hass_energy.lib.home_assistant import HomeAssistantClient
from hass_energy.lib.home_assistant_ws import HomeAssistantWebSocketClientImpl
from hass_energy.lib.source_resolver.hass_provider import HassDataProviderImpl
from hass_energy.lib.source_resolver.resolver import ValueResolverImpl
from hass_energy.plotting import plot_plan
from hass_energy.worker import Worker

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _common_options[**P, R](func: Callable[P, R]) -> Callable[P, R]:
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
    hass_data_provider = HassDataProviderImpl(hass_client=hass_client)

    resolver = ValueResolverImpl(hass_data_provider=hass_data_provider)
    ha_ws_client = HomeAssistantWebSocketClientImpl(config=app_config.homeassistant)
    worker = Worker(app_config=app_config, resolver=resolver, ha_ws_client=ha_ws_client)
    shutdown_event = Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        logging.info("Received signal %s, shutting down", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    app = create_app(app_config=app_config, worker=worker)

    server = uvicorn.Server(
        config=uvicorn.Config(
            app,
            host=app_config.server.host,
            port=app_config.server.port,
            reload=False,
            log_level="info",
        )
    )

    async def _serve() -> None:
        if worker:
            logging.info("Starting worker from CLI")
            worker.start()
        try:
            await server.serve()
        finally:
            if worker:
                logging.info("Stopping worker from CLI")
                worker.stop()

    server_task = _serve()
    try:
        if inspect.iscoroutine(server_task):
            asyncio.run(server_task)
        else:
            _ = server_task
    finally:
        shutdown_event.set()
    return 0


@cli.group("ems")
@click.pass_context
def ems(ctx: click.Context) -> None:
    """EMS MILP playground commands."""
    _ = ctx


@ems.command("solve")
@click.option(
    "--output",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Write the extracted plan JSON to this path (defaults to data_dir/ems_plan.json).",
)
@click.option(
    "--stdout/--no-stdout",
    default=False,
    show_default=True,
    help="Also print the plan JSON to stdout.",
)
@click.option(
    "--plot/--no-plot",
    default=True,
    show_default=True,
    help="Plot the extracted plan.",
)
@click.option(
    "--plot-output",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Save the plot to this path instead of showing it.",
)
@click.option(
    "--solver-msg/--no-solver-msg",
    default=False,
    show_default=True,
    help="Enable solver output (CBC).",
)
@click.pass_context
def ems_solve(
    ctx: click.Context,
    output: Path | None,
    stdout: bool,
    plot: bool,
    plot_output: Path | None,
    solver_msg: bool,
) -> None:
    _configure_logging(str(ctx.obj.get("log_level", "INFO")))
    config_path = Path(ctx.obj.get("config", Path("config.yaml")))
    app_config = load_app_config(config_path)

    if output is None:
        output = app_config.server.data_dir / "ems_plan.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    if plot_output is not None:
        plot_output.parent.mkdir(parents=True, exist_ok=True)

    try:
        hass_client = HomeAssistantClient(config=app_config.homeassistant)
        hass_data_provider = HassDataProviderImpl(hass_client=hass_client)

        resolver = ValueResolverImpl(hass_data_provider=hass_data_provider)
        resolver.mark_for_hydration(app_config)
        resolver.hydrate_all()

        click.echo("Solving EMS MILP...")
        plan = EmsMilpPlanner(app_config, resolver=resolver).generate_ems_plan(
            solver_msg=solver_msg,
        )
        click.echo(f"Timesteps: {len(plan.timesteps)}")
        timings = plan.timings
        click.echo(
            "Timings (s): build="
            f"{timings.build_seconds:.3f} solve={timings.solve_seconds:.3f} "
            f"total={timings.total_seconds:.3f}"
        )
    except Exception as exc:
        raise click.ClickException(traceback.format_exc()) from exc

    output.write_text(json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True))
    click.echo(f"Wrote EMS plan to {output}")

    if stdout:
        click.echo(json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True))
    if plot:
        plot_plan(plan, title="EMS Plan", output=plot_output)


@ems.command("record-fixture")
@click.option(
    "--output",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Write the fixture JSON to this path.",
)
@click.option(
    "--name",
    type=str,
    default=None,
    help="Fixture name (saved under tests/fixtures/ems/<name>.json).",
)
@click.pass_context
def ems_record_fixture(ctx: click.Context, output: Path | None, name: str | None) -> None:
    """Record a Home Assistant fixture for EMS tests."""
    _configure_logging(str(ctx.obj.get("log_level", "INFO")))
    config_path = Path(ctx.obj.get("config", Path("config.yaml")))
    app_config = load_app_config(config_path)

    if output is None:
        fixture_dir = Path("tests") / "fixtures" / "ems"
        filename = f"{name}.json" if name else "ems_fixture.json"
        output = fixture_dir / filename

    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        hass_client = HomeAssistantClient(config=app_config.homeassistant)
        hass_data_provider = HassDataProviderImpl(hass_client=hass_client)

        resolver = ValueResolverImpl(hass_data_provider=hass_data_provider)
        resolver.mark_for_hydration(app_config)
        resolver.hydrate_all()
    except Exception as exc:
        raise click.ClickException(traceback.format_exc()) from exc

    fixture = hass_data_provider.snapshot()
    fixture["captured_at"] = datetime.now().astimezone().isoformat()
    output.write_text(json.dumps(fixture, indent=2, sort_keys=True))
    click.echo(f"Wrote EMS fixture to {output}")


@cli.command("hydrate-load-forecast")
@click.option(
    "--limit",
    type=int,
    default=5,
    show_default=True,
    help="Number of intervals to print for inspection.",
)
@click.pass_context
def hydrate_load_forecast(ctx: click.Context, limit: int) -> None:
    """Hydrate config and resolve the load forecast source for inspection."""
    _configure_logging(str(ctx.obj.get("log_level", "INFO")))
    config_path = Path(ctx.obj.get("config", Path("config.yaml")))
    app_config = load_app_config(config_path)

    load_forecast = app_config.plant.load.forecast

    try:
        hass_client = HomeAssistantClient(config=app_config.homeassistant)
        hass_data_provider = HassDataProviderImpl(hass_client=hass_client)

        resolver = ValueResolverImpl(hass_data_provider=hass_data_provider)
        resolver.mark_for_hydration(app_config)
        resolver.hydrate_all()

        resolved = resolver.resolve(load_forecast)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    sorted_intervals = sorted(resolved, key=lambda interval: interval.start)
    entity = getattr(load_forecast, "entity", "<unknown>")
    click.echo(f"Resolved {len(sorted_intervals)} intervals for {entity}")

    show = sorted_intervals[: max(limit, 0)]
    for interval in show:
        click.echo(
            f"{interval.start.isoformat()} -> {interval.end.isoformat()} = {interval.value:.3f} kW"
        )


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
