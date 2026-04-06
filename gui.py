#!/usr/bin/env python3
"""
Epic Seven Spine Asset Converter GUI.

Workflow: .sct + .scsp + .atlas  →  .png + .json + .atlas
"""
from __future__ import annotations

import locale
import os
import re
import sys
import threading
import traceback
from pathlib import Path
from tkinter import (
    END, DISABLED, NORMAL, WORD,
    BooleanVar, StringVar, Tk, filedialog, messagebox, ttk,
)

from sct2png import convert_sct_to_png
from scsp2json import convert_scsp_to_json

VERSION = "1.0.2"

# ==============================
# i18n
# ==============================
STRINGS: dict[str, dict[str, str]] = {
    "title":              {"zh": "E7 Spine 资源转换器",           "en": "E7 Spine Asset Converter"},
    "tab_single":         {"zh": "单文件转换",                     "en": "Single File"},
    "tab_batch":          {"zh": "批量转换",                       "en": "Batch"},
    "input_files":        {"zh": "输入文件",                       "en": "Input Files"},
    "sct_label":          {"zh": ".sct 文件:",                     "en": ".sct File:"},
    "scsp_label":         {"zh": ".scsp 文件:",                    "en": ".scsp File:"},
    "atlas_label":        {"zh": ".atlas 文件:",                   "en": ".atlas File:"},
    "output":             {"zh": "输出",                           "en": "Output"},
    "output_dir":         {"zh": "输出目录:",                      "en": "Output Dir:"},
    "browse":             {"zh": "浏览…",                          "en": "Browse…"},
    "convert":            {"zh": "转换",                           "en": "Convert"},
    "batch_convert":      {"zh": "批量转换",                       "en": "Batch Convert"},
    "input_folder":       {"zh": "输入文件夹",                     "en": "Input Folder"},
    "output_folder":      {"zh": "输出文件夹",                     "en": "Output Folder"},
    "folder":             {"zh": "文件夹:",                        "en": "Folder:"},
    "recursive":          {"zh": "递归搜索子文件夹",               "en": "Search subfolders recursively"},
    "options":            {"zh": "选项",                           "en": "Options"},
    "fix_pma":            {"zh": "修复 Atlas pma 字段 (Spine 2.x 兼容)", "en": "Fix Atlas pma field (Spine 2.x compat)"},
    "log":                {"zh": "日志",                           "en": "Log"},
    "language":           {"zh": "语言",                           "en": "Language"},
    "warn":               {"zh": "提示",                           "en": "Warning"},
    "warn_no_input":      {"zh": "请至少选择一个输入文件。",       "en": "Please select at least one input file."},
    "warn_no_output":     {"zh": "请选择输出目录。",               "en": "Please select an output directory."},
    "warn_no_batch_in":   {"zh": "请选择输入文件夹。",             "en": "Please select an input folder."},
    "warn_bad_dir":       {"zh": "输入路径不是有效的文件夹。",     "en": "Input path is not a valid folder."},
    "done_single":        {"zh": "完成: {ok}/{total} 个文件转换成功。", "en": "Done: {ok}/{total} file(s) converted."},
    "batch_scan":         {"zh": "扫描到 {n} 组文件，开始转换…",   "en": "Found {n} group(s), converting…"},
    "batch_none":         {"zh": "未在文件夹中找到任何 .sct / .scsp / .atlas 文件。",
                           "en": "No .sct / .scsp / .atlas files found in the folder."},
    "batch_done":         {"zh": "批量转换完成: {gok}/{gtotal} 组全部成功, 共 {fok}/{ftotal} 个文件。",
                           "en": "Batch done: {gok}/{gtotal} group(s) fully succeeded, {fok}/{ftotal} file(s) total."},
    "stop":               {"zh": "停止",                           "en": "Stop"},
    "cancelled":          {"zh": "转换已被用户取消。",               "en": "Conversion cancelled by user."},
    "fail":               {"zh": "失败",                           "en": "FAIL"},
    "fail_summary_header": {"zh": "━━ 失败文件列表 ({n} 个) ━━",   "en": "━━ Failed files ({n}) ━━"},
    "ft_sct":             {"zh": "SCT 纹理",                       "en": "SCT Texture"},
    "ft_scsp":            {"zh": "SCSP Spine 二进制",              "en": "SCSP Spine Binary"},
    "ft_atlas":           {"zh": "Atlas 文件",                     "en": "Atlas File"},
    "ft_all":             {"zh": "所有文件",                       "en": "All Files"},
}

