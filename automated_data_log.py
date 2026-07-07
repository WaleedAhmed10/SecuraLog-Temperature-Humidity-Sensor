import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import serial
import time
import threading
import queue
import csv
from datetime import datetime
from PIL import Image, ImageTk

BAUD_RATE = 9600

stop_event = threading.Event()
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

def listen_to_port(port_name):
    try:
        ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
        msg_queue.put({"type": "status", "msg": f"[SUCCESS] Connected to {port_name}."})
        
        while not stop_event.is_set():
            if ser.in_waiting > 0:
                raw_line = ser.readline().decode('utf-8', errors='ignore').strip()
                if raw_line:
                    parsed_data = parse_fluke_data(raw_line)
                    msg_queue.put({
                        "type": "data", 
                        "port": port_name, 
                        "parsed": parsed_data
                    })
            time.sleep(0.1)
    except serial.SerialException as e:
        if "Access is denied" in str(e):
            msg_queue.put({"type": "status", "msg": f"[BLOCKED] {port_name} locked."})
    except Exception:
        pass
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()

class FlukeLoggerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Temperature & Humidity Sensor - RSPK Services Lab")
        self.root.geometry("1000x650")         
        self.current_date = datetime.now().strftime('%Y-%m-%d')
        self.trees = {}  
        self.setup_ui()
        stop_event.set()        
        self.process_queue()
        self.auto_save_backup()

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

        left_org = Image.open("Pakistan.png")  
        left_resized = left_org.resize((110, 60))
        self.left_logo = ImageTk.PhotoImage(left_resized)

        right_org = Image.open("R&S.jfif") 
        right_resized = right_org.resize((80, 60))
        self.right_logo = ImageTk.PhotoImage(right_resized)

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
        self.save_btn = tk.Button(btn_frame, text="Save Current Tab to CSV", command=self.save_to_csv, bg="blue", fg="white", font=("Arial", 10, "bold"), width=25)
        self.save_btn.pack(side=tk.RIGHT, padx=10)

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
        self.notebook.select(frame)

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

    def save_to_csv(self, filename=None, show_popup=True):
        current_tab_id = self.notebook.select()
        if not current_tab_id: return        
        selected_date = self.notebook.tab(current_tab_id, "text")
        active_tree = self.trees[selected_date]
        
        if filename is None:
            filename = f"Fluke_Log_{selected_date}.csv"
            
        try:
            with open(filename, mode='w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow(['Date', 'Time', 'COM_Port', 'Temp_1', 'Hum_1', 'Temp_2', 'Hum_2']) 
                for row_id in active_tree.get_children():
                    row_data = active_tree.item(row_id)['values']
                    writer.writerow(row_data)
            if show_popup:
                messagebox.showinfo("Success", f"Data saved successfully to:\n{filename}")
        except Exception as e:
            if show_popup:
                messagebox.showerror("Error", f"Failed to save file: {e}")

    def auto_save_backup(self):
        current_tab_id = self.notebook.select()
        if current_tab_id:
            selected_date = self.notebook.tab(current_tab_id, "text")
            backup_filename = f"BACKUP_Fluke_Log_{selected_date}.csv"
            self.save_to_csv(filename=backup_filename, show_popup=False)
        self.root.after(300000, self.auto_save_backup)

    def check_daily_rollover(self):
        actual_date = datetime.now().strftime('%Y-%m-%d')
        if actual_date != self.current_date:
            self.current_date = actual_date
            self.add_day_tab(self.current_date)
            messagebox.showinfo("New Day", f"Midnight passed. A new logging tab for {self.current_date} has been created.")

    def process_queue(self):
        self.check_daily_rollover() 
        while not msg_queue.empty():
            msg = msg_queue.get()
            if msg["type"] == "data":
                active_tree = self.trees[self.current_date]
                date, time_val, t1, h1, t2, h2 = msg["parsed"]
                active_tree.insert("", tk.END, values=(date, time_val, msg["port"], t1, h1, t2, h2))
                active_tree.yview_moveto(1)
        self.root.after(100, self.process_queue)

    def start_logging(self):
        if not stop_event.is_set(): return
        stop_event.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        
        for x in range(1, 32):
            port_name = f"COM{x}"
            t = threading.Thread(target=listen_to_port, args=(port_name,), daemon=True)
            t.start()

    def stop_logging(self):
        stop_event.set()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

if __name__ == '__main__':
    root = tk.Tk()
    app = FlukeLoggerApp(root)
    root.mainloop()