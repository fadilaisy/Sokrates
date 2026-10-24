"""
plc_adapter/predictor.py
────────────────────────
Spot a tag heading for its limit before the alarm fires.

Keeps a short window of readings per machine and tag, fits a straight line,
and when the line is clear (enough points, a good fit, a real slope towards
the limit) estimates how long until the tag reaches its warning and critical
values from the limits file. A readable ETA ("CNC-04 reaches 85 °C in about
40 s") is what lets a supervisor act while the machine is still healthy.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Optional


class TrendPredictor:
    def __init__(self, window_s: float = 20.0, min_points: int = 10, min_r2: float = 0.8,
                 horizon_s: float = 3600.0) -> None:
        self.window_s = window_s
        self.min_points = min_points
        self.min_r2 = min_r2
        self.horizon_s = horizon_s
        self._series: dict[tuple[str, str], deque[tuple[float, float]]] = {}

    def reset(self) -> None:
        self._series.clear()

    def observe(self, machine_id: str, metric: str, t: float, value: float) -> None:
        s = self._series.setdefault((machine_id, metric), deque())
        s.append((t, value))
        while s and t - s[0][0] > self.window_s:
            s.popleft()

    def predict(self, machine_id: str, metric: str, limits: dict[str, Any]) -> Optional[dict[str, Any]]:
        """ETA to the warning and critical values, or None when there is no clear trend towards them."""
        s = self._series.get((machine_id, metric))
        if not s or len(s) < self.min_points:
            return None
        n = len(s)
        t0 = s[0][0]
        xs = [t - t0 for t, _ in s]
        ys = [v for _, v in s]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0:
            return None
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        ss_tot = sum((y - my) ** 2 for y in ys)
        ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        rising = limits.get("direction", "rising") == "rising"
        if r2 < self.min_r2 or (slope <= 0 if rising else slope >= 0):
            return None
        # Sensor noise can line up by chance; the fitted change must stand well clear of the scatter.
        resid_sd = (ss_res / max(1, n - 2)) ** 0.5
        if abs(slope) * (xs[-1] - xs[0]) < 3.0 * resid_sd:
            return None
        now_fit = my + slope * (xs[-1] - mx)

        def eta(limit: Optional[float]) -> Optional[float]:
            if limit is None:
                return None
            gap = (limit - now_fit) if rising else (now_fit - limit)
            return max(0.0, gap / abs(slope))

        eta_warn, eta_crit = eta(limits.get("warning")), eta(limits.get("critical"))
        first = eta_warn if eta_warn is not None else eta_crit
        if first is None or first > self.horizon_s:
            return None
        return {
            "machine_id": machine_id,
            "metric": metric,
            "current": round(ys[-1], 2),
            "slope_per_min": round(slope * 60.0, 2),
            "r2": round(r2, 3),
            "warning": limits.get("warning"),
            "critical": limits.get("critical"),
            "eta_warning_s": None if eta_warn is None else round(eta_warn, 1),
            "eta_critical_s": None if eta_crit is None else round(eta_crit, 1),
        }
