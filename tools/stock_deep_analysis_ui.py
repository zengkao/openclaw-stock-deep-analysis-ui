#!/usr/bin/env python3
from __future__ import annotations

import argparse
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from stock_deep_analysis_core import DEFAULT_REPORT_DIR, analyze, save_report


BG = "#0f172a"
CARD = "#111827"
INPUT = "#1f2937"
FG = "#e5e7eb"
MUTED = "#94a3b8"
ACCENT = "#38bdf8"
GOOD = "#86efac"
BAD = "#fda4af"
FONT_UI = ("Microsoft JhengHei UI", 10)
FONT_TITLE = ("Microsoft JhengHei UI", 14, "bold")
FONT_MONO = ("Consolas", 10)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Stock Deep Analysis UI")
        self.configure(bg=BG)
        self.minsize(920, 700)
        self.report_path: Path | None = None
        self.result: dict | None = None
        self._build()
        self._center(980, 760)

    def _center(self, width: int, height: int) -> None:
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{width}x{height}+{(sw-width)//2}+{(sh-height)//2}")

    def _build(self) -> None:
        header = tk.Frame(self, bg=BG, pady=12)
        header.pack(fill=tk.X, padx=18)
        tk.Label(header, text="股票深度分析", bg=BG, fg=ACCENT, font=FONT_TITLE).pack(anchor="w")
        tk.Label(
            header,
            text="從舊 gateway2 深度研究流程抽出的獨立分析 UI",
            bg=BG,
            fg=MUTED,
            font=FONT_UI,
        ).pack(anchor="w")

        controls = tk.Frame(self, bg=CARD, padx=14, pady=12)
        controls.pack(fill=tk.X, padx=18, pady=(0, 10))

        tk.Label(controls, text="股票代號", bg=CARD, fg=FG, font=FONT_UI).grid(row=0, column=0, sticky="w")
        self.code_var = tk.StringVar()
        code_entry = tk.Entry(
            controls,
            textvariable=self.code_var,
            width=12,
            bg=INPUT,
            fg=FG,
            insertbackground=FG,
            relief=tk.FLAT,
            font=("Consolas", 13, "bold"),
        )
        code_entry.grid(row=0, column=1, sticky="w", padx=(8, 14), ipady=4)
        code_entry.bind("<Return>", lambda _e: self.run_analysis())

        tk.Label(controls, text="輸出資料夾", bg=CARD, fg=FG, font=FONT_UI).grid(row=0, column=2, sticky="w")
        self.report_dir_var = tk.StringVar(value=str(DEFAULT_REPORT_DIR))
        tk.Entry(
            controls,
            textvariable=self.report_dir_var,
            width=46,
            bg=INPUT,
            fg=FG,
            insertbackground=FG,
            relief=tk.FLAT,
            font=FONT_UI,
        ).grid(row=0, column=3, sticky="ew", padx=(8, 8), ipady=4)
        tk.Button(
            controls,
            text="選擇",
            command=self.pick_dir,
            bg=INPUT,
            fg=FG,
            relief=tk.FLAT,
            padx=10,
        ).grid(row=0, column=4, sticky="w")

        self.auto_save_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            controls,
            text="分析後自動存成 MD",
            variable=self.auto_save_var,
            bg=CARD,
            fg=FG,
            activebackground=CARD,
            activeforeground=FG,
            selectcolor=INPUT,
            font=FONT_UI,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.run_btn = tk.Button(
            controls,
            text="開始分析",
            command=self.run_analysis,
            bg=ACCENT,
            fg="#082f49",
            relief=tk.FLAT,
            font=("Microsoft JhengHei UI", 10, "bold"),
            padx=14,
            pady=4,
        )
        self.run_btn.grid(row=1, column=3, sticky="e", pady=(8, 0))
        tk.Button(
            controls,
            text="另存報告",
            command=self.save_current,
            bg=INPUT,
            fg=FG,
            relief=tk.FLAT,
            padx=12,
            pady=4,
        ).grid(row=1, column=4, sticky="w", padx=(8, 0), pady=(8, 0))
        controls.grid_columnconfigure(3, weight=1)

        self.status_var = tk.StringVar(value="輸入股票代號後按「開始分析」。")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=MUTED, font=FONT_UI).pack(fill=tk.X, padx=18, pady=(0, 8))

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill=tk.X, padx=18, pady=(0, 10))

        self.text = scrolledtext.ScrolledText(
            self,
            bg=INPUT,
            fg=FG,
            font=FONT_MONO,
            relief=tk.FLAT,
            wrap=tk.WORD,
            padx=10,
            pady=10,
        )
        self.text.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))

    def pick_dir(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.report_dir_var.get() or str(Path.home()))
        if folder:
            self.report_dir_var.set(folder)

    def run_analysis(self) -> None:
        code = self.code_var.get().strip().upper()
        if not code:
            messagebox.showwarning("缺少代號", "請先輸入股票代號，例如 2324。")
            return
        if self.run_btn["state"] == tk.DISABLED:
            return
        self.run_btn.config(state=tk.DISABLED)
        self.status_var.set(f"正在分析 {code} ...")
        self.progress.start(12)
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, f"正在分析 {code} ...\n")
        threading.Thread(target=self._worker, args=(code,), daemon=True).start()

    def _worker(self, code: str) -> None:
        try:
            result = analyze(code)
            report_path = None
            if self.auto_save_var.get():
                report_path = save_report(result, Path(self.report_dir_var.get().strip()))
            self.after(0, self._done, result, report_path, None)
        except Exception as exc:
            self.after(0, self._done, None, None, exc)

    def _done(self, result: dict | None, report_path: Path | None, error: Exception | None) -> None:
        self.progress.stop()
        self.run_btn.config(state=tk.NORMAL)
        if error is not None:
            self.status_var.set(f"分析失敗: {error}")
            self.text.delete("1.0", tk.END)
            self.text.insert(tk.END, f"[分析失敗]\n{error}\n")
            return

        assert result is not None
        self.result = result
        self.report_path = report_path
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, result["report_text"])
        if report_path is not None:
            self.status_var.set(f"分析完成，已存檔: {report_path}")
        else:
            self.status_var.set("分析完成，尚未存檔。")

    def save_current(self) -> None:
        if not self.result:
            return
        try:
            self.report_path = save_report(self.result, Path(self.report_dir_var.get().strip()))
        except Exception as exc:
            messagebox.showerror("存檔失敗", str(exc))
            return
        self.status_var.set(f"已存檔: {self.report_path}")


def run_cli(code: str, report_dir: str, save: bool) -> int:
    result = analyze(code)
    path = None
    if save:
        path = save_report(result, Path(report_dir))
    print(result["report_text"])
    if path is not None:
        print(f"\n[SAVED] {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stock deep analysis UI / CLI")
    parser.add_argument("--code", help="股票代號，例如 2324")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="報告輸出資料夾")
    parser.add_argument("--save", action="store_true", help="CLI 模式時順便存成 MD")
    parser.add_argument("--no-gui", action="store_true", help="使用 CLI 模式")
    args = parser.parse_args()

    if args.no_gui or args.code:
        if not args.code:
            parser.error("CLI 模式必須搭配 --code")
        return run_cli(args.code, args.report_dir, args.save)

    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
