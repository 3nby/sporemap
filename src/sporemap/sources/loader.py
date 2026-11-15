import logging
from importlib.metadata import entry_points

from sporemap.sources.base import Source

log = logging.getLogger(__name__)
GROUP = "sporemap.sources"


def load_sources():
    # name -> Source class. a broken plugin gets skipped, not fatal
    out = {}
    for ep in entry_points(group=GROUP):
        try:
            cls = ep.load()
        except Exception as e:  # noqa: BLE001  any import error from 3rd party code
            log.warning("skipping source " + ep.name + ": " + repr(e))
            continue
        if not (isinstance(cls, type) and issubclass(cls, Source)):
            log.warning("skipping source " + ep.name + ": not a Source subclass")
            continue
        if ep.name in out:
            log.warning("duplicate source name " + ep.name + ", keeping the first one")
            continue
        out[ep.name] = cls
    # print(out)
    return out
