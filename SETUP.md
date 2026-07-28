# GRAINLINE Free — Setup

Installation for a workstation, a shop-floor PC, and an air-gapped CNC network.

---

## 1. Requirements

| | |
|---|---|
| Python | 3.11 or newer (3.13 recommended) |
| OS | Windows 10/11, macOS 12+, Linux |
| RAM | 2 GB free is plenty; nesting is not memory-hungry |
| Disk | ~120 MB including dependencies |
| Network | **Not required at runtime.** Only to install. |

Check what you have:

```bash
python --version
```

On Windows, if `python` opens the Microsoft Store, use the launcher instead:

```powershell
py -3.13 --version
```

---

## 2. Standard install

A virtual environment keeps GRAINLINE's dependencies away from anything else on
the machine. This matters more than usual on a shop PC, which often has a
vendor's Python installed for a machine controller.

### Windows (PowerShell)

```powershell
py -3.13 -m venv C:\grainline\.venv
C:\grainline\.venv\Scripts\python.exe -m pip install --upgrade pip
C:\grainline\.venv\Scripts\python.exe -m pip install "grainline[web]"
```

Add it to your `PATH` so `grainline` works from any folder:

```powershell
$env:PATH = "C:\grainline\.venv\Scripts;$env:PATH"
# To make it permanent:
[Environment]::SetEnvironmentVariable(
    "PATH", "C:\grainline\.venv\Scripts;$env:PATH", "User")
```

### macOS / Linux

```bash
python3 -m venv ~/grainline/.venv
source ~/grainline/.venv/bin/activate
pip install --upgrade pip
pip install "grainline[web]"
```

### Verify

```bash
grainline version
```

You should see:

```
GRAINLINE 0.1.0 - Free edition (no licence installed)
```

---

## 3. Offline / air-gapped install

CNC networks are frequently isolated on purpose. GRAINLINE is built to run there
— it makes no network calls at runtime — but you have to carry the packages in.

**On an internet-connected machine with the same OS and Python version:**

```bash
mkdir grainline-offline
pip download "grainline[web]" -d grainline-offline
```

Copy the `grainline-offline` folder to the target machine on a USB stick, then:

```bash
python -m venv .venv
.venv/bin/pip install --no-index --find-links grainline-offline "grainline[web]"
```

On Windows replace `.venv/bin/pip` with `.venv\Scripts\pip.exe`.

> **Platform must match.** `shapely` and `numpy` ship compiled wheels. Download
> on Windows for Windows, on Linux for Linux. Downloading on a Mac for a Windows
> shop PC will not work.

Confirm nothing reaches out:

```bash
grainline nest job.toml --out out
```

You can run this with the network cable unplugged. It will behave identically.

---

## 4. Shop-floor deployment

### Web interface as a service

The web UI is convenient for operators who do not want a terminal.

```bash
grainline serve --host 127.0.0.1 --port 8711
```

**Binding beyond loopback exposes your part geometry to the network.**
GRAINLINE warns when you do it. If you genuinely want several workstations
hitting one machine, put it behind a reverse proxy with authentication — the
Free edition has none of its own.

#### Windows service (NSSM)

```powershell
nssm install GRAINLINE "C:\grainline\.venv\Scripts\grainline.exe" serve
nssm set GRAINLINE AppDirectory C:\grainline
nssm start GRAINLINE
```

#### systemd (Linux)

`/etc/systemd/system/grainline.service`:

```ini
[Unit]
Description=GRAINLINE nesting
After=network.target

[Service]
Type=simple
User=grainline
WorkingDirectory=/opt/grainline
ExecStart=/opt/grainline/.venv/bin/grainline serve --host 127.0.0.1 --port 8711
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now grainline
```

### Shared job folder

Put job files and part geometry on a share so the office can prepare a job and
the shop can run it:

```
\\fileserver\jobs\
    2026-10-gate-panels\
        job.toml
        parts\
            bracket.dxf
            stile.dxf
```

Paths inside `job.toml` are resolved **relative to the job file**, so the whole
folder can be copied or moved without editing anything.

---

## 5. Configuration

GRAINLINE needs no configuration to run. Two environment variables adjust it:

| Variable | Purpose | Default |
|---|---|---|
| `GRAINLINE_HOME` | Where configuration and licence live | `%LOCALAPPDATA%\grainline` (Windows) · `~/.config/grainline` (Linux/macOS) |
| `GRAINLINE_LICENSE` | A licence key, or a path to a file holding one | unset |

Find the directory:

```bash
grainline license path
```

---

## 6. Upgrading

```bash
pip install --upgrade grainline
```

Job files are forward-compatible. An unknown setting is a **hard error**, never
silently ignored — if you misspell `part_spacing` as `spacing`, GRAINLINE stops
and tells you rather than quietly using the default and giving you undersized
gaps.

---

## 7. Uninstall

```bash
pip uninstall grainline
```

Then remove the config directory reported by `grainline license path`. Nothing
else is left behind — GRAINLINE writes no registry keys, no temp files, and no
data outside that directory and wherever you pointed `--out`.

---

## 8. Troubleshooting the install

**`grainline: command not found`**
The virtual environment's `Scripts`/`bin` directory is not on your `PATH`. Either
add it, or call it directly: `C:\grainline\.venv\Scripts\grainline.exe version`.

**`ERROR: Could not build wheels for shapely`**
No prebuilt wheel exists for your Python version yet — usually because the
version is very new. Install on Python 3.13, which has full wheel coverage:

```bash
py -3.13 -m venv .venv
```

**`ModuleNotFoundError: No module named 'fastapi'`**
You installed `grainline` without the web extra. Run
`pip install "grainline[web]"`.

**Web UI loads but looks unstyled**
It shouldn't — the page is fully self-contained with no CDN references. If this
happens, a proxy is rewriting the response. Bypass it, or use the CLI.

**Import produces zero shapes**
Not an install problem. See the Troubleshooting section of
[HOW-TO.md](HOW-TO.md#8-troubleshooting) — start with
`grainline inspect yourfile.dxf`.
