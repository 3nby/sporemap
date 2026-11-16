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
    from sporemap.sources import load_sources
    from sporemap.sources.config import SourceConfigError, redacted

    s = get_settings()
    for k, v in s.model_dump().items():
        if k != "sources":
            typer.echo(k + ": " + str(v))
    srcs = load_sources()
    for n in sorted(s.sources):
        if n not in srcs:
            typer.echo("source " + n + ": not installed")
            continue
        try:
            typer.echo("source " + n + ": " + str(redacted(n, srcs[n])))
        except SourceConfigError as e:
            typer.echo("source " + n + ": ERROR " + str(e))


src_app = typer.Typer(help="Discovery sources.", no_args_is_help=True)
app.add_typer(src_app, name="sources")


@src_app.command("list")
def sources_list():
    """List installed discovery sources."""
    from sporemap.sources import load_sources

    for n, cls in sorted(load_sources().items()):
        d = (cls.__doc__ or "").strip().splitlines()
        typer.echo(n + "  " + (d[0] if d else ""))


@src_app.command("test")
def sources_test(name: str):
    """Check that a source's config can reach its system."""
    import asyncio

    from sporemap.sources import load_sources
    from sporemap.sources.config import SourceConfigError, build_config

    srcs = load_sources()
    if name not in srcs:
        typer.echo("no source named " + name, err=True)
        raise typer.Exit(1)
    try:
        cfg = build_config(name, srcs[name])
        asyncio.run(srcs[name]().test(cfg))
    except SourceConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    except Exception as e:  # show whatever the source threw
        typer.echo(name + ": FAILED " + type(e).__name__ + ": " + str(e), err=True)
        raise typer.Exit(1) from e
    typer.echo(name + ": ok")


@src_app.command("forget")
def sources_forget(name: str, yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation.")):
    """Delete everything a source has reported and rebuild the graph."""
    from sporemap.core.scans import forget
    from sporemap.db import migrate

    if not yes:
        typer.confirm("forget every scan from " + name + "? nodes only it knew about will be deleted", abort=True)
    migrate()
    r = forget(name)
    if r is None:
        typer.echo("nothing stored for " + name)
        return
    typer.echo("forgot " + name + ": " + str(r["scans"]) + " scans, " + str(r["observations"]) + " observations, "
               + str(r["removed_nodes"]) + " nodes removed")
    typer.echo("graph: " + str(r["nodes"]) + " nodes, " + str(r["edges"]) + " edges")
    # dont unconfigure it, if its still in sporemap.yaml the next scan brings it right back


@app.command()
def scan(source: str = typer.Argument(..., help="Source name, see: sporemap sources list")):
    """Run one source now and store what it finds."""
    import asyncio

    from sqlmodel import Session, func, select

    from sporemap.core.correlate import correlate
    from sporemap.core.scans import run_scan
    from sporemap.db import get_engine, migrate
    from sporemap.db.models import Observation, RawLink
    from sporemap.sources import load_sources

    srcs = load_sources()
    if source not in srcs:
        typer.echo("no source named " + source + ". installed: " + ", ".join(sorted(srcs)), err=True)
        raise typer.Exit(1)

    from sporemap.sources.config import SourceConfigError, build_config

    if source not in get_settings().sources:
        typer.echo("warning: " + source + " isn't configured in sporemap.yaml, using defaults", err=True)
    try:
        cfg = build_config(source, srcs[source])
    except SourceConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e

    migrate()
    sc = asyncio.run(run_scan(source, cfg=cfg, sources=srcs))
    if sc.status != "ok":
        typer.echo("scan " + str(sc.id) + " failed: " + str(sc.error), err=True)
        raise typer.Exit(1)

    with Session(get_engine()) as s:
        no = s.exec(select(func.count()).select_from(Observation).where(Observation.scan_id == sc.id)).one()
        nl = s.exec(select(func.count()).select_from(RawLink).where(RawLink.scan_id == sc.id)).one()
    typer.echo("scan " + str(sc.id) + " ok: " + str(no) + " observations, " + str(nl) + " links")
    g = correlate()
    typer.echo("graph: " + str(g["nodes"]) + " nodes, " + str(g["edges"]) + " edges")


@app.command()
def version():
    """Print the version."""
    typer.echo("sporemap " + __version__)
