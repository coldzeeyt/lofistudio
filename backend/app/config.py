import os
from pathlib import Path
DATA = Path(os.getenv('DATA_DIR', './data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
MAX_UPLOAD = int(os.getenv('MAX_UPLOAD_MB', '250')) * 1024**2
MAX_STORAGE = int(os.getenv('MAX_STORAGE_GB', '40')) * 1024**3
TTL = int(os.getenv('RETENTION_HOURS', '24')) * 3600
MAX_ACTIVE = int(os.getenv('MAX_ACTIVE_JOBS', '3'))
MAX_SECONDS = int(os.getenv('MAX_RENDER_SECONDS', '10800'))

DAILY_SECONDS = int(os.getenv('MAX_DAILY_RENDER_SECONDS', '21600'))
