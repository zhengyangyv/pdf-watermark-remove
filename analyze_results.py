# -*- coding: utf-8 -*-

import argparse
import os
import sys

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pymupdf as fitz
from PIL import Image

from pdf_watermark_remove import watermark_mask  # 与处理工具共用同一水印口径

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun"]
plt.rcParams["axes.unicode_minus"] = False

DPI = 300
GRID = 5                          # 网格切分数（与主程序网格方案一致）
GRID_WM_MIN = 0.5                 # 网格判定为"有水印"的最小处理前浅灰占比(%)
GRID_DROP_MIN = 0.15              # 网格判定所需的最小前后降幅(个百分点)
DARK_T = 140                      # 深色文字阈值
TEXT_PAD = 2                      # 文字邻域膨胀次数（与主程序文字保护一致）


def wm_with_text_protect(bgr):
    """亮灰水印掩码 − 文字邻域（与主程序文字保护口径一致，排除文字抗锯齿边缘）"""
    wm = watermark_mask(bgr) > 0
    gray = bgr.mean(axis=2)
    dark = gray < DARK_T
    kernel = np.ones((3, 3), np.uint8)
    dark_dil = cv2.dilate(dark.astype(np.uint8), kernel, iterations=TEXT_PAD).astype(bool)
    return wm & ~dark_dil


def page_metrics(doc, pno):
    """单页完整指标（前后两文档）"""
    k = DPI / 72.0
    pix = doc[pno].get_pixmap(matrix=fitz.Matrix(k, k), alpha=False)
    img = np.array(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    bgr = img[:, :, ::-1]
    wm = wm_with_text_protect(bgr)
    h, w = wm.shape
    gh, gw = h // GRID, w // GRID
    grids = []
    for gy in range(GRID):
        for gx in range(GRID):
            cell = wm[gy * gh:(gy + 1) * gh, gx * gw:(gx + 1) * gw]
            grids.append(cell.mean() * 100)
    gray = img.mean(axis=2)
    dark = (gray < 140).mean() * 100
    return grids, wm.mean() * 100, dark


def analyze(doc_before, doc_after, name_before, name_after):
    """逐页差分统计，返回统计字典"""
    n = min(len(doc_before), len(doc_after))
    rows = []
    for i in range(n):
        gb, wb, db = page_metrics(doc_before, i)
        ga, wa, da = page_metrics(doc_after, i)
        wm_idx = [j for j in range(GRID * GRID)
                  if gb[j] >= GRID_WM_MIN and (gb[j] - ga[j]) >= GRID_DROP_MIN]
        if wm_idx:
            s_b = sum(gb[j] for j in wm_idx)
            s_a = sum(ga[j] for j in wm_idx)
            removal = (s_b - s_a) / s_b * 100 if s_b > 0 else 0.0
        else:
            removal = 0.0
        rows.append({"page": i + 1, "wm_before": round(wb, 2), "wm_after": round(wa, 2),
                     "wm_grids": len(wm_idx), "removal": round(removal, 1),
                     "dark_before": round(db, 2), "dark_after": round(da, 2)})
    size0 = os.path.getsize(name_before) / 1024
    size1 = os.path.getsize(name_after) / 1024
    return {"rows": rows, "pages": n, "size_before": round(size0, 1),
            "size_after": round(size1, 1)}


def print_table(stats):
    print(f"{'页':<4}{'水印网格':<8}{'浅灰%前':<10}{'浅灰%后':<10}{'水印去除率%':<12}{'文字保留%':<10}")
    print("-" * 62)
    for r in stats["rows"]:
        keep = r["dark_after"] / r["dark_before"] * 100 if r["dark_before"] > 0 else 100
        removal_txt = f"{r['removal']:.1f}" if r["wm_grids"] > 0 else "—"
        print(f"{r['page']:<4}{r['wm_grids']:<8}{r['wm_before']:<10}{r['wm_after']:<10}"
              f"{removal_txt:<12}{keep:<10.1f}")
    print(f"\n文件体积: {stats['size_before']} KB → {stats['size_after']} KB"
          f"（{stats['size_after'] / stats['size_before'] * 100:.0f}%）")


def plot(stats, out_png):
    pages = [r["page"] for r in stats["rows"]]
    removal = [r["removal"] if r["wm_grids"] > 0 else 0 for r in stats["rows"]]
    grids = [r["wm_grids"] for r in stats["rows"]]
    keep = [r["dark_after"] / r["dark_before"] * 100 if r["dark_before"] > 0 else 100
            for r in stats["rows"]]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    x = np.arange(len(pages))
    ax1.bar(x, removal, 0.55, color="#2ca02c", label="水印去除率")
    ax1.axhline(100, color="gray", ls="--", lw=1)
    ax1.set_xticks(x, [f"P{p}" for p in pages])
    ax1.set_ylabel("水印去除率 (%)")
    ax1.set_title("各页水印去除率（差分法·仅水印网格）")
    ax1.set_ylim(0, 115)
    ax1.legend()
    for i, (v, g) in enumerate(zip(removal, grids)):
        ax1.text(i, v + 2, f"{v:.0f}%" if g > 0 else "—",
                 ha="center", fontsize=9)

    ax2.bar(x, keep, 0.5, color="#1f77b4", label="文字保留率")
    ax2.axhline(100, color="gray", ls="--", lw=1)
    ax2.set_xticks(x, [f"P{p}" for p in pages])
    ax2.set_ylabel("深色文字保留率 (%)")
    ax2.set_title("文字完整性（100%=零损伤）")
    ax2.set_ylim(80, 110)
    ax2.legend()

    fig.suptitle("PDF 去水印效果数据分析（差分法）", fontsize=15)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(out_png, dpi=150)
    print(f"\n图表已保存 → {out_png}")


def main():
    ap = argparse.ArgumentParser(description="PDF 去水印效果数据分析（差分法）")
    ap.add_argument("before", nargs="?", help="处理前 PDF")
    ap.add_argument("after", nargs="?", help="处理后 PDF")
    ap.add_argument("-o", "--output", default="analysis.png", help="图表输出路径")
    ap.add_argument("--folder", nargs=2, metavar=("BEFORE_DIR", "AFTER_DIR"),
                    help="批量：原文件夹 处理文件夹（按文件名配对）")
    args = ap.parse_args()

    pairs = []
    if args.folder:
        bdir, adir = args.folder
        for f in sorted(os.listdir(bdir)):
            if f.lower().endswith(".pdf"):
                b = os.path.join(bdir, f)
                a = os.path.join(adir, f)
                if os.path.exists(a):
                    pairs.append((b, a))
    elif args.before and args.after:
        pairs = [(args.before, args.after)]
    else:
        print("用法: python analyze_results.py 原.pdf 处理后.pdf [-o 图.png]")
        print("       python analyze_results.py --folder 原文件夹 处理文件夹")
        sys.exit(1)

    for i, (b, a) in enumerate(pairs):
        print(f"\n===== {os.path.basename(b)} =====")
        db, da = fitz.open(b), fitz.open(a)
        stats = analyze(db, da, b, a)
        print_table(stats)
        db.close(); da.close()
        out = args.output if len(pairs) == 1 else f"analysis_{i + 1}.png"
        plot(stats, out)


if __name__ == "__main__":
    main()
