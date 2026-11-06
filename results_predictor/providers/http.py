"""Serialized, rate-limited HTTP; blocked hosts stay blocked for this run."""
import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
import requests

class StopNetwork(RuntimeError):
    pass

class PoliteClient:
    _locks = {}
    _last = {}
    _guard = threading.Lock()

    def __init__(self, session=None, sleep=time.sleep):
        self.session = session or requests.Session()
        self.sleep = sleep
        self.stopped = set()
        self.session.headers.update({'User-Agent':'Mozilla/5.0 (results_predictor; public financial research)'}) if hasattr(self.session, 'headers') else None

    def get(self, url, **kwargs):
        host = urlsplit(url).hostname
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            if host in self.stopped:
                raise StopNetwork(f'{host}: stopped after persistent 403/429')
            for attempt in range(3):
                wait = random.uniform(3,5) - (time.monotonic()-self._last.get(host, 0))
                if wait > 0:
                    self.sleep(wait)
                try:
                    response = self.session.get(url, timeout=(5,20), **kwargs)
                except requests.RequestException:
                    self._last[host] = time.monotonic()
                    if attempt == 2:
                        raise
                    self.sleep(2 ** attempt)
                    continue
                self._last[host] = time.monotonic()
                if response.status_code not in (403,429) and response.status_code < 500:
                    response.raise_for_status()
                    return response
                if attempt == 2:
                    if response.status_code in (403,429):
                        self.stopped.add(host)
                        raise StopNetwork(f'{host}: persistent HTTP {response.status_code}; coverage unavailable')
                    response.raise_for_status()
                retry = response.headers.get('Retry-After', '')
                try:
                    delay = float(retry)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError):
                        delay = 0
                self.sleep(max(delay, 2 ** attempt))
        raise RuntimeError('unreachable')
