import os

from dotenv import dotenv_values
from pydantic import SecretStr, ValidationError

from sporemap.settings import env_files, get_settings


class SourceConfigError(ValueError):
    pass


def _is_secret(f):
    return f.annotation is SecretStr or f.annotation == (SecretStr | None)


def env_name(source, field):
    # proxmox + token_secret -> SPOREMAP_PROXMOX_TOKEN_SECRET
    return "SPOREMAP_" + source.upper().replace("-", "_") + "_" + field.upper()


def build_config(name, cls):
    """Merge sporemap.yaml settings with env vars (and .env) into the source's Config.

    Secret fields (SecretStr) may only come from env vars, never the yaml file.
    """
    model = cls.Config
    base = dict(get_settings().sources.get(name) or {})
    dot = {}
    for f in env_files():  # later files win
        dot.update(dotenv_values(f))

    for fname, f in model.model_fields.items():
        if _is_secret(f) and fname in base:
            raise SourceConfigError(
                name + "." + fname + " is a secret, set it with " + env_name(name, fname) + " instead of sporemap.yaml")
        k = env_name(name, fname)
        v = os.environ.get(k, dot.get(k))  # real env beats .env
        if v is not None:
            base[fname] = v

    try:
        return model(**base)
    except ValidationError as e:
        # print(e)
        bad = ", ".join(".".join(str(x) for x in err["loc"]) for err in e.errors())
        raise SourceConfigError("bad config for source " + name + ": " + bad) from e


def redacted(name, cls):
    # for showing config without leaking secrets
    c = build_config(name, cls)
    out = {}
    for k, v in c.model_dump().items():
        out[k] = "********" if isinstance(v, SecretStr) else v
    return out
