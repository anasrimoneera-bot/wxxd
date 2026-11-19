"""China time (UTC+8): what 微信小店后台 shows. The server may run in UTC."""
from datetime import datetime, timedelta, timezone

CN = timezone(timedelta(hours=8))


def cn_time(ts: int | float) -> datetime:
    """A Unix timestamp as naive China time."""
    return datetime.fromtimestamp(ts, CN).replace(tzinfo=None)


def cn_ts(d: datetime) -> int:
    """Naive China time → Unix timestamp."""
    return int(d.replace(tzinfo=CN).timestamp())
