import math
import statistics

from .template_config import TemplateConfig


def apply_price(source_fen: int, cfg: TemplateConfig) -> int:
    """Return the new price in fen."""
    p = cfg.price
    yuan = source_fen / 100
    if yuan >= p.threshold:
        yuan = yuan * p.multiplier + p.add - p.subtract
    if cfg.fixed_tail:
        yuan = math.floor(yuan) + cfg.fixed_tail_value
    fen = yuan * 100
    if p.rounding == "jiao":
        fen = round(fen / 10) * 10
    elif p.rounding == "yuan":
        fen = round(fen / 100) * 100
    else:
        fen = round(fen)
    return max(int(fen), 1)


def drop_lure_skus(skus: list[dict], ratio: float) -> list[dict]:
    """Remove SKUs priced far below the others (引流款). Keeps at least one SKU."""
    if len(skus) < 2:
        return skus
    med = statistics.median(s["price"] for s in skus)
    kept = [s for s in skus if s["price"] >= med * ratio]
    return kept or skus
