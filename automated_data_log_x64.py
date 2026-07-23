import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import serial
import serial.tools.list_ports
import os
import sys
import time
import threading
import queue
import csv
from datetime import datetime
from PIL import Image, ImageTk

BAUD_RATE = 9600
PORT_SCAN_INTERVAL = 20
PORT_RETRY_COOLDOWN = 30

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

msg_queue = queue.Queue()

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

        while not port_stop_event.is_set():
            try:
                if ser.in_waiting > 0:
                    raw_line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if raw_line:
                        parsed_data = parse_fluke_data(raw_line)
                        msg_queue.put({"type": "data", "port": port_name,
                            "parsed": parsed_data
                        })
            except (OSError, serial.SerialException) as e:
                msg_queue.put({"type": "status", "msg": f"[DISCONNECTED] {port_name} removed."})
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

class FlukeLoggerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Temperature & Humidity Sensor - RSPK Services Lab")
        self.root.geometry("1000x650")
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
        
        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.process_queue()        
        self.load_existing_data()
        self.auto_save_all()        
        self.root.after(100, self.start_logging)

    def setup_ui(self):
        BG_COLOR = "grey"
        LOGS_BG = "white"
        TEXT_COLOR = "dark blue"
        
        self.root.configure(bg=BG_COLOR)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=BG_COLOR, borderwidth=0)
        style.configure("TNotebook.Tab", background="#A0A0A0", foreground="black", font=("Arial", 10, "bold"), padding=[10, 2])
        style.map("TNotebook.Tab", background=[("selected", LOGS_BG)])
        style.configure("TFrame", background=BG_COLOR)
        style.configure("Treeview", background=LOGS_BG, fieldbackground=LOGS_BG, foreground="black", rowheight=25)
        style.configure("Treeview.Heading", background="#A0A0A0", foreground="black", font=("Arial", 10, "bold"))

        try:
            left_org = Image.open(os.path.join(BASE_DIR, "Pakistan.png"))
            left_resized = left_org.resize((110, 60))
            self.left_logo = ImageTk.PhotoImage(left_resized)
        except Exception as e:
            self.left_logo = None
            messagebox.showwarning("Logo Missing", f"Could not load Pakistan.png:\n{e}")

        try:
            right_org = Image.open(os.path.join(BASE_DIR, "R&S.jfif"))
            right_resized = right_org.resize((80, 60))
            self.right_logo = ImageTk.PhotoImage(right_resized)
        except Exception as e:
            self.right_logo = None
            messagebox.showwarning("Logo Missing", f"Could not load R&S.jfif:\n{e}")

        header_frame = tk.Frame(self.root, bg=BG_COLOR)
        header_frame.pack(fill=tk.X, pady=(15, 5))        
        header_frame.columnconfigure(0, weight=0)
        header_frame.columnconfigure(1, weight=1)
        header_frame.columnconfigure(2, weight=0)

        left_img_lbl = tk.Label(header_frame, image=self.left_logo, bg=BG_COLOR, bd=0, highlightthickness=0)
        left_img_lbl.grid(row=0, column=0, padx=20, sticky="w")

        center_text_frame = tk.Frame(header_frame, bg=BG_COLOR)
        center_text_frame.grid(row=0, column=1)

        title_lbl = tk.Label(center_text_frame, text="RSPK Services Lab Islamabad", font=("Arial", 18, "bold"), fg=TEXT_COLOR, bg=BG_COLOR)
        title_lbl.pack(side=tk.TOP, anchor=tk.CENTER)
        
        subtitle_lbl = tk.Label(center_text_frame, text="Fluke Automated Data Logger", font=("Arial", 12, "italic"), fg=TEXT_COLOR, bg=BG_COLOR)
        subtitle_lbl.pack(side=tk.TOP, anchor=tk.CENTER, pady=(2, 0))

        right_img_lbl = tk.Label(header_frame, image=self.right_logo, bg=BG_COLOR, bd=0, highlightthickness=0)
        right_img_lbl.grid(row=0, column=2, padx=20, sticky="e")

        btn_frame = tk.Frame(self.root, bg=BG_COLOR, pady=10)
        btn_frame.pack(fill=tk.X)

        self.start_btn = tk.Button(btn_frame, text="Start Logging", command=self.start_logging, bg="green", fg="white", font=("Arial", 10, "bold"), width=15)
        self.start_btn.pack(side=tk.LEFT, padx=10)
        self.stop_btn = tk.Button(btn_frame, text="Stop Logging", command=self.stop_logging, bg="red", fg="white", font=("Arial", 10, "bold"), width=15, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=10)
        
        self.status_label = tk.Label(btn_frame, text="Auto-save: Active", font=("Arial", 9), fg="dark green", bg=BG_COLOR)
        self.status_label.pack(side=tk.RIGHT, padx=15)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)        
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

    def open_csv_file(self, date_str):
        """Open or create CSV file for the given date"""
        filename = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")
        
        file_exists = os.path.isfile(filename)        
        csv_file = open(filename, mode='a', newline='', encoding='utf-8')
        writer = csv.writer(csv_file)
        
        if not file_exists:
            writer.writerow(['Date', 'Time', 'COM_Port', 'Temp_1', 'Hum_1', 'Temp_2', 'Hum_2'])
            csv_file.flush()
        
        self.csv_files[date_str] = csv_file
        self.csv_writers[date_str] = writer

    def load_existing_data(self):
        """Load existing data from CSV files into the treeview"""
        for date_str in list(self.trees.keys()):
            filename = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")
            if os.path.isfile(filename):
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
            current_values = list(active_tree.item(selected_iid, 'values'))
            current_values[col_index] = new_value
            active_tree.item(selected_iid, values=current_values)
            self.pending_save = True
            self.last_save_time[current_date_str] = 0

    def append_to_csv(self, date_str, row_data):
        """Append a single row to the CSV file"""
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
        try:
            source_file = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")
            if os.path.isfile(source_file):
                backup_file = os.path.join(BASE_DIR, f"BACKUP_Fluke_Log_{date_str}.csv")
                with open(source_file, 'r', encoding='utf-8') as src:
                    with open(backup_file, 'w', encoding='utf-8') as dst:
                        dst.write(src.read())
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
                        
                        with open(filename, 'w', newline='', encoding='utf-8') as file:
                            writer = csv.writer(file)
                            writer.writerow(['Date', 'Time', 'COM_Port', 'Temp_1', 'Hum_1', 'Temp_2', 'Hum_2'])
                            writer.writerows(rows)
                            file.flush()
                        
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
                    filename = os.path.join(BASE_DIR, f"Fluke_Log_{date_str}.csv")
                    
                    if date_str in self.csv_files:
                        self.csv_files[date_str].close()
                    
                    with open(filename, 'w', newline='', encoding='utf-8') as file:
                        writer = csv.writer(file)
                        writer.writerow(['Date', 'Time', 'COM_Port', 'Temp_1', 'Hum_1', 'Temp_2', 'Hum_2'])
                        writer.writerows(rows)
                    
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
        
        self.root.destroy()

if __name__ == '__main__':
    root = tk.Tk()
    app = FlukeLoggerApp(root)
    root.mainloop()