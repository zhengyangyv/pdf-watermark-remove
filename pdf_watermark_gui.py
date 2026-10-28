# -*- coding: utf-8 -*-
"""
PDF 去水印工具 —— 图形界面版（tkinter）
复用 pdf_watermark_remove.py / analyze_results.py 的核心函数，
提供：文件选择、参数设置、去水印进度、实时日志、效果分析一键出图。
"""

import os
import queue
import sys
import threading

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

import pymupdf as fitz

# 复用核心算法（与命令行版同一套代码，保证结果一致）
import pdf_watermark_remove as core
import analyze_results as ar


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF 去水印工具 · OpenCV 版")
        self.root.geometry("760x620")
        self.root.minsize(680, 540)

        self.msg_q = queue.Queue()          # 工作线程 → 界面线程 消息队列
        self.busy = False                   # 是否正在处理
        self.src = ""
        self.dst = ""

        self._build_ui()
        self.root.after(120, self._drain_queue)

    # ---------------- 界面 ----------------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}

        frm_in = ttk.LabelFrame(self.root, text="1. 选择输入 PDF")
        frm_in.pack(fill="x", **pad)
        self.in_var = tk.StringVar()
        ttk.Entry(frm_in, textvariable=self.in_var).pack(side="left", fill="x",
                                                         expand=True, padx=(10, 6), pady=8)
        ttk.Button(frm_in, text="浏览…", command=self._pick_in).pack(side="left",
                                                                     padx=(0, 10))

        frm_out = ttk.LabelFrame(self.root, text="2. 输出设置")
        frm_out.pack(fill="x", **pad)
        self.out_var = tk.StringVar()
        ttk.Entry(frm_out, textvariable=self.out_var).pack(side="left", fill="x",
                                                            expand=True, padx=(10, 6), pady=8)
        ttk.Button(frm_out, text="浏览…", command=self._pick_out).pack(side="left", padx=(0, 6))
        ttk.Button(frm_out, text="自动命名", command=self._auto_out).pack(side="left", padx=(0, 10))

        frm_opt = ttk.LabelFrame(self.root, text="3. 处理参数")
        frm_opt.pack(fill="x", **pad)
        ttk.Label(frm_opt, text="渲染 DPI（越高越清晰，越慢）:").pack(side="left", padx=(10, 6))
        self.dpi_var = tk.StringVar(value="300")
        ttk.Spinbox(frm_opt, from_=72, to=600, increment=72, width=8,
                    textvariable=self.dpi_var).pack(side="left")
        ttk.Label(frm_opt, text="  文字保留阈值: 灰度 < 140 自动保护，无需设置").pack(side="left")

        frm_btn = ttk.Frame(self.root)
        frm_btn.pack(fill="x", **pad)
        self.run_btn = ttk.Button(frm_btn, text="开始去水印", command=self._start)
        self.run_btn.pack(side="left")
        self.ana_btn = ttk.Button(frm_btn, text="运行数据分析", command=self._start_analyze,
                                  state="disabled")
        self.ana_btn.pack(side="left", padx=8)
        self.open_btn = ttk.Button(frm_btn, text="打开输出目录", command=self._open_dir,
                                   state="disabled")
        self.open_btn.pack(side="left")
        self.progress = ttk.Progressbar(frm_btn, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(14, 10))

        frm_log = ttk.LabelFrame(self.root, text="4. 处理日志")
        frm_log.pack(fill="both", expand=True, **pad)
        self.log_txt = scrolledtext.ScrolledText(frm_log, height=16, font=("Consolas", 10),
                                                 state="disabled")
        self.log_txt.pack(fill="both", expand=True, padx=10, pady=10)

    # ---------------- 文件选择 ----------------
    def _pick_in(self):
        p = filedialog.askopenfilename(
            title="选择 PDF 文件",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
        if p:
            self.in_var.set(p)
            self._auto_out()

    def _pick_out(self):
        p = filedialog.asksaveasfilename(
            title="保存为", defaultextension=".pdf",
            filetypes=[("PDF 文件", "*.pdf")],
            initialfile=os.path.basename(self.out_var.get()) if self.out_var.get() else "output_clean.pdf")
        if p:
            self.out_var.set(p)

    def _auto_out(self):
        s = self.in_var.get().strip()
        if s and not self.out_var.get().strip():
            self.out_var.set(os.path.splitext(s)[0] + "_clean.pdf")

    # ---------------- 核心逻辑（工作线程） ----------------
    def _start(self):
        if self.busy:
            return
        src = self.in_var.get().strip()
        if not src:
            messagebox.showwarning("提示", "请先选择输入 PDF 文件")
            return
        if not os.path.exists(src):
            messagebox.showerror("错误", "输入文件不存在:\n" + src)
            return
        dst = self.out_var.get().strip() or os.path.splitext(src)[0] + "_clean.pdf"
        try:
            dpi = int(self.dpi_var.get())
        except ValueError:
            messagebox.showwarning("提示", "DPI 必须是整数，已恢复默认 300")
            self.dpi_var.set("300")
            dpi = 300

        self.src, self.dst = src, dst
        self._set_busy(True)
        self._log("开始处理: " + src + "\n")
        threading.Thread(target=self._worker, args=(src, dst, dpi), daemon=True).start()

    def _worker(self, src, dst, dpi):
        try:
            core.DPI = dpi            # 与命令行版共用核心，临时调整渲染分辨率
            ar.DPI = dpi
            src_doc = fitz.open(src)
            out_doc = fitz.open()
            total = len(src_doc)
            self.msg_q.put(("prog", 0, total))
            for pno in range(total):
                tiles = core.process_page(src_doc[pno], out_doc, src_doc, pno)
                self.msg_q.put(("log", f"页 {pno + 1}/{total}: {tiles} 个水印块\n"))
                self.msg_q.put(("prog", pno + 1, total))
            out_doc.save(dst, garbage=4, deflate=True)
            out_doc.close()
            src_doc.close()
            size = os.path.getsize(dst) / 1024
            self.msg_q.put(("log", f"\n处理完成 → {dst}（{size:.0f} KB）\n"))
            self.msg_q.put(("done", True))
        except Exception as e:        # 任何异常都要反馈到界面，不让线程静默失败
            self.msg_q.put(("log", f"\n[错误] {type(e).__name__}: {e}\n"))
            self.msg_q.put(("done", False))

    def _start_analyze(self):
        if self.busy or not (self.src and self.dst):
            return
        if not (os.path.exists(self.src) and os.path.exists(self.dst)):
            messagebox.showerror("错误", "原文件或处理结果不存在，请重新处理")
            return
        self._set_busy(True)
        self._log("\n===== 运行数据分析（差分法）=====\n")
        threading.Thread(target=self._worker_analyze, daemon=True).start()

    def _worker_analyze(self):
        try:
            db = fitz.open(self.src)
            da = fitz.open(self.dst)
            stats = ar.analyze(db, da, self.src, self.dst)
            db.close()
            da.close()
            lines = ["页   水印网格   浅灰%前   浅灰%后   水印去除率%   文字保留%\n",
                     "-" * 62 + "\n"]
            for r in stats["rows"]:
                keep = r["dark_after"] / r["dark_before"] * 100 if r["dark_before"] > 0 else 100
                removal_txt = f"{r['removal']:.1f}" if r["wm_grids"] > 0 else "—"
                lines.append(f"{r['page']:<5}{r['wm_grids']:<9}{r['wm_before']:<9}"
                             f"{r['wm_after']:<9}{removal_txt:<13}{keep:<9.1f}\n")
            lines.append(f"\n文件体积: {stats['size_before']} KB → {stats['size_after']} KB"
                         f"（{stats['size_after'] / stats['size_before'] * 100:.0f}%）\n")
            out_png = os.path.splitext(self.dst)[0] + "_分析.png"
            ar.plot(stats, out_png)
            lines.append(f"\n分析图表已保存 → {out_png}\n")
            self.msg_q.put(("log", "".join(lines)))
            self.msg_q.put(("ana_done", out_png))
        except Exception as e:
            self.msg_q.put(("log", f"\n[分析错误] {type(e).__name__}: {e}\n"))
            self.msg_q.put(("done", False))

    # ---------------- 界面线程刷新 ----------------
    def _drain_queue(self):
        try:
            while True:
                item = self.msg_q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._log(item[1])
                elif kind == "prog":
                    self.progress["maximum"] = item[2]
                    self.progress["value"] = item[1]
                elif kind == "done":
                    self._set_busy(False)
                    ok = item[1]
                    if ok:
                        self.ana_btn.config(state="normal")
                        self.open_btn.config(state="normal")
                        messagebox.showinfo("完成", "去水印完成！\n\n可点击“运行数据分析”查看效果")
                elif kind == "ana_done":
                    self._set_busy(False)
                    self.open_btn.config(state="normal")
                    png = item[1]
                    if messagebox.askyesno("分析完成", "分析图表已生成:\n" + png + "\n\n是否立即打开查看？"):
                        try:
                            os.startfile(png)   # noinspection PyUnresolvedReferences
                        except Exception:
                            pass
        except queue.Empty:
            pass
        self.root.after(120, self._drain_queue)

    def _log(self, text):
        self.log_txt.config(state="normal")
        self.log_txt.insert("end", text)
        self.log_txt.see("end")
        self.log_txt.config(state="disabled")

    def _set_busy(self, b):
        self.busy = b
        self.run_btn.config(state="disabled" if b else "normal")
        self.ana_btn.config(state="disabled" if b else
                            ("normal" if self.dst and os.path.exists(self.dst) else "disabled"))

    def _open_dir(self):
        d = os.path.dirname(self.dst) or os.getcwd()
        try:
            os.startfile(d)   # noinspection PyUnresolvedReferences
        except Exception as e:
            self._log(f"[提示] 无法打开目录: {e}\n")

    def on_close(self):
        if self.busy:
            if not messagebox.askokcancel("退出", "正在处理中，确定退出吗？"):
                return
        self.root.destroy()


def main():
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
