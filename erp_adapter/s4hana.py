"""
erp_adapter/s4hana.py
─────────────────────
Read real production orders from an SAP S/4HANA OData API and lay them
over the demo shop floor in mock_sap_state.json.

Default target is the free SAP Business Accelerator Hub sandbox
(S/4HANA Cloud, API_PRODUCTION_ORDER_2_SRV), authenticated with the
APIKey header. A real tenant works too: set SAP_API_BASE and SAP_USER /
SAP_PASSWORD for basic auth.

The sandbox's orders do not run on our five CNC machines, so the overlay
keeps the demo's machines, timings and SLA fields (the solver needs them)
and replaces each order's SAP identity with a real one: order number,
material, quantity, unit, plant and order type. If the API is not
configured or fails, the mock is used unchanged and the reason is kept
in _meta so /health and the UI can say which source is live.

Env:
  SAP_API_KEY    Business Accelerator Hub API key (sandbox)
  SAP_API_BASE   OData service root (default: sandbox API_PRODUCTION_ORDER_2_SRV)
  SAP_USER, SAP_PASSWORD   basic auth for a real tenant instead of an API key
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

import httpx

SANDBOX_BASE = (
    "https://sandbox.api.sap.com/s4hanacloud/sap/opu/odata/sap/API_PRODUCTION_ORDER_2_SRV"
)
_SELECT = ",".join([
    "ManufacturingOrder", "ManufacturingOrderType", "Material", "ProductionPlant",
    "MfgOrderPlannedTotalQty", "ProductionUnit", "MfgOrderPlannedStartDate",
    "MfgOrderPlannedEndDate", "OrderIsReleased", "OrderIsClosed",
    "OrderIsMarkedForDeletion",
])
_TIMEOUT = 15.0

_lock = threading.Lock()
_cache: dict[str, Any] | None = None   # {"orders": [...], "fetched_at": str} or {"error": str}


def configured() -> bool:
    return bool(os.getenv("SAP_API_KEY") or (os.getenv("SAP_API_BASE") and os.getenv("SAP_USER")))


def _base() -> str:
    return (os.getenv("SAP_API_BASE") or SANDBOX_BASE).rstrip("/")


def _odata_date(value: Any) -> str | None:
    """OData v2 '/Date(1492041600000)/' → ISO date; pass ISO strings through."""
    if not value:
        return None
    m = re.match(r"/Date\((-?\d+)", str(value))
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).date().isoformat()
    return str(value)[:10]


def _usable(o: dict[str, Any]) -> bool:
    return bool(o.get("ManufacturingOrder")) and not o.get("OrderIsMarkedForDeletion") \
        and not o.get("OrderIsClosed")


def fetch_orders(client: httpx.Client | None = None, top: int = 50) -> list[dict[str, Any]]:
    """GET production orders from the OData service. Raises on any failure."""
    headers = {"Accept": "application/json"}
    auth = None
    if os.getenv("SAP_API_KEY"):
        headers["APIKey"] = os.environ["SAP_API_KEY"]
    if os.getenv("SAP_USER"):
        auth = (os.environ["SAP_USER"], os.getenv("SAP_PASSWORD", ""))
    params = {"$top": str(top), "$select": _SELECT, "$format": "json"}
    url = f"{_base()}/A_ProductionOrder_2"
    own = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        r = client.get(url, params=params, headers=headers, auth=auth)
        r.raise_for_status()
        body = r.json()
    finally:
        if own:
            client.close()
    d = body.get("d", body)
    rows = d.get("results", d.get("value", [])) if isinstance(d, dict) else []
    return [o for o in rows if isinstance(o, dict) and _usable(o)]


def _load_once(client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch once per process; reset() reuses the result so the demo stays stable."""
    global _cache
    with _lock:
        if _cache is None:
            try:
                orders = fetch_orders(client)
                if not orders:
                    raise ValueError("API returned no open production orders")
                _cache = {"orders": orders,
                          "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            except Exception as exc:  # network, auth, JSON — fall back to the mock
                _cache = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        return _cache


def overlay(state: dict[str, Any], client: httpx.Client | None = None) -> dict[str, Any]:
    """Put real S/4HANA order identities onto the mock's orders (in place)."""
    meta = state.setdefault("_meta", {})
    if not configured():
        meta["source"] = "mock"
        return state
    result = _load_once(client)
    if "error" in result:
        meta["source"] = "mock"
        meta["s4hana_error"] = result["error"]
        return state
    sap_orders = result["orders"]
    for order, sap in zip(state.get("production_orders", []), sap_orders):
        order["sap_order_number"] = str(sap["ManufacturingOrder"])
        order["sap_order_type"] = sap.get("ManufacturingOrderType")
        order["sap_plant"] = sap.get("ProductionPlant")
        order["sap_planned_start_date"] = _odata_date(sap.get("MfgOrderPlannedStartDate"))
        order["sap_planned_end_date"] = _odata_date(sap.get("MfgOrderPlannedEndDate"))
        if sap.get("Material"):
            order["product_code"] = str(sap["Material"])
        try:
            qty = float(sap.get("MfgOrderPlannedTotalQty") or 0)
            if qty > 0:
                order["quantity"] = int(qty) if qty.is_integer() else qty
        except (TypeError, ValueError):
            pass
        if sap.get("ProductionUnit"):
            order["quantity_unit"] = str(sap["ProductionUnit"])
    meta["source"] = "s4hana_sandbox" if _base() == SANDBOX_BASE else "s4hana"
    meta["s4hana_service"] = _base()
    meta["s4hana_fetched_at"] = result["fetched_at"]
    meta["s4hana_orders_mapped"] = min(len(sap_orders), len(state.get("production_orders", [])))
    return state


def _reset_cache_for_tests() -> None:
    global _cache
    with _lock:
        _cache = None
