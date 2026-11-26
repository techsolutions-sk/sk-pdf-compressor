"""SK PDF Compressor - Windows desktop app (Tech Solutions)."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from engine import TARGETS, compress, human, output_name

__version__ = "1.1.0"

try:  # optional drag & drop
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    BaseTk = TkinterDnD.Tk
    HAS_DND = True
except Exception:  # pragma: no cover
    BaseTk = tk.Tk
    HAS_DND = False

APP_NAME = "SK PDF Compressor"
F = "Segoe UI"
WHITE = "#FFFFFF"
PAGE = "#FFFFFF"
BLUE = "#0B76D8"
BLUE_DARK = "#075BA8"
DROP_BG = "#E6F0FB"
DROP_BORDER = "#8DB8E6"
ORANGE = "#E07B12"
ORANGE_DARK = "#C86A08"
TEXT = "#1F2937"
MUTED = "#6B7280"
LINE = "#D1D5DB"
GREEN = "#15803D"
RED = "#DC2626"


def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def round_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class PillButton(tk.Canvas):
    """Rounded button drawn on a canvas (tkinter has no rounded buttons)."""

    def __init__(self, parent, text, command, bg, hover, fg=WHITE, w=200, h=46,
                 font=(F, 13, "bold"), parent_bg=PAGE, icon=""):
        super().__init__(parent, width=w, height=h, bg=parent_bg, highlightthickness=0,
                         cursor="hand2")
        self.cmd, self.bg_c, self.hover_c, self.enabled = command, bg, hover, True
        self.shape = round_rect(self, 1, 1, w - 1, h - 1, h // 2, fill=bg, outline=bg)
        self.label = self.create_text(w // 2, h // 2, text=(icon + "  " + text) if icon else text,
                                      fill=fg, font=font)
        self.bind("<Enter>", lambda e: self.enabled and self.itemconfig(self.shape, fill=hover, outline=hover))
        self.bind("<Leave>", lambda e: self.itemconfig(self.shape, fill=self.bg_c, outline=self.bg_c))
        self.bind("<Button-1>", lambda e: self.enabled and self.cmd())

    def set_text(self, text):
        self.itemconfig(self.label, text=text)

    def set_enabled(self, on: bool):
        self.enabled = on
        col = self.bg_c if on else "#9CA3AF"
        self.itemconfig(self.shape, fill=col, outline=col)
        self.configure(cursor="hand2" if on else "arrow")


class FileCard(tk.Frame):
    """One selected PDF: icon, name, size, X (remove) button, status line."""

    W, H = 190, 150

    def __init__(self, parent, app, path):
        super().__init__(parent, bg=WHITE, highlightthickness=1, highlightbackground=LINE,
                         width=self.W, height=self.H)
        self.pack_propagate(False)
        self.app, self.path, self.result = app, path, None

        icon = tk.Canvas(self, width=46, height=56, bg=WHITE, highlightthickness=0)
        icon.place(x=self.W // 2 - 23, y=14)
        icon.create_polygon(2, 2, 32, 2, 44, 14, 44, 54, 2, 54, fill="#FDECEC", outline=RED, width=2)
        icon.create_polygon(32, 2, 32, 14, 44, 14, fill=RED, outline=RED)
        icon.create_rectangle(6, 30, 40, 44, fill=RED, outline=RED)
        icon.create_text(23, 37, text="PDF", fill=WHITE, font=(F, 8, "bold"))

        name = os.path.basename(path)
        short = name if len(name) <= 24 else name[:21] + "..."
        tk.Label(self, text=short, bg=WHITE, fg=TEXT, font=(F, 9, "bold")).place(
            relx=0.5, y=82, anchor="n")
        self.size_lbl = tk.Label(self, text=human(os.path.getsize(path)), bg=WHITE, fg=MUTED,
                                 font=(F, 9))
        self.size_lbl.place(relx=0.5, y=102, anchor="n")
        self.status = tk.Label(self, text="", bg=WHITE, fg=BLUE, font=(F, 9, "bold"))
        self.status.place(relx=0.5, y=122, anchor="n")

        # X close button (top-right)
        self.x_btn = tk.Canvas(self, width=24, height=24, bg=WHITE, highlightthickness=0, cursor="hand2")
        self.x_btn.place(x=self.W - 30, y=4)
        self._xc = self.x_btn.create_oval(2, 2, 22, 22, fill="#F3F4F6", outline="#F3F4F6")
        self.x_btn.create_text(12, 12, text="✕", fill="#4B5563", font=(F, 9, "bold"))
        self.x_btn.bind("<Enter>", lambda e: self.x_btn.itemconfig(self._xc, fill="#FEE2E2", outline="#FEE2E2"))
        self.x_btn.bind("<Leave>", lambda e: self.x_btn.itemconfig(self._xc, fill="#F3F4F6", outline="#F3F4F6"))
        self.x_btn.bind("<Button-1>", lambda e: self.app.remove_file(self))

    def set_status(self, text, color=BLUE):
        self.status.configure(text=text, fg=color)

    def set_done(self, r):
        self.result = r
        if r.mode == "error":
            self.set_status("Failed", RED)
            return
        self.size_lbl.configure(text=f"{human(r.input_size)}  →  {human(r.output_size)}",
                                fg=GREEN if r.ok else ORANGE, font=(F, 9, "bold"))
        self.set_status(f"-{r.saved_percent:.0f}%   Open ↗", GREEN if r.ok else ORANGE)
        self.status.configure(cursor="hand2")
        self.status.bind("<Button-1>", lambda e: open_path(r.output_path))


def open_path(p):
    if sys.platform == "win32":
        os.startfile(p)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", p])


class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}")
        self.geometry("900x760")
        self.minsize(820, 700)
        self.configure(bg=PAGE)
        try:
            self.iconbitmap(resource_path("app.ico"))
        except Exception:
            pass

        self.cards: list[FileCard] = []
        self.sel = 1  # default 1 MB
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.out_dir = ""

        self._header()
        self.body = tk.Frame(self, bg=PAGE)
        self.body.pack(fill="both", expand=True, padx=28)
        self._title()
        self.drop_holder = tk.Frame(self.body, bg=PAGE)
        self.drop_holder.pack(fill="x", pady=(10, 0))
        self._build_drop()
        self._build_files()
        self._build_options()
        self._footer()
        self._refresh()
        self.after(100, self._poll)

    # ------------------------------------------------------------ layout
    def _header(self):
        bar = tk.Frame(self, bg=WHITE)
        bar.pack(fill="x")
        tk.Label(bar, text="SK PDF Compressor", bg=WHITE, fg=BLUE, font=(F, 16, "bold")).pack(
            side="left", padx=20, pady=10)
        tk.Label(bar, text="by Tech Solutions", bg=WHITE, fg=MUTED, font=(F, 9)).pack(
            side="left", pady=(16, 10))
        tk.Frame(self, bg=BLUE, height=2).pack(fill="x")

    def _title(self):
        tk.Label(self.body, text="Compress PDF", bg=PAGE, fg="#111827", font=(F, 24, "bold")).pack(
            pady=(18, 2))
        tk.Label(self.body, text="Make your PDF smaller - choose the size, keep the quality",
                 bg=PAGE, fg=TEXT, font=(F, 11)).pack()
        row = tk.Frame(self.body, bg=PAGE)
        row.pack(pady=(6, 0))
        for t in ("Free", "Offline", "No limits", "Secure"):
            tk.Label(row, text="✓ " + t, bg=PAGE, fg="#374151", font=(F, 9)).pack(side="left", padx=8)

    def _dashed_panel(self, parent, height):
        c = tk.Canvas(parent, height=height, bg=PAGE, highlightthickness=0)
        c.pack(fill="x")

        def redraw(_e=None):
            c.delete("panel")
            w = c.winfo_width()
            round_rect(c, 2, 2, w - 2, height - 2, 14, fill=DROP_BG, outline=DROP_BG, tags="panel")
            c.create_rectangle(10, 10, w - 10, height - 10, outline=DROP_BORDER, dash=(3, 3),
                               tags="panel")
            c.tag_lower("panel")
        c.bind("<Configure>", redraw)
        return c

    def _build_drop(self):
        self.drop = tk.Frame(self.drop_holder, bg=PAGE)
        c = self._dashed_panel(self.drop, 200)
        btn = PillButton(c, "Choose files", self.choose, ORANGE, ORANGE_DARK, w=220, h=50,
                         parent_bg=DROP_BG, icon="\U0001F4C4")
        c.create_window(0, 0, window=btn, tags="btn")
        lbl = tk.Label(c, text="... or drop PDF files here" if HAS_DND else "Select one or more PDF files",
                       bg=DROP_BG, fg=TEXT, font=(F, 10))
        c.create_window(0, 0, window=lbl, tags="lbl")
        small = tk.Label(c, text="\U0001F512 Files stay on your computer", bg=DROP_BG, fg=MUTED, font=(F, 9))
        c.create_window(0, 0, window=small, tags="small", anchor="w")

        def place(_e=None):
            w = c.winfo_width()
            c.coords("btn", w // 2, 78)
            c.coords("lbl", w // 2, 126)
            c.coords("small", 22, 178)
        c.bind("<Configure>", lambda e: place(), add="+")
        self.after(50, place)
        if HAS_DND:
            for w in (c, lbl, small):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

    def _build_files(self):
        self.files_box = tk.Frame(self.drop_holder, bg=DROP_BG, highlightthickness=1,
                                  highlightbackground=DROP_BORDER)
        self.grid_fr = tk.Frame(self.files_box, bg=DROP_BG)
        self.grid_fr.pack(fill="x", padx=14, pady=14)
        if HAS_DND:
            self.files_box.drop_target_register(DND_FILES)
            self.files_box.dnd_bind("<<Drop>>", self._on_drop)

    def _build_options(self):
        self.opt = tk.Frame(self.body, bg=PAGE)
        tk.Label(self.opt, text="Choose the size you need", bg=PAGE, fg="#111827",
                 font=(F, 12, "bold")).pack(pady=(16, 8))
        row = tk.Frame(self.opt, bg=PAGE)
        row.pack()
        self.size_btns = []
        for i, (label, _) in enumerate(TARGETS):
            c = tk.Canvas(row, width=120, height=58, bg=PAGE, highlightthickness=0, cursor="hand2")
            c.pack(side="left", padx=6)
            c.bind("<Button-1>", lambda e, k=i: self._select(k))
            self.size_btns.append((c, label))
        self.hint = tk.Label(self.opt, text="", bg=PAGE, fg=MUTED, font=(F, 9))
        self.hint.pack(pady=(8, 0))
        self.go = PillButton(self.opt, "Compress", self.start, BLUE, BLUE_DARK, w=240, h=50)
        self.go.pack(pady=(16, 4))
        self.status = tk.Label(self.opt, text="", bg=PAGE, fg=MUTED, font=(F, 9))
        self.status.pack()
        self.folder_btn = tk.Label(self.opt, text="\U0001F4C2  Open output folder", bg=PAGE, fg=BLUE,
                                   font=(F, 10, "underline"), cursor="hand2")
        self.folder_btn.bind("<Button-1>", lambda e: self.out_dir and open_path(self.out_dir))
        self._draw_sizes()

    def _footer(self):
        tk.Label(self, text=f"SK PDF Compressor v{__version__}  •  Tech Solutions",
                 bg=PAGE, fg="#9CA3AF", font=(F, 8)).pack(side="bottom", pady=6)

    def _draw_sizes(self):
        for i, (c, label) in enumerate(self.size_btns):
            c.delete("all")
            on = i == self.sel
            round_rect(c, 2, 2, 118, 56, 12, fill="#E0EDFB" if on else WHITE,
                       outline=BLUE if on else LINE, width=2 if on else 1)
            c.create_text(60, 23, text=label, fill=BLUE if on else TEXT, font=(F, 14, "bold"))
            c.create_text(60, 43, text="max size", fill=BLUE if on else MUTED, font=(F, 8))
        label, target = TARGETS[self.sel]
        self.hint.configure(
            text=f"Output will be just under {label} (about {human(int(target * 0.95))} - {human(target)}). "
                 "Text stays sharp, images at the best quality that fits.")

    def _select(self, i):
        if not self.busy:
            self.sel = i
            self._draw_sizes()

    # ------------------------------------------------------------ files
    def _refresh(self):
        for w in self.grid_fr.winfo_children():
            w.grid_forget()
        if self.cards:
            self.drop.pack_forget()
            self.files_box.pack(fill="x")
            cols = 4
            for i, card in enumerate(self.cards):
                card.grid(row=i // cols, column=i % cols, padx=7, pady=7)
            n = len(self.cards)
            add = self._add_tile()
            add.grid(row=n // cols, column=n % cols, padx=7, pady=7)
            self.opt.pack(fill="x")
        else:
            self.files_box.pack_forget()
            self.drop.pack(fill="x")
            self.opt.pack_forget()

    def _add_tile(self):
        t = tk.Frame(self.grid_fr, bg=DROP_BG, width=FileCard.W, height=FileCard.H,
                     highlightthickness=1, highlightbackground=DROP_BORDER, cursor="hand2")
        t.pack_propagate(False)
        l1 = tk.Label(t, text="+", bg=DROP_BG, fg=BLUE, font=(F, 30), cursor="hand2")
        l1.pack(pady=(26, 0))
        l2 = tk.Label(t, text="Add more files", bg=DROP_BG, fg=BLUE, font=(F, 10, "bold"), cursor="hand2")
        l2.pack()
        for w in (t, l1, l2):
            w.bind("<Button-1>", lambda e: self.choose())
        return t

    def add_files(self, paths):
        have = {c.path for c in self.cards}
        bad = 0
        for p in paths:
            if not (p.lower().endswith(".pdf") and os.path.isfile(p)):
                bad += 1
                continue
            if p not in have:
                self.cards.append(FileCard(self.grid_fr, self, p))
                have.add(p)
        if bad and not self.cards:
            messagebox.showwarning(APP_NAME, "Please choose PDF files.")
        self.folder_btn.pack_forget()
        self.status.configure(text="")
        self._refresh()

    def remove_file(self, card):
        if self.busy:
            return
        self.cards.remove(card)
        card.destroy()
        self._refresh()

    def choose(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(title="Select PDF files", filetypes=[("PDF files", "*.pdf")])
        if paths:
            self.add_files(list(paths))

    def _on_drop(self, event):
        if not self.busy:
            self.add_files(list(self.tk.splitlist(event.data)))

    # ------------------------------------------------------------ work
    def start(self):
        if self.busy or not self.cards:
            return
        self.busy = True
        self.go.set_enabled(False)
        self.go.set_text("Compressing...")
        self.folder_btn.pack_forget()
        label, target = TARGETS[self.sel]
        for c in self.cards:
            c.set_status("Waiting...", MUTED)
        threading.Thread(target=self._worker, args=(list(self.cards), label, target), daemon=True).start()

    def _worker(self, cards, label, target):
        for i, card in enumerate(cards):
            dst = output_name(card.path, label)

            def prog(p, msg, card=card, i=i):
                self.q.put(("prog", card, p, f"File {i + 1} of {len(cards)}: {msg}"))

            res = compress(card.path, dst, target, prog)
            self.q.put(("done", card, res))
        self.q.put(("all",))

    def _poll(self):
        try:
            while True:
                it = self.q.get_nowait()
                if it[0] == "prog":
                    it[1].set_status(f"{it[2]}%")
                    self.status.configure(text=it[3], fg=MUTED)
                elif it[0] == "done":
                    it[1].set_done(it[2])
                    if it[2].mode != "error":
                        self.out_dir = os.path.dirname(it[2].output_path)
                elif it[0] == "all":
                    self.busy = False
                    self.go.set_enabled(True)
                    self.go.set_text("Compress")
                    fails = [c for c in self.cards if c.result and c.result.mode == "error"]
                    msg = "Done! Click 'Open' on a file to view it." if not fails else \
                        fails[0].result.message
                    notes = [c.result.message for c in self.cards
                             if c.result and c.result.mode != "error" and not c.result.ok]
                    if notes and not fails:
                        msg = notes[0]
                    self.status.configure(text=msg, fg=GREEN if not fails and not notes else ORANGE,
                                          wraplength=760)
                    if self.out_dir:
                        self.folder_btn.pack(pady=(6, 0))
        except queue.Empty:
            pass
        self.after(100, self._poll)


def main():
    app = App()
    if os.environ.get("SK_SCREENSHOT"):  # CI: capture UI screenshots for review
        _ci_screens(app, sys.argv[1:])
    elif len(sys.argv) > 1:
        app.add_files(sys.argv[1:])
    app.mainloop()


def _ci_screens(app, files):
    from PIL import ImageGrab
    out = os.environ["SK_SCREENSHOT"]

    def grab(name):
        app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        ImageGrab.grab((x, y, x + app.winfo_width(), y + app.winfo_height())).save(
            os.path.join(out, name))

    def step0():
        grab("0_home.png")
        app.add_files(files)
        app.after(800, step1)

    def step1():
        grab("1_files_selected.png")
        app.start()
        app.after(500, wait_done)

    def wait_done():
        if app.busy:
            app.after(500, wait_done)
        else:
            grab("2_done.png")
            app.destroy()

    app.after(1500, step0)


if __name__ == "__main__":
    main()
