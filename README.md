# RadarScope

A small, always-on-top desktop widget that draws a classic circular radar scope centred
on your UK postcode and plots live nearby aircraft (ADS-B) as blips under a rotating
sweep. One Python/Qt codebase for **Linux (Ubuntu/GNOME), macOS and Windows**.

- Frameless, draggable, square-resizable window that floats in a corner of the screen
- Range rings at 25/50/75/100 % of the scope range, labelled in **statute miles**
- Clockwise sweep (6 s/rev, ~60 fps) with a fading afterglow; blips brighten as the sweep
  passes and decay, like a real PPI scope
- Blips are triangles pointing along each aircraft's ground track, labelled
  callsign + `FLxxx`, colour-banded by altitude (amber < 10,000 ft, green < 30,000 ft,
  cyan above, grey on the ground) and dimmed as their position ages
- Emergency squawks (7500/7600/7700) are red with a flashing ring
- Hover a blip for details; click to pin them (callsign, type, registration, altitude +
  climb/descent arrow, ground speed, track, squawk, distance in miles and bearing from home)
- Blips glide between polls (dead-reckoning from ground speed and track), then snap to
  the true position on each poll
- Network problems degrade gracefully: last good data stays up with a **STALE** indicator,
  retries back off exponentially (capped at 2 min), and after 3 consecutive failures the
  feed fails over to the other source

## Install & run

Requires Python 3.11+.

**Linux / macOS**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m radarscope            # first run prompts for postcode
# Linux/Wayland: prefix with QT_QPA_PLATFORM=xcb (or use the .desktop launcher)
```

**Windows (PowerShell)**
```powershell
py -3 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m radarscope            # first run prompts for postcode
```

`python -m radarscope` works from the repository root. To get a `radarscope` command on
your PATH that works from anywhere (and is what the launchers below use), install the
package into the venv:

```bash
pip install .
radarscope
```

Command-line options: `--config-dir DIR` (use another config directory), `--verbose`,
`--version`, and `--quit-after SECONDS` (exit cleanly after a delay; for smoke tests).

### Controls

| Action | How |
|---|---|
| Move | drag anywhere on the scope that isn't a blip |
| Resize | drag an edge or corner (kept square) |
| Details | hover a blip; click to pin, click again / Esc to unpin |
| Menu | right-click: Settings, Refresh, Range, Always on top, About, Quit |
| Range | `+` / `-` step through 10 / 25 / 50 / 100 / 150 / 250 mi |
| Refresh / Settings / Quit | `R` / `S` / `Ctrl+Q` |

A system-tray icon (where the desktop has a tray) offers show/hide and the same menu.

## Configuration

The first run shows a small dialog: enter a UK postcode; it is validated and geocoded with
postcodes.io and the latitude/longitude are cached. Change it later from
right-click › Settings.

Config is `config.toml` in the platform's per-user config directory, resolved at runtime
with `platformdirs` (override with `RADARSCOPE_CONFIG_DIR` or `--config-dir`):

| OS      | Config directory |
|---------|------------------|
| Linux   | `$XDG_CONFIG_HOME/radarscope/` (default `~/.config/radarscope/`) |
| macOS   | `~/Library/Application Support/radarscope/` |
| Windows | `%APPDATA%\radarscope\` |

```toml
postcode       = "GL14 2AB"   # entered once; lat/lon cached below
home_lat       = 51.82
home_lon       = -2.49
scope_range_mi = 50           # statute miles, 1–287
poll_seconds   = 8            # 5–120
data_source    = "adsb.lol"   # or "airplanes.live" (the other is the automatic fallback)

