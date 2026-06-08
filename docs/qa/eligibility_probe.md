# Eligibility Probe Documentation

This document explains the purpose, architecture, and interpretation of the **Live-Eligibility-Probe** (`scripts/eligibility_probe.py`). The probe acts as a Go/No-Go pre-flight check before activating the `neo-p002-r3` real-analysis feeder (`EXECUTION_SHADOW_REAL_GENERATOR=true`).

---

## 1. Why `DocumentRepository` is the Correct Data Source

The probe queries `DocumentRepository.list()` (which reads the `canonical_documents` database table) rather than downstream audit logs or queues.

- **Ground Truth of Ingestion:** `DocumentRepository` contains all documents processed, normalized, and analyzed by the ingestion pipeline (RSS, Newsdata, Twitter, YouTube). This is the raw "top-of-funnel" input before any gating logic is applied.
- **Funnel Visibility:** By querying the repository, we can build a complete rejection funnel (e.g., how many documents failed symbol mapping, had no confidence metrics, or were non-directional). This helps diagnose whether a low signal count is due to lack of raw data or aggressive filtering.
- **Database-First Integrity:** The feeder itself relies on database states. Querying the DB directly ensures we are inspecting the exact same records that the feeder would evaluate.

---

## 2. Why `alert_audit` is NOT the Feeder Path

It is critical not to use `alert_audit.jsonl` (or the `/trail` / alert registry outputs) to measure feeder eligibility.

- **Architectural Separation:** The alert delivery pipeline (`alert_audit`) is designed to capture alerts dispatched to the operator (via Telegram, email, etc.). In contrast, the feeder is part of the **trading loop and execution pipeline**, which operates independently of alerting.
- **Different Eligibility Rules:** A document can be "alert-worthy" without meeting the precise execution predicates required by the real feeder (and vice versa). For example, the feeder has strict requirements regarding tickers, confidence presence, and directional confidence.
- **Independent Failure Domains:** Using `alert_audit` would introduce a dependency on alert delivery channels. If Telegram notifications are throttled or fail, the alert audit log would show zero entries, but the execution loop would still be fully capable of processing documents from the database.

---

## 3. Interpretation of GO / DÜNN / NO-GO Verdicts

The probe outputs a verdict based on the number of documents in the **last 48 hours** that are fully eligible and satisfy the D-182 gate (`priority_score >= 10`).

### 🟢 **GO** (`eligible_AND_gate_ge_10 >= 10`)
* **Interpretation:** There is a robust volume of high-conviction, eligible signals (at least 10 in 48 hours).
* **Execution Context:** In this state, enabling the feeder yields a substantial sample of shadow candidates for execution and calibration analysis.

### 🟡 **DÜNN** (`0 < eligible_AND_gate_ge_10 < 10`)
* **Interpretation:** The pipeline is active, but very few signals pass the high-conviction priority threshold.
* **Execution Context:** In this state, enabling the feeder results in "honest silence" during quiet market regimes, yielding fewer ledger samples for analysis.

### 🔴 **NO-GO heute** (`eligible_AND_gate_ge_10 == 0`)
* **Interpretation:** Zero high-conviction signals were matched in the last 48 hours.
* **Execution Context:** In this state, the feeder remains inactive or, if enabled, results in an empty ledger. Pipeline health should be verified first using `scripts/server_status.sh`.

---

## 4. Windows and Console Compatibility

The Live-Eligibility-Probe script is fully compatible with Windows legacy command prompt and PowerShell host encodings (such as **cp1252**):
- **ASCII-Only Outputs:** Stdout does not output mathematical or Unicode-only symbols (e.g., `∧`, `≥`) that trigger `UnicodeEncodeError` or `UnicodeDecodeError` in standard Windows consoles.
- **Run Command:** Developers can safely run the probe locally on Windows workstations using:
  ```powershell
  python scripts/eligibility_probe.py
  ```

