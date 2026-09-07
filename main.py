import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw, ImageTk


SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]

# Maps a size unit suffix to a byte multiplier (used for sorting on raw values).
UNIT_FACTORS = {
    "B": 1,
    "KB": 1024,
    "MB": 1024 ** 2,
    "GB": 1024 ** 3,
    "TB": 1024 ** 4,
    "PB": 1024 ** 5,
}


def format_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0 B"
    unit_idx = 0
    value = float(size_bytes)
    while value >= 1024 and unit_idx < len(SIZE_UNITS) - 1:
        value /= 1024.0
        unit_idx += 1
    return f"{value:.2f} {SIZE_UNITS[unit_idx]}"


def get_folder_size(folder_path: str) -> tuple[int, int]:
    total_size = 0
    file_count = 0
    try:
        for dirpath, dirnames, filenames in os.walk(folder_path):
            for fname in filenames:
                try:
                    fp = os.path.join(dirpath, fname)
                    stat = os.lstat(fp)
                    if os.path.islink(fp):
                        continue
                    total_size += stat.st_size
                    file_count += 1
                except OSError:
                    pass
    except PermissionError:
        pass
    return total_size, file_count


class FolderSpaceEagleEye:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Folder Space Eagle Eye - 文件夹空间分析器")
        self.root.geometry("900x600")
        self.root.minsize(700, 450)
        self.root.resizable(True, True)

        self.scanning = False
        self.scan_cancelled = False
        self.executor = None
        self._path_cache = {}
        # Thread-safe bridge from worker threads to the Tk UI thread.
        self._ui_queue = queue.Queue()
        self._poll_ui_queue()

        self._make_icons()
        self._setup_styles()
        self._build_ui()
        self._center_window()

    def _make_icons(self):
        self.icons = {}
        icon_palette = {
            ".": "#F0F0F0",
            "Y": "#FFA000",
            "G": "#4CAF50",
            "R": "#E53935",
            "U": "#1565C0",
            "W": "#FFFFFF",
            "D": "#37474F",
            "L": "#90A4AE",
        }

        patterns = {
            "folder": [
                "..YYYY......",
                ".YYYYYYYY...",
                "YYYYYYYYYY..",
                "YYYYYYYYYYYY",
                "YYYYYYYYYYYY",
                "YYYYYYYYYYYY",
                "YYYYYYYYYYYY",
                "YYYYYYYYYYYY",
                "YYYYYYYYYYYY",
                "YYYYYYYYYYYY",
                "YYYYYYYYYYYY",
                "YYYYYYYYYYYY",
            ],
            "play": [
                "GG..........",
                "GGGG........",
                "GGGGGG......",
                "GGGGGGGG....",
                "GGGGGGGGGG..",
                "GGGGGGGGGGGG",
                "GGGGGGGGGG..",
                "GGGGGGGG....",
                "GGGGGG......",
                "GGGG........",
                "GG..........",
                "............",
            ],
            "stop": [
                "............",
                "..RRRRRRRR..",
                "..RRRRRRRR..",
                "..RRRRRRRR..",
                "..RRRRRRRR..",
                "..RRRRRRRR..",
                "..RRRRRRRR..",
                "..RRRRRRRR..",
                "..RRRRRRRR..",
                "..RRRRRRRR..",
                "..RRRRRRRR..",
                "............",
            ],
        }

        for name, pattern in patterns.items():
            h = len(pattern)
            w = len(pattern[0]) if h > 0 else 0
            img = tk.PhotoImage(width=w, height=h)
            for y, row in enumerate(pattern):
                for x, ch in enumerate(row):
                    color = icon_palette.get(ch, "#F0F0F0")
                    img.put(color, (x, y))
            scaled = img.zoom(3, 3).subsample(2, 2)
            self.icons[name] = scaled
            del img

        self._generate_app_icon()

    def _generate_app_icon(self):
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
        size = 32
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        margin = 2
        draw.ellipse(
            [margin, margin, size - margin, size - margin],
            fill=(21, 101, 192, 255), outline=(13, 71, 161, 255), width=2,
        )
        inner_margin = 9
        draw.ellipse(
            [inner_margin, inner_margin, size - inner_margin, size - inner_margin],
            fill=(255, 255, 255, 255), outline=(187, 222, 251, 255), width=1,
        )
        pupil_margin = 13
        draw.ellipse(
            [pupil_margin, pupil_margin, size - pupil_margin, size - pupil_margin],
            fill=(13, 71, 161, 255),
        )
        try:
            img.save(icon_path, format="ICO", sizes=[(32, 32)])
        except OSError:
            # The committed app.ico may be read-only (e.g. running from dist\);
            # skip regeneration and just use the in-memory icon.
            pass
        self.root.iconphoto(True, ImageTk.PhotoImage(img))

    def _setup_styles(self):
        style = ttk.Style(self.root)
        available_themes = style.theme_names()
        if "clam" in available_themes:
            style.theme_use("clam")
        elif "vista" in available_themes:
            style.theme_use("vista")

        style.configure(
            "TButton", padding=(12, 6), font=("Microsoft YaHei UI", 10),
            background="#F0F0F0",
        )
        style.configure(
            "TEntry", padding=(4, 6), font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "TLabel", font=("Microsoft YaHei UI", 10), background="#F0F0F0"
        )
        style.configure(
            "TFrame", background="#F0F0F0"
        )
        style.configure(
            "Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold")
        )
        style.configure(
            "Treeview", font=("Microsoft YaHei UI", 10), rowheight=26, background="white",
        )

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=(16, 12))
        main_frame.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X, pady=(0, 12))

        logo_canvas = tk.Canvas(
            top_frame, width=36, height=36, highlightthickness=0,
            bg="#F0F0F0",
        )
        logo_canvas.pack(side=tk.LEFT)
        logo_canvas.create_oval(
            3, 3, 33, 33, fill="#1565C0", outline="#0D47A1", width=2
        )
        logo_canvas.create_oval(
            10, 10, 26, 26, fill="#FFFFFF", outline="#BBDEFB", width=1
        )
        logo_canvas.create_oval(
            14, 14, 22, 22, fill="#0D47A1", outline=""
        )

        title_label = ttk.Label(
            top_frame,
            text="  Folder Space Eagle Eye",
            font=("Microsoft YaHei UI", 18, "bold"),
        )
        title_label.pack(side=tk.LEFT)

        self.info_label = ttk.Label(
            top_frame, text="", foreground="gray"
        )
        self.info_label.pack(side=tk.LEFT, padx=(16, 0))

        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=(0, 10))

        path_frame = ttk.Frame(control_frame)
        path_frame.pack(fill=tk.X)

        self.path_var = tk.StringVar()
        path_entry = ttk.Entry(
            path_frame, textvariable=self.path_var,
        )
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)

        browse_btn = ttk.Button(
            path_frame, text="浏览...", image=self.icons["folder"],
            compound=tk.LEFT, command=self._browse_folder,
        )
        browse_btn.pack(side=tk.LEFT, padx=(8, 0))

        scan_btn = ttk.Button(
            path_frame, text="开始分析", image=self.icons["play"],
            compound=tk.LEFT, command=self._start_scan,
        )
        scan_btn.pack(side=tk.LEFT, padx=(6, 0))
        self.action_btn = scan_btn

        tree_frame = ttk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("name", "size", "files", "percent")
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )

        self.tree.heading("name", text="文件夹名称",
                          command=lambda: self._sort_column("name"))
        self.tree.heading("size", text="占用空间",
                          command=lambda: self._sort_column("size"))
        self.tree.heading("files", text="文件数",
                          command=lambda: self._sort_column("files"))
        self.tree.heading("percent", text="占比",
                          command=lambda: self._sort_column("percent"))

        self.tree.column("name", width=320, minwidth=160)
        self.tree.column("size", width=140, minwidth=100, anchor=tk.E)
        self.tree.column("files", width=80, minwidth=70, anchor=tk.E)
        self.tree.column("percent", width=80, minwidth=70, anchor=tk.E)

        self._sort_state = {"column": "size", "reverse": True}

        tree_scrollbar = ttk.Scrollbar(
            tree_frame, orient=tk.VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=tree_scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<Button-2>", self._on_tree_right_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Double-Button-1>", self._on_tree_double_click)

        self.empty_label = ttk.Label(
            self.tree,
            text="请选择一个文件夹，然后点击「开始分析」",
            foreground="gray",
            font=("Microsoft YaHei UI", 12),
        )
        self.empty_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(
            fill=tk.X, side=tk.BOTTOM, pady=(0, 2)
        )

        status_frame = tk.Frame(main_frame, height=22, bg="#F0F0F0")
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        status_frame.pack_propagate(False)

        self.status_var = tk.StringVar()
        self.status_var.set("就绪")
        self.status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            anchor=tk.W,
            bg="#F0F0F0",
            fg="gray",
            font=("Microsoft YaHei UI", 9),
            padx=8, pady=1,
        )
        self.status_label.pack(side=tk.LEFT)

        self._progress_width = 160
        self._progress_block = 40
        self._progress_h = 4
        self._progress_canvas = tk.Canvas(
            status_frame,
            width=self._progress_width, height=self._progress_h,
            bg="#F0F0F0", highlightthickness=0, bd=0,
        )
        self._progress_rect = self._progress_canvas.create_rectangle(
            0, 0, self._progress_block, self._progress_h,
            fill="#4CAF50", outline="",
        )
        self._progress_dir = 1
        self._progress_pos = 0
        self._progress_anim_id = None

    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"+{x}+{y}")

    def _post(self, callback, *args):
        """Thread-safely schedule a callback to run on the Tk UI thread."""
        self._ui_queue.put((callback, args))

    def _poll_ui_queue(self):
        """Drain queued UI callbacks on the main thread; reschedules itself."""
        try:
            while True:
                callback, args = self._ui_queue.get_nowait()
                callback(*args)
        except queue.Empty:
            pass
        self.root.after(50, self._poll_ui_queue)

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="选择要分析的文件夹")
        if folder:
            self.path_var.set(folder)
            self._show_empty()

    def _start_scan(self):
        if self.scanning:
            return
        target = self.path_var.get().strip()
        if not target:
            messagebox.showwarning("提示", "请先选择一个文件夹。")
            return
        if not os.path.isdir(target):
            messagebox.showerror("错误", f"文件夹不存在：\n{target}")
            return

        self._clear_results()
        self.scanning = True
        self.scan_cancelled = False
        self._set_controls_state(scanning=True)

        self.status_var.set("正在分析文件夹结构...")
        self._start_animate_progress()

        self.empty_label.place_forget()

        thread = threading.Thread(target=self._scan_worker, args=(target,), daemon=True)
        thread.start()

    def _stop_scan(self):
        self.scan_cancelled = True
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
        self._on_scan_finished()

    def _scan_single_folder(self, folder_path: str) -> tuple[int, int]:
        name = os.path.basename(folder_path)
        self._post(self.status_var.set, f"正在扫描: {name}  [{folder_path}]")
        return get_folder_size(folder_path)

    def _scan_worker(self, target: str):
        subdirs = []
        try:
            with os.scandir(target) as entries:
                subdirs = [
                    e.path for e in entries if e.is_dir(follow_symlinks=False)
                ]
        except PermissionError:
            pass

        if not subdirs:
            self._post(self._scan_empty_result)
            return

        total_subdirs = len(subdirs)
        grand_total = 0
        results = []

        self._post(self.status_var.set,
                   f"正在分析 {total_subdirs} 个子文件夹...")

        self.executor = ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, 16))
        futures = {}
        for sd in subdirs:
            futures[self.executor.submit(self._scan_single_folder, sd)] = sd

        completed = 0
        try:
            for future in as_completed(futures):
                if self.scan_cancelled:
                    break
                sd = futures[future]
                name = os.path.basename(sd)
                try:
                    size, fcount = future.result()
                except Exception:
                    size, fcount = 0, 0
                results.append((name, sd, size, fcount))
                grand_total += size
                completed += 1
                self._post(
                    self.status_var.set,
                    f"已分析 {completed}/{total_subdirs}  —  {name}",
                )
        finally:
            if self.executor:
                self.executor.shutdown(wait=False, cancel_futures=True)
                self.executor = None

        if self.scan_cancelled:
            self._post(self._on_scan_finished)
            return

        results.sort(key=lambda r: r[2], reverse=True)

        self._post(self._populate_results, results, grand_total)

    def _populate_results(self, results: list, grand_total: int):
        if not self.scanning:
            return

        for name, full_path, size, fcount in results:
            pct = (size / grand_total * 100) if grand_total > 0 else 0
            item_id = self.tree.insert(
                "",
                tk.END,
                values=(
                    name,
                    format_size(size),
                    f"{fcount:,}",
                    f"{pct:.1f}%",
                ),
            )
            self._path_cache[item_id] = (full_path, size)

        self.status_var.set(
            f"完成 — {len(results)} 个子文件夹，合计 {format_size(grand_total)}"
        )
        self.empty_label.place_forget()

        if self._sort_state["column"]:
            self._sort_column(self._sort_state["column"], force=True)

        self._on_scan_finished()

    def _scan_empty_result(self):
        if not self.scanning:
            return
        self.status_var.set("该文件夹下没有子文件夹")
        self._on_scan_finished()

    def _on_scan_finished(self):
        self.scanning = False
        self.scan_cancelled = False
        self._set_controls_state(scanning=False)
        self._stop_animate_progress()

    def _start_animate_progress(self):
        self._progress_canvas.pack(side=tk.RIGHT, pady=4)
        self._progress_anim_id = self.root.after(20, self._step_animate_progress)

    def _step_animate_progress(self):
        if not self.scanning:
            return
        self._progress_pos += 3 * self._progress_dir
        limit = self._progress_width - self._progress_block
        if self._progress_pos >= limit:
            self._progress_pos = limit
            self._progress_dir = -1
        elif self._progress_pos <= 0:
            self._progress_pos = 0
            self._progress_dir = 1
        self._progress_canvas.coords(
            self._progress_rect,
            self._progress_pos, 0,
            self._progress_pos + self._progress_block, self._progress_h,
        )
        self._progress_anim_id = self.root.after(20, self._step_animate_progress)

    def _stop_animate_progress(self):
        if self._progress_anim_id is not None:
            self.root.after_cancel(self._progress_anim_id)
            self._progress_anim_id = None
        self._progress_canvas.pack_forget()

    def _set_controls_state(self, scanning: bool):
        state = tk.DISABLED if scanning else tk.NORMAL
        for widget in self.root.winfo_children():
            self._set_widget_state(widget, state)
        if scanning:
            self.action_btn.configure(
                text="停止分析", image=self.icons["stop"],
                command=self._stop_scan,
            )
        else:
            self.action_btn.configure(
                text="开始分析", image=self.icons["play"],
                command=self._start_scan,
            )

    def _set_widget_state(self, widget, state):
        if isinstance(widget, ttk.Button):
            if widget is self.action_btn:
                return
            widget.configure(state=state)
        elif isinstance(widget, ttk.Entry):
            widget.configure(state=state)
        elif isinstance(widget, ttk.Frame) or isinstance(widget, tk.Frame):
            for child in widget.winfo_children():
                self._set_widget_state(child, state)

    def _clear_results(self):
        self._path_cache.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.empty_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

    def _show_empty(self):
        self._clear_results()
        self.empty_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self.status_var.set("就绪")

    def _sort_column(self, column: str, force: bool = False):
        if not force and self._sort_state["column"] == column:
            self._sort_state["reverse"] = not self._sort_state["reverse"]
        else:
            self._sort_state["column"] = column
            self._sort_state["reverse"] = (column == "size")

        def parse_size(val: str) -> float:
            parts = val.split()
            if len(parts) != 2:
                return 0.0
            try:
                num = float(parts[0])
            except ValueError:
                return 0.0
            return num * UNIT_FACTORS.get(parts[1].upper(), 1)

        def sort_key(item_id: str):
            if column == "name":
                return self.tree.set(item_id, "name").lower()
            if column == "size":
                return parse_size(self.tree.set(item_id, "size"))
            if column == "files":
                val = self.tree.set(item_id, "files").replace(",", "")
                try:
                    return int(val)
                except ValueError:
                    return 0
            if column == "percent":
                val = self.tree.set(item_id, "percent").rstrip("%")
                try:
                    return float(val)
                except ValueError:
                    return 0.0
            return self.tree.set(item_id, column)

        items = list(self.tree.get_children(""))
        items.sort(key=sort_key, reverse=self._sort_state["reverse"])

        for idx, item_id in enumerate(items):
            self.tree.move(item_id, "", idx)

    def _on_tree_right_click(self, event):
        if self.scanning:
            return
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        self.tree.selection_set(item_id)
        cached = self._path_cache.get(item_id)
        full_path = cached[0] if isinstance(cached, tuple) else cached
        if not full_path or not os.path.isdir(full_path):
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="用文件浏览器打开",
            command=lambda fp=full_path: os.startfile(fp),
        )
        menu.add_command(
            label="进行分析",
            command=lambda: self._analyze_from_menu(full_path),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _on_tree_double_click(self, event):
        if self.scanning:
            return
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        cached = self._path_cache.get(item_id)
        full_path = cached[0] if isinstance(cached, tuple) else cached
        if not full_path or not os.path.isdir(full_path):
            return
        self._analyze_from_menu(full_path)

    def _analyze_from_menu(self, full_path: str):
        self.path_var.set(full_path)
        self._start_scan()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = FolderSpaceEagleEye()
    app.run()
