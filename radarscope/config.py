"""Cross-platform configuration: locate the per-user config dir, load/save TOML.

Config directory (resolved at runtime via ``platformdirs``, never hard-coded):

* Linux   ``$XDG_CONFIG_HOME/radarscope/`` (default ``~/.config/radarscope/``)
* macOS   ``~/Library/Application Support/radarscope/``
* Windows ``%APPDATA%\\radarscope\\``

``RADARSCOPE_CONFIG_DIR`` (or ``--config-dir``) overrides it, e.g. for testing.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import platformdirs
import tomli_w

from . import geo

log = logging.getLogger(__name__)

APP_DIR_NAME = "radarscope"
CONFIG_FILE_NAME = "config.toml"

DATA_SOURCES = ("adsb.lol", "airplanes.live")
MIN_POLL_S, MAX_POLL_S, DEFAULT_POLL_S = 5, 120, 8
MIN_WINDOW_SIZE, MAX_WINDOW_SIZE, DEFAULT_WINDOW_SIZE = 200, 1600, 360


@dataclass
class WindowConfig:
    #: ``None`` = auto-place in the top-right corner of the primary screen.
    x: int | None = None
    y: int | None = None
    size: int = DEFAULT_WINDOW_SIZE
    always_on_top: bool = True
    #: Reopen in full screen if it was full screen when last closed.
    fullscreen: bool = False


@dataclass
class Config:
    postcode: str = ""
    home_lat: float | None = None
    home_lon: float | None = None
    scope_range_mi: int = 50
    poll_seconds: int = DEFAULT_POLL_S
    data_source: str = DATA_SOURCES[0]
    #: Third label line with the airline / operator name.
    show_airline_names: bool = True
    window: WindowConfig = field(default_factory=WindowConfig)

    @property
    def has_home(self) -> bool:
        return self.home_lat is not None and self.home_lon is not None

    @property
    def scope_range_nm(self) -> float:
        return geo.miles_to_nm(self.scope_range_mi)

    @property
    def fallback_source(self) -> str:
        return next(s for s in DATA_SOURCES if s != self.data_source)


def config_dir(override: str | os.PathLike[str] | None = None) -> Path:
    """The per-user config directory for this OS (not created)."""
    chosen = override or os.environ.get("RADARSCOPE_CONFIG_DIR")
    if chosen:
        return Path(chosen).expanduser()
    # appauthor=False keeps Windows at %APPDATA%\radarscope (no vendor folder);
    # roaming=True selects Roaming rather than Local AppData.
    return Path(platformdirs.user_config_dir(APP_DIR_NAME, appauthor=False, roaming=True))


def config_path(override: str | os.PathLike[str] | None = None) -> Path:
    return config_dir(override) / CONFIG_FILE_NAME


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _opt_float(value: Any, lo: float, hi: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if lo <= value <= hi else None


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def from_dict(data: dict[str, Any]) -> Config:
    """Build a validated Config from parsed TOML, falling back to defaults."""
    cfg = Config()
    cfg.postcode = str(data.get("postcode", "") or "").strip().upper()
    cfg.home_lat = _opt_float(data.get("home_lat"), -90, 90)
    cfg.home_lon = _opt_float(data.get("home_lon"), -180, 180)
    if cfg.home_lat is None or cfg.home_lon is None:
        cfg.home_lat = cfg.home_lon = None
    cfg.scope_range_mi = _clamp_int(data.get("scope_range_mi"), geo.MIN_SCOPE_RANGE_MI,
                                    geo.MAX_SCOPE_RANGE_MI, cfg.scope_range_mi)
    cfg.poll_seconds = _clamp_int(data.get("poll_seconds"), MIN_POLL_S, MAX_POLL_S, cfg.poll_seconds)
    source = data.get("data_source")
    cfg.data_source = source if source in DATA_SOURCES else DATA_SOURCES[0]
    cfg.show_airline_names = bool(data.get("show_airline_names", True))
    win = data.get("window") if isinstance(data.get("window"), dict) else {}
    cfg.window = WindowConfig(
        x=_opt_int(win.get("x")),
        y=_opt_int(win.get("y")),
        size=_clamp_int(win.get("size"), MIN_WINDOW_SIZE, MAX_WINDOW_SIZE, DEFAULT_WINDOW_SIZE),
        always_on_top=bool(win.get("always_on_top", True)),
        fullscreen=bool(win.get("fullscreen", False)),
    )
    return cfg


def to_dict(cfg: Config) -> dict[str, Any]:
    """TOML-serialisable dict (TOML has no null, so unset values are omitted)."""
    data = asdict(cfg)
    window = {k: v for k, v in data.pop("window").items() if v is not None}
    data = {k: v for k, v in data.items() if v is not None}
    data["window"] = window
    return data


def load_config(path: Path | None = None) -> Config:
    """Load config, returning defaults if the file is missing or unreadable."""
    path = path or config_path()
    try:
        with open(path, "rb") as fh:
            return from_dict(tomllib.load(fh))
    except FileNotFoundError:
        return Config()
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("Could not read %s (%s); using defaults", path, exc)
        return Config()


def save_config(cfg: Config, path: Path | None = None) -> Path:
    """Atomically write config, creating the directory if missing."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    with open(tmp, "wb") as fh:
        tomli_w.dump(to_dict(cfg), fh)
    os.replace(tmp, path)
    return path
