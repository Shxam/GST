# GST Reconciliation — OpenEnv

An OpenEnv-compliant reinforcement-learning environment for Indian GST invoice
reconciliation against GSTR-2B auto-generated ITC statements.

---

## Table of Contents

- [Problem Statement](#problem-statement)
- [Objective](#objective)
- [Data Flow](#data-flow)
- [Environment Design](#environment-design)
- [Tasks](#tasks)
- [Reward Structure](#reward-structure)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Agent](#agent)
- [Baseline](#baseline)
- [Docker](#docker)

---

## Problem Statement

Every registered business in India must file monthly GST returns. A critical
step is **reconciling purchase invoices** against **GSTR-2B** — the
government-generated statement that lists all inward supplies reported by
suppliers. Mismatches between a buyer's purchase register and their GSTR-2B
directly affect how much **Input Tax Credit (ITC)** they can legally claim.

Manual reconciliation is error-prone and time-consuming:

- Large businesses process **thousands of invoices** every month
- Mismatches arise from **amount discrepancies**, **date shifts**, **GSTIN
  errors**, **missing entries**, and **duplicate filings**
- Incorrect ITC claims trigger **audits, penalties, and interest charges**
- Late filing adds **penalty days** that further reduce recoverable ITC

This environment simulates the full reconciliation pipeline as an RL task,
enabling agents to learn accurate and efficient invoice matching strategies.

---

## Objective

Given a set of **purchase invoices** and the corresponding **GSTR-2B entries**
for a tax period, the agent must:

1. **Classify** every invoice into exactly one of four statuses:

   | Status | Meaning |
   |---|---|
   | `MATCHED` | Invoice found in GSTR-2B with all fields matching |
   | `MISMATCH` | Invoice found but one or more fields differ |
   | `MISSING_IN_2B` | Invoice not present in GSTR-2B at all |
   | `EXTRA_IN_2B` | Same invoice appears more than once in GSTR-2B |

2. **Identify mismatch fields** for every `MISMATCH` entry (e.g. `taxable_value`,
   `invoice_date`, `supplier_gstin`, `itc_available`)

3. **Compute claimable ITC** as the sum of `cgst + sgst + igst` for
   `MATCHED` invoices only

4. **Maximise the grader score** across all three difficulty levels

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DATA GENERATION                              │
│                                                                     │
│  data_generator.py                                                  │
│  ┌──────────────┐    seed-based    ┌──────────────────────────┐    │
│  │  Faker (IN)  │ ─────────────── ▶│  Purchase Invoices       │    │
│  │  Random RNG  │                  │  (vendor, GSTIN, HSN,    │    │
│  └──────────────┘                  │   taxable value, taxes)  │    │
│                                    └────────────┬─────────────┘    │
│                                                 │                  │
│                                    ┌────────────▼─────────────┐    │
│                                    │  GSTR-2B Entries          │   │
│                                    │  (mirrored + injected     │   │
│                                    │   errors per task)        │   │
│                                    └────────────┬─────────────┘    │
│                                                 │                  │
│                                    ┌────────────▼─────────────┐    │
│                                    │  Ground Truth Dict        │   │
│                                    │  {invoice_id → status}    │   │
│                                    └──────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                    POST /reset   │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        ENVIRONMENT (env.py)                         │
│                                                                     │
│   reset(task_id)                                                    │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │  Observation                                                 │  │
│   │  ├── task_id, episode_id, step_number                        │  │
│   │  ├── invoices: List[Invoice]                                 │  │
│   │  ├── gstr2b_entries: List[GSTR2BEntry]                       │  │
│   │  ├── tax_period, max_itc_possible                            │  │
│   │  └── instructions                                            │  │
│   └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  │  Observation sent to Agent
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        AGENT (agent.py)                             │
│                                                                     │
│  Step 1 — Deterministic Pre-Pass                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  For each invoice:                                           │   │
│  │  • 0 GSTR-2B matches  →  MISSING_IN_2B  (resolved)          │   │
│  │  • 2+ GSTR-2B matches →  EXTRA_IN_2B    (resolved)          │   │
│  │  • 1 match, no diff   →  MATCHED        (resolved)          │   │
│  │  • 1 match, has diff  →  ambiguous      (→ LLM)             │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Step 2 — LLM Batching (Groq, llama-3.3-70b-versatile)             │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Ambiguous invoices split into batches of 10                 │   │
│  │  Each batch → structured prompt → Groq API → JSON response  │   │
│  │  65s sleep between batches (TPM rate limit management)       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Step 3 — Post-Processing                                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  • Merge pre-resolved + LLM results                          │   │
│  │  • Enrich mismatch_fields deterministically                  │   │
│  │  • Recompute ITC from MATCHED invoices only                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                    POST /step    │   Action {reconciliation_result,
                                  │           claimable_itc, confidence}
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        GRADER (graders/)                            │
│                                                                     │
│  grader1 (easy)   →  0.7 × accuracy  +  0.3 × itc_score            │
│  grader2 (medium) →  0.4 × weighted_acc  +  0.4 × itc  + 0.2 × pen │
│  grader3 (hard)   →  0.35 × weighted_acc + 0.35 × itc              │
│                    +  0.15 × penalty  +  0.15 × field_bonus         │
│                                                                     │
│  Returns ──▶  Reward { total, match_score, itc_score,              │
│                         penalty_day_penalty, done, info }           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Environment Design

```
GST/
├── gst_env/
│   ├── __init__.py          # Public exports
│   ├── models.py            # Pydantic schemas (Invoice, Action, Reward …)
│   ├── env.py               # GSTReconciliationEnv — reset / step / state
│   ├── data_generator.py    # Seed-based invoice & GSTR-2B generation
│   ├── main.py              # FastAPI app — all HTTP endpoints
│   ├── agent.py             # Groq-powered agent with deterministic pre-pass
│   ├── baseline.py          # Naive baseline (rule-based, no LLM)
│   └── graders/
│       ├── __init__.py      # Route to task-specific grader
│       ├── grader1.py       # Easy task grader
│       ├── grader2.py       # Medium task grader
│       └── grader3.py       # Hard task grader
├── Dockerfile
├── requirements.txt
├── openenv.yaml
└── .env
```

**Key design decisions:**

- **Single-step environment** — each episode is one reset + one step
- **Seed-based determinism** — same seed always produces identical invoices
- **Hybrid agent** — Python diff handles clear cases; LLM handles only ambiguous ones
- **Batched LLM calls** — avoids Groq free-tier TPM limits (12K tokens/min)

---

## Tasks

| Task | Invoices | GSTR-2B | Mismatch Types | Difficulty |
|---|---|---|---|---|
| `task1_easy` | 10 | 10 | None — all perfect matches | Easy |
| `task2_medium` | 50 | 48 | Amount, date, GSTIN, missing | Medium |
| `task3_hard` | 200 | 190 | All types + duplicates + reverse charge + penalty days | Hard |

### Mismatch types injected

- **Amount mismatch** — taxable value changed by ±15% (medium) or +20–50% (hard)
- **Date shift** — invoice date shifted forward 5–30 days
- **GSTIN error** — one character in supplier GSTIN changed
- **Missing in 2B** — invoice has no corresponding GSTR-2B entry
- **Duplicate (EXTRA_IN_2B)** — same invoice appears twice in GSTR-2B
- **Reverse charge** — `itc_available` set to `False`
- **Penalty days** — random 0–30 days added to task3, reducing max reward

---

## Reward Structure

### Task 1 (Easy)
```
score = 0.70 × accuracy + 0.30 × itc_score
```

### Task 2 (Medium)
```
score = 0.40 × weighted_accuracy + 0.40 × itc_score + 0.20 × penalty_score
```
MISMATCH and MISSING_IN_2B carry 1.5× weight in accuracy calculation.

### Task 3 (Hard)
```
score = 0.35 × weighted_accuracy + 0.35 × itc_score
      + 0.15 × penalty_score    + 0.15 × field_bonus
```
MISMATCH and MISSING_IN_2B carry 2.3× weight. `field_bonus` rewards correct
identification of which specific fields mismatched.

**ITC Score** = `max(0, 1 − |predicted_itc − true_itc| / true_itc)`

**Penalty Score** = `max(0, 1 − penalty_days / 30)`

---

## Quick Start

### Local (without Docker)

```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start server  (Terminal 1)
uvicorn gst_env.main:app --host 0.0.0.0 --port 7860 --reload

# 4. Run agent    (Terminal 2)
python -m gst_env.agent
```

### Test endpoints manually

```bash
curl http://localhost:7860/health
curl http://localhost:7860/tasks
curl http://localhost:7860/state

curl -X POST http://localhost:7860/reset \
  -H "Content-Type: application/json" \
  -d '{"task_id": "task1_easy"}'

curl http://localhost:7860/baseline
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Liveness probe |
| GET | `/tasks` | List all tasks |
| GET | `/state` | Current episode state |
| POST | `/reset` | Start new episode — body: `{"task_id": "..."}` |
| POST | `/step` | Submit action — body: `Action` schema |
| POST | `/grader` | Grade without advancing state |
| GET | `/baseline` | Run naive baseline on all tasks |

Interactive docs available at: `http://localhost:7860/docs`

---

## Agent

The agent uses a **two-stage hybrid strategy**:

**Stage 1 — Deterministic pre-pass (Python)**
- Matches every invoice against GSTR-2B by `invoice_number`
- Classifies clear-cut MISSING, EXTRA, and MATCHED cases in milliseconds
- Identifies ambiguous invoices (those with exactly one GSTR-2B match but field differences)

**Stage 2 — LLM reasoning (Groq)**
- Only ambiguous invoices are sent to the LLM
- Sent in batches of 10 to stay within the 12K TPM free-tier limit
- Model: `llama-3.3-70b-versatile`
- ITC and mismatch_fields are recomputed deterministically after LLM classification

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | — | Required. Get free key at console.groq.com |
| `BASE_URL` | `http://localhost:7860` | Server URL |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model ID |

---

## Baseline

A naive rule-based baseline (no LLM) is available:

```bash
curl http://localhost:7860/baseline
# or
python -m gst_env.baseline
```

Baseline strategy: marks the expected number of invoices as MISMATCH/MISSING
by index position rather than by actual field comparison.

---

## Docker

```bash
# Build
docker build -t gst-recon-env .

# Run server
docker run -p 7860:7860 gst-recon-env

# Run agent (connect to running server)
docker run --network host \
  -e GROQ_API_KEY=your_key_here \
  gst-recon-env \
  python -m gst_env.agent
```

---

## Results

| Task | Match Score | ITC Score | Total | Rating |
|---|---|---|---|---|
| task1_easy | 1.0000 | 1.0000 | **1.0000** | ✅ Perfect |
| task2_medium | 1.0000 | 1.0000 | **1.0000** | ✅ Perfect |
| task3_hard | 1.0000 | 1.0000 | **0.9250** | ✅ Excellent |
| **Average** | | | **0.9750** | ⭐⭐⭐⭐⭐ |

Task 3's score gap from 1.0 is due to randomly generated `penalty_days`
(0–30), which is outside the agent's control by design.

---
