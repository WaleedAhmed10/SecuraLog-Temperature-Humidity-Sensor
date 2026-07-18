# Temperature & Humidity Sensor Logger — RSPK Services Lab

This software connects to a Fluke temperature & humidity sensor plugged into your computer, shows live readings on screen, and automatically saves them into daily files you can open in Excel. This guide explains, step by step, how to install and run it — no coding knowledge needed.

## What this software does

- Automatically finds and connects to the sensor — you don't need to pick a port or type anything.
- Shows live Temperature and Humidity readings in a table, updated automatically.
- Supports two probes at once (Temp 1/Hum 1 and Temp 2/Hum 2).
- Saves your data automatically every 30 seconds, so you never lose readings.
- Creates one file per day, named like `Fluke_Log_2026-07-18.csv`, which opens directly in Excel.
- Keeps working even if the sensor is unplugged and plugged back in.

## Which version do I need?

This project comes in two versions of the same program. Use whichever matches the computer you're installing it on:

| File | For | Use this if... |
|---|---|---|
| `automated_data_log_x64.py` | Python 3 (modern computers) | You're on Windows 7, 8, 10, 11, or any recent Linux/Mac — **this is the one most people need.** |
| `automated_data_log_x32.py` | Python 2.7 (legacy) | You're on an old computer running **Windows XP** or another machine that only has Python 2.7 installed. |

If you already have a ready-made `.exe` file (in the `dist` folder), you can skip all the installation steps below — just double-click it. The steps below are only needed if you're installing from the source code files.

---

## Installing on Windows (7, 8, 10, 11)

### Step 1: Install Python

1. Open your internet browser and go to **https://www.python.org/downloads/**
2. Click the yellow **"Download Python"** button (this gets the latest Python 3 version).
3. Once downloaded, double-click the installer file to open it.
4. **Important:** On the very first installer screen, tick the checkbox at the bottom that says **"Add Python to PATH"**. This step is easy to miss but very important.
5. Click **"Install Now"** and wait for it to finish.
6. Click **Close** when done.

### Step 2: Check Python installed correctly

1. Press the **Windows key**, type `cmd`, and press **Enter** to open the Command Prompt (a black text window).
2. Type the following and press Enter:
   ```
   python --version
   ```
3. You should see something like `Python 3.12.0`. If instead you see an error, restart your computer and try again (this refreshes the PATH settings from Step 1).

### Step 3: Install the required add-ons (libraries)

Still in the same Command Prompt window, type this and press Enter:
```
pip install pyserial pillow
```
Wait for it to finish downloading and installing (this can take a minute or two).

### Step 4: Run the program

1. In the Command Prompt, navigate to the project folder. For example, if the folder is on your Desktop:
   ```
   cd Desktop\Temperature-Humidity-Sensor-RSPK-Lab
   ```
2. Run the program by typing:
   ```
   python automated_data_log_x64.py
   ```
3. The program window should open. Plug in your sensor (if not already connected) and click **Start Logging**.

---

## Installing on Windows XP (or any PC with only Python 2.7)

Older machines that can't run modern Python need the legacy version instead.

### Step 1: Install Python 2.7

1. Download Python 2.7 from **https://www.python.org/downloads/release/python-2718/** (scroll down to find the Windows installer, e.g. "Windows x86 MSI installer").
2. Double-click the downloaded file and follow the installer, clicking **Next** through the default options.
3. By default this installs Python into `C:\Python27`.

### Step 2: Add Python to your system so it's easy to run

1. Right-click **My Computer** (or **This PC**) → **Properties** → **Advanced system settings** → **Environment Variables**.
2. Under "System variables", find **Path**, click **Edit**, and add `;C:\Python27` and `;C:\Python27\Scripts` to the end of the existing text.
3. Click OK on all windows to save.

### Step 3: Install pip (Python's installer tool) if it's missing

1. Open Command Prompt (Start → type `cmd` → Enter).
2. Type:
   ```
   C:\Python27\python.exe -m ensurepip
   ```

### Step 4: Install the required add-ons

```
C:\Python27\python.exe -m pip install pyserial pillow
```

### Step 5: Run the program

1. In Command Prompt, go to the project folder, for example:
   ```
   cd Desktop\Temperature-Humidity-Sensor-RSPK-Lab
   ```
2. Run:
   ```
   C:\Python27\python.exe automated_data_log_x32.py
   ```
