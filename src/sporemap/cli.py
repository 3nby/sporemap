import typer

from sporemap import __version__
from sporemap.settings import get_settings

app = typer.Typer(help="Sporemap: your homelab, mapped automatically.", no_args_is_help=True)


@app.command()
def serve(host: str = typer.Option(None, help="Defaults to the configured host."),
          port: int = typer.Option(None, help="Defaults to the configured port."),
          reload: bool = False,
          schedule: bool = typer.Option(True, "--schedule/--no-schedule",
                                        help="Scan sources on their scan_every interval.")):
    """Run the web server (and scan on a schedule)."""
    import os

    import uvicorn  # lazy so the cli stays snappy i guess

    if not schedule:
        os.environ["SPOREMAP_SCHEDULE"] = "false"  # env so the app factory (and --reload kids) see it
        get_settings.cache_clear()
    s = get_settings()
    h = host or s.host
    p = port or s.port
    # print("serving on " + h + ":" + str(p))
    uvicorn.run("sporemap.api.app:create_app", factory=True, host=h, port=p,
                reload=reload, log_level=s.log_level)


@app.command()
def config():
    """Show the settings Sporemap will use."""
    from sporemap.settings import _cfg_path, env_files
    from sporemap.sources import load_sources
    from sporemap.sources.config import SourceConfigError, redacted

    s = get_settings()
    c = _cfg_path()
    typer.echo("config file: " + str(c.resolve()) + ("" if c.exists() else " (not found, using defaults)"))
    typer.echo("env files: " + (", ".join(str(f.resolve()) for f in env_files()) or "none"))
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

    from sporemap.core.doctor import check

    x = asyncio.run(check(name))
    if not x["ok"]:
        _bad(x)
        raise typer.Exit(1)
    typer.echo(name + ": ok")


def _bad(x):
    typer.echo(x["name"] + ": FAILED " + str(x["error"]), err=True)
    if x["hint"]:
        typer.echo("  fix: " + x["hint"], err=True)


@app.command()
def doctor():
    """Check the config, the database and every configured source, with fix hints."""
    import asyncio

    from sporemap.core.doctor import check_all
    from sporemap.db import migrate
    from sporemap.settings import _cfg_path, env_files

    bad = 0
    try:
        s = get_settings()
    except Exception as e:  # bad yaml or a bad value, nothing else will work either
        typer.echo("config: FAILED " + str(e), err=True)
        typer.echo("  fix: correct " + str(_cfg_path()) + " and run sporemap doctor again", err=True)
        raise typer.Exit(1) from e
    c = _cfg_path()
    typer.echo("config: " + str(c.resolve()) + ("" if c.exists() else " (not found, using defaults)"))
    typer.echo("env files: " + (", ".join(str(f.resolve()) for f in env_files()) or "none"))

    try:
        s.data_dir.mkdir(parents=True, exist_ok=True)
        migrate()
        typer.echo("database: ok (" + str(s.data_dir) + ")")
    except Exception as e:  # noqa: BLE001  mostly permissions, eg root made the files and the service user cant write them
        bad += 1
        typer.echo("database: FAILED " + type(e).__name__ + ": " + str(e), err=True)
        typer.echo("  fix: make sure " + str(s.data_dir) + " exists and the user running sporemap can write to it", err=True)

    from sporemap import auth
    if auth.enabled():
        typer.echo("login: password set")
    elif auth.exposed():
        bad += 1
        typer.echo("login: FAILED no password, and serve listens on " + s.host + " so anyone who can reach it sees your lab", err=True)
        typer.echo("  fix: sporemap set-password", err=True)
    else:
        typer.echo("login: off (fine, serve only listens on " + s.host + ")")

    if not s.sources:
        typer.echo("sources: none configured", err=True)
        typer.echo("  fix: add some to " + str(c) + " (see sporemap.example.yaml), or try: sporemap scan demo", err=True)
        raise typer.Exit(1)

    for x in asyncio.run(check_all()):
        if x["ok"]:
            typer.echo(x["name"] + ": ok")
        else:
            bad += 1
            _bad(x)
    if bad:
        raise typer.Exit(1)


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


def _counts(sid):
    from sqlmodel import Session, func, select

    from sporemap.db import get_engine
    from sporemap.db.models import Observation, RawLink

    with Session(get_engine()) as s:
        no = s.exec(select(func.count()).select_from(Observation).where(Observation.scan_id == sid)).one()
        nl = s.exec(select(func.count()).select_from(RawLink).where(RawLink.scan_id == sid)).one()
    return str(no) + " observations, " + str(nl) + " links"


