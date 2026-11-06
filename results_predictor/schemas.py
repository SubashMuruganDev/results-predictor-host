"""Provenance-bearing records shared by providers and storage."""
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class CalendarEvent:
    symbol: str
    result_date: str
    status: str = 'CONFIRMED_NSE'
    source: str = 'NSE'
    fetched_at: str = ''
    previous_date: Optional[str] = None
    secondary_date: Optional[str] = None

@dataclass
class QuarterlyResult:
    symbol: str
    quarter_end: str
    basis: str
    revenue: Optional[float] = None
    pat: Optional[float] = None
    ebitda: Optional[float] = None
    filing_timestamp: Optional[str] = None
    available_at: Optional[str] = None
    fetched_at: str = ''
    source: str = 'NSE'

@dataclass
class Prediction:
    symbol: str
    quarter_end: str
    result_date: str
    metric: str
    basis: str
    E1: Optional[float]
    threshold: float
    v2_call: str
    state: str = 'PREDICTED'

@dataclass
class Filing:
    symbol: str
    quarter_end: str
    basis: str
    filing_timestamp: str
    available_at: str
    fetched_at: str
    source: str = 'NSE'
    url: Optional[str] = None

@dataclass
class OptionsContext:
    implied_move_pct: Optional[float] = None
    hist_abs_move_pct: Optional[float] = None
    hist_n: int = 0
    atm_iv_pct: Optional[float] = None
    pcr_oi: Optional[float] = None
    expiry_dte: Optional[int] = None
    liquidity: str = 'THIN'
    flags: list[str] = field(default_factory=list)
    signal: None = None