[window]
x = 1500                      # omitted = top-right corner of the primary screen
y = 60
size = 360
always_on_top = true
```

Window position and size are saved as you move/resize. A saved position that is no longer
on any connected monitor is pulled back onto the primary screen.

### Units

Everything you see is in **statute miles**. Internally RadarScope computes in nautical
miles, because the feed's query radius is in nm and ground speed is in knots; it converts
once, at display time (`1 nm = 1.150779 mi`). The feed caps the query radius at 250 nm,
so the largest useful scope range is 287 mi.

## Per-OS notes

### Linux / GNOME / Wayland
GNOME defaults to Wayland, which does not let applications keep themselves on top or
choose their own position. These work under **X11 / XWayland**, so:

- When RadarScope detects a Wayland session (and `QT_QPA_PLATFORM` isn't already set) it
  prefers XWayland itself by using `QT_QPA_PLATFORM=xcb;wayland`. The `;wayland` part is
  a fallback, so it still starts natively if the xcb plugin can't load. Set
  `RADARSCOPE_NATIVE_WAYLAND=1` to opt out; on native Wayland you can still drag and
  resize, but always-on-top and the saved position may be ignored (a note is logged).
- Qt 6's xcb plugin needs `libxcb-cursor0`: `sudo apt install libxcb-cursor0`.
- `radarscope.desktop` launches with `QT_QPA_PLATFORM=xcb;wayland`. After `pip install .`
  (so `radarscope` is on PATH):
  ```bash
  cp radarscope.desktop ~/.local/share/applications/         # app launcher
  cp radarscope.desktop ~/.config/autostart/                 # start at login
  ```
  If you installed into a venv, edit `Exec=` to the full path of `.venv/bin/radarscope`.
- Optional systemd user service: see the comments in `systemd/radarscope.service`.

### macOS
Always-on-top and positioning work natively. The window is a `Qt.Tool` window, so it
doesn't take a Dock icon or app-switcher slot; use the menu-bar icon or right-click › Quit.
To start at login, edit the path in `packaging/macos-launchd/com.user.radarscope.plist`
and copy it to `~/Library/LaunchAgents/` (instructions are in the file).

### Windows
Always-on-top and positioning work natively; high-DPI scaling is automatic. The widget
has no taskbar button; use the tray icon or right-click › Quit. To start at login without
a console window, `pip install .` into the venv, edit `VenvDir` in
`packaging/windows/radarscope.vbs`, and copy it to your Startup folder
(`Win+R` › `shell:startup`). Without a console, logs go to `radarscope.log` in the
config directory.

## Development & verification

```bash
pip install -r requirements.txt pytest
QT_QPA_PLATFORM=offscreen python -m pytest
```

The suite covers:

1. `geo.py` against known values (London→Paris ≈ 185 nm ≈ 213 mi, bearing ≈ 148°;
   `nm_to_miles(1) ≈ 1.1508`; due-North lands straight up; 600 kt for 60 s = 10 nm),
   with no Qt and no network.
2. Headless rendering under `QT_QPA_PLATFORM=offscreen`: a normal aircraft, an
   emergency squawk, one on the ground, one beyond scope range and one without a position
   are injected, `widget.grab()` forces a real `paintEvent`, and the on-scope / clipped /
   dropped counts and the blip's pixel position are checked.
3. The real launch commands: `python -m radarscope` is run as a subprocess (with a
   seeded config, and again on a first run). The installed `radarscope` console
   script is also run when it is on PATH (`pip install .` first).

Network clients are tested against mocked transports (validation, failover and recovery,
the rate-limit gap, timeouts, bad payloads).

### Project layout

```
radarscope/
  __main__.py        python -m radarscope
  main.py            QApplication, frameless window, wiring, main()
  scope_widget.py    QPainter: rings, sweep, blips, labels, hit-testing, detail panel
  geo.py             haversine, bearing, polar→screen, dead-reckoning (no Qt/network)
  data_source.py     postcodes.io + adsb.lol / airplanes.live clients, failover
  poller.py          background worker thread + Qt signals, backoff
  model.py           Aircraft dataclass, ageing, expiry
  config.py          cross-platform config dir, TOML load/save, validation
  settings_dialog.py postcode / range / interval / source dialog, About box
radarscope.desktop   Linux launcher (QT_QPA_PLATFORM=xcb;wayland)
systemd/             optional Linux user service
packaging/           optional macOS launchd agent and Windows startup launcher
tests/
```

## Data sources, attribution & licensing

- Aircraft data from **[adsb.lol](https://adsb.lol)**, licensed under the
  [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  Fallback data from **[airplanes.live](https://airplanes.live)**.
- Postcode geocoding by **[postcodes.io](https://postcodes.io)**, built on Open
  Government Licence data (contains OS data © Crown copyright and database right; Royal
  Mail data © Royal Mail copyright and database right; ONS data © Crown copyright).
- These APIs are free and need no key. RadarScope identifies itself with a descriptive
  `User-Agent`, never polls faster than your configured interval, and keeps at least
  1 s between requests to a source.
- This is a personal/hobby tool. Don't redistribute the feed or use it commercially
  without checking each source's terms.

Attribution is also shown in the Settings dialog and right-click › About.
