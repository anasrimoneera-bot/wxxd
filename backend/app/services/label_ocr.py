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
    r"(1[\d*＊]{10}|1?[\d*＊]{0,7}[*＊][\d*＊]{0,7}\d{3,4}|1\d{10})(?:[\s.·,，]+|$)(.*)$")
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
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(image)).convert("RGB")
    if max(img.size) < 1200:  # small previews: enlarge for OCR
        k = 1600 / max(img.size)
        img = img.resize((int(img.width * k), int(img.height * k)))
    return _ocr_lines(np.array(img))


def _ocr_lines(arr) -> list[str]:
    res, _ = _engine()(arr)
    rows = sorted(((b[0][1], b[0][0], t) for b, t, _ in res or []), key=lambda r: (round(r[0] / 12), r[1]))
    return [t.strip() for _, _, t in rows if t.strip()]


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
        if not m or "隐私" in line:
            continue
        name, phone, rest = m.group(1), m.group(2), m.group(3).strip()
        if _is_sender(name, phone, sender):
            continue
        parts = [rest] if rest else []
        for nxt in lines[i + 1:i + 9]:  # the address goes on until the sender / goods
            if NOISE.match(nxt.replace(" ", "")):
                continue
            if STOP.match(nxt) or NAME_PHONE.match(nxt):
                break
            parts.append(nxt)
        address = re.sub(r"\s+", "", "".join(parts))
        if not REGION.search(address):
            continue
        digits = re.sub(r"\D", "", phone)
        last4 = digits[-4:] if len(digits) >= 5 or (len(digits) == 4 and not phone.startswith("1")) else ""
        return {"surname": name.strip("*＊")[:1], "masked_name": name, "masked_phone": phone, "last4": last4,
                "address": address, "order_id": order_id}
    return None
