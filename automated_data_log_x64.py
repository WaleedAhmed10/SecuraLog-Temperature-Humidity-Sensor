import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, scrolledtext
import serial
import serial.tools.list_ports
import os
import sys
import time
import threading
import queue
import csv
import hashlib
import re
import random
from datetime import datetime

BAUD_RATE = 9600
PORT_SCAN_INTERVAL = 20
PORT_RETRY_COOLDOWN = 30
MAX_LINE_LENGTH = 500
ALLOWED_LINE_CHARS = re.compile(r'^[\x20-\x7E]*$')  # printable ASCII only

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

AUDIT_LOG_PATH = os.path.join(BASE_DIR, "audit_log.txt")

msg_queue = queue.Queue()

SECURITY_STATS_LOCK = threading.Lock()
SECURITY_STATS = {
    "packets_validated": 0,
    "packets_rejected_length": 0,
    "packets_rejected_chars": 0,
    "csv_fields_sanitized": 0,
    "integrity_checks_pass": 0,
    "integrity_checks_fail": 0,
}


def bump_stat(key, amount=1):
    """Thread-safe increment of a live security counter."""
    with SECURITY_STATS_LOCK:
        SECURITY_STATS[key] = SECURITY_STATS.get(key, 0) + amount


def get_stats_snapshot():
    """Thread-safe read of all live security counters."""
    with SECURITY_STATS_LOCK:
        return dict(SECURITY_STATS)


def sanitize_serial_input(raw_line):
    """Input validation for data coming from the serial port.

    Rejects lines that are too long or that contain control/non-printable
    characters before they ever reach the parser. This is basic defense
    against a malfunctioning, spoofed, or malicious serial device sending
    unexpected data (e.g. escape sequences, huge payloads)."""
    if not raw_line:
        return None
    if len(raw_line) > MAX_LINE_LENGTH:
        bump_stat("packets_rejected_length")
        audit_log(f"[SECURITY] Rejected oversized serial line ({len(raw_line)} chars)")
        return None
    if not ALLOWED_LINE_CHARS.match(raw_line):
        bump_stat("packets_rejected_chars")
        audit_log("[SECURITY] Rejected serial line with non-printable characters")
        return None
    bump_stat("packets_validated")
    return raw_line


def sanitize_csv_field(value):
    """CSV/formula-injection protection.

    Excel, LibreOffice and Google Sheets will treat a cell starting with
    =, +, - or @ as a formula when the CSV is opened. Since this field
    ultimately originates from an external serial device (or manual edit),
    it is untrusted input; prefix it with a single quote so spreadsheet
    software always renders it as plain text instead of executing it."""
    text = "" if value is None else str(value)
    if text and text[0] in ('=', '+', '-', '@'):
        bump_stat("csv_fields_sanitized")
        return "'" + text
    return text


def is_valid_date_str(date_str):
    """Validates a date string before it is used to build a filename,
    preventing path traversal / directory injection via a crafted value."""
    if not isinstance(date_str, str):
        return False
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%Y-%m-%d') == date_str
    except ValueError:
        return False


def sha256_of_file(path):
    """Computes a SHA-256 checksum of a file for integrity/tamper detection."""
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def write_checksum_file(csv_path):
    """Writes/refreshes a .sha256 sidecar file next to a CSV log so its
    integrity can be verified later (tamper-evidence for lab data)."""
    digest = sha256_of_file(csv_path)
    if digest:
        try:
            with open(csv_path + ".sha256", "w", encoding='utf-8') as f:
                f.write(f"{digest}  {os.path.basename(csv_path)}\n")
            secure_file_permissions(csv_path + ".sha256")
        except Exception:
            pass


def verify_checksum_file(csv_path):
    """Verifies a CSV log against its saved .sha256 checksum.

    Returns True (matches), False (tampered/corrupted), or None (no
    checksum on record yet, e.g. first run)."""
    checksum_path = csv_path + ".sha256"
    if not os.path.isfile(checksum_path):
        return None
    try:
        with open(checksum_path, "r", encoding='utf-8') as f:
            saved = f.read().split()[0]
        return saved == sha256_of_file(csv_path)
    except Exception:
        return None


