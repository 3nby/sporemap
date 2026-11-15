import typer

from sporemap import __version__
from sporemap.settings import get_settings

app = typer.Typer(help="Sporemap: your homelab, mapped automatically.", no_args_is_help=True)


@app.command()
def serve(host: str = typer.Option(None, help="Defaults to the configured host."),
          port: int = typer.Option(None, help="Defaults to the configured port."),
          reload: bool = False):
    """Run the web server."""
    import uvicorn  # lazy so the cli stays snappy i guess

    s = get_settings()
    h = host or s.host
    p = port or s.port
    # print("serving on " + h + ":" + str(p))
    uvicorn.run("sporemap.api.app:create_app", factory=True, host=h, port=p,
                reload=reload, log_level=s.log_level)


@app.command()
def config():
    """Show the settings Sporemap will use."""
    s = get_settings()
    for k, v in s.model_dump().items():
        typer.echo(k + ": " + str(v))


src_app = typer.Typer(help="Discovery sources.", no_args_is_help=True)
app.add_typer(src_app, name="sources")


@src_app.command("list")
def sources_list():
    """List installed discovery sources."""
    from sporemap.sources import load_sources

    for n, cls in sorted(load_sources().items()):
        d = (cls.__doc__ or "").strip().splitlines()
        typer.echo(n + "  " + (d[0] if d else ""))


@app.command()
def version():
    """Print the version."""
    typer.echo("sporemap " + __version__)
