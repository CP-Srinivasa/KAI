# RISK_REGISTER.md — Aktive Risiken (lebend)

**Stand:** 2026-07-02 · **Status:** reaktiviert als lebendes Top-Level-Register.
Historischer Phase-4/5-Stand (2026-03-24) bleibt unverändert in [`docs/archive/RISK_REGISTER.md`](docs/archive/RISK_REGISTER.md).

**Pflege-Regel:** Einträge werden bei Statuswechsel aktualisiert und geschlossen, nie gelöscht. Neue strukturelle Risiken bekommen die nächste freie `R`-Nummer. Schema: `risk_id · einstufung · beschreibung · auswirkung · gegenmassnahme · status`.

| risk_id | einstufung | beschreibung | auswirkung | gegenmassnahme | status |
|---|---|---|---|---|---|
| **R1** | hoch | **Bus-Faktor 1** — Wissen UND Governance liegen allein beim Operator; alle Gates (Live, Kapital, LN) binden nur ihn selbst, es gibt kein zweites Paar Augen | Ausfall oder Fehlentscheid einer einzelnen Person = Projekt-Stillstand bzw. ungeprüfter Gate-Flip | Selbstbindungs-Checkliste für Gate-Flips (dokumentierter Entscheid + Pre-Mortem VOR jedem Flip, kein Flip in derselben Session wie die Idee); Dokumentations-Disziplin (ADRs, Memory, DECISION_LOG) | offen |
| **R2** | hoch | **Nachfrage unbewiesen** — Kernrisiko des ADR-0012-Wegpunkts: zahlbare Wahrheits-Dienste wurden nie fair getestet (der G0-`/oracle`-Pfad war bis nach dem Pivot gated-off und ungelistet) | Truth-Plattform ohne externen Abnehmer bleibt Selbstzweck; Monetarisierungs-Gate kann nie auslösen | Faire Demand-Proben mit prä-registrierten Kill-Kriterien (ADR 0011); Revisit-Trigger M3 im ADR-0012-Addendum (2026-07-02) | offen |
| **R3** | mittel | **Ein-Maschinen-Betrieb** — Source of truth ist ein einzelner Pi 5; Warm-Standby bewusst verschoben | Hardware-Ausfall = Betriebsunterbrechung bis Restore (Stunden bis Tage), keine Datenverlust-Erwartung | 3-Schicht-Backup existiert (USB / KAI-mirror / OneDrive) inkl. Runtime-History; Warm-Standby bleibt gegated bewusst verschoben | akzeptiert (bewusst) |
| **R4** | mittel | **LN-Wert-Schicht armiert** — Zahlungs-Infrastruktur ist gebaut und auf dem Zielsystem vorhanden, nur durch Policy-Flags inert | Fehl-Flip oder Lücke könnte echte Sats bewegen | Restrisiko begrenzt durch Policy-Gate (pay disabled), HOTP-Kapital-Confirm, Reserve-Floor; Härtung läuft (LN value-layer hardening, siehe `SECURITY.md`) | in Arbeit |
| **R5** | mittel | **God-File-/Typ-Schulden-Zone im Zubringer-Pfad** (settings, bridge, engine — große gewachsene Module im Messinstrument-Pfad) | erhöhtes Regressions-/Wartungsrisiko genau dort, wo die Mess-Wahrheit entsteht | God-File-Ratchet in CI (Dateien dürfen nur schrumpfen), mypy-Gate, Feature-Freeze auf Alpha-Schichten (nur Pflege, siehe `ARCHITECTURE.md` § Messinstrument) | offen |
| **R6** | mittel | **Flag-Komplexität** — ~114 Bool-Flags; kombinatorische Zustände sind nicht vollständig prüfbar | Fehlkonfiguration kann Gates entwerten oder Messungen verfälschen | fail-closed-Defaults, mehrarmige Scharfschaltung (Triple-Flag + ACK-Sentinels), `docs/feature_flags.md`, Preflight + Post-Deploy-Smoke | offen |
| **R7** | niedrig | **Supply-Chain** — kompromittierte oder verwundbare Dependencies | Code-Ausführung im Truth-/Betriebs-Pfad | CI-mitigiert: `pip-audit` + `bandit` + Lock-File-Workflow (`docs/security/lock_file_workflow.md`); MAL-Advisories werden nie ignoriert | mitigiert |

## Verweise

- Sicherheits-Überblick: [`SECURITY.md`](SECURITY.md)
- Aktive Annahmen: [`ASSUMPTIONS.md`](ASSUMPTIONS.md)
- Wegpunkt + Alternativen/Revisit-Trigger: [`docs/adr/0012-north-star-pivot-research-truth-platform.md`](docs/adr/0012-north-star-pivot-research-truth-platform.md) (Addendum 2026-07-02)
