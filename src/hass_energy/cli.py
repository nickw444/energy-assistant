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
from typing import cast

import click
import uvicorn
import yaml

from hass_energy.api.server import create_app
from hass_energy.config import load_app_config
from hass_energy.ems.fixture_harness import (
    EmsFixturePaths,
    compute_plan_hash,
    resolve_ems_fixture_paths,
    summarize_plan,
)
from hass_energy.ems.planner import EmsMilpPlanner
from hass_energy.lib.home_assistant import (
    HomeAssistantClient,
    HomeAssistantHistoryStateDict,
    HomeAssistantStateDict,
)
from hass_energy.lib.home_assistant_ws import HomeAssistantWebSocketClientImpl
from hass_energy.lib.source_resolver.fixtures import (
    FixtureHassDataProvider,
    freeze_hass_source_time,
)
from hass_energy.lib.source_resolver.hass_provider import HassDataProviderImpl
from hass_energy.lib.source_resolver.resolver import ValueResolverImpl
from hass_energy.models.config import AppConfig
from hass_energy.plotting import (
    ScenarioPlot,
    plot_plan_html,
    plot_scenarios_html,
    write_plan_image,
)
from hass_energy.worker import Worker

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _parse_fixture_scenario(
    fixture: str | None, scenario: str | None
) -> tuple[str | None, str | None]:
    """Parse fixture/scenario, allowing combined 'fixture/scenario' format or full path."""
    if fixture is None:
        return None, scenario
    if "/" not in fixture:
        return fixture, scenario
    if scenario is not None:
        return fixture, scenario

    path = Path(fixture.rstrip("/"))
    parts = path.parts

    ems_indices = [i for i, p in enumerate(parts) if p == "ems"]
    if ems_indices:
        ems_idx = ems_indices[-1]
        after_ems = parts[ems_idx + 1 :]
        if len(after_ems) == 1:
            return after_ems[0], None
        if len(after_ems) >= 2:
            return after_ems[0], after_ems[1]

    if len(parts) == 2:
        return parts[0], parts[1] if parts[1] else None
    if len(parts) == 1:
        return parts[0], None

    return fixture, scenario


