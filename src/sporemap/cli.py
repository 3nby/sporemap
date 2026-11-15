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
def scan(source: str = typer.Argument(..., help="Source name, see: sporemap sources list")):
    """Run one source now and store what it finds."""
    import asyncio

    from sqlmodel import Session, func, select

    from sporemap.core.scans import run_scan
    from sporemap.db import get_engine, migrate
    from sporemap.db.models import Observation, RawLink
    from sporemap.sources import load_sources

    srcs = load_sources()
    if source not in srcs:
        typer.echo("no source named " + source + ". installed: " + ", ".join(sorted(srcs)), err=True)
        raise typer.Exit(1)

    migrate()
    sc = asyncio.run(run_scan(source, sources=srcs))
    if sc.status != "ok":
        typer.echo("scan " + str(sc.id) + " failed: " + str(sc.error), err=True)
        raise typer.Exit(1)

    with Session(get_engine()) as s:
        no = s.exec(select(func.count()).select_from(Observation).where(Observation.scan_id == sc.id)).one()
        nl = s.exec(select(func.count()).select_from(RawLink).where(RawLink.scan_id == sc.id)).one()
    typer.echo("scan " + str(sc.id) + " ok: " + str(no) + " observations, " + str(nl) + " links")


@app.command()
def version():
    """Print the version."""
    typer.echo("sporemap " + __version__)