3. Plug in the sensor and click **Start Logging**.

---

## Installing on Linux (Ubuntu/Debian and similar)

### Step 1: Open a Terminal

Look for "Terminal" in your applications menu and open it.

### Step 2: Install Python and the required system packages

Copy and paste this line, then press Enter (it may ask for your password):
```
sudo apt update && sudo apt install python3 python3-pip python3-tk
```

### Step 3: Install the required add-ons

```
pip3 install pyserial pillow
```

### Step 4: Allow the program to access the sensor's USB port

```
sudo usermod -aG dialout $USER
```
After running this, **log out and log back in** (or restart the computer) for it to take effect.

### Step 5: Run the program

1. In the Terminal, go to the project folder, for example:
   ```
   cd ~/Desktop/Temperature-Humidity-Sensor-RSPK-Lab
   ```
2. Run:
   ```
   python3 automated_data_log_x64.py
   ```
3. Plug in the sensor and click **Start Logging**.

---

## Installing on Mac

### Step 1: Install Python

1. Go to **https://www.python.org/downloads/** in your browser.
2. Click **Download Python** and open the downloaded installer.
3. Follow the on-screen steps, clicking **Continue** and **Install**.

### Step 2: Open the Terminal

Press **Command + Space**, type `Terminal`, and press Enter.

### Step 3: Install the required add-ons

```
pip3 install pyserial pillow
```

### Step 4: Give the Terminal access to your files (if asked)

If macOS asks for permission to access files or connected devices the first time you run the program, click **Allow**.

### Step 5: Run the program

1. In Terminal, go to the project folder, for example:
   ```
   cd ~/Desktop/Temperature-Humidity-Sensor-RSPK-Lab
   ```
2. Run:
   ```
   python3 automated_data_log_x64.py
   ```
3. Plug in the sensor and click **Start Logging**.

---

## Using the software (all platforms)

1. Plug in the Fluke sensor with its USB/serial cable.
2. Open the program (double-click the `.exe` on Windows, or run the command shown above).
3. Click the green **Start Logging** button — the sensor is found and connected automatically.
4. Watch live readings appear in the table: Date, Time, COM Port, Temp 1, Hum 1, Temp 2, Hum 2.
5. Each day gets its own tab at the top of the window.
6. Click the red **Stop Logging** button when finished, or simply close the window — your data is already saved automatically.
7. Find your saved readings in the same folder as the program, in files named `Fluke_Log_YYYY-MM-DD.csv` — double-click one to open it in Excel.

## Troubleshooting

**The program doesn't detect the sensor**
- Check the cable is firmly plugged in at both ends, then try unplugging and re-plugging it.
- Make sure no other program (e.g. Fluke's own software) is already connected to the sensor — close it first.

**A message says a port is "in use by another program"**
- Another copy of this program, or other sensor software, is already connected. Close it and try again.

**"python is not recognized" error on Windows**
- Python wasn't added to PATH during installation. Reinstall Python and make sure to tick **"Add Python to PATH"** on the first screen.

**A logo/image doesn't load when the program opens**
- This is just a missing image warning and won't stop sensor readings from being logged — you can close the warning and continue.

**Windows shows a security warning when opening the program**
- Click **More info → Run anyway**, or right-click the file and choose **Run as administrator**.

## Building a standalone .exe (optional, for IT/developers)

If you have the source code and want to create a ready-to-click `.exe` file (so others don't need to install Python at all), this project includes PyInstaller spec files:

```
# Python 3 (x64) build
pip install pyinstaller
pyinstaller automated_data_log.spec

# Python 2.7 (x32) build
C:\Python27\python.exe -m pip install "pyinstaller<4"
C:\Python27\Scripts\pyinstaller.exe automated_data_log_x32.spec
```

The finished program will appear inside the `dist` folder.

## Project structure

```
├── automated_data_log_x32.py     # Python 2.7 version of the logger
├── automated_data_log_x64.py     # Python 3 version of the logger
├── automated_data_log_x32.spec   # PyInstaller build settings (x32)
├── automated_data_log.spec       # PyInstaller build settings (x64)
├── Pakistan.png                  # Logo used in the app header
├── R&S.jfif                      # Logo used in the app header
├── Fluke_Log_*.csv               # Daily logged sensor data
└── LICENSE                       # GPLv3
```

## License

This project is licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE) for details.