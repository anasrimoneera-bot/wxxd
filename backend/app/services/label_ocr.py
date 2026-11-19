"""面单 PDF → the receiver (收件人) on each label.

微信小店 电子面单 PDFs draw their text as shapes (no text layer), so each page is rendered and read
with OCR (RapidOCR, runs on the server's CPU, ~1-2 s a page). A PDF with a text layer is read directly.
The receiver block on a label: 「收」 name + masked phone (王** *******4921), then the full address over
one or more lines; the sender (寄) block follows. The order number is printed as 订单号：…"""
import io
import re
import threading

_ocr = None
_lock = threading.Lock()

# a name (王** / 杨*芸 / 张三) then a phone: masked with digits (*******4921, 15******547) or fully masked (1**********)
NAME_PHONE = re.compile(
    r"^[收\s]*([\u4e00-\u9fa5][\u4e00-\u9fa5*＊]{0,4})[\s.·,，:：]*"
    r"(1[\d*＊]{10}|1?[\d*＊]{0,7}[*＊][\d*＊]{0,7}\d{3,4}|1\d{10})(?!\d)[\s.·,，]*(.*)$")
STOP = re.compile(r"^(寄|商品|订单|备注|签收|已验视|提高警惕|谨防)")
REGION = re.compile(r"(省|自治区|市|特别行政区|县|区)")
NOISE = re.compile(r"^[A-Z]{0,4}\d{3,}$")  # waybill numbers printed down the sides, barcode digits


def _engine():
    global _ocr
    with _lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        return _ocr


def image_lines(image: bytes) -> list[str]:
    """The text lines of one label image (e.g. the 打印组件's preview), top to bottom."""
    return read_image(image)[0]


def read_image(image: bytes, sender=None) -> tuple[list[str], dict | None]:
    """One label image: (its lines, the receiver or None), read at a few sizes until the receiver is found."""
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(image)).convert("RGB")
    lines, lab = [], None
    for kw in PASSES:
        for long_side in (1100, 1400, 1700, 2200):
            k = long_side / max(img.size)
            lines = _ocr_lines(np.array(img.resize((int(img.width * k), int(img.height * k)))), **kw)
            lab = parse_label(lines, sender)
            if lab:
                return lines, lab
    return lines, lab


MASKED_ONLY = re.compile(r"^[?？]?[*＊]{3,}\d{3,4}$")  # 「张 *******3324」 read without its one-character name


def _with_name(eng, arr, box) -> str:
    """Read a masked number's box again, widened to the left: a lone 1-character name (张) next to it is
    often missed by the text detector, but the recognizer reads it on the wider crop."""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    h = y1 - y0
    for left in (1.6, 2.2, 1.2):
        crop = arr[max(int(y0 - 2), 0):int(y1 + 2), max(int(x0 - left * h), 0):int(x1)]
        if not crop.size:
            continue
        out = eng.text_recognizer([crop])
        text = (out[0][0][0] if out and out[0] else "") or ""
        if re.match(r"^[\u4e00-\u9fa5][*＊\s]*[*＊]{3,}\d{3,4}", text):
            return text
    return ""


def _ocr_lines(arr, **kw) -> list[str]:
    eng = _engine()
    res, _ = eng(arr, **kw)
    rows = []
    for b, t, _ in res or []:
        t = t.strip()
        if MASKED_ONLY.match(t):
            try:
                t = _with_name(eng, arr, b) or t
            except Exception:
                pass
        rows.append((b[0][1], b[0][0], t))
    rows.sort(key=lambda r: (round(r[0] / 12), r[1]))
    return [t for _, _, t in rows if t]


SCALES = (2, 2.5, 3, 4)  # OCR misses a line now and then at one size: another size reads it
PASSES = ({}, {"box_thresh": 0.3})  # a looser text-box threshold finds faint/short lines ("余** ****2441")


