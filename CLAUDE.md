# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TahtaKilit** (alternative name: Nöbetçi) is a Windows-only smart board security and application-tracking system for Turkish schools. It combines kiosk-mode OS lockdown, face-recognition-based authentication, and background process telemetry into a single Python application compiled to an executable via PyInstaller.

The UI and variable names are in Turkish. Documentation files (PRD.md, TECH_DESIGN.md, DEEP_RESEARCH.md) are in English.

**Target platform:** Windows 10/11 (64-bit), admin privileges required. Do not add Linux/macOS compatibility — this project depends on `pywin32` and Windows registry APIs.

---

## Running the Code

There is no `requirements.txt` yet. Install dependencies manually:

```bash
pip install opencv-contrib-python face_recognition dlib pillow pywin32 numpy
```

> `face_recognition` requires a C++ compiler and cmake for dlib. On Windows: install Visual Studio Build Tools first.

Run the UI prototype (no camera needed):

```bash
python app.py
```

Run the working face-recognition demo (requires a webcam and `ogretmen.jpg` in the same directory):

```bash
python school_lock.py
```

Exit the demo safely with `Ctrl+Shift+Q` (the kiosk blocks Alt+F4 and Escape).

Build to a single executable (run from the project root, once `build.py` exists):

```bash
pyinstaller --noconfirm --onedir --windowed --uac-admin \
  --add-data "profiles;profiles" \
  --name "TahtaKilitCore" "src/watchdog/watchdog.py"
```

---

## Architecture

The system is a **dual-process watchdog architecture**:

```
Windows Registry (HKCU Shell)
    └─► WatchdogCore.exe          ← spawns and monitors the GUI process
            └─► KilitArayuzu.exe  ← Tkinter fullscreen GUI
                    ├─ Thread 1: Face recognition loop (OpenCV / face_recognition)
                    └─ Thread 2: Telemetry monitor (win32gui + psutil)
```

The two processes communicate via **Windows Named Pipes** (`\\.\pipe\TahtaKilitIPC`). The GUI sends three signal types:
- `HEARTBEAT` — app is alive, maintain lockdown
- `AUTH_SUCCESS` — face matched, watchdog temporarily unlocks Task Manager
- `LOCK_COMMAND` — teacher manually locked; reactivate all hardening

### Planned directory structure (from TECH_DESIGN.md)

```
src/
  watchdog/watchdog.py     # Named Pipe server, process monitor, recovery engine
  gui/app.py               # Tkinter fullscreen controller (currently at root: app.py)
  gui/biometric.py         # Face verification worker thread
  gui/telemetry.py         # Background win32 telemetry loop
  utils/registry_ops.py    # Registry shell swap + TaskMgr disable/enable
  utils/crypto_ops.py      # Vector encryption
  config.py                # Constants, bypass keys, file paths
profiles/vectors.dat       # Encrypted 128-D face vectors (no raw images stored)
logs/telemetry_[teacher]_[date].jsonl
build.py                   # PyInstaller orchestration
```

Most of this does not exist yet — see **Current State** below.

---

## Current State of the Codebase

Two proof-of-concept files exist at the root:

### `app.py` — UI prototype (Phase 1)
- Two-state Tkinter fullscreen app: `LockScreen` ↔ `DashboardScreen`
- The `Theme` class centralises all design tokens (colors, fonts, spacing). Always use `Theme.*` constants rather than hardcoding values.
- The lock screen's camera frame is a placeholder. The line tagged `# TEMP-AUTH-HOOK` simulates a successful auth on click. **Replace this hook in Phase 2** with a real signal from `biometric.py`.
- ESC exits fullscreen in dev mode. This binding must be removed before production.

### `school_lock.py` — functional MVP demo
- Uses OpenCV LBPH (not dlib) for face recognition against a single reference photo (`ogretmen.jpg`).
- Requires `GEREKLI_ESLESME` (default 5) consecutive matching frames before unlocking.
- Background thread (`log_dongusu`) samples the foreground window every `LOG_ARALIGI_SN` seconds and writes cumulative usage time to `kullanim_log.txt`.
- Calls `os._exit(0)` in `guvenli_cikis` to kill the process cleanly including daemon threads.

**Note:** `school_lock.py` uses OpenCV's lightweight LBPH recogniser. The production architecture (TECH_DESIGN.md Phase 3) upgrades this to `face_recognition` (dlib ResNet-34) for better accuracy under varying classroom lighting.

---

## Key Conventions

### OS Hardening
- Shell replacement targets `HKCU\Software\Microsoft\Windows NT\CurrentVersion\Winlogon` → `Shell` value.
- Task Manager disabling targets `HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System` → `DisableTaskMgr`.
- Always restore `explorer.exe` before the process exits, or provide the USB bypass key mechanism as a recovery path.

### Face Recognition Pipeline (production target)
1. Downscale captured frame to 1/4 size before calling `face_recognition.face_encodings()`.
2. Compare against 128-D vectors stored in `profiles/vectors.dat` (pickle/json, encrypted with a machine-specific key).
3. Raw reference images are not stored after the enrolment step.

### Telemetry Schema
Each log line is JSONL:
```json
{"timestamp": "...", "process": "chrome.exe", "window_title": "...", "cpu_percent": 0.4}
```
Logs rotate daily with a 30-day TTL.

### UI Design Constraints
- All font sizes ≥ 24pt (`Theme.SIZE_BODY`). The display is a large touchscreen.
- Touch targets must use generous `padx`/`pady` — see `DashboardScreen`'s lock button as the reference.
- Lock state uses Crimson (`#DC143C`) accents; authenticated state uses Emerald (`#0F9D58`).
- No stock titles, console windows, or debug panels in production builds.

### Build / Compilation
- The PyInstaller `.spec` must include `uac_admin=True` to request administrator elevation on launch.
- Target: `--onedir` (not `--onefile`) to keep startup latency low when replacing the Windows shell.
- Azure Trusted Signing is required before deploying to school networks to pass SmartScreen.