def _common_options[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    func = click.option(
        "--config",
        type=click.Path(path_type=Path, dir_okay=False),
        default=None,
        show_default=False,
        help="Path to YAML config (defaults to config.yaml, then config.dev.yaml).",
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
def cli(ctx: click.Context, config: Path | None, log_level: str) -> int | None:
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
    help="Save the interactive HTML plot to this path.",
)
@click.option(
    "--solver-msg/--no-solver-msg",
    default=False,
    show_default=True,
    help="Enable solver output (CBC).",
)
@click.option(
    "--fixture",
    type=str,
    default=None,
    help="Replay a recorded fixture by name (supports 'fixture/scenario' format).",
)
@click.option(
    "--scenario",
    type=str,
    default=None,
    help="Scenario name within the fixture (optional).",
)
@click.option(
    "--scenario-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("tests/fixtures/ems"),
    show_default=True,
    help="Base directory containing fixture bundles.",
)
@click.pass_context
def ems_solve(
    ctx: click.Context,
    output: Path | None,
    stdout: bool,
    plot: bool,
    plot_output: Path | None,
    solver_msg: bool,
    fixture: str | None,
    scenario: str | None,
    scenario_dir: Path,
) -> None:
    _configure_logging(str(ctx.obj.get("log_level", "INFO")))
    config_path = ctx.obj.get("config")
    fixture, scenario = _parse_fixture_scenario(fixture, scenario)
    use_fixture = fixture is not None
    paths: EmsFixturePaths | None = None
    if use_fixture:
        paths = resolve_ems_fixture_paths(scenario_dir, fixture, scenario)
        if not paths.fixture_path.exists() or not paths.config_path.exists():
            raise click.ClickException(
                "Fixture/config not found. "
                f"Expected {paths.fixture_path} and {paths.config_path}."
            )
        config_path = paths.config_path

    app_config = load_app_config(config_path)

    if output is None:
        output = app_config.server.data_dir / "ems_plan.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    if plot_output is not None:
        plot_output.parent.mkdir(parents=True, exist_ok=True)

    try:
        if use_fixture:
            if paths is None:
                raise click.ClickException("Fixture paths not resolved.")
            provider, captured_at = FixtureHassDataProvider.from_path(paths.fixture_path)
            now = datetime.fromisoformat(captured_at) if captured_at else None
            resolver = ValueResolverImpl(hass_data_provider=provider)
            resolver.mark_for_hydration(app_config)
            resolver.hydrate_all()

            click.echo("Solving EMS MILP (fixture replay)...")
            with freeze_hass_source_time(now):
                plan = EmsMilpPlanner(app_config, resolver=resolver).generate_ems_plan(
                    now=now,
                    solver_msg=solver_msg,
                    deterministic=True,
                )
        else:
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
        html_output = plot_output or Path("ems_plan.html")
        if html_output.suffix != ".html":
            html_output = html_output.with_suffix(".html")
        plot_plan_html(plan, output=html_output)
        click.echo(f"Wrote interactive HTML plot to {html_output}")


@ems.command("record-scenario")
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("tests/fixtures/ems"),
    show_default=True,
    help="Directory to write fixture bundles.",
)
@click.option(
    "--fixture",
    type=str,
    required=True,
    help="Fixture name (supports 'fixture/scenario' format).",
)
@click.option(
    "--name",
    type=str,
    default=None,
    help="Scenario name within the fixture (optional subdirectory).",
)
@click.option(
    "--write-plan/--no-write-plan",
    default=True,
    show_default=True,
    help="Also write a summarized plan baseline.",
)
@click.option(
    "--redact/--no-redact",
    default=True,
    show_default=True,
    help="Redact Home Assistant credentials in the saved config.",
)
@click.option(
    "--solver-msg/--no-solver-msg",
    default=False,
    show_default=True,
    help="Enable solver output (CBC).",
)
@click.pass_context
def ems_record_scenario(
    ctx: click.Context,
    output_dir: Path,
    fixture: str,
    name: str | None,
    write_plan: bool,
    redact: bool,
    solver_msg: bool,
) -> None:
    """Record fixture data + config for offline EMS replay."""
    _configure_logging(str(ctx.obj.get("log_level", "INFO")))
    app_config = load_app_config(ctx.obj.get("config"))

    fixture_parsed, name = _parse_fixture_scenario(fixture, name)
    if fixture_parsed is None:
        raise click.ClickException("--fixture is required.")
    paths = resolve_ems_fixture_paths(output_dir, fixture_parsed, name)
    paths.scenario_dir.mkdir(parents=True, exist_ok=True)

    try:
        hass_client = HomeAssistantClient(config=app_config.homeassistant)
        hass_data_provider = HassDataProviderImpl(hass_client=hass_client)

        resolver = ValueResolverImpl(hass_data_provider=hass_data_provider)
        resolver.mark_for_hydration(app_config)
        resolver.hydrate_all()

        captured_at = datetime.now().astimezone()
        fixture_data = hass_data_provider.snapshot()
        fixture_data["captured_at"] = captured_at.isoformat()
        paths.fixture_path.write_text(json.dumps(fixture_data, indent=2, sort_keys=True))
        click.echo(f"Wrote EMS fixture to {paths.fixture_path}")

        if not paths.config_path.exists():
            config_payload = _serialize_fixture_config(app_config, redact=redact)
            paths.config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False))
            click.echo(f"Wrote EMS config to {paths.config_path}")
        else:
            click.echo(f"EMS config already exists at {paths.config_path}, skipping.")

        if write_plan:
            fixture_states = cast(dict[str, HomeAssistantStateDict], fixture_data["states"])
            fixture_history = cast(
                dict[str, list[HomeAssistantHistoryStateDict]],
                fixture_data["history"],
            )
            fixture_provider = FixtureHassDataProvider(
                states=fixture_states,
                history=fixture_history,
            )
            fixture_resolver = ValueResolverImpl(hass_data_provider=fixture_provider)
            fixture_resolver.mark_for_hydration(app_config)
            fixture_resolver.hydrate_all()
            with freeze_hass_source_time(captured_at):
                plan = EmsMilpPlanner(app_config, resolver=fixture_resolver).generate_ems_plan(
                    now=captured_at,
                    solver_msg=solver_msg,
                    deterministic=True,
                )
            plan_payload = summarize_plan(plan)
            paths.plan_path.write_text(json.dumps(plan_payload, indent=2, sort_keys=True))
            click.echo(f"Wrote EMS baseline summary to {paths.plan_path}")

            plan_hash = compute_plan_hash(plan_payload)
            write_plan_image(plan, paths.plot_path)
            click.echo(f"Wrote plan image to {paths.plot_path}")

            paths.hash_path.write_text(plan_hash + "\n")
            click.echo(f"Wrote plan hash to {paths.hash_path}")
    except Exception as exc:
        raise click.ClickException(traceback.format_exc()) from exc