def read_labels(pdf: bytes, sender=None, max_pages: int = 200) -> list[tuple[list[str], dict | None]]:
    """Each page: (its lines, the receiver or None). A page whose receiver is not found is read again
    at the next size."""
    import numpy as np
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(io.BytesIO(pdf))
    out = []
    for i in range(min(len(doc), max_pages)):
        page = doc[i]
        text = page.get_textpage().get_text_bounded() or ""
        if len(text.strip()) > 20:  # a real text layer
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            out.append((lines, parse_label(lines, sender)))
            continue
        lines, lab = [], None
        for kw in PASSES:
            for sc in SCALES:
                lines = _ocr_lines(np.array(page.render(scale=sc).to_pil().convert("RGB")), **kw)
                lab = parse_label(lines, sender)
                if lab:
                    break
            if lab:
                break
        out.append((lines, lab))
    return out


def page_lines(pdf: bytes, max_pages: int = 200) -> list[list[str]]:
    """The text lines of each page, top to bottom."""
    import numpy as np
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(io.BytesIO(pdf))
    out = []
    for i in range(min(len(doc), max_pages)):
        page = doc[i]
        text = page.get_textpage().get_text_bounded() or ""
        if len(text.strip()) > 20:  # a real text layer
            out.append([l.strip() for l in text.splitlines() if l.strip()])
            continue
        out.append(_ocr_lines(np.array(page.render(scale=3).to_pil().convert("RGB"))))
    return out


def _is_sender(name: str, phone: str, sender) -> bool:
    """The store's own sender (saved for 打单): same surname and the same last digits."""
    if isinstance(sender, list):
        return any(_is_sender(name, phone, x) for x in sender)
    if not sender or not sender.get("name"):
        return False
    digits = re.sub(r"\D", "", phone)
    mobile = re.sub(r"\D", "", str(sender.get("mobile") or ""))
    same_tail = bool(digits) and len(digits) >= 3 and mobile.endswith(digits[-3:])
    return name[:1] == str(sender["name"])[:1] and (same_tail or not digits)


def parse_label(lines: list[str], sender=None) -> dict | None:
    """{surname, last4, address, order_id} of the receiver (收件人) on one label, or None. The receiver
    block comes before the sender (寄) block; reading stops at the 寄 mark, and the store's own sender
    (when known) is never taken for the receiver."""
    joined = "\n".join(lines)
    order = re.search(r"订单[号編编]?[:：]?\s*(\d{16,20})", joined)
    order_id = order.group(1) if order else ""
    for i, line in enumerate(lines):
        if re.fullmatch(r"寄(件人?|方)?[:：]?", line):  # the sender block starts: the receiver is above it
            break
        m = NAME_PHONE.match(line.replace(" ", " "))
        if m and "隐私" not in line:
            name, phone, rest = m.group(1), m.group(2), m.group(3).strip()
        elif MASKED_ONLY.match(line.strip()):  # the name was not read: the receiver without a surname
            name, phone, rest = "", line.strip().lstrip("?？"), ""
        else:
            continue
        if _is_sender(name, phone, sender):
            continue
        parts = [rest] if rest else []
        for nxt in lines[i + 1:i + 9]:  # the address goes on until the sender / goods
            if NOISE.match(nxt.replace(" ", "")):
                continue
            if STOP.match(nxt) or NAME_PHONE.match(nxt) or re.search(r"[*＊]{2,}\d{3}", nxt):
                break  # the sender (寄) block: a name with a masked phone
            parts.append(nxt)
        address = re.sub(r"\s+", "", "".join(parts))
        if not REGION.search(address):
            continue
        digits = re.sub(r"\D", "", phone)
        last4 = digits[-4:] if len(digits) >= 5 or (len(digits) == 4 and not phone.startswith("1")) else ""
        return {"surname": name.strip("*＊")[:1], "masked_name": name, "masked_phone": phone, "last4": last4,
                "address": address, "order_id": order_id}
    return None
