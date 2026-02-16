"""Ingest today's research into Brain."""
import requests
import time

URL = "http://localhost:18800/ingest/message"

messages = [
    {
        "session_id": "wingman-main",
        "role": "assistant", 
        "content": "OI Tracker selling dual target implementation (Feb 16, 2026). Updated selling_tracker.py with T1(25% drop, 1:1 RR) and T2(50% drop, 1:2 RR). T1 sends Telegram notification but doesn't auto-exit. T2 is the auto-exit target. Dashboard shows both targets with T1 hit status indicator. DB columns added: target2_premium, t1_hit, t1_hit_at. Selling backtest: all 1:2 configs maintain 83.3% WR. 25/50 PF=6.81, 30/60 PF=8.00. Mason keeps buying at 1:1 (20/22) with 81.8% WR.",
        "metadata": {"type": "implementation", "project": "oi_tracker"}
    },
    {
        "session_id": "wingman-main",
        "role": "assistant",
        "content": "OI Tracker PM tracker analysis (Feb 16, 2026). 262 detected patterns across 8 days. Types: PM_STRONG_REVERSAL_ALERT(95), PM_REVERSAL_FROM_EXTREME(93), PM_RECOVERING_TO_NEUTRAL(63), PM_CROSSED_ABOVE_MINUS50(11). PM tracker NOT useful for 1:2 buying entries - fires indiscriminately on both winning and range-bound days. Feb 5 had 22 patterns but 0 winners. Feb 9 had 59 patterns but 0 winners. Early winning days (Feb 1-3) had ZERO PM patterns but best 1:2 entries.",
        "metadata": {"type": "research", "project": "oi_tracker"}
    },
    {
        "session_id": "wingman-main", 
        "role": "assistant",
        "content": "OI Tracker Brain fix (Feb 16, 2026). Fixed /stats endpoint timeout - replaced tbl.search().limit(100000).to_list() with tbl.count_rows() in lancedb.py line 248. Root cause: PDF ingestion from Project Athena bloated tables. Brain now uses local embeddings only (384-dim sentence-transformers), no Gemini API calls. Process can hang after extended use - kill and restart resolves it. Brain process: wingman-brain conda env, uvicorn on port 18800.",
        "metadata": {"type": "bugfix", "project": "wingman-brain"}
    },
]

for i, msg in enumerate(messages):
    print(f"Ingesting {i+1}/{len(messages)}...", end=" ", flush=True)
    try:
        r = requests.post(URL, json=msg, timeout=30)
        print(f"OK - {r.json().get('chunks_created', 0)} chunks")
    except Exception as e:
        print(f"Error: {e}")
    time.sleep(1)

print("Done!")
