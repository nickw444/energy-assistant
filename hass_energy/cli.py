import click


@click.group()
def cli() -> None:
    """Entry point for the hass-energy CLI."""


@cli.command()
def hello() -> None:
    """Placeholder command to verify the CLI wiring."""
    click.echo("Hello from hass-energy!")


def main() -> None:
    """Invoke the CLI."""
    cli(prog_name="hass-energy")


if __name__ == "__main__":
    main()