@app.command()
def scan(source: str = typer.Argument(None, help="Source name, see: sporemap sources list. Leave it off to scan everything in sporemap.yaml.")):
    """Run one source (or every configured one) now and store what it finds."""
    import asyncio

    from sporemap.core.correlate import correlate
    from sporemap.core.scans import run_scan, scan_all
    from sporemap.db import migrate
    from sporemap.sources import load_sources
    from sporemap.sources.config import SourceConfigError, build_config

    srcs = load_sources()

    if source is None:
        if not get_settings().sources:
            typer.echo("no sources configured. add some to sporemap.yaml (see sporemap.example.yaml), or try: sporemap scan demo", err=True)
            raise typer.Exit(1)
        migrate()
        res, g = asyncio.run(scan_all(sources=srcs))
        bad = 0
        for x in res:
            sc = x["scan"]
            if sc is not None and sc.status == "ok":
                typer.echo(x["name"] + ": ok, " + _counts(sc.id))
            else:
                bad += 1
                typer.echo(x["name"] + ": FAILED " + str(x["error"]), err=True)
        if g:
            typer.echo("graph: " + str(g["nodes"]) + " nodes, " + str(g["edges"]) + " edges")
        _prune()
        if bad:
            raise typer.Exit(1)
        return

    if source not in srcs:
        typer.echo("no source named " + source + ". installed: " + ", ".join(sorted(srcs)), err=True)
        raise typer.Exit(1)
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
        _prune()
        raise typer.Exit(1)
    typer.echo("scan " + str(sc.id) + " ok: " + _counts(sc.id))
    g = correlate()
    typer.echo("graph: " + str(g["nodes"]) + " nodes, " + str(g["edges"]) + " edges")
    _prune()


def _said(r):
    return str(r["scans"]) + " scans, " + str(r["observations"]) + " observations, " + str(r["links"]) + " links"


def _prune():
    # quiet unless it actually deleted something
    from sporemap.core.scans import prune

    r = prune()
    if r["scans"] or r["observations"] or r["links"]:
        typer.echo("pruned " + _said(r))


@app.command("prune")
def prune_cmd(keep: int = typer.Option(None, help="Scans to keep per source. Defaults to keep_scans."),
              vacuum: bool = typer.Option(True, "--vacuum/--no-vacuum",
                                          help="Shrink the db file afterwards (rewrites it, takes a moment).")):
    """Delete old scans now. The latest good scan of every source always stays."""
    import os

    from sporemap.core.scans import prune
    from sporemap.core.scans import vacuum as vac
    from sporemap.db import get_engine, migrate

    if keep is not None and keep < 1:
        typer.echo("--keep needs to be at least 1", err=True)
        raise typer.Exit(1)
    migrate()
    if keep is None and get_settings().keep_scans is None:
        typer.echo("keep_scans is off, nothing to prune. pass --keep to prune anyway")
        return
    r = prune(keep)
    typer.echo("pruned " + _said(r))
    if not vacuum:
        return
    db = get_engine().url.database
    was = os.path.getsize(db)
    try:
        vac()
    except Exception as e:  # noqa: BLE001  usually serve is busy writing, the space still gets reused
        typer.echo("vacuum skipped: " + str(e), err=True)
        return
    typer.echo("db file: " + str(was // 1024) + " KB -> " + str(os.path.getsize(db) // 1024) + " KB")


@app.command("set-password")
def set_password(off: bool = typer.Option(False, "--off", help="Remove the password, so no login (only safe on 127.0.0.1).")):
    """Set the password for the web UI and API. Only a hash of it is stored."""
    from sporemap.auth import hash_password, write_hash

    if off:
        p = write_hash(None)
        typer.echo("password removed from " + str(p) + ". restart sporemap serve to apply")
        return
    pw = typer.prompt("new password", hide_input=True, confirmation_prompt=True)
    if len(pw) < 8:
        typer.echo("use at least 8 characters", err=True)
        raise typer.Exit(1)
    p = write_hash(hash_password(pw))
    typer.echo("password saved as a hash in " + str(p) + ". restart sporemap serve to apply (signs everyone out)")


@app.command()
def version():
    """Print the version."""
    typer.echo("sporemap " + __version__)
