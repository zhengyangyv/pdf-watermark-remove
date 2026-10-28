# -*- coding: utf-8 -*-

import io
import os
import sys

import cv2
import numpy as np
import pymupdf as fitz
from PIL import Image

DPI = 300  # 渲染分辨率（越高越清晰，越慢）
AREA_RATIO = 0.0005  # 水印面积占比低于此值的页面视为无水印，跳过


def watermark_mask(img_bgr):
    """亮灰/浅彩水印掩码：亮度 170-254（V），饱和度 ≤60（S）"""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, np.array([0, 0, 170]), np.array([179, 60, 254]))


def image_rects(page):
    """页面内真实图片 bbox（pt），水印检测中排除，避免误伤内容图"""
    rects = []
    for info in page.get_image_info():
        b = info.get("bbox")
        if b and b[2] - b[0] > 20 and b[3] - b[1] > 20:
            rects.append(fitz.Rect(b))
    return rects


def process_page(page, out_doc, src_doc, pno):
    """处理单页：保留原矢量 → 只把水印笔画区域填白贴回。返回贴片数。"""
    k = DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(k, k), alpha=False)
    img = cv2.cvtColor(np.array(Image.frombytes("RGB", (pix.width, pix.height), pix.samples)),
                       cv2.COLOR_RGB2BGR)
    mask = watermark_mask(img)

    # 图片保护：真实图片区域从水印掩码中挖除
    for r in image_rects(page):
        x0, y0 = int(r.x0 * k), int(r.y0 * k)
        x1, y1 = int(r.x1 * k), int(r.y1 * k)
        mask[max(0, y0):min(pix.height, y1), max(0, x0):min(pix.width, x1)] = 0

    new_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
    new_page.show_pdf_page(new_page.rect, src_doc, pno)  # 原矢量原样保留

    if (mask > 0).sum() / mask.size < AREA_RATIO:
        return 0  # 无明显水印，本页不动

    # 文字保护：扣除深色文字/图形邻域（斜纹叠字处保留文字，不涂抹）
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dark = ((gray < 140).astype(np.uint8)) * 255
    dark = cv2.dilate(dark, np.ones((3, 3), np.uint8), iterations=2)
    mask = cv2.bitwise_and(mask, cv2.bitwise_not(dark))
    # 轻微膨胀盖住水印抗锯齿边缘
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)

    # 背景色：取页面浅色中值（斜纹水印的底色通常是纯白/浅灰）
    bg_zone = (~(mask > 0)) & (gray >= 150)
    if bg_zone.sum() > 500:
        bg = np.median(img[bg_zone].reshape(-1, 3), axis=0).astype(np.uint8)
    else:
        bg = np.array([255, 255, 255], dtype=np.uint8)

    repaired = img.copy()
    repaired[mask > 0] = bg

    # 连通域 → 每个水印笔画块做小贴片（只覆盖笔画 bbox + 5px 外扩）
    num, _, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    count = 0
    for i in range(1, num):
        x, y, w, h = (stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP],
                      stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT])
        if w < 5 or h < 5:
            continue
        x0, y0 = max(0, x - 5), max(0, y - 5)
        x1, y1 = min(pix.width, x + w + 5), min(pix.height, y + h + 5)
        sub = repaired[y0:y1, x0:x1]
        buf = io.BytesIO()
        Image.fromarray(cv2.cvtColor(sub, cv2.COLOR_BGR2RGB)).save(buf, format="JPEG", quality=95)
        new_page.insert_image(fitz.Rect(x0 / k, y0 / k, x1 / k, y1 / k), stream=buf.getvalue())
        count += 1
    return count


def pick_file_windows():
    """Windows 原生文件选择框（ctypes，不依赖 Tk/Tcl）。失败或非 Windows 返回 None"""
    import ctypes
    from ctypes import wintypes
    try:
        class OFN(ctypes.Structure):
            _fields_ = [("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
                        ("hinstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
                        ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
                        ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
                        ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
                        ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
                        ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
                        ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
                        ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
                        ("openHook", wintypes.LPVOID), ("lpTemplateName", wintypes.LPCWSTR),
                        ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
                        ("FlagsEx", wintypes.DWORD)]
        buf = ctypes.create_unicode_buffer(2048)
        filt = ctypes.create_unicode_buffer("PDF 文件\0*.pdf\0所有文件\0*.*\0\0")
        ofn = OFN()
        ofn.lStructSize = ctypes.sizeof(OFN)
        ofn.lpstrFilter = ctypes.cast(filt, wintypes.LPCWSTR)
        ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
        ofn.nMaxFile = 2048
        ofn.Flags = 0x00000004 | 0x00001000 | 0x00000800 | 0x00080000
        if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
            return buf.value
    except (OSError, AttributeError):
        pass
    return None


def main():
    if len(sys.argv) < 2:
        src = pick_file_windows()
        if not src:
            print("用法: python pdf_watermark_remove.py 输入.pdf [输出.pdf]")
            print("（未传参数时，Windows 下会自动弹出文件选择窗口）")
            sys.exit(1)
    else:
        src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + "_clean.pdf"

    src_doc = fitz.open(src)
    out_doc = fitz.open()
    for pno in range(len(src_doc)):
        tiles = process_page(src_doc[pno], out_doc, src_doc, pno)
        print(f"页 {pno + 1}: {tiles} 个水印块", flush=True)
    out_doc.save(dst, garbage=4, deflate=True)
    out_doc.close()
    src_doc.close()
    print(f"完成 → {dst}（{os.path.getsize(dst) / 1024:.0f} KB）")

    try:
        ans = input("\n是否运行数据分析模块（水印占比/文字保留率/图表）？[y/N]: ").strip().lower()
    except EOFError:
        ans = "n"
    if ans in ("y", "yes"):
        import analyze_results as ar
        db, da = fitz.open(src), fitz.open(dst)
        stats = ar.analyze(db, da, src, dst)
        ar.print_table(stats)
        out_png = os.path.splitext(dst)[0] + "_分析.png"
        ar.plot(stats, out_png)
        db.close()
        da.close()
        print(f"分析图表已保存 → {out_png}")


if __name__ == "__main__":
    main()