def secure_file_permissions(path):
    """Restricts a log/backup file to owner read+write only. Defense in
    depth against other local accounts on the same machine reading or
    tampering with logged lab data."""
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def audit_log(event):
    """Appends a timestamped entry to a local audit trail (connections,
    manual edits, integrity failures) so activity on the log files is
    traceable after the fact."""
    try:
        with open(AUDIT_LOG_PATH, "a", encoding='utf-8') as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {event}\n")
        secure_file_permissions(AUDIT_LOG_PATH)
    except Exception:
        pass


def parse_fluke_data(raw_line):
    parts = [p.strip() for p in raw_line.split(',')]
    date_str = datetime.now().strftime('%Y-%m-%d')
    time_str = datetime.now().strftime('%H:%M:%S')
    data_parts = parts
    
    if len(parts) > 0 and " " in parts[0] and ":" in parts[0]:
        dt_parts = parts[0].split(" ", 1)
        date_str = dt_parts[0]
        time_str = dt_parts[1]
        data_parts = parts[1:] 

    temp1, hum1, temp2, hum2 = "", "", "", ""
    
    if len(data_parts) >= 4:
        temp1 = f"{data_parts[0]} {data_parts[1]}" 
        hum1 = f"{data_parts[2]} {data_parts[3]}"  
        
    if len(data_parts) >= 8:
        t2_val, t2_unit, h2_val, h2_unit = data_parts[4:8]
        try:
            if float(t2_val) != 0.0 or float(h2_val) != 0.0:
                temp2 = f"{t2_val} {t2_unit}"
                hum2 = f"{h2_val} {h2_unit}"
        except ValueError:
            temp2 = f"{t2_val} {t2_unit}"
            hum2 = f"{h2_val} {h2_unit}"

    return date_str, time_str, temp1, hum1, temp2, hum2

def get_available_ports():
    """Return a sorted list of COM port device names currently visible to Windows.

    Using serial.tools.list_ports instead of blindly probing COM1-COM32 makes the
    logger work on any Windows machine regardless of how its ports are numbered
    (some systems, especially with several USB-serial adapters, assign port
    numbers well above 32).
    """
    ports = [p.device for p in serial.tools.list_ports.comports()]

    def sort_key(name):
        digits = ''.join(ch for ch in name if ch.isdigit())
        return int(digits) if digits else 0

    return sorted(ports, key=sort_key)


def listen_to_port(port_name, port_stop_event):
    ser = None
    close_reason = "stopped"
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        msg_queue.put({"type": "status", "msg": f"[SUCCESS] Connected to {port_name}."})
        audit_log(f"[CONNECTED] {port_name}")

        while not port_stop_event.is_set():
            try:
                if ser.in_waiting > 0:
                    raw_line = ser.readline().decode('utf-8', errors='ignore').strip()
                    raw_line = sanitize_serial_input(raw_line)  # input validation
                    if raw_line:
                        parsed_data = parse_fluke_data(raw_line)
                        msg_queue.put({"type": "data", "port": port_name,
                            "parsed": parsed_data
                        })
            except (OSError, serial.SerialException) as e:
                msg_queue.put({"type": "status", "msg": f"[DISCONNECTED] {port_name} removed."})
                audit_log(f"[DISCONNECTED] {port_name}: {e}")
                close_reason = "disconnected"
                break
            time.sleep(0.1)

    except serial.SerialException as e:
        msg = str(e)
        if "Access is denied" in msg or "PermissionError" in msg:
            msg_queue.put({"type": "status", "msg": f"[BLOCKED] {port_name} is in use by another program."})
        else:
            msg_queue.put({"type": "status", "msg": f"[ERROR] Could not open {port_name}: {msg}"})
        close_reason = "open_failed"

    except Exception as e:
        msg_queue.put({"type": "status", "msg": f"[ERROR] Unexpected error on {port_name}: {e}"})
        close_reason = "open_failed"

    finally:
        try:
            if ser is not None and ser.is_open:
                ser.close()
        except Exception:
            pass
        msg_queue.put({"type": "port_closed", "port": port_name, "reason": close_reason})


