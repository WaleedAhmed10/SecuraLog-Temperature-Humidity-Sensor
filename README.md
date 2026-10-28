# Temperature & Humidity Sensor Logger

Connects to a Fluke temperature & humidity sensor, shows live readings, and saves them to daily CSV files (opens in Excel).

## Features

- Auto-detects and connects to the sensor; reconnects if unplugged.
- Live table for two probes (Temp 1/Hum 1, Temp 2/Hum 2), one tab per day.
- Auto-saves every 30 seconds to `Fluke_Log_YYYY-MM-DD.csv`.
- **Security Dashboard** tab and **Demo Mode** (sample data, no sensor needed).

## Security Controls

| Principle | Function | What it does |
|---|---|---|
| Input validation | `sanitize_serial_input()` | Rejects serial lines that are oversized or contain non-printable characters |
| Injection prevention | `sanitize_csv_field()` | Neutralizes values starting with `=`, `+`, `-`, `@` so Excel never runs them as formulas |
| Path validation | `is_valid_date_str()` | Accepts only strict `YYYY-MM-DD` dates when building file paths |
| Integrity | `sha256_of_file()`, `write_checksum_file()`, `verify_checksum_file()` | SHA-256 `.sha256` file per log/backup; warns on mismatch at load or on demand |
| Least privilege | `secure_file_permissions()` | Logs, backups, checksums and audit log set to owner-only (`chmod 600`) |
| Auditability | `audit_log()` | Timestamped `audit_log.txt`: connections, rejections, edits, integrity checks, shutdown |
| Availability | existing | Auto-reconnect, auto-save and backups keep logging through failures |

Together these give defense in depth: no single control is relied on alone. `chmod 600` has full effect on Linux/Mac; on Windows, NTFS manages permissions.

### Security Dashboard

Live counters (validated/rejected packets, sanitized fields, integrity pass/fail), a per-file integrity table, a **Run Full Integrity Check Now** button, and a live tail of `audit_log.txt`.

### Demo Mode

**Start Demo Mode (No Hardware)** sends synthetic readings through the same validation pipeline as real data. Every ~9th packet is deliberately malformed so you can watch it get rejected.

## Which version?

| File | Python | Use on |
|---|---|---|
| `automated_data_log_x64.py` | 3 | Windows 7/8/10/11, Linux, Mac (most users) |
| `automated_data_log_x32.py` | 2.7 | Windows XP / legacy machines |

## Install & Run

Run each command from inside the project folder.

**Windows (7–11):** install Python 3 from python.org (tick **"Add Python to PATH"**), then:
```
pip install pyserial
python automated_data_log_x64.py
```

**Windows XP / Python 2.7:** install Python 2.7 (`C:\Python27`), then:
```
C:\Python27\python.exe -m ensurepip
C:\Python27\python.exe -m pip install pyserial
C:\Python27\python.exe automated_data_log_x32.py
```

**Linux (Ubuntu/Debian):**
```
sudo apt update && sudo apt install python3 python3-pip python3-tk
pip3 install pyserial
sudo usermod -aG dialout $USER     # then log out and back in
python3 automated_data_log_x64.py
```

**Mac:** install Python 3 from python.org (allow permissions if asked), then:
```
pip3 install pyserial
python3 automated_data_log_x64.py
```

## Usage

1. Plug in the sensor and click **Start Logging** (or **Start Demo Mode** with no sensor).
2. Readings appear in the day's tab; check the **Security Dashboard** for counts, file integrity and the audit log.
3. Click **Stop Logging** or close the window — data is already saved.
4. Find `Fluke_Log_YYYY-MM-DD.csv` (plus its `.sha256`) next to the program.

### Demo walkthrough

1. Start Demo Mode and watch the validated-packets counter rise.
2. Wait for a malformed packet — see the rejected counter and audit log entry.
3. Double-click a cell, enter `=1+1` — it is stored as text (`'=1+1`).
4. Close the app, edit a byte in a `Fluke_Log_*.csv`, relaunch — "Integrity Check Failed" appears.
5. Click **Run Full Integrity Check Now**, then review the audit log.

## Troubleshooting

- **Sensor not detected / port in use:** reseat the cable and close any other program using the sensor.
- **"python is not recognized":** reinstall Python with "Add Python to PATH" ticked.
- **Windows security warning:** More info → Run anyway.
- **"Integrity Check Failed":** a log was edited outside the program. Verify the file, then click **Run Full Integrity Check Now** to refresh its checksum.

## Build .exe (optional)

```
pip install pyinstaller
pyinstaller automated_data_log.spec                      # Python 3 (x64)

C:\Python27\python.exe -m pip install "pyinstaller<4"    # Python 2.7 (x32)
C:\Python27\Scripts\pyinstaller.exe automated_data_log_x32.spec
```
Output appears in `dist/`. Existing `dist/` builds predate the security update — rebuild to include it.

## Files

```
automated_data_log_x64.py / _x32.py   Logger (Python 3 / 2.7)
automated_data_log.spec / _x32.spec   PyInstaller build settings
Fluke_Log_*.csv (+ .sha256)           Daily logs and checksums
BACKUP_Fluke_Log_*.csv                Periodic backups
audit_log.txt                         Security activity log (created on first run)
LICENSE                               GPLv3
```

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).