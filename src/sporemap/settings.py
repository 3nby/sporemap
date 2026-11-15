import os
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_dir
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


def _cfg_path():
    # SPOREMAP_CONFIG wins, otherwise look for sporemap.yaml in cwd
    p = os.environ.get("SPOREMAP_CONFIG")
    if p:
        return Path(p)
    return Path("sporemap.yaml")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPOREMAP_", extra="ignore")

    data_dir: Path = Path(user_data_dir("sporemap", appauthor=False))
    host: str = "127.0.0.1"  # localhost by default, on purpose
    port: int = 8420
    log_level: str = "info"
    scan_timeout: float = 120  # seconds per source run

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        # order = priority: explicit args > env vars > yaml file > defaults
        y = YamlConfigSettingsSource(settings_cls, yaml_file=_cfg_path())
        return (init_settings, env_settings, y)


@lru_cache
def get_settings():
    s = Settings()
    # print("data dir: " + str(s.data_dir))
    return s
