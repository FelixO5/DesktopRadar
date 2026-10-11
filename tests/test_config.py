from pathlib import Path

from radarscope import config
from radarscope.config import Config, load_config, save_config


def test_platform_config_dir_names_radarscope(monkeypatch):
    monkeypatch.delenv("RADARSCOPE_CONFIG_DIR", raising=False)
    assert config.config_dir().name == "radarscope"


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("RADARSCOPE_CONFIG_DIR", str(tmp_path))
    assert config.config_path() == tmp_path / "config.toml"


def test_round_trip(tmp_path):
    cfg = Config(postcode="GL14 2AB", home_lat=51.82, home_lon=-2.49, scope_range_mi=75,
                 poll_seconds=10, data_source="airplanes.live")
    cfg.window.x, cfg.window.y, cfg.window.size = 1500, 60, 400
    cfg.window.fullscreen = True
    cfg.show_airline_names = False
    path = save_config(cfg, tmp_path / "sub" / "config.toml")  # creates the directory
    loaded = load_config(path)
    assert loaded == cfg
    text = path.read_text()
    assert 'postcode = "GL14 2AB"' in text and "[window]" in text


def test_defaults_when_missing_or_corrupt(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not toml")
    assert load_config(bad) == Config()


def test_validation_clamps(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('scope_range_mi = 9999\npoll_seconds = 1\ndata_source = "bogus"\nhome_lat = 51.0\n'
                 '[window]\nsize = 5\n')
    cfg = load_config(p)
    assert cfg.scope_range_mi == 287
    assert cfg.poll_seconds == config.MIN_POLL_S
    assert cfg.data_source == "adsb.lol"
    assert not cfg.has_home  # lat without lon is discarded
    assert cfg.window.size == config.MIN_WINDOW_SIZE
    assert cfg.window.x is None


def test_unset_position_is_omitted_from_toml(tmp_path):
    path = save_config(Config(), tmp_path / "c.toml")
    assert "x =" not in Path(path).read_text()