@ems.command("refresh-baseline")
@click.option(
    "--fixture",
    type=str,
    required=False,
    default=None,
    help="Fixture name (supports 'fixture/scenario' format; omit to refresh all).",
)
@click.option(
    "--name",
    type=str,
    required=False,
    default=None,
    help="Scenario name within fixture (omit to refresh all scenarios in fixture).",
)
@click.option(
    "--scenario-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("tests/fixtures/ems"),
    show_default=True,
    help="Base directory containing fixture bundles.",
)
@click.option(
    "--solver-msg/--no-solver-msg",
    default=False,
    show_default=True,
    help="Enable solver output (CBC).",
)
@click.option(
    "--force-image/--no-force-image",
    default=False,
    show_default=True,
    help="Regenerate the plot image even if the plan hash is unchanged.",
)
@click.pass_context
def ems_refresh_baseline(
    ctx: click.Context,
    fixture: str | None,
    name: str | None,
    scenario_dir: Path,
    solver_msg: bool,
    force_image: bool,
) -> None:
    """Recompute the summarized baseline from a recorded fixture."""
    _configure_logging(str(ctx.obj.get("log_level", "INFO")))
    fixture, name = _parse_fixture_scenario(fixture, name)
    if fixture and name:
        paths = resolve_ems_fixture_paths(scenario_dir, fixture, name)
        if not paths.fixture_path.exists() or not paths.config_path.exists():
            raise click.ClickException(
                "Fixture/config not found. "
                f"Expected {paths.fixture_path} and {paths.config_path}."
            )
        try:
            _refresh_baseline_bundle(paths, solver_msg=solver_msg, force_image=force_image)
        except Exception as exc:
            raise click.ClickException(traceback.format_exc()) from exc
        return

    scenarios = _discover_fixture_scenarios(scenario_dir, fixture)
    if not scenarios:
        raise click.ClickException(f"No EMS fixture scenarios found under {scenario_dir}.")

    failures: list[tuple[tuple[str, str | None], str]] = []
    for fixture_name, scenario_name in scenarios:
        paths = resolve_ems_fixture_paths(scenario_dir, fixture_name, scenario_name)
        click.echo(f"Refreshing EMS baseline for {paths.scenario_dir}")
        try:
            _refresh_baseline_bundle(paths, solver_msg=solver_msg, force_image=force_image)
        except Exception as exc:
            failures.append(((fixture_name, scenario_name), _format_exception_message(exc)))

    if failures:
        failure_lines = "\n".join(
            f"- {f}/{s if s else ''}: {message}" for (f, s), message in failures
        )
        raise click.ClickException(
            "Failed to refresh one or more EMS baselines:\n" + failure_lines
        )


def _is_fixture_bundle(paths: EmsFixturePaths) -> bool:
    if not paths.fixture_path.exists():
        return False
    if not paths.config_path.exists():
        return False
    return True


def _refresh_baseline_bundle(
    paths: EmsFixturePaths,
    *,
    solver_msg: bool,
    force_image: bool,
) -> None:
    if not paths.fixture_path.exists() or not paths.config_path.exists():
        raise click.ClickException(
            "Fixture/config not found. "
            f"Expected {paths.fixture_path} and {paths.config_path}."
        )

    app_config = load_app_config(paths.config_path)
    provider, captured_at = FixtureHassDataProvider.from_path(paths.fixture_path)
    now = datetime.fromisoformat(captured_at) if captured_at else None

    resolver = ValueResolverImpl(hass_data_provider=provider)
    resolver.mark_for_hydration(app_config)
    resolver.hydrate_all()

    with freeze_hass_source_time(now):
        plan = EmsMilpPlanner(app_config, resolver=resolver).generate_ems_plan(
            now=now,
            solver_msg=solver_msg,
            deterministic=True,
        )
    plan_payload = summarize_plan(plan)
    paths.plan_path.write_text(json.dumps(plan_payload, indent=2, sort_keys=True))
    click.echo(f"Wrote EMS baseline summary to {paths.plan_path}")

    new_hash = compute_plan_hash(plan_payload)
    old_hash = paths.hash_path.read_text().strip() if paths.hash_path.exists() else None
    if new_hash != old_hash or force_image:
        write_plan_image(plan, paths.plot_path)
        click.echo(f"Wrote plan image to {paths.plot_path}")

        paths.hash_path.write_text(new_hash + "\n")
        click.echo(f"Wrote plan hash to {paths.hash_path}")
    else:
        click.echo("Plan unchanged, skipping image regeneration.")


def _format_exception_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message.splitlines()[0]
    return type(exc).__name__


