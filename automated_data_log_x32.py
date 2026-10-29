import Tkinter as tk
import ttk
import tkSimpleDialog as simpledialog
import tkMessageBox as messagebox
import ScrolledText as scrolledtext
import serial
import serial.tools.list_ports
import os
import sys
import time
import threading
import Queue as queue
import csv
import hashlib
import re
import random
from datetime import datetime

BAUD_RATE = 9600
PORT_SCAN_INTERVAL = 2
PORT_RETRY_COOLDOWN = 30
MAX_LINE_LENGTH = 500
ALLOWED_LINE_CHARS = re.compile(r'^[\x20-\x7E]*$')  # printable ASCII only
DATE_STR_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')

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
    SECURITY_STATS_LOCK.acquire()
    try:
        SECURITY_STATS[key] = SECURITY_STATS.get(key, 0) + amount
    finally:
        SECURITY_STATS_LOCK.release()


def get_stats_snapshot():
    """Thread-safe read of all live security counters."""
    SECURITY_STATS_LOCK.acquire()
    try:
        return dict(SECURITY_STATS)
    finally:
        SECURITY_STATS_LOCK.release()


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
        audit_log("[SECURITY] Rejected oversized serial line ({0} chars)".format(len(raw_line)))
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
    return bool(DATE_STR_PATTERN.match(date_str))


def sha256_of_file(path):
    """Computes a SHA-256 checksum of a file for integrity/tamper detection."""
    h = hashlib.sha256()
    try:
        f = open(path, 'rb')
        try:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        finally:
            f.close()
        return h.hexdigest()
    except Exception:
        return None


def write_checksum_file(csv_path):
    """Writes/refreshes a .sha256 sidecar file next to a CSV log so its
    integrity can be verified later (tamper-evidence for lab data)."""
    digest = sha256_of_file(csv_path)
    if digest:
        try:
            f = open(csv_path + ".sha256", "w")
            try:
                f.write("{0}  {1}\n".format(digest, os.path.basename(csv_path)))
            finally:
                f.close()
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
        f = open(checksum_path, "r")
        try:
            saved = f.read().split()[0]
        finally:
            f.close()
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
        f = open(AUDIT_LOG_PATH, "a")
        try:
            f.write("[{0}] {1}\n".format(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), event))
        finally:
            f.close()
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
        temp1 = "{0} {1}".format(data_parts[0], data_parts[1])
        hum1 = "{0} {1}".format(data_parts[2], data_parts[3])

    if len(data_parts) >= 8:
        t2_val, t2_unit, h2_val, h2_unit = data_parts[4:8]
        try:
            if float(t2_val) != 0.0 or float(h2_val) != 0.0:
                temp2 = "{0} {1}".format(t2_val, t2_unit)
                hum2 = "{0} {1}".format(h2_val, h2_unit)
        except ValueError:
            temp2 = "{0} {1}".format(t2_val, t2_unit)
            hum2 = "{0} {1}".format(h2_val, h2_unit)

    return date_str, time_str, temp1, hum1, temp2, hum2

def get_available_ports():
    """Return a sorted list of COM port device names currently visible to Windows.

    Using serial.tools.list_ports instead of blindly probing COM1-COM32 makes the
    logger work on any Windows machine regardless of how its ports are numbered
    (some systems, especially with several USB-serial adapters, assign port
    numbers well above 32).
    """
    ports = []
    for p in serial.tools.list_ports.comports():
        if isinstance(p, tuple):
            ports.append(p[0])
        else:
            ports.append(p.device)

    def sort_key(name):
        digits = ''.join(ch for ch in name if ch.isdigit())
        return int(digits) if digits else 0

    return sorted(ports, key=sort_key)