def demo_data_generator(stop_event):
    """Generates synthetic Fluke-format readings so the whole app - including
    its security controls - can be demonstrated without any physical sensor
    or serial cable attached.
    Every reading is pushed through the exact same sanitize_serial_input() /
    parse_fluke_data() pipeline a real device's data would go through, so
    this is a faithful demo, not a shortcut around the security logic. Every
    9th cycle intentionally sends a deliberately oversized/garbled line so a
    viewer can watch input validation reject it in real time."""
    cycle = 0
    while not stop_event.is_set():
        cycle += 1
        if cycle % 9 == 0:
            # Deliberately malformed sample - demonstrates input validation.
            bad_line = "".join(random.choice("\x00\x01\x02#$%") for _ in range(40)) + ("Z" * 600)
            result = sanitize_serial_input(bad_line)
            if result is None:
                msg_queue.put({"type": "status",
                    "msg": "[DEMO] Simulated malformed packet was rejected by input validation."})
        else:
            t1 = round(random.uniform(18.0, 26.0), 1)
            h1 = round(random.uniform(30.0, 60.0), 1)
            t2 = round(random.uniform(18.0, 26.0), 1)
            h2 = round(random.uniform(30.0, 60.0), 1)
            line = f"{t1} C,{h1} %RH,{t2} C,{h2} %RH"
            raw_line = sanitize_serial_input(line)
            if raw_line:
                parsed_data = parse_fluke_data(raw_line)
                msg_queue.put({"type": "data", "port": "DEMO-SIM", "parsed": parsed_data})
        time.sleep(2)


class FlukeLoggerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Secura Temperature and Humidity Sensor")
        self.root.geometry("1050x700")
        self.current_date = datetime.now().strftime('%Y-%m-%d')
        self.trees = {}
        self.logging_active = False
        self.port_threads = {}
        self.port_cooldowns = {}
        
        self.auto_save_interval = 30000
        self.last_save_time = {}
        self.pending_save = False
        
        self.csv_files = {} 
        self.csv_writers = {} 

        self.demo_mode_active = False
        self.demo_stop_event = None
        self.demo_thread = None

        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.process_queue()        
        self.load_existing_data()
        self.auto_save_all()        
        self.root.after(100, self.start_logging)
        self.root.after(1000, self.refresh_security_dashboard)

    def setup_ui(self):
        BG_COLOR = "grey"
        LOGS_BG = "white"
        TEXT_COLOR = "dark blue"
        
        self.root.configure(bg=BG_COLOR)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=BG_COLOR, borderwidth=0, tabmargins=[0, 0, 0, 0])
        style.configure("TNotebook.Tab", background="#A0A0A0", foreground="black",
                        font=("Segoe UI", 10, "bold"), padding=[16, 8])
        style.map("TNotebook.Tab",
                  background=[("selected", LOGS_BG), ("active", "#A0A0A0")],
                  foreground=[("selected", TEXT_COLOR)])
        style.configure("TFrame", background=BG_COLOR)
        style.configure("Treeview", background=LOGS_BG, fieldbackground=LOGS_BG,
                        foreground="black", rowheight=32, borderwidth=0,
                        font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background="#A0A0A0", foreground="black",
                        font=("Segoe UI", 10, "bold"), padding=[8, 8])
        style.map("Treeview",
                  background=[("selected", TEXT_COLOR)],
                  foreground=[("selected", LOGS_BG)])
        style.configure("TLabel", background=BG_COLOR, foreground=TEXT_COLOR,
                        font=("Segoe UI", 10))
        style.configure("TLabelframe", background=BG_COLOR, foreground=TEXT_COLOR,
                        bordercolor="#A0A0A0")
        style.configure("TLabelframe.Label", background=BG_COLOR, foreground=TEXT_COLOR,
                        font=("Segoe UI", 10, "bold"))
        style.configure("Card.TLabelframe", background=LOGS_BG, foreground=TEXT_COLOR,
                        bordercolor="#A0A0A0", relief="solid")
        style.configure("Card.TLabelframe.Label", background=LOGS_BG, foreground=TEXT_COLOR,
                        font=("Segoe UI", 10, "bold"))

        header_frame = tk.Frame(self.root, bg=TEXT_COLOR, padx=24, pady=18)
        header_frame.pack(fill=tk.X, padx=18, pady=(18, 12))

        title_lbl = tk.Label(header_frame, text="Secura Temperature and Humidity Sensor",
                             font=("Segoe UI", 21, "bold"), fg=LOGS_BG, bg=TEXT_COLOR)
        title_lbl.pack(side=tk.TOP, anchor=tk.W)

        btn_frame = tk.Frame(self.root, bg=LOGS_BG, padx=12, pady=12)
        btn_frame.pack(fill=tk.X, padx=18, pady=(0, 8))

        button_options = {
            "fg": "white",
            "font": ("Segoe UI", 10, "bold"),
            "relief": tk.FLAT,
            "bd": 0,
            "padx": 14,
            "pady": 9,
            "cursor": "hand2",
        }
        self.start_btn = tk.Button(btn_frame, text="Start Logging", command=self.start_logging,
                                   bg="green", width=15, **button_options)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = tk.Button(btn_frame, text="Stop Logging", command=self.stop_logging,
                                  bg="red", width=15, state=tk.DISABLED, **button_options)
        self.stop_btn.pack(side=tk.LEFT, padx=8)
        self.demo_btn = tk.Button(btn_frame, text="Start Demo Mode (No Hardware)",
                                  command=self.toggle_demo_mode, bg="purple", width=28,
                                  **button_options)
        self.demo_btn.pack(side=tk.LEFT, padx=8)
        
        self.status_label = tk.Label(btn_frame, text="Auto-save: Active",
                                     font=("Segoe UI", 9, "bold"), fg="dark green",
                                     bg=LOGS_BG, padx=8)
        self.status_label.pack(side=tk.RIGHT, padx=8)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill=tk.BOTH, padx=18, pady=(0, 18))
        self.add_security_dashboard_tab()
        self.add_day_tab(self.current_date)

    def add_day_tab(self, date_str):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text=date_str)
        
        columns = ("Date", "Time", "COM_Port", "Temp_1", "Hum_1", "Temp_2", "Hum_2")
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        
        tree.heading("Date", text="Date")
        tree.heading("Time", text="Time")
        tree.heading("COM_Port", text="COM Port")
        tree.heading("Temp_1", text="Temp 1")
        tree.heading("Hum_1", text="Hum 1")
        tree.heading("Temp_2", text="Temp 2")
        tree.heading("Hum_2", text="Hum 2")
        
        tree.column("Date", width=100, anchor=tk.CENTER)
        tree.column("Time", width=100, anchor=tk.CENTER)
        tree.column("COM_Port", width=90, anchor=tk.CENTER)
        tree.column("Temp_1", width=120, anchor=tk.CENTER)
        tree.column("Hum_1", width=120, anchor=tk.CENTER)
        tree.column("Temp_2", width=120, anchor=tk.CENTER)
        tree.column("Hum_2", width=120, anchor=tk.CENTER)
        
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.bind("<Double-1>", self.on_double_click)        
        self.trees[date_str] = tree
        self.last_save_time[date_str] = time.time()
        
        self.open_csv_file(date_str)        
        self.notebook.select(frame)

    def add_security_dashboard_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Security Dashboard")

        intro = tk.Label(frame, text="Infosecurity Controls — Live Status",
                         font=("Segoe UI", 14, "bold"), anchor="w",
                         bg="grey", fg="dark blue")
        intro.pack(fill=tk.X, padx=14, pady=(14, 2))

        stats_frame = ttk.LabelFrame(frame, text="Input Validation & Injection Prevention",
                                     style="Card.TLabelframe", padding=10)
        stats_frame.pack(fill=tk.X, padx=14, pady=8)

        self.stat_labels = {}
        stat_rows = [
            ("packets_validated", "Packets validated & accepted"),
            ("packets_rejected_length", "Packets rejected (oversized)"),
            ("packets_rejected_chars", "Packets rejected (invalid characters)"),
            ("csv_fields_sanitized", "CSV/formula-injection fields sanitized"),
            ("integrity_checks_pass", "Integrity checks passed"),
            ("integrity_checks_fail", "Integrity checks FAILED"),
        ]
        for i, (key, label) in enumerate(stat_rows):
            r, c = divmod(i, 2)
            row_frame = tk.Frame(stats_frame, bg="white")
            row_frame.grid(row=r, column=c, sticky="w", padx=15, pady=6)
            tk.Label(row_frame, text=label + ":", font=("Segoe UI", 9),
                     fg="dark blue", bg="white").pack(side=tk.LEFT)
            val_lbl = tk.Label(row_frame, text="0", font=("Segoe UI", 10, "bold"),
                               fg="dark blue", bg="white")
            val_lbl.pack(side=tk.LEFT, padx=(7, 0))
            self.stat_labels[key] = val_lbl

        controls_frame = tk.Frame(frame, bg="grey")
        controls_frame.pack(fill=tk.X, padx=14, pady=(0, 8))
        tk.Button(controls_frame, text="Run Full Integrity Check Now",
                  command=self.run_integrity_check_now, bg="dark blue", fg="white",
                  font=("Segoe UI", 9, "bold"), relief=tk.FLAT, bd=0,
                  padx=14, pady=8, cursor="hand2").pack(side=tk.LEFT, padx=(0, 10))

        integrity_frame = ttk.LabelFrame(frame, text="File Integrity Status (SHA-256)",
                                         style="Card.TLabelframe", padding=8)
        integrity_frame.pack(fill=tk.X, padx=14, pady=(0, 8))
        self.integrity_tree = ttk.Treeview(integrity_frame, columns=("date", "status"),
                                            show="headings", height=4)
        self.integrity_tree.heading("date", text="Date / File")
        self.integrity_tree.heading("status", text="Status")
        self.integrity_tree.column("date", width=260, anchor=tk.W)
        self.integrity_tree.column("status", width=220, anchor=tk.CENTER)
        self.integrity_tree.pack(fill=tk.X, padx=8, pady=6)

        audit_frame = ttk.LabelFrame(frame, text="Audit Log (live tail — audit_log.txt)",
                                     style="Card.TLabelframe", padding=8)
        audit_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))
        self.audit_text = scrolledtext.ScrolledText(
            audit_frame, height=8, font=("Consolas", 9), state=tk.DISABLED,
            wrap=tk.NONE, bg="white", fg="dark blue", insertbackground="dark blue",
            selectbackground="dark blue", selectforeground="white", relief=tk.FLAT,
            padx=8, pady=8
        )
        self.audit_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

    def refresh_security_dashboard(self):
        """Periodically refreshes the counters, integrity table, and audit
        log tail on the Security Dashboard tab."""
        try:
            stats = get_stats_snapshot()
            for key, lbl in self.stat_labels.items():
                value = stats.get(key, 0)
                lbl.config(text=str(value))
                if key == "integrity_checks_fail":
                    lbl.config(fg="red" if value > 0 else "dark blue")

            for row in self.integrity_tree.get_children():
                self.integrity_tree.delete(row)
            for date_str in sorted(self.trees.keys()):
                filename = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")
                if not os.path.isfile(filename):
                    continue
                result = verify_checksum_file(filename)
                if result is True:
                    status_text, tag = "OK — verified", "ok"
                elif result is False:
                    status_text, tag = "MISMATCH — possible tampering", "bad"
                else:
                    status_text, tag = "No checksum yet", "unknown"
                self.integrity_tree.insert("", tk.END, values=(f"Fluke_Log_{date_str}.csv", status_text), tags=(tag,))
            self.integrity_tree.tag_configure("ok", foreground="dark green")
            self.integrity_tree.tag_configure("bad", foreground="red")
            self.integrity_tree.tag_configure("unknown", foreground="grey")

            if os.path.isfile(AUDIT_LOG_PATH):
                with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-200:]
                self.audit_text.config(state=tk.NORMAL)
                self.audit_text.delete("1.0", tk.END)
                self.audit_text.insert(tk.END, "".join(lines))
                self.audit_text.see(tk.END)
                self.audit_text.config(state=tk.DISABLED)
        except Exception as e:
            print(f"Dashboard refresh error: {e}")

        self.root.after(2000, self.refresh_security_dashboard)

    def run_integrity_check_now(self):
        """Manually verifies every known CSV log against its saved SHA-256
        checksum and reports the result. A failed check must not replace the
        saved checksum, so the mismatch remains detectable on later checks."""
        checked = 0
        failed = 0
        for date_str in list(self.trees.keys()):
            if not is_valid_date_str(date_str):
                continue
            filename = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")
            if not os.path.isfile(filename):
                continue
            result = verify_checksum_file(filename)
            checked += 1
            if result is False:
                failed += 1
                bump_stat("integrity_checks_fail")
                audit_log(f"[SECURITY] Manual integrity check FAILED for {filename}")
            elif result is True:
                bump_stat("integrity_checks_pass")
            else:
                write_checksum_file(filename)
        audit_log(f"[SECURITY] Manual integrity check complete: {checked} file(s) checked, {failed} failure(s)")
        messagebox.showinfo("Integrity Check Complete",
            f"Checked {checked} file(s).\n{failed} integrity failure(s) detected.\n"
            f"See the Security Dashboard and audit_log.txt for details.")
        self.refresh_security_dashboard()

    def toggle_demo_mode(self):
        if self.demo_mode_active:
            self.stop_demo_mode()
        else:
            self.start_demo_mode()

    def start_demo_mode(self):
        if self.demo_mode_active:
            return
        self.demo_mode_active = True
        self.demo_stop_event = threading.Event()
        self.demo_thread = threading.Thread(target=demo_data_generator,
                                             args=(self.demo_stop_event,), daemon=True)
        self.demo_thread.start()
        self.demo_btn.config(text="Stop Demo Mode", bg="dark red")
        self.status_label.config(text="Demo Mode Active — simulated data, no hardware", fg="purple")
        audit_log("[DEMO] Simulation mode started (no hardware)")

    def stop_demo_mode(self):
        if not self.demo_mode_active:
            return
        self.demo_mode_active = False
        if self.demo_stop_event:
            self.demo_stop_event.set()
        self.demo_btn.config(text="Start Demo Mode (No Hardware)", bg="purple")
        self.status_label.config(text="Demo Mode Stopped", fg="orange")
        audit_log("[DEMO] Simulation mode stopped")

    def open_csv_file(self, date_str):
        """Open or create CSV file for the given date"""
        if not is_valid_date_str(date_str):  # security: reject unexpected filenames
            audit_log(f"[SECURITY] Rejected invalid date_str for CSV open: {date_str!r}")
            return
        filename = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")
        
        file_exists = os.path.isfile(filename)        
        csv_file = open(filename, mode='a', newline='', encoding='utf-8')
        writer = csv.writer(csv_file)
        
        if not file_exists:
            writer.writerow(['Date', 'Time', 'COM_Port', 'Temp_1', 'Hum_1', 'Temp_2', 'Hum_2'])
            csv_file.flush()

        secure_file_permissions(filename)  # security: owner-only file permissions
        self.csv_files[date_str] = csv_file
        self.csv_writers[date_str] = writer

    def load_existing_data(self):
        """Load existing data from CSV files into the treeview"""
        for date_str in list(self.trees.keys()):
            if not is_valid_date_str(date_str):
                continue
            filename = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")
            if os.path.isfile(filename):
                integrity = verify_checksum_file(filename)
                if integrity is False:
                    bump_stat("integrity_checks_fail")
                    audit_log(f"[SECURITY] Checksum mismatch on load: {filename}")
                    messagebox.showwarning(
                        "Integrity Check Failed",
                        f"The log file for {date_str} does not match its saved checksum.\n"
                        f"It may have been modified outside this application."
                    )
                elif integrity is True:
                    bump_stat("integrity_checks_pass")
                try:
                    with open(filename, 'r', encoding='utf-8') as file:
                        reader = csv.reader(file)
                        next(reader, None)
                        tree = self.trees[date_str]
                        for row in reader:
                            if len(row) >= 7:
                                tree.insert("", tk.END, values=row[:7])
                except Exception as e:
                    print(f"Error loading data from {filename}: {e}")

    def on_double_click(self, event):
        current_tab_id = self.notebook.select()
        current_date_str = self.notebook.tab(current_tab_id, "text")
        active_tree = self.trees[current_date_str]
        
        region = active_tree.identify("region", event.x, event.y)
        if region != "cell": return
        selected_iid = active_tree.focus()
        column = active_tree.identify_column(event.x)
        col_index = int(column[1:]) - 1 
        
        current_value = active_tree.item(selected_iid, 'values')[col_index]
        column_name = active_tree.heading(column, 'text')
        
        new_value = simpledialog.askstring("Edit Data", f"Modify {column_name}:", initialvalue=current_value)
        if new_value is not None:
            new_value = sanitize_csv_field(new_value)  # security: block CSV/formula injection
            current_values = list(active_tree.item(selected_iid, 'values'))
            current_values[col_index] = new_value
            active_tree.item(selected_iid, values=current_values)
            self.pending_save = True
            self.last_save_time[current_date_str] = 0
            audit_log(f"[EDIT] {current_date_str} row edited: column={column_name!r} new_value={new_value!r}")

    def append_to_csv(self, date_str, row_data):
        """Append a single row to the CSV file"""
        row_data = tuple(sanitize_csv_field(v) for v in row_data)  # security: CSV injection guard
        try:
            writer = self.csv_writers.get(date_str)
            if writer:
                writer.writerow(row_data)
                self.csv_files[date_str].flush()
                return True
        except Exception as e:
            print(f"Error appending to CSV: {e}")
            self.open_csv_file(date_str)
            try:
                writer = self.csv_writers.get(date_str)
                if writer:
                    writer.writerow(row_data)
                    self.csv_files[date_str].flush()
                    return True
            except:
                pass
        return False

    def create_backup(self, date_str):
        """Create a backup of the CSV file"""
        if not is_valid_date_str(date_str):  # security: filename validation
            return False
        try:
            source_file = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")
            if os.path.isfile(source_file):
                backup_file = os.path.join(BASE_DIR, f"BACKUP_Fluke_Log_{date_str}.csv")
                with open(source_file, 'r', encoding='utf-8') as src:
                    with open(backup_file, 'w', encoding='utf-8') as dst:
                        dst.write(src.read())
                secure_file_permissions(backup_file)  # security: owner-only permissions
                write_checksum_file(backup_file)       # security: integrity checksum
                return True
        except Exception as e:
            print(f"Backup creation failed for {date_str}: {e}")
        return False

    def auto_save_all(self):
        """Automatically save all tabs to CSV"""
        current_time = time.time()
        save_count = 0
        
        for date_str, tree in self.trees.items():
            if self.pending_save or (current_time - self.last_save_time.get(date_str, 0) >= (self.auto_save_interval / 1000)):
                try:
                    rows = []
                    for row_id in tree.get_children():
                        row_data = tree.item(row_id)['values']
                        if len(row_data) >= 7:
                            rows.append(row_data[:7])
                    
                    if rows:
                        filename = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")                        
                        if date_str in self.csv_files:
                            self.csv_files[date_str].close()
                        
                        rows = [[sanitize_csv_field(v) for v in row] for row in rows]  # security: CSV injection guard

                        with open(filename, 'w', newline='', encoding='utf-8') as file:
                            writer = csv.writer(file)
                            writer.writerow(['Date', 'Time', 'COM_Port', 'Temp_1', 'Hum_1', 'Temp_2', 'Hum_2'])
                            writer.writerows(rows)
                            file.flush()

                        secure_file_permissions(filename)  # security: owner-only permissions
                        write_checksum_file(filename)       # security: refresh integrity checksum

                        self.open_csv_file(date_str)
                        self.create_backup(date_str)
                        
                        save_count += 1
                        self.last_save_time[date_str] = current_time
                        self.pending_save = False
                        
                except Exception as e:
                    print(f"Auto-save failed for {date_str}: {e}")
        
        if save_count > 0:
            self.status_label.config(
                text=f"Auto-saved: {save_count} file(s) at {datetime.now().strftime('%H:%M:%S')}",
                fg="dark green"
            )
        
        self.root.after(self.auto_save_interval, self.auto_save_all)

    def check_daily_rollover(self):
        actual_date = datetime.now().strftime('%Y-%m-%d')
        if actual_date != self.current_date:
            self.current_date = actual_date
            self.add_day_tab(self.current_date)
            self.load_existing_data()
            messagebox.showinfo("New Day", f"Midnight passed. A new logging tab for {self.current_date} has been created.")

    def process_queue(self):
        self.check_daily_rollover() 
        while not msg_queue.empty():
            msg = msg_queue.get()
            if msg["type"] == "data":
                active_tree = self.trees[self.current_date]
                date, time_val, t1, h1, t2, h2 = msg["parsed"]
                row_data = (date, time_val, msg["port"], t1, h1, t2, h2)
                
                active_tree.insert("", tk.END, values=row_data)
                active_tree.yview_moveto(1)                
                self.append_to_csv(self.current_date, row_data)
                
                if len(active_tree.get_children()) % 10 == 0:
                    self.create_backup(self.current_date)
                
                self.pending_save = True
                
            elif msg["type"] == "port_closed":
                self.port_threads.pop(msg["port"], None)
                if msg.get("reason") == "open_failed":
                    self.port_cooldowns[msg["port"]] = time.time() + PORT_RETRY_COOLDOWN
        self.root.after(100, self.process_queue)

    def start_logging(self):
        if self.logging_active:
            return
        self.logging_active = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_label.config(text="Logging Active - Auto-save running", fg="dark green")
        self.scan_and_connect_ports()

    def scan_and_connect_ports(self):
        """Look for any COM port Windows currently sees and, if we're not already
        listening to it, spin up a thread for it. Runs repeatedly while logging is
        active so devices plugged in later (or on a different Windows machine with
        different port numbers/counts) are picked up automatically."""
        if not self.logging_active:
            return

        now = time.time()
        available_ports = get_available_ports()
        for port_name in available_ports:
            if port_name in self.port_threads:
                continue
            cooldown_until = self.port_cooldowns.get(port_name)
            if cooldown_until is not None and now < cooldown_until:
                continue
            self.port_cooldowns.pop(port_name, None)
            port_stop_event = threading.Event()
            t = threading.Thread(target=listen_to_port, args=(port_name, port_stop_event), daemon=True)
            self.port_threads[port_name] = (t, port_stop_event)
            t.start()

        self.root.after(int(PORT_SCAN_INTERVAL * 1000), self.scan_and_connect_ports)

    def stop_logging(self):
        self.logging_active = False
        for _, port_stop_event in list(self.port_threads.values()):
            port_stop_event.set()
        self.port_threads.clear()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_label.config(text="Logging Stopped - Auto-save still active", fg="orange")

    def on_closing(self):
        """Called when the user closes the window. Stops all listener threads and
        performs a final save of all data."""
        self.logging_active = False
        if self.demo_mode_active:
            self.stop_demo_mode()
        for _, port_stop_event in list(self.port_threads.values()):
            port_stop_event.set()
        
        final_save_count = 0
        for date_str, tree in self.trees.items():
            try:
                rows = []
                for row_id in tree.get_children():
                    row_data = tree.item(row_id)['values']
                    if len(row_data) >= 7:
                        rows.append(row_data[:7])
                
                if rows:
                    filename = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")
                    rows = [[sanitize_csv_field(v) for v in row] for row in rows]  # security: CSV injection guard
                    
                    if date_str in self.csv_files:
                        self.csv_files[date_str].close()
                    
                    with open(filename, 'w', newline='', encoding='utf-8') as file:
                        writer = csv.writer(file)
                        writer.writerow(['Date', 'Time', 'COM_Port', 'Temp_1', 'Hum_1', 'Temp_2', 'Hum_2'])
                        writer.writerows(rows)

                    secure_file_permissions(filename)  # security: owner-only permissions
                    write_checksum_file(filename)       # security: refresh integrity checksum
                    self.create_backup(date_str)
                    final_save_count += 1
                    
            except Exception as e:
                print(f"Final save failed for {date_str}: {e}")
        
        if final_save_count > 0:
            messagebox.showinfo("Auto-Save Complete", 
                f"All data has been automatically saved.\n"
                f"Saved {final_save_count} data files and backups."
                f"Saving to: {BASE_DIR}")
        
        for csv_file in self.csv_files.values():
            try:
                csv_file.close()
            except:
                pass

        audit_log("[SHUTDOWN] Application closed, final save complete")
        self.root.destroy()

if __name__ == '__main__':
    root = tk.Tk()
    app = FlukeLoggerApp(root)
    root.mainloop()