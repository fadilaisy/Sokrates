"""S/4HANA overlay: real order identities over the mock floor, mock on failure."""

import json
from pathlib import Path

import httpx
import pytest

from erp_adapter import s4hana

MOCK = Path(__file__).resolve().parent.parent / "erp_adapter" / "mock_sap_state.json"

SAMPLE = {"d": {"results": [
    {"ManufacturingOrder": "1000011", "ManufacturingOrderType": "YBM1", "Material": "FG126",
     "ProductionPlant": "1710", "MfgOrderPlannedTotalQty": "250", "ProductionUnit": "PC",
     "MfgOrderPlannedStartDate": "/Date(1492041600000)/", "MfgOrderPlannedEndDate": "/Date(1492128000000)/",
     "OrderIsClosed": False, "OrderIsMarkedForDeletion": False},
    {"ManufacturingOrder": "1000012", "Material": "FG127", "OrderIsClosed": True},
    {"ManufacturingOrder": "1000013", "Material": "FG128", "MfgOrderPlannedTotalQty": "12.5"},
]}}


def _state():
    return json.loads(MOCK.read_text(encoding="utf-8"))


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("SAP_API_KEY", "SAP_API_BASE", "SAP_USER", "SAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    s4hana._reset_cache_for_tests()
    yield
    s4hana._reset_cache_for_tests()


def test_unconfigured_keeps_mock():
    state = s4hana.overlay(_state())
    assert state["_meta"]["source"] == "mock"
    assert "sap_order_number" not in state["production_orders"][0]


def test_sandbox_orders_mapped(monkeypatch):
    monkeypatch.setenv("SAP_API_KEY", "k")
    seen = {}

    def handler(req):
        seen["key"] = req.headers.get("APIKey")
        seen["path"] = req.url.path
        return httpx.Response(200, json=SAMPLE)

    state = s4hana.overlay(_state(), _client(handler))
    first, second = state["production_orders"][:2]
    assert seen["key"] == "k" and seen["path"].endswith("/A_ProductionOrder_2")
    assert state["_meta"]["source"] == "s4hana_sandbox"
    assert first["id"] == "WO-2025-001"                      # demo ids kept for the solver
    assert first["sap_order_number"] == "1000011"
    assert (first["product_code"], first["quantity"], first["quantity_unit"]) == ("FG126", 250, "PC")
    assert first["sap_planned_start_date"] == "2017-04-13"
    assert second["sap_order_number"] == "1000013"            # closed order skipped
    assert second["quantity"] == 12.5
    assert "sap_order_number" not in state["production_orders"][2]
    assert state["_meta"]["s4hana_orders_mapped"] == 2


def test_api_failure_falls_back(monkeypatch):
    monkeypatch.setenv("SAP_API_KEY", "bad")
    state = s4hana.overlay(_state(), _client(lambda req: httpx.Response(401, text="Invalid ApiKey")))
    assert state["_meta"]["source"] == "mock"
    assert "401" in state["_meta"]["s4hana_error"]
    assert "sap_order_number" not in state["production_orders"][0]


def test_fetch_is_cached(monkeypatch):
    monkeypatch.setenv("SAP_API_KEY", "k")
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=SAMPLE)

    c = _client(handler)
    s4hana.overlay(_state(), c)
    s4hana.overlay(_state(), c)
    assert len(calls) == 1
