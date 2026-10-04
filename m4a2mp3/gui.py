"""tkinter の画面。処理は別スレッドで行い、結果はキュー経由で画面に反映する。"""

from __future__ import annotations

import datetime
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import APP_NAME, __version__
from . import ffmpeg_tools as ff
from . import settings as settings_mod
from .processor import Options, Processor, format_summary

try:  # ドラッグ&ドロップは tkinterdnd2 が入っている場合のみ有効
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    TkinterDnD = None

BITRATES = (128, 192, 256, 320)
LEVEL_COLORS = {
    "OK": "#1b7f3b",
    "RENAMED": "#1f5fbf",
    "SKIP": "#b36b00",
    "WARN": "#8a3fb3",
    "FAIL": "#c62828",
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = settings_mod.load()
        self.tools = ff.find_tools(self.settings.ffmpeg_path)
        self.queue: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.log_file = None
        self.log_path: Path | None = None
        self.closing = False

        root.title(f"{APP_NAME} - m4a 連番削除 & MP3 変換  v{__version__}")
        root.geometry("900x680")
        root.minsize(720, 520)
        self._build()
        self._update_ffmpeg_label()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll)
        if not self.tools:
            root.after(300, self._show_ffmpeg_dialog)

    # ---------- 画面の構築 ----------

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)

        # 対象フォルダ
        folders = ttk.LabelFrame(main, text="対象フォルダ", padding=6)
        folders.pack(fill="x", **pad)
        list_frame = ttk.Frame(folders)
        list_frame.pack(side="left", fill="both", expand=True)
        self.folder_list = tk.Listbox(list_frame, height=6, selectmode="extended", activestyle="none")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.folder_list.yview)
        self.folder_list.configure(yscrollcommand=scroll.set)
        self.folder_list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        for folder in self.settings.folders:
            self.folder_list.insert("end", folder)

        buttons = ttk.Frame(folders)
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="追加...", command=self._add_folder).pack(fill="x", pady=2)
        ttk.Button(buttons, text="削除", command=self._remove_selected).pack(fill="x", pady=2)
        ttk.Button(buttons, text="すべて削除", command=lambda: self.folder_list.delete(0, "end")).pack(fill="x", pady=2)

        if TkinterDnD is not None:
            self.folder_list.drop_target_register(DND_FILES)
            self.folder_list.dnd_bind("<<Drop>>", self._on_drop)
            hint = "フォルダをここにドラッグ&ドロップしても追加できます"
        else:
            hint = "「追加...」でフォルダを選んでください"
        ttk.Label(main, text=hint, foreground="#666").pack(anchor="w", padx=12)

        # オプション
        opts = ttk.LabelFrame(main, text="オプション", padding=6)
        opts.pack(fill="x", **pad)
        s = self.settings
        self.var_recursive = tk.BooleanVar(value=s.recursive)
        self.var_rename = tk.BooleanVar(value=s.rename)
        self.var_convert = tk.BooleanVar(value=s.convert)
        self.var_overwrite = tk.BooleanVar(value=s.overwrite)
        self.var_bitrate = tk.StringVar(value=str(s.bitrate if s.bitrate in BITRATES else 320))
        ttk.Checkbutton(opts, text="サブフォルダも処理する", variable=self.var_recursive).grid(row=0, column=0, sticky="w", padx=4)
        ttk.Checkbutton(opts, text="連番 (_001 など) を削除する", variable=self.var_rename).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Checkbutton(opts, text="MP3 に変換する", variable=self.var_convert).grid(row=0, column=2, sticky="w", padx=4)
        ttk.Label(opts, text="ビットレート:").grid(row=1, column=0, sticky="w", padx=4, pady=(4, 0))
        bitrate_frame = ttk.Frame(opts)
        bitrate_frame.grid(row=1, column=1, sticky="w", pady=(4, 0))
        ttk.Combobox(bitrate_frame, textvariable=self.var_bitrate, values=[str(b) for b in BITRATES],
                     width=6, state="readonly").pack(side="left")
        ttk.Label(bitrate_frame, text="kbps (CBR)").pack(side="left", padx=4)
        ttk.Checkbutton(opts, text="既存の MP3 を上書きする", variable=self.var_overwrite).grid(row=1, column=2, sticky="w", padx=4, pady=(4, 0))

        # ffmpeg の状態
        ff_frame = ttk.Frame(main)
        ff_frame.pack(fill="x", **pad)
        self.ffmpeg_label = ttk.Label(ff_frame, text="")
        self.ffmpeg_label.pack(side="left")
        ttk.Button(ff_frame, text="ffmpeg 設定...", command=self._show_ffmpeg_dialog).pack(side="right")

        # 実行ボタン
        actions = ttk.Frame(main)
        actions.pack(fill="x", **pad)
        self.btn_dry = ttk.Button(actions, text="実行前に確認", command=lambda: self._start(dry_run=True))
        self.btn_run = ttk.Button(actions, text="実行", command=lambda: self._start(dry_run=False))
        self.btn_cancel = ttk.Button(actions, text="中止", command=self._request_cancel, state="disabled")
        self.btn_dry.pack(side="left")
        self.btn_run.pack(side="left", padx=6)
        self.btn_cancel.pack(side="left")
        self.progress_label = ttk.Label(actions, text="")
        self.progress_label.pack(side="right")

        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.pack(fill="x", **pad)

        # ログ
        log_frame = ttk.LabelFrame(main, text="ログ", padding=4)
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = ScrolledText(log_frame, height=16, wrap="none", font=("BIZ UDGothic", 9), state="disabled")
        self.log_text.pack(fill="both", expand=True)
        for level, color in LEVEL_COLORS.items():
            self.log_text.tag_configure(level, foreground=color)

    # ---------- フォルダ操作 ----------

    def _folders(self) -> list[str]:
        return list(self.folder_list.get(0, "end"))

    def _add_paths(self, paths) -> None:
        existing = {p.lower() for p in self._folders()}
        for p in paths:
            path = Path(p)
            if path.is_file():
                path = path.parent
            if path.is_dir() and str(path).lower() not in existing:
                self.folder_list.insert("end", str(path))
                existing.add(str(path).lower())

    def _add_folder(self) -> None:
        initial = self.settings.last_dir or (self._folders()[-1] if self._folders() else "")
        folder = filedialog.askdirectory(parent=self.root, title="対象フォルダを選択", initialdir=initial or None)
        if folder:
            folder = str(Path(folder))
            self.settings.last_dir = str(Path(folder).parent)
            self._add_paths([folder])

    def _remove_selected(self) -> None:
        for index in reversed(self.folder_list.curselection()):
            self.folder_list.delete(index)

    def _on_drop(self, event) -> None:
        self._add_paths(self.root.tk.splitlist(event.data))

    # ---------- ffmpeg ----------

    def _update_ffmpeg_label(self) -> None:
        if self.tools:
            self.ffmpeg_label.configure(text=f"ffmpeg: {self.tools.ffmpeg}", foreground="")
        else:
            self.ffmpeg_label.configure(text="ffmpeg: 見つかりません (MP3 変換には ffmpeg が必要です)", foreground="#c62828")

    def _show_ffmpeg_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("ffmpeg の設定")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)

        status = ttk.Label(frame, wraplength=520, justify="left")
        status.pack(anchor="w")

        def refresh_status() -> None:
            self._update_ffmpeg_label()
            if self.tools:
                status.configure(text=f"ffmpeg を使用します:\n{self.tools.ffmpeg}", foreground="")
            else:
                status.configure(text="ffmpeg が見つかりませんでした。MP3 への変換には ffmpeg が必要です。",
                                 foreground="#c62828")
        refresh_status()

        ttk.Label(frame, wraplength=520, justify="left",
                  text="インストールしていない場合は、PowerShell で次のコマンドを実行してから「再検出」を押してください。"
                  ).pack(anchor="w", pady=(10, 4))
        cmd_frame = ttk.Frame(frame)
        cmd_frame.pack(fill="x")
        cmd = ttk.Entry(cmd_frame, width=56)
        cmd.insert(0, ff.WINGET_INSTALL_COMMAND)
        cmd.configure(state="readonly")
        cmd.pack(side="left", fill="x", expand=True)

        def copy_command() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(ff.WINGET_INSTALL_COMMAND)
        ttk.Button(cmd_frame, text="コピー", command=copy_command).pack(side="left", padx=(6, 0))
        ttk.Label(frame, wraplength=520, justify="left", foreground="#666",
                  text="※ --source winget を付けないと、環境によっては msstore ソースの証明書エラー (0x8a15005e) で失敗します。"
                  ).pack(anchor="w", pady=(4, 10))

        def choose() -> None:
            path = filedialog.askopenfilename(parent=dialog, title="ffmpeg.exe を選択",
                                              filetypes=[("ffmpeg.exe", "ffmpeg.exe"), ("実行ファイル", "*.exe")])
            if not path:
                return
            tools = ff.tools_from_path(path)
            if tools is None:
                messagebox.showerror("ffmpeg の設定", "同じフォルダに ffprobe.exe が見つかりません。", parent=dialog)
                return
            self.tools = tools
            self.settings.ffmpeg_path = str(tools.ffmpeg)
            settings_mod.save(self.settings)
            refresh_status()

        def redetect() -> None:
            self.tools = ff.find_tools(self.settings.ffmpeg_path)
            refresh_status()
            if not self.tools:
                messagebox.showwarning("ffmpeg の設定", "まだ見つかりません。インストールが終わっているか確認してください。",
                                       parent=dialog)

        btns = ttk.Frame(frame)
        btns.pack(fill="x")
        ttk.Button(btns, text="ffmpeg.exe を指定...", command=choose).pack(side="left")
        ttk.Button(btns, text="再検出", command=redetect).pack(side="left", padx=6)
        ttk.Button(btns, text="閉じる", command=dialog.destroy).pack(side="right")
        dialog.grab_set()

    # ---------- 実行 ----------

    def _options(self) -> Options:
        return Options(recursive=self.var_recursive.get(), rename=self.var_rename.get(),
                       convert=self.var_convert.get(), bitrate=int(self.var_bitrate.get()),
                       overwrite=self.var_overwrite.get())

    def _save_settings(self) -> None:
        o = self._options()
        s = self.settings
        s.folders = self._folders()
        s.recursive, s.rename, s.convert, s.bitrate, s.overwrite = o.recursive, o.rename, o.convert, o.bitrate, o.overwrite
        settings_mod.save(s)

    def _start(self, dry_run: bool) -> None:
        folders = self._folders()
        options = self._options()
        if not folders:
            messagebox.showinfo(APP_NAME, "対象フォルダを追加してください。", parent=self.root)
            return
        if not options.rename and not options.convert:
            messagebox.showinfo(APP_NAME, "「連番を削除する」か「MP3 に変換する」のどちらかを選んでください。", parent=self.root)
            return
        if options.convert and not self.tools:
            self._show_ffmpeg_dialog()
            return
        if options.overwrite and not dry_run and not messagebox.askyesno(
                APP_NAME, "既存の MP3 を上書きします。よろしいですか?", parent=self.root):
            return
        self._save_settings()

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._open_log_file(dry_run)
        self.progress.configure(value=0, maximum=1)
        self.progress_label.configure(text="")
        self._set_running(True)
        self.cancel.clear()

        title = "実行前の確認 (ファイルは変更しません)" if dry_run else "実行"
        self._log("INFO", f"{title}  ビットレート {options.bitrate}kbps"
                          f" / 連番削除 {'ON' if options.rename else 'OFF'} / 変換 {'ON' if options.convert else 'OFF'}"
                          f" / サブフォルダ {'ON' if options.recursive else 'OFF'} / 上書き {'ON' if options.overwrite else 'OFF'}")

        processor = Processor(
            self.tools, options,
            log=lambda level, msg: self.queue.put(("log", level, msg)),
            progress=lambda done, total: self.queue.put(("progress", done, total)),
            cancel=self.cancel)

        def work() -> None:
            try:
                results = processor.run([Path(f) for f in folders], dry_run=dry_run)
                self.queue.put(("done", results, dry_run, self.cancel.is_set()))
            except Exception as e:  # 想定外のエラーも画面に出して止める
                self.queue.put(("log", "FAIL", f"予期しないエラー: {e!r}"))
                self.queue.put(("done", [], dry_run, True))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _request_cancel(self) -> None:
        self.cancel.set()
        self.btn_cancel.configure(state="disabled")
        self._log("INFO", "中止しています...")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.btn_dry.configure(state=state)
        self.btn_run.configure(state=state)
        self.btn_cancel.configure(state="normal" if running else "disabled")

    # ---------- ログ ----------

    def _open_log_file(self, dry_run: bool) -> None:
        log_dir = settings_mod.app_data_dir() / "logs"
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = log_dir / f"{stamp}{'_dryrun' if dry_run else ''}.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            self.log_file = open(self.log_path, "w", encoding="utf-8")
        except OSError:
            self.log_file = None
            self.log_path = None

    def _log(self, level: str, message: str) -> None:
        prefix = "" if level == "INFO" else f"{level:<7} "
        self.log_text.configure(state="normal")
        self.log_text.insert("end", prefix + message + "\n", level)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        if self.log_file:
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            self.log_file.write(f"{stamp} [{level}] {message}\n")

    def _poll(self) -> None:
        try:
            for _ in range(200):
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._log(item[1], item[2])
                elif kind == "progress":
                    done, total = item[1], item[2]
                    self.progress.configure(maximum=max(total, 1), value=done)
                    self.progress_label.configure(text=f"{done} / {total}")
                elif kind == "done":
                    self._finish(*item[1:])
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self, results, dry_run: bool, cancelled: bool) -> None:
        summary = format_summary(results, dry_run, cancelled)
        for line in summary.splitlines():
            self._log("INFO", line)
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        self._set_running(False)
        self.worker = None
        if self.closing:
            self.root.destroy()
            return

        failed = sum(r.failed for r in results)
        skipped = sum(r.skipped for r in results)
        warnings = sum(r.warnings for r in results)
        verb = "予定" if dry_run else ""
        message = (f"{'中止しました。' if cancelled else ('確認が終わりました (ファイルは変更していません)。' if dry_run else '完了しました。')}\n\n"
                   f"フォルダ: {len(results)}\n"
                   f"リネーム{verb}: {sum(r.renamed for r in results)}\n"
                   f"変換{verb}: {sum(r.converted for r in results)}\n"
                   f"スキップ: {skipped}\n失敗: {failed}\n警告: {warnings}")
        if skipped or failed or warnings:
            message += "\n\n詳しくはログの「要確認」を見てください。"
        if self.log_path:
            message += f"\n\nログ: {self.log_path}"
        show = messagebox.showwarning if (failed or warnings) else messagebox.showinfo
        show(APP_NAME, message, parent=self.root)

    def _on_close(self) -> None:
        self._save_settings()
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "処理中です。中止して終了しますか?", parent=self.root):
                return
            self.closing = True
            self.cancel.set()
            return  # 作りかけのファイルを片付けてから _finish で閉じる
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def main() -> None:
    _enable_dpi_awareness()
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    App(root)
    root.mainloop()
