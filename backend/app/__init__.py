import os
import time

# China time for datetime.now() and naive timestamps, like 微信小店后台 (the server may run in UTC).
# Needs the system time-zone data (tzdata, installed in the Docker image).
os.environ.setdefault("TZ", "Asia/Shanghai")
if hasattr(time, "tzset"):
    time.tzset()