def listen_to_port(port_name, port_stop_event):
    ser = None
    close_reason = "stopped"
    try:
        time.sleep(0.5)
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        msg_queue.put({"type": "status", "msg": "[SUCCESS] Connected to {0}.".format(port_name)})
        audit_log("[CONNECTED] {0}".format(port_name))
        time.sleep(0.5)
        
        while not port_stop_event.is_set():
            try:
                waiting = ser.in_waiting if hasattr(ser, 'in_waiting') else ser.inWaiting()
                if waiting > 0:
                    raw_bytes = ser.readline()
                    raw_line = raw_bytes.decode('utf-8', 'ignore').strip()
                    raw_line = sanitize_serial_input(raw_line)  # input validation
                    if raw_line:
                        parsed_data = parse_fluke_data(raw_line)
                        msg_queue.put({"type": "data", "port": port_name,
                            "parsed": parsed_data
                        })
            except (OSError, serial.SerialException) as e:
                msg_queue.put({"type": "status", "msg": "[DISCONNECTED] {0} removed.".format(port_name)})
                audit_log("[DISCONNECTED] {0}: {1}".format(port_name, e))
                close_reason = "disconnected"
                break
            time.sleep(0.1)

    except serial.SerialException as e:
        msg = str(e)
        if "Access is denied" in msg or "PermissionError" in msg:
            msg_queue.put({"type": "status", "msg": "[INFO] {0} is currently in use, will retry...".format(port_name)})
            close_reason = "open_failed"
            msg_queue.put({"type": "port_closed", "port": port_name, "reason": "retry_later"})
        else:
            msg_queue.put({"type": "status", "msg": "[INFO] Could not open {0}, will retry...".format(port_name)})
            close_reason = "open_failed"

    except Exception as e:
        msg_queue.put({"type": "status", "msg": "[INFO] Retrying connection to {0}...".format(port_name)})
        close_reason = "open_failed"

    finally:
        try:
            if ser is not None and ser.is_open:
                ser.close()
        except Exception:
            pass
        if close_reason != "retry_later":
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
            line = "{0} C,{1} %RH,{2} C,{3} %RH".format(t1, h1, t2, h2)
            raw_line = sanitize_serial_input(line)
            if raw_line:
                parsed_data = parse_fluke_data(raw_line)
                msg_queue.put({"type": "data", "port": "DEMO-SIM", "parsed": parsed_data})
        time.sleep(2)

class FlukeLoggerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SecuraLog Temperature and Humidity Sensor")
        self.root.geometry("1050x700")
        self.current_date = datetime.now().strftime('%Y-%m-%d')
        self.trees = {}
        self.logging_active = False
        self.port_threads = {}
        self.port_cooldowns = {}

        self.auto_save_interval = 30000  # Save every 30 seconds
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
        style.configure("TNotebook", background=BG_COLOR, borderwidth=0)
        style.configure("TNotebook.Tab", background="#A0A0A0", foreground="black",
                        font=("Arial", 10, "bold"), padding=[10, 2])
        style.map("TNotebook.Tab", background=[("selected", LOGS_BG)])
        style.configure("TFrame", background=BG_COLOR)
        style.configure("Treeview", background=LOGS_BG, fieldbackground=LOGS_BG,
                        foreground="black", rowheight=25)
        style.configure("Treeview.Heading", background="#A0A0A0", foreground="black",
                        font=("Arial", 10, "bold"))

        header_frame = tk.Frame(self.root, bg=BG_COLOR)
        header_frame.pack(fill=tk.X, pady=(15, 5))

        title_lbl = tk.Label(header_frame, text="SecuraLog Temperature and Humidity Sensor",
                             font=("Arial", 18, "bold"), fg=TEXT_COLOR, bg=BG_COLOR)
        title_lbl.pack(side=tk.TOP, anchor=tk.CENTER)

        btn_frame = tk.Frame(self.root, bg=BG_COLOR, pady=10)
        btn_frame.pack(fill=tk.X)

        self.start_btn = tk.Button(btn_frame, text="Start Logging", command=self.start_logging, bg="green", fg="white", font=("Arial", 10, "bold"), width=15)
        self.start_btn.pack(side=tk.LEFT, padx=10)
        self.stop_btn = tk.Button(btn_frame, text="Stop Logging", command=self.stop_logging, bg="red", fg="white", font=("Arial", 10, "bold"), width=15, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=10)
        self.demo_btn = tk.Button(btn_frame, text="Start Demo Mode (No Hardware)", command=self.toggle_demo_mode, bg="purple", fg="white", font=("Arial", 10, "bold"), width=28)
        self.demo_btn.pack(side=tk.LEFT, padx=10)

        self.status_label = tk.Label(btn_frame, text="Auto-save: Active", font=("Arial", 9), fg="dark green", bg=BG_COLOR)
        self.status_label.pack(side=tk.RIGHT, padx=15)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
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

        intro = tk.Label(frame, text="Cybersecurity Controls - Live Status",
                          font=("Arial", 13, "bold"), anchor="w")
        intro.pack(fill=tk.X, padx=10, pady=(10, 0))

        stats_frame = ttk.LabelFrame(frame, text="Input Validation & Injection Prevention")
        stats_frame.pack(fill=tk.X, padx=10, pady=10)

        self.stat_labels = {}
        stat_rows = [
            ("packets_validated", "Packets validated & accepted"),
            ("packets_rejected_length", "Packets rejected (oversized)"),
            ("packets_rejected_chars", "Packets rejected (invalid characters)"),
            ("csv_fields_sanitized", "CSV/formula-injection fields sanitized"),
            ("integrity_checks_pass", "Integrity checks passed"),
            ("integrity_checks_fail", "Integrity checks FAILED"),
        ]
        for i in range(len(stat_rows)):
            key, label = stat_rows[i]
            r = i // 2
            c = i % 2
            row_frame = tk.Frame(stats_frame)
            row_frame.grid(row=r, column=c, sticky="w", padx=15, pady=4)
            tk.Label(row_frame, text=label + ":", font=("Arial", 9)).pack(side=tk.LEFT)
            val_lbl = tk.Label(row_frame, text="0", font=("Arial", 9, "bold"), fg="dark blue")
            val_lbl.pack(side=tk.LEFT, padx=(5, 0))
            self.stat_labels[key] = val_lbl

        controls_frame = tk.Frame(frame)
        controls_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        tk.Button(controls_frame, text="Run Full Integrity Check Now",
                  command=self.run_integrity_check_now, bg="dark blue", fg="white",
                  font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 10))

        integrity_frame = ttk.LabelFrame(frame, text="File Integrity Status (SHA-256)")
        integrity_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.integrity_tree = ttk.Treeview(integrity_frame, columns=("date", "status"),
                                            show="headings", height=4)
        self.integrity_tree.heading("date", text="Date / File")
        self.integrity_tree.heading("status", text="Status")
        self.integrity_tree.column("date", width=200, anchor=tk.W)
        self.integrity_tree.column("status", width=150, anchor=tk.CENTER)
        self.integrity_tree.pack(fill=tk.X, padx=10, pady=5)

        audit_frame = ttk.LabelFrame(frame, text="Audit Log (live tail - audit_log.txt)")
        audit_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.audit_text = scrolledtext.ScrolledText(audit_frame, height=8, font=("Consolas", 9),
                                                      state=tk.DISABLED, wrap=tk.NONE)
        self.audit_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

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
                filename = os.path.join(BASE_DIR, "Fluke_Log_{0}.csv".format(date_str))
                if not os.path.isfile(filename):
                    continue
                result = verify_checksum_file(filename)
                if result is True:
                    status_text, tag = "OK - verified", "ok"
                elif result is False:
                    status_text, tag = "MISMATCH - possible tampering", "bad"
                else:
                    status_text, tag = "No checksum yet", "unknown"
                self.integrity_tree.insert("", tk.END, values=("Fluke_Log_{0}.csv".format(date_str), status_text), tags=(tag,))
            self.integrity_tree.tag_configure("ok", foreground="dark green")
            self.integrity_tree.tag_configure("bad", foreground="red")
            self.integrity_tree.tag_configure("unknown", foreground="grey")

            if os.path.isfile(AUDIT_LOG_PATH):
                f = open(AUDIT_LOG_PATH, "r")
                try:
                    lines = f.readlines()[-200:]
                finally:
                    f.close()
                self.audit_text.config(state=tk.NORMAL)
                self.audit_text.delete("1.0", tk.END)
                self.audit_text.insert(tk.END, "".join(lines))
                self.audit_text.see(tk.END)
                self.audit_text.config(state=tk.DISABLED)
        except Exception as e:
            print("Dashboard refresh error: {0}".format(e))

        self.root.after(2000, self.refresh_security_dashboard)

    def run_integrity_check_now(self):
        """Manually verifies every known CSV log against its saved SHA-256
        checksum, reports the result, then refreshes the baseline checksum
        so the next check reflects the current (now-verified) state."""
        checked = 0
        failed = 0
        for date_str in list(self.trees.keys()):
            if not is_valid_date_str(date_str):
                continue
            filename = os.path.join(BASE_DIR, "Fluke_Log_{0}.csv".format(date_str))
            if not os.path.isfile(filename):
                continue
            result = verify_checksum_file(filename)
            checked += 1
            if result is False:
                failed += 1
                bump_stat("integrity_checks_fail")
                audit_log("[SECURITY] Manual integrity check FAILED for {0}".format(filename))
            elif result is True:
                bump_stat("integrity_checks_pass")
            write_checksum_file(filename)
        audit_log("[SECURITY] Manual integrity check complete: {0} file(s) checked, {1} failure(s)".format(checked, failed))
        messagebox.showinfo("Integrity Check Complete",
            "Checked {0} file(s).\n{1} integrity failure(s) detected.\n"
            "See the Security Dashboard and audit_log.txt for details.".format(checked, failed))
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
                                             args=(self.demo_stop_event,))
        self.demo_thread.daemon = True
        self.demo_thread.start()
        self.demo_btn.config(text="Stop Demo Mode", bg="dark red")
        self.status_label.config(text="Demo Mode Active - simulated data, no hardware", fg="purple")
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
            audit_log("[SECURITY] Rejected invalid date_str for CSV open: {0!r}".format(date_str))
            return
        filename = os.path.join(BASE_DIR, "Fluke_Log_{0}.csv".format(date_str))

        file_exists = os.path.isfile(filename)
        csv_file = open(filename, 'ab')
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
            filename = os.path.join(BASE_DIR, "Fluke_Log_{0}.csv".format(date_str))
            if os.path.isfile(filename):
                # security: verify the file wasn't tampered with since last checksum
                integrity = verify_checksum_file(filename)
                if integrity is False:
                    bump_stat("integrity_checks_fail")
                    audit_log("[SECURITY] Checksum mismatch on load: {0}".format(filename))
                    messagebox.showwarning(
                        "Integrity Check Failed",
                        "The log file for {0} does not match its saved checksum.\n"
                        "It may have been modified outside this application.".format(date_str)
                    )
                elif integrity is True:
                    bump_stat("integrity_checks_pass")
                try:
                    file = open(filename, 'rb')
                    try:
                        reader = csv.reader(file)
                        next(reader, None)
                        tree = self.trees[date_str]
                        for row in reader:
                            if len(row) >= 7:  # Make sure we have all columns
                                tree.insert("", tk.END, values=row[:7])
                    finally:
                        file.close()
                except Exception as e:
                    print("Error loading data from {0}: {1}".format(filename, e))

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

        new_value = simpledialog.askstring("Edit Data", "Modify {0}:".format(column_name), initialvalue=current_value)
        if new_value is not None:
            new_value = sanitize_csv_field(new_value)  # security: block CSV/formula injection
            current_values = list(active_tree.item(selected_iid, 'values'))
            current_values[col_index] = new_value
            active_tree.item(selected_iid, values=current_values)
            self.pending_save = True
            self.last_save_time[current_date_str] = 0
            audit_log("[EDIT] {0} row edited: column={1!r} new_value={2!r}".format(current_date_str, column_name, new_value))

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
            print("Error appending to CSV: {0}".format(e))
            self.open_csv_file(date_str)
            try:
                writer = self.csv_writers.get(date_str)
                if writer:
                    writer.writerow(row_data)
                    self.csv_files[date_str].flush()
                    return True
            except Exception:
                pass
        return False

    def create_backup(self, date_str):
        """Create a backup of the CSV file"""
        if not is_valid_date_str(date_str):  # security: filename validation
            return False
        try:
            source_file = os.path.join(BASE_DIR, "Fluke_Log_{0}.csv".format(date_str))
            if os.path.isfile(source_file):
                backup_file = os.path.join(BASE_DIR, "BACKUP_Fluke_Log_{0}.csv".format(date_str))
                src = open(source_file, 'rb')
                try:
                    data = src.read()
                finally:
                    src.close()
                dst = open(backup_file, 'wb')
                try:
                    dst.write(data)
                finally:
                    dst.close()
                secure_file_permissions(backup_file)  # security: owner-only permissions
                write_checksum_file(backup_file)       # security: integrity checksum
                return True
        except Exception as e:
            print("Backup creation failed for {0}: {1}".format(date_str, e))
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
                        filename = os.path.join(BASE_DIR, "Fluke_Log_{0}.csv".format(date_str))
                        if date_str in self.csv_files:
                            self.csv_files[date_str].close()

                        rows = [[sanitize_csv_field(v) for v in row] for row in rows]  # security: CSV injection guard

                        file = open(filename, 'wb')
                        try:
                            writer = csv.writer(file)
                            writer.writerow(['Date', 'Time', 'COM_Port', 'Temp_1', 'Hum_1', 'Temp_2', 'Hum_2'])
                            writer.writerows(rows)
                            file.flush()
                        finally:
                            file.close()

                        secure_file_permissions(filename)  # security: owner-only permissions
                        write_checksum_file(filename)       # security: refresh integrity checksum

                        self.open_csv_file(date_str)
                        self.create_backup(date_str)

                        save_count += 1
                        self.last_save_time[date_str] = current_time
                        self.pending_save = False

                except Exception as e:
                    print("Auto-save failed for {0}: {1}".format(date_str, e))

        if save_count > 0:
            self.status_label.config(
                text="Auto-saved: {0} file(s) at {1}".format(save_count, datetime.now().strftime('%H:%M:%S')),
                fg="dark green"
            )

        self.root.after(self.auto_save_interval, self.auto_save_all)

    def check_daily_rollover(self):
        actual_date = datetime.now().strftime('%Y-%m-%d')
        if actual_date != self.current_date:
            self.current_date = actual_date
            self.add_day_tab(self.current_date)
            self.load_existing_data()
            messagebox.showinfo("New Day", "Midnight passed. A new logging tab for {0} has been created.".format(self.current_date))

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

            elif msg["type"] == "status":
                self.status_label.config(text=msg["msg"], fg="dark blue")

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
        """Look for any COM port Windows currently sees and, if not already listening to it, spin up a thread for it. 
        Runs repeatedly while logging is active so devices plugged in later (or on a different Windows machine with
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
            t = threading.Thread(target=listen_to_port, args=(port_name, port_stop_event))
            t.daemon = True
            self.port_threads[port_name] = (t, port_stop_event)
            t.start()

        self.root.after(int(PORT_SCAN_INTERVAL * 1000), self.scan_and_connect_ports)

    def stop_logging(self):
        self.logging_active = False
        for port_name, (t, port_stop_event) in list(self.port_threads.items()):
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
        for port_name, (t, port_stop_event) in list(self.port_threads.items()):
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
                    filename = os.path.join(BASE_DIR, "Fluke_Log_{0}.csv".format(date_str))

                    if date_str in self.csv_files:
                        self.csv_files[date_str].close()

                    rows = [[sanitize_csv_field(v) for v in row] for row in rows]  # security: CSV injection guard

                    file = open(filename, 'wb')
                    try:
                        writer = csv.writer(file)
                        writer.writerow(['Date', 'Time', 'COM_Port', 'Temp_1', 'Hum_1', 'Temp_2', 'Hum_2'])
                        writer.writerows(rows)
                    finally:
                        file.close()

                    secure_file_permissions(filename)  # security: owner-only permissions
                    write_checksum_file(filename)       # security: refresh integrity checksum
                    self.create_backup(date_str)
                    final_save_count += 1

            except Exception as e:
                print("Final save failed for {0}: {1}".format(date_str, e))

        if final_save_count > 0:
            messagebox.showinfo("Auto-Save Complete",
                "All data has been automatically saved.\n"
                "Saved {0} data files and backups.".format(final_save_count))

        for csv_file in self.csv_files.values():
            try:
                csv_file.close()
            except Exception:
                pass

        audit_log("[SHUTDOWN] Application closed, final save complete")
        self.root.destroy()

if __name__ == '__main__':
    root = tk.Tk()
    app = FlukeLoggerApp(root)
    root.mainloop()