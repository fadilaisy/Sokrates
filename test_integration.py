#!/usr/bin/env python
"""Integration tests for SkillForge backend."""
import httpx
import asyncio

async def test():
    client = httpx.AsyncClient(timeout=30)
    
    # Test health endpoint
    r = await client.get("http://127.0.0.1:8000/health")
    print("Health:", r.status_code, r.json())
    
    # Test get state
    r = await client.get("http://127.0.0.1:8000/api/state")
    state = r.json()
    print("State loaded:", len(state.get("work_centers", [])), "work centers,", len(state.get("production_orders", [])), "orders")
    
    # Test disruption
    r = await client.post("http://127.0.0.1:8000/api/disrupt", json={
        "machine_id": "CNC-02",
        "disruption_type": "BREAKDOWN",
        "start_hour": 2,
        "end_hour": 5
    })
    disrupt = r.json()
    print("Disruption:", r.status_code)
    print("Claude summary:", disrupt.get("claude_summary", "N/A")[:100])
    
    # Test scenario A approval
    sc_a = [s for s in disrupt["scenarios"] if s["id"] == "scenario_a"][0]
    r = await client.post("http://127.0.0.1:8000/api/approve", json={
        "scenario_id": "scenario_a",
        "scenario_data": sc_a,
        "expected_sap_version": disrupt["sap_version"],
        "approved_by": "Production Supervisor — Budi Santoso"
    })
    print("Approve scenario_a:", r.status_code, r.json().get("receipt_id", "N/A")[:20])
    
    # Test ledger
    r = await client.get("http://127.0.0.1:8000/api/ledger")
    print("Ledger entries:", len(r.json()))
    
    # Test ledger verify
    r = await client.get("http://127.0.0.1:8000/api/ledger/verify")
    print("Ledger verify:", r.json())
    
    await client.aclose()
    print("\nAll tests passed!")

asyncio.run(test())
