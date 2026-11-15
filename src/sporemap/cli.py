import typer

from sporemap import __version__

app = typer.Typer(help="Sporemap: your homelab, mapped automatically.", no_args_is_help=True)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8420, reload: bool = False):
    """Run the web server."""
    import uvicorn  # lazy so the cli stays snappy i guess

    # print("serving on " + host + ":" + str(port))
    uvicorn.run("sporemap.api.app:create_app", factory=True, host=host, port=port, reload=reload)


@app.command()
def version():
    """Print the version."""
    typer.echo("sporemap " + __version__)