def _detect_lang() -> str:
    # Windows: query the OS UI language directly
    if sys.platform == "win32":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if lang_id & 0xFF == 0x04:  # LANG_CHINESE
                return "zh"
        except Exception:
            pass
    # Unix / fallback: check locale settings
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(key, "")
        if val:
            if val.startswith("zh"):
                return "zh"
            return "en"
    try:
        loc = locale.getlocale()[0] or ""
        if loc.startswith("zh") or loc.startswith("Chinese"):
            return "zh"
    except Exception:
        pass
    return "en"


class I18n:
    def __init__(self, lang: str | None = None) -> None:
        self.lang = lang or _detect_lang()

    def t(self, key: str, **kwargs: object) -> str:
        entry = STRINGS.get(key, {})
        s = entry.get(self.lang, entry.get("en", key))
        if kwargs:
            s = s.format(**kwargs)
        return s


# ==============================
# Atlas helpers
# ==============================
def fix_atlas_sct_ref(input_path: str, output_path: str) -> bool:
    """Read atlas, replace the texture reference on line 2 from .sct to .png."""
    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if len(lines) < 2:
        raise ValueError("Atlas file has fewer than 2 lines")

    old_ref = lines[1].rstrip("\n").rstrip("\r")
    new_ref = re.sub(r"\.sct\s*$", ".png", old_ref)
    if new_ref == old_ref:
        new_ref = Path(old_ref.strip()).stem + ".png"
    lines[1] = new_ref + "\n"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True


def scan_folder_groups(folder: str, recursive: bool = False) -> dict[str, dict[str, Path]]:
    """Scan *folder* for .sct / .scsp / .atlas files and group by stem name.

    Returns ``{stem: {"sct": Path, "scsp": Path, "atlas": Path}}``
    where each key is only present when the file exists.
    """
    exts = (".sct", ".scsp", ".atlas")
    root = Path(folder)
    files = root.rglob("*") if recursive else root.iterdir()

    groups: dict[str, dict[str, Path]] = {}
    for p in files:
        if p.is_file() and p.suffix.lower() in exts:
            key = p.suffix.lstrip(".").lower()
            groups.setdefault(p.stem, {})[key] = p

    return dict(sorted(groups.items()))


