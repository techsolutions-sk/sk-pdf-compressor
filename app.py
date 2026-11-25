"""SK PDF Compressor - Windows desktop app (Tech Solutions)."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

__version__ = "1.0.0"
from engine import TARGETS, compress, human, output_name

try:  # optional drag & drop
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    BaseTk = TkinterDnD.Tk
    HAS_DND = True
except Exception:  # pragma: no cover
    BaseTk = tk.Tk
    HAS_DND = False

APP_NAME = "SK PDF Compressor"
BG = "#F5F7FB"
HEADER = "#1E3A8A"
ACCENT = "#2563EB"
CARD = "#FFFFFF"
CARD_SEL = "#DBEAFE"
TEXT = "#111827"
MUTED = "#6B7280"
OK = "#059669"
WARN = "#D97706"


def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{__version__}")
        self.geometry("760x640")
        self.minsize(680, 580)
        self.configure(bg=BG)
        try:
            self.iconbitmap(resource_path("app.ico"))
        except Exception:
            pass

        self.files: list[str] = []
        self.target_idx = tk.IntVar(value=1)  # default 1 MB
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.last_out_dir = ""

        self._style()
        self._build()
        self.after(100, self._poll)

    # ------------------------------------------------------------------ UI
    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure("TProgressbar", troughcolor="#E5E7EB", background=ACCENT, thickness=10)

    def _build(self):
        header = tk.Frame(self, bg=HEADER, height=70)
        header.pack(fill="x")
        tk.Label(header, text="SK PDF Compressor", bg=HEADER, fg="white",
                 font=("Segoe UI Semibold", 18)).pack(side="left", padx=20, pady=14)
        tk.Label(header, text="Smaller size  •  Same quality", bg=HEADER, fg="#BFDBFE",
                 font=("Segoe UI", 10)).pack(side="left", pady=(22, 0))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=14)

        # --- Step 1: files
        tk.Label(body, text="1.  Select PDF file(s)", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 11)).pack(anchor="w")
        drop = tk.Frame(body, bg=CARD, highlightthickness=2, highlightbackground="#CBD5E1")
        drop.pack(fill="x", pady=(6, 12))
        self.drop_lbl = tk.Label(
            drop, bg=CARD, fg=MUTED, font=("Segoe UI", 10), justify="center",
            text=("Drag & drop PDF files here\nor click to choose" if HAS_DND
                  else "Click here to choose PDF files"), height=3, cursor="hand2")
        self.drop_lbl.pack(fill="x", padx=10, pady=8)
        self.drop_lbl.bind("<Button-1>", lambda e: self.choose())
        if HAS_DND:
            self.drop_lbl.drop_target_register(DND_FILES)
            self.drop_lbl.dnd_bind("<<Drop>>", self._on_drop)

        # --- Step 2: size
        tk.Label(body, text="2.  Choose maximum file size", bg=BG, fg=TEXT,
                 font=("Segoe UI Semibold", 11)).pack(anchor="w")
        cards = tk.Frame(body, bg=BG)
        cards.pack(fill="x", pady=(6, 12))
        self.card_widgets = []
        for i, (label, _) in enumerate(TARGETS):
            f = tk.Frame(cards, bg=CARD, highlightthickness=2,
                         highlightbackground="#CBD5E1", cursor="hand2")
            f.grid(row=0, column=i, padx=4, sticky="nsew")
            cards.grid_columnconfigure(i, weight=1)
            l1 = tk.Label(f, text=label, bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 14), cursor="hand2")
            l1.pack(pady=(10, 0))
            l2 = tk.Label(f, text="max size", bg=CARD, fg=MUTED, font=("Segoe UI", 8), cursor="hand2")
            l2.pack(pady=(0, 10))
            for w in (f, l1, l2):
                w.bind("<Button-1>", lambda e, k=i: self._select(k))
            self.card_widgets.append((f, l1, l2))
        self._select(self.target_idx.get())

        # --- Step 3: go
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(4, 8))
        self.go_btn = tk.Button(row, text="Compress PDF", command=self.start, bg=ACCENT, fg="white",
                                activebackground="#1D4ED8", activeforeground="white", relief="flat",
                                font=("Segoe UI Semibold", 12), padx=24, pady=8, cursor="hand2")
        self.go_btn.pack(side="left")
        self.open_btn = tk.Button(row, text="Open output folder", command=self.open_folder,
                                  relief="flat", bg="#E5E7EB", font=("Segoe UI", 10),
                                  padx=14, pady=8, state="disabled", cursor="hand2")
        self.open_btn.pack(side="left", padx=10)

        self.progress = ttk.Progressbar(body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(6, 2))
        self.status = tk.Label(body, text="Ready", bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.status.pack(fill="x")

        # --- Results
        tk.Label(body, text="Results", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(10, 4))
        self.results = tk.Text(body, height=8, bg=CARD, fg=TEXT, relief="flat", font=("Consolas", 9),
                               highlightthickness=1, highlightbackground="#CBD5E1", wrap="word")
        self.results.pack(fill="both", expand=True)
        self.results.tag_configure("ok", foreground=OK)
        self.results.tag_configure("warn", foreground=WARN)
        self.results.configure(state="disabled")

        tk.Label(self, text="Tech Solutions  •  Your files never leave your computer",
                 bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(pady=(0, 8))

    def _select(self, idx: int):
        self.target_idx.set(idx)
        for i, (f, l1, l2) in enumerate(self.card_widgets):
            sel = i == idx
            bg = CARD_SEL if sel else CARD
            f.configure(bg=bg, highlightbackground=ACCENT if sel else "#CBD5E1")
            l1.configure(bg=bg, fg=ACCENT if sel else TEXT)
            l2.configure(bg=bg)

    # ------------------------------------------------------------- actions
    def _set_files(self, paths):
        pdfs = [p for p in paths if p.lower().endswith(".pdf") and os.path.isfile(p)]
        if not pdfs:
            messagebox.showwarning(APP_NAME, "Please choose PDF files.")
            return
        self.files = pdfs
        total = sum(os.path.getsize(p) for p in pdfs)
        if len(pdfs) == 1:
            txt = f"{os.path.basename(pdfs[0])}\n{human(total)}"
        else:
            txt = f"{len(pdfs)} PDF files selected\nTotal {human(total)}"
        self.drop_lbl.configure(text=txt, fg=TEXT)

    def choose(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(title="Select PDF files", filetypes=[("PDF files", "*.pdf")])
        if paths:
            self._set_files(list(paths))

    def _on_drop(self, event):
        if not self.busy:
            self._set_files(list(self.tk.splitlist(event.data)))

    def start(self):
        if self.busy:
            return
        if not self.files:
            self.choose()
            if not self.files:
                return
        label, target = TARGETS[self.target_idx.get()]
        self.busy = True
        self.go_btn.configure(state="disabled", text="Compressing…")
        self._clear_results()
        threading.Thread(target=self._worker, args=(list(self.files), label, target), daemon=True).start()

    def _worker(self, files, label, target):
        n = len(files)
        for i, src in enumerate(files):
            name = os.path.basename(src)
            dst = output_name(src, label)

            def prog(p, msg, i=i):
                self.q.put(("progress", (i * 100 + p) / n, f"[{i + 1}/{n}] {name}: {msg}"))

            res = compress(src, dst, target, prog)
            self.q.put(("result", name, res))
        self.q.put(("done",))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "progress":
                    self.progress["value"] = item[1]
                    self.status.configure(text=item[2])
                elif item[0] == "result":
                    self._show_result(item[1], item[2])
                elif item[0] == "done":
                    self.busy = False
                    self.progress["value"] = 100
                    self.status.configure(text="Finished")
                    self.go_btn.configure(state="normal", text="Compress PDF")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _clear_results(self):
        self.results.configure(state="normal")
        self.results.delete("1.0", "end")
        self.results.configure(state="disabled")
        self.progress["value"] = 0

    def _show_result(self, name, r):
        self.results.configure(state="normal")
        if r.mode == "error":
            self.results.insert("end", f"✗ {name}\n   {r.message}\n\n", "warn")
        else:
            tag = "ok" if r.ok else "warn"
            mark = "✓" if r.ok else "!"
            self.results.insert("end", f"{mark} {name}\n", tag)
            self.results.insert(
                "end",
                f"   {human(r.input_size)}  →  {human(r.output_size)}   (-{r.saved_percent:.0f}%)\n"
                f"   Quality: {r.level}\n   {r.message}\n   Saved: {r.output_path}\n\n")
            self.last_out_dir = os.path.dirname(r.output_path)
            self.open_btn.configure(state="normal")
        self.results.configure(state="disabled")
        self.results.see("end")

    def open_folder(self):
        if self.last_out_dir and os.path.isdir(self.last_out_dir):
            if sys.platform == "win32":
                os.startfile(self.last_out_dir)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", self.last_out_dir])


def main():
    app = App()
    if len(sys.argv) > 1:
        app._set_files(sys.argv[1:])
    app.mainloop()


if __name__ == "__main__":
    main()
