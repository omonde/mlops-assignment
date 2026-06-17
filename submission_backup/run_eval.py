"""Eval runner using execution accuracy.

Reads evals/eval_set.jsonl, calls the agent at AGENT_URL on each question,
then compares the agent's SQL output to the gold SQL by *executed rows*
(canonicalized: sorted, stringified, None-coerced to empty).

Helpers (run_sql / canonicalize / matches) are provided. You implement
eval_one() and summarize().

Run:
    uv run python evals/run_eval.py --out results/eval_baseline.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVAL_FILE = ROOT / "evals" / "eval_set.jsonl"
DEFAULT_OUT_FILE = ROOT / "results" / "eval_baseline.json"
DB_DIR = ROOT / "data" / "bird"
AGENT_URL_DEFAULT = "http://localhost:8001/answer"


# ---------- Helpers (provided) -----------------------------------------

def run_sql(db_id: str, sql: str, timeout: float = 5.0) -> tuple[bool, list[tuple] | None, str | None]:
    """Run sql against db_id in read-only mode. Returns (ok, rows, error)."""
    path = DB_DIR / f"{db_id}.sqlite"
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout) as conn:
            cur = conn.execute(sql)
            rows = cur.fetchall()
            return True, rows, None
    except Exception as e:  # noqa: BLE001
        return False, None, f"{type(e).__name__}: {e}"


def canonicalize(rows: list[tuple] | None) -> list[tuple] | None:
    """Sort rows; coerce cells to str; None -> ''."""
    if rows is None:
        return None
    return sorted(tuple("" if c is None else str(c) for c in row) for row in rows)


def matches(gold_rows: list[tuple] | None, pred_rows: list[tuple] | None) -> bool:
    if gold_rows is None or pred_rows is None:
        return False
    return canonicalize(gold_rows) == canonicalize(pred_rows)


# ---------- Implement these (Phase 5) ----------------------------------

def eval_one(question: dict, agent_url: str) -> dict:
    """Score one question. Return a dict capturing per-iteration correctness."""
    q_text = question["question"]
    db_id = question["db_id"]
    gold_sql = question["gold_sql"]

    gold_ok, gold_rows, gold_error = run_sql(db_id, gold_sql)

    t0 = time.monotonic()
    try:
        resp = httpx.post(
            agent_url,
            json={
                "question": q_text,
                "db": db_id,
                "tags": {
                    "phase": "eval_baseline",
                    "db": db_id,
                },
            },
            timeout=120.0,
        )
        latency = time.monotonic() - t0
        resp.raise_for_status()
        agent = resp.json()
    except Exception as e:  # noqa: BLE001
        return {
            "question": q_text,
            "db_id": db_id,
            "gold_sql": gold_sql,
            "gold_ok": gold_ok,
            "gold_error": gold_error,
            "agent_ok": False,
            "agent_error": f"{type(e).__name__}: {e}",
            "final_sql": "",
            "iterations": 0,
            "latency_seconds": time.monotonic() - t0,
            "correct": False,
            "per_iteration": [],
        }

    final_sql = agent.get("sql", "")
    pred_ok, pred_rows, pred_error = run_sql(db_id, final_sql)
    correct = gold_ok and pred_ok and matches(gold_rows, pred_rows)

    history = agent.get("history", [])
    per_iteration = []
    current_sql = None
    iteration = 0

    for item in history:
        node = item.get("node")
        if node in {"generate_sql", "revise"}:
            iteration += 1
            current_sql = item.get("sql", "")
            ok_i, rows_i, err_i = run_sql(db_id, current_sql)
            correct_i = gold_ok and ok_i and matches(gold_rows, rows_i)
            per_iteration.append({
                "iteration": iteration,
                "node": node,
                "sql": current_sql,
                "ok": ok_i,
                "error": err_i,
                "correct": correct_i,
            })

    if not per_iteration:
        per_iteration.append({
            "iteration": 1,
            "node": "final",
            "sql": final_sql,
            "ok": pred_ok,
            "error": pred_error,
            "correct": correct,
        })

    return {
        "question": q_text,
        "db_id": db_id,
        "gold_sql": gold_sql,
        "gold_ok": gold_ok,
        "gold_error": gold_error,
        "final_sql": final_sql,
        "agent_ok": bool(agent.get("ok", False)),
        "agent_error": agent.get("error"),
        "pred_ok": pred_ok,
        "pred_error": pred_error,
        "iterations": agent.get("iterations", len(per_iteration)),
        "latency_seconds": latency,
        "correct": correct,
        "per_iteration": per_iteration,
        "history": history,
    }


def summarize(results: list[dict]) -> dict:
    """Aggregate per-question results."""
    n = len(results)
    correct = sum(1 for r in results if r.get("correct"))
    max_iter = max(
        (len(r.get("per_iteration", [])) for r in results),
        default=0,
    )

    per_iteration_pass_rate = {}
    for k in range(1, max_iter + 1):
        passed = 0
        for r in results:
            pis = r.get("per_iteration", [])
            if not pis:
                continue
            idx = min(k, len(pis)) - 1
            if pis[idx].get("correct"):
                passed += 1
        per_iteration_pass_rate[str(k)] = passed / n if n else 0.0

    latencies = sorted(r.get("latency_seconds", 0.0) for r in results)
    p95 = latencies[int(0.95 * (n - 1))] if n else 0.0

    return {
        "n": n,
        "correct": correct,
        "accuracy": correct / n if n else 0.0,
        "per_iteration_pass_rate": per_iteration_pass_rate,
        "avg_latency_seconds": sum(latencies) / n if n else 0.0,
        "p95_latency_seconds": p95,
        "avg_iterations": (
            sum(r.get("iterations", 0) for r in results) / n if n else 0.0
        ),
    }


# ---------- Main (provided) --------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-set", type=Path, default=DEFAULT_EVAL_FILE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_FILE)
    parser.add_argument("--agent-url", default=AGENT_URL_DEFAULT)
    args = parser.parse_args()

    questions = [json.loads(line) for line in args.eval_set.read_text().splitlines() if line.strip()]
    print(f"Loaded {len(questions)} eval questions from {args.eval_set}")

    results: list[dict] = []
    t0 = time.monotonic()
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q['db_id']}: {q['question'][:60]}...", flush=True)
        results.append(eval_one(q, args.agent_url))
    elapsed = time.monotonic() - t0

    summary = summarize(results)
    out = {
        "summary": summary,
        "wall_clock_seconds": elapsed,
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    print(f"Wrote {args.out}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