# ==============================
# GUI
# ==============================
class App:
    def __init__(self) -> None:
        self.i = I18n()
        self.root = Tk()
        self.root.title(f"{self.i.t('title')} v{VERSION}")
        self.root.resizable(False, False)

        self.sct_path = StringVar()
        self.scsp_path = StringVar()
        self.atlas_path = StringVar()
        self.out_dir = StringVar()
        self.fix_atlas_pma = BooleanVar(value=True)

        self.batch_in_dir = StringVar()
        self.batch_out_dir = StringVar()
        self.batch_recursive = BooleanVar(value=False)

        self._widgets: dict[str, object] = {}
        self._cancel_event = threading.Event()

        self._build_ui()
        self._center_window()

    def _center_window(self) -> None:
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    # ---------- language switch ----------
    def _switch_lang(self, lang: str) -> None:
        self.i.lang = lang
        self._refresh_texts()

    def _refresh_texts(self) -> None:
        t = self.i.t
        self.root.title(f"{t('title')} v{VERSION}")
        ww = self._widgets
        ww["notebook"].tab(0, text=t("tab_single"))
        ww["notebook"].tab(1, text=t("tab_batch"))
        ww["input_frame"].configure(text=t("input_files"))
        ww["sct_lbl"].configure(text=t("sct_label"))
        ww["scsp_lbl"].configure(text=t("scsp_label"))
        ww["atlas_lbl"].configure(text=t("atlas_label"))
        ww["out_frame"].configure(text=t("output"))
        ww["out_lbl"].configure(text=t("output_dir"))
        for b in ww["browse_btns"]:
            b.configure(text=t("browse"))
        ww["convert_btn"].configure(text=t("convert"))
        ww["stop_btn"].configure(text=t("stop"))
        ww["batch_stop_btn"].configure(text=t("stop"))
        ww["batch_in_frame"].configure(text=t("input_folder"))
        ww["batch_in_lbl"].configure(text=t("folder"))
        ww["batch_out_frame"].configure(text=t("output_folder"))
        ww["batch_out_lbl"].configure(text=t("output_dir"))
        ww["batch_recursive_cb"].configure(text=t("recursive"))
        ww["batch_btn"].configure(text=t("batch_convert"))
        ww["opt_frame"].configure(text=t("options"))
        ww["pma_cb"].configure(text=t("fix_pma"))
        ww["log_frame"].configure(text=t("log"))
        ww["lang_frame"].configure(text=t("language"))

    # ---------- build UI ----------
    def _build_ui(self) -> None:
        import tkinter as tk

        t = self.i.t
        pad = {"padx": 8, "pady": 4}
        root = self.root
        ww = self._widgets
        browse_btns: list[ttk.Button] = []

        notebook = ttk.Notebook(root)
        notebook.grid(row=0, column=0, sticky="nsew", **pad)
        ww["notebook"] = notebook

        # ==================== Tab 1: single-file ====================
        tab_single = ttk.Frame(notebook, padding=8)
        notebook.add(tab_single, text=t("tab_single"))

        input_frame = ttk.LabelFrame(tab_single, text=t("input_files"), padding=8)
        input_frame.pack(fill="x")
        input_frame.columnconfigure(1, weight=1)
        ww["input_frame"] = input_frame

        ww["sct_lbl"] = ttk.Label(input_frame, text=t("sct_label"))
        ww["sct_lbl"].grid(row=0, column=0, sticky="e", padx=(0, 4), pady=2)
        ttk.Entry(input_frame, textvariable=self.sct_path, width=52).grid(row=0, column=1, sticky="ew", pady=2)
        b = ttk.Button(input_frame, text=t("browse"), width=8,
                       command=lambda: self._browse_file(self.sct_path, "sct"))
        b.grid(row=0, column=2, padx=(4, 0), pady=2); browse_btns.append(b)

        ww["scsp_lbl"] = ttk.Label(input_frame, text=t("scsp_label"))
        ww["scsp_lbl"].grid(row=1, column=0, sticky="e", padx=(0, 4), pady=2)
        ttk.Entry(input_frame, textvariable=self.scsp_path, width=52).grid(row=1, column=1, sticky="ew", pady=2)
        b = ttk.Button(input_frame, text=t("browse"), width=8,
                       command=lambda: self._browse_file(self.scsp_path, "scsp"))
        b.grid(row=1, column=2, padx=(4, 0), pady=2); browse_btns.append(b)

        ww["atlas_lbl"] = ttk.Label(input_frame, text=t("atlas_label"))
        ww["atlas_lbl"].grid(row=2, column=0, sticky="e", padx=(0, 4), pady=2)
        ttk.Entry(input_frame, textvariable=self.atlas_path, width=52).grid(row=2, column=1, sticky="ew", pady=2)
        b = ttk.Button(input_frame, text=t("browse"), width=8,
                       command=lambda: self._browse_file(self.atlas_path, "atlas"))
        b.grid(row=2, column=2, padx=(4, 0), pady=2); browse_btns.append(b)

        out_frame = ttk.LabelFrame(tab_single, text=t("output"), padding=8)
        out_frame.pack(fill="x", pady=(4, 0))
        out_frame.columnconfigure(1, weight=1)
        ww["out_frame"] = out_frame

        ww["out_lbl"] = ttk.Label(out_frame, text=t("output_dir"))
        ww["out_lbl"].grid(row=0, column=0, sticky="e", padx=(0, 4))
        ttk.Entry(out_frame, textvariable=self.out_dir, width=52).grid(row=0, column=1, sticky="ew")
        b = ttk.Button(out_frame, text=t("browse"), width=8, command=self._browse_out_dir)
        b.grid(row=0, column=2, padx=(4, 0)); browse_btns.append(b)

        btn_frame_single = ttk.Frame(tab_single)
        btn_frame_single.pack(pady=8)
        self.convert_btn = ttk.Button(btn_frame_single, text=t("convert"), width=16, command=self._on_convert)
        self.convert_btn.pack(side="left", padx=(0, 4))
        ww["convert_btn"] = self.convert_btn
        self.stop_btn = ttk.Button(btn_frame_single, text=t("stop"), width=8, command=self._on_stop, state=DISABLED)
        self.stop_btn.pack(side="left")
        ww["stop_btn"] = self.stop_btn

        # ==================== Tab 2: batch ====================
        tab_batch = ttk.Frame(notebook, padding=8)
        notebook.add(tab_batch, text=t("tab_batch"))

        batch_in_frame = ttk.LabelFrame(tab_batch, text=t("input_folder"), padding=8)
        batch_in_frame.pack(fill="x")
        batch_in_frame.columnconfigure(1, weight=1)
        ww["batch_in_frame"] = batch_in_frame

        ww["batch_in_lbl"] = ttk.Label(batch_in_frame, text=t("folder"))
        ww["batch_in_lbl"].grid(row=0, column=0, sticky="e", padx=(0, 4))
        ttk.Entry(batch_in_frame, textvariable=self.batch_in_dir, width=52).grid(row=0, column=1, sticky="ew")
        b = ttk.Button(batch_in_frame, text=t("browse"), width=8,
                       command=lambda: self._browse_dir(self.batch_in_dir))
        b.grid(row=0, column=2, padx=(4, 0)); browse_btns.append(b)

        batch_out_frame = ttk.LabelFrame(tab_batch, text=t("output_folder"), padding=8)
        batch_out_frame.pack(fill="x", pady=(4, 0))
        batch_out_frame.columnconfigure(1, weight=1)
        ww["batch_out_frame"] = batch_out_frame

        ww["batch_out_lbl"] = ttk.Label(batch_out_frame, text=t("output_dir"))
        ww["batch_out_lbl"].grid(row=0, column=0, sticky="e", padx=(0, 4))
        ttk.Entry(batch_out_frame, textvariable=self.batch_out_dir, width=52).grid(row=0, column=1, sticky="ew")
        b = ttk.Button(batch_out_frame, text=t("browse"), width=8,
                       command=lambda: self._browse_dir(self.batch_out_dir))
        b.grid(row=0, column=2, padx=(4, 0)); browse_btns.append(b)

        batch_opt = ttk.Frame(tab_batch)
        batch_opt.pack(fill="x", pady=(4, 0))
        ww["batch_recursive_cb"] = ttk.Checkbutton(batch_opt, text=t("recursive"), variable=self.batch_recursive)
        ww["batch_recursive_cb"].pack(anchor="w")

        btn_frame_batch = ttk.Frame(tab_batch)
        btn_frame_batch.pack(pady=8)
        self.batch_btn = ttk.Button(btn_frame_batch, text=t("batch_convert"), width=16, command=self._on_batch)
        self.batch_btn.pack(side="left", padx=(0, 4))
        ww["batch_btn"] = self.batch_btn
        self.batch_stop_btn = ttk.Button(btn_frame_batch, text=t("stop"), width=8, command=self._on_stop, state=DISABLED)
        self.batch_stop_btn.pack(side="left")
        ww["batch_stop_btn"] = self.batch_stop_btn

        # ==================== Shared: options + language + log ====================
        bottom = ttk.Frame(root)
        bottom.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        bottom.columnconfigure(0, weight=1)

        opt_frame = ttk.LabelFrame(bottom, text=t("options"), padding=8)
        opt_frame.grid(row=0, column=0, sticky="ew")
        ww["opt_frame"] = opt_frame
        ww["pma_cb"] = ttk.Checkbutton(opt_frame, text=t("fix_pma"), variable=self.fix_atlas_pma)
        ww["pma_cb"].pack(anchor="w")

        lang_frame = ttk.LabelFrame(bottom, text=t("language"), padding=8)
        lang_frame.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        ww["lang_frame"] = lang_frame
        ttk.Button(lang_frame, text="中文", width=6, command=lambda: self._switch_lang("zh")).pack(side="left", padx=2)
        ttk.Button(lang_frame, text="EN", width=6, command=lambda: self._switch_lang("en")).pack(side="left", padx=2)

        log_frame = ttk.LabelFrame(root, text=t("log"), padding=4)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        root.rowconfigure(2, weight=1)
        root.columnconfigure(0, weight=1)
        ww["log_frame"] = log_frame

        self.log_text = tk.Text(log_frame, height=12, width=72, wrap=WORD, state=DISABLED,
                                bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ww["browse_btns"] = browse_btns

    # ---- filetypes (localised) ----
    def _ft(self, kind: str) -> list[tuple[str, str]]:
        t = self.i.t
        if kind == "sct":
            return [(t("ft_sct"), "*.sct"), (t("ft_all"), "*.*")]
        elif kind == "scsp":
            return [(t("ft_scsp"), "*.scsp"), (t("ft_all"), "*.*")]
        else:
            return [(t("ft_atlas"), "*.atlas"), (t("ft_all"), "*.*")]

    # ---- helpers ----
    def _browse_file(self, var: StringVar, kind: str) -> None:
        init_dir = str(Path(var.get()).parent) if var.get() else None
        path = filedialog.askopenfilename(filetypes=self._ft(kind), initialdir=init_dir)
        if path:
            var.set(path)
            if not self.out_dir.get():
                self.out_dir.set(str(Path(path).parent))

    def _browse_dir(self, var: StringVar) -> None:
        init_dir = var.get() or None
        d = filedialog.askdirectory(initialdir=init_dir)
        if d:
            var.set(d)

    def _browse_out_dir(self) -> None:
        self._browse_dir(self.out_dir)

    def _log(self, msg: str, tag: str = "") -> None:
        self.log_text.configure(state=NORMAL)
        self.log_text.insert(END, msg + "\n", tag)
        self.log_text.see(END)
        self.log_text.configure(state=DISABLED)

    def _set_buttons(self, state: str) -> None:
        self.convert_btn.configure(state=state)
        self.batch_btn.configure(state=state)
        stop_state = DISABLED if state == NORMAL else NORMAL
        self.stop_btn.configure(state=stop_state)
        self.batch_stop_btn.configure(state=stop_state)

    def _on_stop(self) -> None:
        self._cancel_event.set()

    # ---- single convert ----
    def _on_convert(self) -> None:
        t = self.i.t
        sct = self.sct_path.get().strip()
        scsp = self.scsp_path.get().strip()
        atlas = self.atlas_path.get().strip()
        out = self.out_dir.get().strip()

        if not any([sct, scsp, atlas]):
            messagebox.showwarning(t("warn"), t("warn_no_input"))
            return
        if not out:
            messagebox.showwarning(t("warn"), t("warn_no_output"))
            return

        self._cancel_event.clear()
        self._set_buttons(DISABLED)
        threading.Thread(target=self._do_convert, args=(sct, scsp, atlas, out), daemon=True).start()

    def _convert_one_group(
        self, sct: str, scsp: str, atlas: str, out_dir: str,
        failures: list[str] | None = None,
    ) -> tuple[int, int]:
        """Convert one set of files. Returns (success_count, total_count).

        If *failures* is provided, failed file paths are appended to it.
        """
        t = self.i.t
        ok = 0
        total = sum(1 for p in [sct, scsp, atlas] if p)

        if sct:
            if self._cancel_event.is_set():
                return ok, total
            self.root.after(0, self._log, f"  [SCT]   {Path(sct).name}")
            try:
                out_png = str(Path(out_dir) / Path(sct).with_suffix(".png").name)
                if convert_sct_to_png(sct, out_png):
                    self.root.after(0, self._log, f"          → {out_png}")
                    ok += 1
                else:
                    self.root.after(0, self._log, f"          {t('fail')}")
                    if failures is not None:
                        failures.append(sct)
            except Exception as e:
                self.root.after(0, self._log, f"          {t('fail')}: {e}")
                if failures is not None:
                    failures.append(sct)
                traceback.print_exc()

        if scsp:
            if self._cancel_event.is_set():
                return ok, total
            self.root.after(0, self._log, f"  [SCSP]  {Path(scsp).name}")
            try:
                out_json = str(Path(out_dir) / Path(scsp).with_suffix(".json").name)
                if convert_scsp_to_json(scsp, out_json):
                    self.root.after(0, self._log, f"          → {out_json}")
                    ok += 1
                else:
                    self.root.after(0, self._log, f"          {t('fail')}")
                    if failures is not None:
                        failures.append(scsp)
            except Exception as e:
                self.root.after(0, self._log, f"          {t('fail')}: {e}")
                if failures is not None:
                    failures.append(scsp)
                traceback.print_exc()

        if atlas:
            if self._cancel_event.is_set():
                return ok, total
            self.root.after(0, self._log, f"  [ATLAS] {Path(atlas).name}")
            try:
                out_atlas = str(Path(out_dir) / Path(atlas).name)
                fix_atlas_sct_ref(atlas, out_atlas)
                if self.fix_atlas_pma.get():
                    from fix_atlas import fix_atlas
                    fix_atlas(out_atlas, out_atlas)
                self.root.after(0, self._log, f"          → {out_atlas}")
                ok += 1
            except Exception as e:
                self.root.after(0, self._log, f"          {t('fail')}: {e}")
                if failures is not None:
                    failures.append(atlas)
                traceback.print_exc()

        return ok, total

    def _log_failure_summary(self, failures: list[str]) -> None:
        if not failures:
            return
        t = self.i.t
        self.root.after(0, self._log, t("fail_summary_header", n=len(failures)))
        for f in failures:
            self.root.after(0, self._log, f"  • {f}")
        self.root.after(0, self._log, "")

    def _do_convert(self, sct: str, scsp: str, atlas: str, out_dir: str) -> None:
        try:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            failures: list[str] = []
            ok, total = self._convert_one_group(sct, scsp, atlas, out_dir, failures)
            if self._cancel_event.is_set():
                self.root.after(0, self._log, "\n" + self.i.t("cancelled") + "\n")
            else:
                self.root.after(0, self._log, "\n" + self.i.t("done_single", ok=ok, total=total) + "\n")
                self._log_failure_summary(failures)
        finally:
            self.root.after(0, self._set_buttons, NORMAL)

    # ---- batch convert ----
    def _on_batch(self) -> None:
        t = self.i.t
        in_dir = self.batch_in_dir.get().strip()
        out_dir = self.batch_out_dir.get().strip()

        if not in_dir:
            messagebox.showwarning(t("warn"), t("warn_no_batch_in"))
            return
        if not Path(in_dir).is_dir():
            messagebox.showwarning(t("warn"), t("warn_bad_dir"))
            return
        if not out_dir:
            out_dir = in_dir
            self.batch_out_dir.set(out_dir)

        self._cancel_event.clear()
        self._set_buttons(DISABLED)
        recursive = self.batch_recursive.get()
        threading.Thread(target=self._do_batch, args=(in_dir, out_dir, recursive), daemon=True).start()

    def _do_batch(self, in_dir: str, out_dir: str, recursive: bool) -> None:
        try:
            groups = scan_folder_groups(in_dir, recursive)
            if not groups:
                self.root.after(0, self._log, self.i.t("batch_none") + "\n")
                return

            self.root.after(0, self._log, self.i.t("batch_scan", n=len(groups)) + "\n")

            total_ok = 0
            total_files = 0
            group_ok = 0
            all_failures: list[str] = []

            for stem, file_map in groups.items():
                if self._cancel_event.is_set():
                    break
                self.root.after(0, self._log, f"━━ {stem} ━━")

                sct_p = file_map.get("sct")
                scsp_p = file_map.get("scsp")
                atlas_p = file_map.get("atlas")

                first = sct_p or scsp_p or atlas_p
                if recursive and first:
                    rel = first.parent.relative_to(in_dir)
                    dest = str(Path(out_dir) / rel)
                else:
                    dest = out_dir

                Path(dest).mkdir(parents=True, exist_ok=True)

                ok, cnt = self._convert_one_group(
                    str(sct_p) if sct_p else "",
                    str(scsp_p) if scsp_p else "",
                    str(atlas_p) if atlas_p else "",
                    dest,
                    all_failures,
                )
                total_ok += ok
                total_files += cnt
                if ok == cnt:
                    group_ok += 1

            if self._cancel_event.is_set():
                self.root.after(0, self._log, "\n" + self.i.t("cancelled") + "\n")
            else:
                self.root.after(
                    0, self._log,
                    "\n" + self.i.t("batch_done", gok=group_ok, gtotal=len(groups),
                                    fok=total_ok, ftotal=total_files) + "\n",
                )
                self._log_failure_summary(all_failures)
        finally:
            self.root.after(0, self._set_buttons, NORMAL)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
