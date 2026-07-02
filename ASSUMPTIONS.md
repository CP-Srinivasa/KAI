# ASSUMPTIONS.md — Aktive Annahmen (lebend)

**Stand:** 2026-07-02 · **Status:** reaktiviert als lebendes Top-Level-Register.
Historischer Stand (2026-03-24, A-001..A-006) bleibt unverändert in [`docs/archive/ASSUMPTIONS.md`](docs/archive/ASSUMPTIONS.md); die dort permanenten Invarianten (u. a. I-13 `actionable`=LLM-only, paper-only Execution) gelten fort.

**Pflege-Regel:** Jede Annahme trägt Status + Überwachungs-/Falsifikationspfad. Annahmen werden geschlossen (bestätigt/widerlegt), nie gelöscht.

---

### AS-1: Zugängliche Signal-Familien haben keinen handelbaren Edge
**Status:** bestätigt — **widerlegt-als-Edge** (Stand 2026-07-01)
**Evidenz:** canonical-edge n=68, P(mu_net>0)=10,44 %, median −93,8 bps (ohne Best-Trade P→3,50 %, nicht ausreißer-robust); Momentum n=178 signaled-dir netto negativ über alle Horizonte; Edge-Discovery 0/12 Survivors; Whale/Unlock/Funding-Familien ebenfalls null. Zitat NUR via `trading canonical-edge` (epochen-/quellen-sauber).
**Konsequenz:** keine neuen naiven Generatoren/Feeder (ADR 0012); Edge-Forschung nur prä-registriert, gegated, max. eine Wette zur Zeit.

### AS-2: Nachfrage für versiegelte Wahrheits-Dienste existiert
**Status:** **OFFEN / ungetestet**
Der zahlbare G0-`/oracle`-Pfad war bis nach dem Pivot gated-off und nie gelistet — die bisherigen 0 Payments sind daher **kein** Beleg gegen Nachfrage (Red-Team-Befund, ADR-0012-Addendum). Nachfrage ist unbewiesen, nicht widerlegt.
**Falsifikationspfad:** faire, prä-registrierte Demand-Probe mit Kill-Kriterien (ADR 0011); Risiko R2 im [`RISK_REGISTER.md`](RISK_REGISTER.md); Revisit-Trigger M3 im ADR-0012-Addendum.

### AS-3: Research-Arbeitsprofil ≠ Bot-Bau-Durchsatz
**Status:** aktiv
Der Truth-Platform-Wegpunkt verlangt Falsifikations-Qualität (prä-registriert, kosten-ehrlich, auditierbar, attestiert) statt Feature-Durchsatz. Priorisierung und Erfolgsmessung folgen dem Research-Profil: ein sauber attestiertes Verdikt zählt mehr als ein neues Feature.
**Überwachung:** Prä-Reg-Ledger + attestierte Verdikte als Arbeits-Output; Feature-Freeze der Alpha-Schichten (`ARCHITECTURE.md` § Messinstrument).

### AS-4: Paper-Fills approximieren reale Kosten gut genug fürs Messinstrument
**Status:** aktiv / überwachbar
Die netto-Verdikte der Falsifikations-Kette stehen auf dem Paper-Kostenmodell (Slippage + venue-spezifische Fees). Bricht diese Annahme, verlieren Netto-Urteile Beweiskraft.
**Überwachung:** Cost-Truth-Panel (bytes-verifiziert), Counterfactual-/Drift-Report (Live-vs-Replay), Fill-Semantik-Audits. Bei materieller Abweichung: zuerst Kostenmodell rekalibrieren, dann betroffene Verdikte re-evaluieren — nicht stillschweigend weiterzitieren.

---

## Verweise

- Risiken: [`RISK_REGISTER.md`](RISK_REGISTER.md)
- Wegpunkt-Entscheid + Addendum: [`docs/adr/0012-north-star-pivot-research-truth-platform.md`](docs/adr/0012-north-star-pivot-research-truth-platform.md)
- Identität/Zielbild: [`docs/KAI_IDENTITY.md`](docs/KAI_IDENTITY.md)
