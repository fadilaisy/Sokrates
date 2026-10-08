"""
rag/eval.py
───────────
Golden-set evaluation harness. Build the golden set before tuning weights so
every stage has to prove it earns its place.

    python -m rag.eval                          # demo golden set, all ablation modes
    python -m rag.eval --golden my_golden.json  # your own set
    python -m rag.eval --answer                 # also generate answers (needs an LLM key)
    python -m rag.eval --json report.json       # write the full report

Golden item format
------------------
{
  "id": "g1",
  "incident": {"machine_id": "CNC-02", "fault_code": "Alarm 108", "disruption_type": "OVERHEAT"},
  "query": "langkah pemulihan setelah alarm overheat spindle",
  "expected": [{"file": "SOP_x.pdf", "page": 1, "contains": "20 menit"}],
  "expect_abstain": false
}
Expected sources are matched by original filename + page + a phrase inside the
chunk, so the set survives re-ingestion (doc_ids contain content hashes).

Metrics: recall@k, MRR, nDCG@k, abstain accuracy, retrieval latency p95, and
with --answer: citation precision and faithfulness (share of claims the
post-check accepted).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .retrieve import Retriever
from .store import Store, get_store

MODES = ["bm25", "bm25_filters", "full"]


def _relevant_sets(store: Store, expected: List[dict]) -> List[Set[str]]:
    """One set of acceptable chunk_ids per expected source."""
    out = []
    for e in expected:
        rows = store.query(
            "SELECT c.chunk_id, c.text, c.header FROM chunks c JOIN documents d ON d.doc_id = c.doc_id "
            "WHERE d.filename = ? AND c.page = ? AND d.status IN ('indexed', 'superseded')",
            (e["file"], e.get("page", 1)))
        phrase = (e.get("contains") or "").lower()
        out.append({r["chunk_id"] for r in rows
                    if not phrase or phrase in (r["text"] + " " + (r["header"] or "")).lower()})
    return out


def _metrics(ranked: List[str], rel_sets: List[Set[str]], k: int) -> Dict[str, float]:
    if not rel_sets:
        return {}
    top = ranked[:k]
    found_rank: List[Optional[int]] = []
    for rs in rel_sets:
        r = next((i for i, cid in enumerate(top) if cid in rs), None)
        found_rank.append(r)
    recall = sum(1 for r in found_rank if r is not None) / len(rel_sets)
    all_rel = set().union(*rel_sets)
    first = next((i for i, cid in enumerate(ranked) if cid in all_rel), None)
    mrr = 1.0 / (first + 1) if first is not None else 0.0
    dcg = sum(1.0 / math.log2(r + 2) for r in found_rank if r is not None)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(rel_sets), k)))
    return {"recall": recall, "mrr": mrr, "ndcg": dcg / idcg if idcg else 0.0}


def run(golden: List[dict], *, k: int = 5, modes: List[str] = MODES, answer: bool = False,
        store: Optional[Store] = None) -> Dict[str, Any]:
    store = store or get_store()
    retriever = Retriever(store=store)
    report: Dict[str, Any] = {"k": k, "modes": {}, "items": []}

    unresolved = []
    rel_cache = {}
    for g in golden:
        rel_cache[g["id"]] = _relevant_sets(store, g.get("expected", []))
        if any(not s for s in rel_cache[g["id"]]):
            unresolved.append(g["id"])
    report["unresolved_expectations"] = unresolved

    for mode in modes:
        rows, lat, abst_ok, abst_n = [], [], 0, 0
        for g in golden:
            res = retriever.retrieve(g.get("incident", {}), g.get("query", ""), mode=mode, top_k=k, log=False)
            lat.append(res["latency_ms"])
            ranked = [r["chunk_id"] for r in res["results"] if not r.get("forced_safety")]
            item = {"id": g["id"], "mode": mode, "abstained": res["abstained"], "ranked": ranked[:k]}
            if g.get("expect_abstain"):
                abst_n += 1
                ok = res["abstained"] if mode == "full" else not ranked
                abst_ok += int(ok)
                item["abstain_correct"] = ok
            else:
                m = _metrics(ranked, rel_cache[g["id"]], k)
                item.update(m)
                rows.append(m)
                if answer and mode == "full" and not res["abstained"]:
                    item.update(_answer_metrics(g, res, rel_cache[g["id"]], store))
            report["items"].append(item)
        agg = {key: round(statistics.mean(r[key] for r in rows), 3) for key in ("recall", "mrr", "ndcg")} if rows else {}
        agg["abstain_accuracy"] = round(abst_ok / abst_n, 3) if abst_n else None
        agg["latency_p95_ms"] = round(sorted(lat)[max(0, math.ceil(0.95 * len(lat)) - 1)], 1) if lat else None
        if answer and mode == "full":
            ans = [i for i in report["items"] if i["mode"] == "full" and "citation_precision" in i]
            if ans:
                agg["citation_precision"] = round(statistics.mean(i["citation_precision"] for i in ans), 3)
                agg["faithfulness"] = round(statistics.mean(i["faithfulness"] for i in ans), 3)
        report["modes"][mode] = agg
    return report


def _answer_metrics(g: dict, retrieval: dict, rel_sets: List[Set[str]], store: Store) -> Dict[str, Any]:
    from .generate import answer_from_retrieval
    ans = answer_from_retrieval(g.get("query", ""), retrieval, store=store)
    cited = [c for cl in ans.get("claims", []) for c in cl.get("chunk_ids", [])]
    all_rel = set().union(*rel_sets) if rel_sets else set()
    # A citation is "precise" if it points at an expected source or at a forced Safety passage.
    allowed = all_rel | {r["chunk_id"] for r in retrieval["results"] if r.get("forced_safety")}
    prec = (sum(1 for c in cited if c in allowed) / len(cited)) if cited else 0.0
    n_claims = len(ans.get("claims", [])) + len(ans.get("rejected_claims", []))
    faith = (len(ans.get("claims", [])) / n_claims) if n_claims else 0.0
    return {"citation_precision": round(prec, 3), "faithfulness": round(faith, 3),
            "answer_status": ans.get("status")}


def _print(report: Dict[str, Any]) -> None:
    k = report["k"]
    print(f"\nRAG evaluation  (k={k})")
    if report.get("unresolved_expectations"):
        print("  ! expected sources not found in the index for:", ", ".join(report["unresolved_expectations"]))
    cols = ["recall", "mrr", "ndcg", "abstain_accuracy", "latency_p95_ms", "citation_precision", "faithfulness"]
    present = [c for c in cols if any(c in m for m in report["modes"].values())]
    print(f"  {'mode':<14}" + "".join(f"{c:>20}" for c in present))
    for mode, m in report["modes"].items():
        print(f"  {mode:<14}" + "".join(f"{str(m.get(c, '-')):>20}" for c in present))
    print()
    for it in report["items"]:
        if it["mode"] != "full":
            continue
        tag = ("abstain ok" if it.get("abstain_correct") else "abstain MISSED") if "abstain_correct" in it \
            else f"recall={it.get('recall', 0):.2f} mrr={it.get('mrr', 0):.2f}"
        print(f"  [{it['id']}] {tag}" + (f"  answer={it['answer_status']} cit_prec={it['citation_precision']}"
                                          f" faith={it['faithfulness']}" if "answer_status" in it else ""))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate Sokrates RAG retrieval against a golden set.")
    ap.add_argument("--golden", type=Path, help="golden set JSON (default: demo set in rag/samples.py)")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--mode", choices=MODES, action="append")
    ap.add_argument("--answer", action="store_true", help="also generate answers (calls the LLM)")
    ap.add_argument("--json", type=Path, help="write the full report here")
    a = ap.parse_args(argv)
    if a.golden:
        golden = json.loads(a.golden.read_text(encoding="utf-8"))
    else:
        from .samples import GOLDEN
        golden = GOLDEN
    report = run(golden, k=a.k, modes=a.mode or MODES, answer=a.answer)
    _print(report)
    if a.json:
        a.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