def _discover_fixture_scenarios(
    base_dir: Path, fixture: str | None = None
) -> list[tuple[str, str | None]]:
    if not base_dir.exists():
        return []
    results: list[tuple[str, str | None]] = []

    if fixture is not None:
        fixture_dir = base_dir / fixture
        if not fixture_dir.is_dir():
            return []
        paths = resolve_ems_fixture_paths(base_dir, fixture, None)
        if _is_fixture_bundle(paths):
            results.append((fixture, None))
        for child in fixture_dir.iterdir():
            if not child.is_dir():
                continue
            scenario_paths = resolve_ems_fixture_paths(base_dir, fixture, child.name)
            if _is_fixture_bundle(scenario_paths):
                results.append((fixture, child.name))
        return sorted(results, key=lambda x: (x[0], x[1] or ""))

    for fixture_child in base_dir.iterdir():
        if not fixture_child.is_dir():
            continue
        fixture_name = fixture_child.name
        paths = resolve_ems_fixture_paths(base_dir, fixture_name, None)
        if _is_fixture_bundle(paths):
            results.append((fixture_name, None))
        for scenario_child in fixture_child.iterdir():
            if not scenario_child.is_dir():
                continue
            scenario_paths = resolve_ems_fixture_paths(base_dir, fixture_name, scenario_child.name)
            if _is_fixture_bundle(scenario_paths):
                results.append((fixture_name, scenario_child.name))

    return sorted(results, key=lambda x: (x[0], x[1] or ""))


@ems.command("scenario-report")
@click.option(
    "--output",
    type=click.Path(path_type=Path, dir_okay=False),
    default=Path("ems_scenarios.html"),
    show_default=True,
    help="Write the multi-scenario HTML report to this path.",
)
@click.option(
    "--fixture",
    type=str,
    default=None,
    help="Filter to a specific fixture (supports 'fixture/scenario' format).",
)
@click.option(
    "--scenario-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("tests/fixtures/ems"),
    show_default=True,
    help="Base directory containing fixture bundles.",
)
@click.option(
    "--solver-msg/--no-solver-msg",
    default=False,
    show_default=True,
    help="Enable solver output (CBC).",
)
@click.pass_context
def ems_scenario_report(
    ctx: click.Context,
    output: Path,
    fixture: str | None,
    scenario_dir: Path,
    solver_msg: bool,
) -> None:
    """Render a single HTML page with plots for every recorded scenario."""
    _configure_logging(str(ctx.obj.get("log_level", "INFO")))

    fixture_parsed, scenario_parsed = _parse_fixture_scenario(fixture, None)
    if fixture_parsed and scenario_parsed:
        scenarios: list[tuple[str, str | None]] = [(fixture_parsed, scenario_parsed)]
    else:
        scenarios = _discover_fixture_scenarios(scenario_dir, fixture_parsed)
    if not scenarios:
        raise click.ClickException(f"No EMS fixture scenarios found under {scenario_dir}.")

    output_path = output
    if output_path.suffix != ".html":
        output_path = output_path.with_suffix(".html")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    results: list[ScenarioPlot] = []
    failures: list[str] = []
    for fixture_name, scenario_name in scenarios:
        paths = resolve_ems_fixture_paths(scenario_dir, fixture_name, scenario_name)
        if not _is_fixture_bundle(paths):
            continue
        label = f"{fixture_name}/{scenario_name}" if scenario_name else fixture_name
        try:
            app_config = load_app_config(paths.config_path)
            provider, captured_at = FixtureHassDataProvider.from_path(paths.fixture_path)
            now = datetime.fromisoformat(captured_at) if captured_at else None

            resolver = ValueResolverImpl(hass_data_provider=provider)
            resolver.mark_for_hydration(app_config)
            resolver.hydrate_all()

            with freeze_hass_source_time(now):
                plan = EmsMilpPlanner(app_config, resolver=resolver).generate_ems_plan(
                    now=now,
                    solver_msg=solver_msg,
                    deterministic=True,
                )
            results.append(ScenarioPlot(name=label, plan=plan))
        except Exception:
            results.append(ScenarioPlot(name=label, error=traceback.format_exc()))
            failures.append(label)

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    subtitle_parts = [
        f"Generated {generated_at}",
        f"Scenarios: {len(results)}",
    ]
    if failures:
        subtitle_parts.append(f"Failures: {len(failures)}")
    subtitle = " | ".join(subtitle_parts)

    plot_scenarios_html(results, output=output_path, subtitle=subtitle)
    click.echo(f"Wrote scenario report to {output_path}")
    if failures:
        click.echo(
            "Scenarios with errors: " + ", ".join(failures) + " (see report for details)."
        )


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
    app_config = load_app_config(ctx.obj.get("config"))

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


def _serialize_fixture_config(app_config: AppConfig, *, redact: bool) -> dict[str, object]:
    payload: dict[str, object] = app_config.model_dump(mode="json")
    server = payload.get("server")
    if isinstance(server, dict):
        server["data_dir"] = "./data"
        payload["server"] = server
    if redact:
        homeassistant = payload.get("homeassistant")
        if isinstance(homeassistant, dict):
            homeassistant["token"] = "fixture-token"
            homeassistant["base_url"] = "http://example.invalid"
            payload["homeassistant"] = homeassistant
    return payload


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
