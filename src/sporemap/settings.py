import os
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_dir
from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

ETC = Path("/etc/sporemap")  # where the lxc install puts things


def _cfg_path():
    # SPOREMAP_CONFIG wins, then sporemap.yaml in cwd (dev), then /etc/sporemap (installed)
    p = os.environ.get("SPOREMAP_CONFIG")
    if p:
        return Path(p)
    if Path("sporemap.yaml").exists():
        return Path("sporemap.yaml")
    if (ETC / "sporemap.yaml").exists():
        return ETC / "sporemap.yaml"
    return Path("sporemap.yaml")


def env_files():
    # sporemap.env next to the yaml, then .env in cwd. later ones win, real env vars beat both
    out = []
    for f in (_cfg_path().parent / "sporemap.env", Path(".env")):
        if f.exists() and f.resolve() not in [x.resolve() for x in out]:
            out.append(f)
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPOREMAP_", env_file=".env", extra="ignore")

    data_dir: Path = Path(user_data_dir("sporemap", appauthor=False))
    host: str = "127.0.0.1"  # localhost by default, on purpose
    port: int = 8420
    log_level: str = "info"
    scan_timeout: float = 120  # seconds per source run
    sources: dict[str, dict] = {}  # per-source settings from sporemap.yaml, NO secrets in here

    @field_validator("sources", mode="before")
    @classmethod
    def _empty_ok(cls, v):
        # "sources:" with everything commented out, or "demo:" with nothing after it, is yaml null
        if v is None:
            return {}
        if isinstance(v, dict):
            return {k: ({} if c is None else c) for k, c in v.items()}
        return v

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # order = priority: explicit args > env vars > .env file > yaml file > defaults
        y = YamlConfigSettingsSource(settings_cls, yaml_file=_cfg_path())
        return (init_settings, env_settings, dotenv_settings, y)


@lru_cache
def get_settings():
    s = Settings(_env_file=env_files() or None)
    # print("data dir: " + str(s.data_dir))
    return s
