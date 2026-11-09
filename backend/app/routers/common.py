import io
from datetime import datetime

from fastapi.responses import StreamingResponse
from openpyxl import Workbook


def page_params(page: int, size: int) -> tuple[int, int]:
    page = max(page, 1)
    size = min(max(size, 1), 200)
    return (page - 1) * size, size


def fmt_dt(d: datetime | None) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S") if d else ""


def xlsx_response(filename: str, headers: list[str], rows: list[list]) -> StreamingResponse:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from urllib.parse import quote
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
