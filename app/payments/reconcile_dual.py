"""Der Abgleich der beiden Geldjournale (ADR 0018 §8/§12).

Waehrend des Dual-Read fuehren zwei Dateien Buch ueber Wertbewegungen: der
Bestand in ``artifacts/ln_ops_ledger_v2.jsonl`` und der Control Plane in
``artifacts/payments/payment_journal.jsonl``. ADR §12 gibt dafuer sieben Tage;
der Reconciler hielt bisher jede Seite EINZELN gegen den Node, aber nie die
beiden gegeneinander — die bekannte Grenze aus dem Evidenzbericht §11.

**Was hier gesucht wird.** Ein ``payment_hash``, den beide Buecher fuehren und
den der Altpfad NICHT bewiesen abgeschlossen hat. Zwei Buchfuehrungen ueber
dasselbe Geld, von denen eine "vielleicht unterwegs" sagt und die andere
"erledigt", sind kein Schoenheitsfehler: solange sie nebeneinander stehen,
kann ein Betreiber den Altpfad in gutem Glauben erneut anstossen.

Warum der ``payment_hash``: er ist der einzige Schluessel, den beide Seiten
kennen, und er ist der, an dem der Rail selbst dedupliziert. Intent-IDs sind je
Journal ein eigenes Vokabular; sie ineinander umzurechnen hiesse, eine
Zuordnung zu erfinden.

**``error`` zaehlt als ungeklaert.** Im Altpfad ist er zwar terminal, aber
terminal OHNE Beweis (``ops_ledger`` m-14: *"an error outcome means we do not
know whether value moved"*). Genau dieser Zustand ist gefaehrlich, wenn zwei
Buecher ihn verschieden bewerten.

**Ein offener Alt-Vorgang ALLEIN ist kein Befund.** Er gehoert dem alten
Reconciler. Ihn hier zu melden hiesse, waehrend der ganzen Uebergangsphase
jede offene Alt-Zeile in den Payment-Alarm zu heben — und eine Wache, die immer
schlaegt, wird abgeschaltet.

**Nur lesen.** Keine Zeile dieses Moduls schreibt in das v2-Journal. Der Befund
landet als ``dual_journal_conflict`` im Payment-Journal und faellt damit in die
bestehende ``attention``-Kette (``_check_payment_reconciliation`` -> Telegram).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.payments.journal import PaymentJournal

#: Zustaende, die der Altpfad als abgeschlossen fuehrt. ``error`` steht
#: ausdruecklich NICHT dabei: er ist terminal ohne Beweis.
LEGACY_PROVEN_STATES = frozenset({"executed"})

#: Der Zustand, der einen Alt-Vorgang trotz Terminalitaet ungeklaert laesst.
LEGACY_UNPROVEN_STATE = "error"


def legacy_journal_path() -> Path:
    """Der Pfad des v2-Altjournals — aus dessen eigener Aufloesung.

    Der Import ist verzoegert, damit ``app.payments`` zur Importzeit nicht an
    ``app.lightning`` haengt (dieselbe Begruendung wie in
    :mod:`app.payments.rails.lightning`: der Paketzyklus ``payments ->
    lightning -> truth -> audit`` entstuende sonst wieder). Den Pfad hier
    nachzubauen waere die schlimmere Loesung — zwei Meinungen darueber, wo das
    alte Journal liegt.
    """
    from app.lightning.ops_ledger import ln_ops_v2_path

    return ln_ops_v2_path()


def dual_journal_pass(
    journal: PaymentJournal,
    *,
    counts: dict[str, int],
    now: datetime,
    legacy_path: Path | None = None,
) -> tuple[str, ...]:
    """Melde Zahlungen, die beide Journale fuehren und das alte nicht geklaert hat.

    Args:
        journal: das Payment-Journal. Es wird geschrieben — aber nur hier.
        counts: Zaehler des laufenden Reconcile-Laufs.
        now: Zeitstempel des Laufs.
        legacy_path: das v2-Journal. ``None`` nimmt den konfigurierten Pfad.

    Returns:
        Die gemeldeten ``payment_hash``-Werte, sortiert. Leer heisst: kein
        Doppelbefund — nicht "nicht geprueft".
    """
    path = legacy_path if legacy_path is not None else legacy_journal_path()
    unresolved = _unresolved_legacy_payments(path)
    if not unresolved:
        return ()

    known = _dedup_keys(journal)
    reported = journal.index.dual_conflict_keys()
    found = tuple(sorted(key for key in unresolved if key in known and key not in reported))
    for key in found:
        journal.append(
            f"dual_{key[:24]}",
            "dual_journal_conflict",
            {
                "status": "attention",
                "rail_dedup_key": key,
                "observed_status": unresolved[key],
                "evidence_source": "ln_ops_ledger_v2",
            },
            ts=now,
        )
        counts["DUAL_JOURNAL_CONFLICT"] = counts.get("DUAL_JOURNAL_CONFLICT", 0) + 1
    return found


# --------------------------------------------------------------------------- #
# Das alte Journal — ausschliesslich lesend
# --------------------------------------------------------------------------- #


def _unresolved_legacy_payments(path: Path) -> dict[str, str]:
    """``payment_hash`` -> letzter Zustand, fuer jeden ungeklaerten Alt-Vorgang.

    Die Kette des v2-Journals wird hier NICHT geprueft: dafuer gibt es
    ``verify_ln_ops_ledger`` im Altpfad, und der Payment-Reconciler darf nicht
    an der Form einer fremden Datei haengenbleiben. Eine unlesbare Zeile wird
    uebersprungen, damit sie den Abgleich der uebrigen nicht verhindert.
    """
    if not path.is_file():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:  # pragma: no cover - Rechte/IO; der Backup-Waechter meldet das
        return {}

    hashes: dict[str, str] = {}
    states: dict[str, list[str]] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        intent_id = str(record.get("intent_id", ""))
        if not intent_id:
            continue
        states.setdefault(intent_id, []).append(str(record.get("state", "")))
        payment_hash = _payment_hash_of(record)
        if payment_hash:
            hashes.setdefault(intent_id, payment_hash)

    out: dict[str, str] = {}
    for intent_id, seen in states.items():
        payment_hash = hashes.get(intent_id, "")
        if not payment_hash or any(state in LEGACY_PROVEN_STATES for state in seen):
            continue
        out[payment_hash] = seen[-1] if seen else LEGACY_UNPROVEN_STATE
    return out


def _payment_hash_of(record: dict[str, Any]) -> str:
    plan = record.get("plan")
    if not isinstance(plan, dict):
        return ""
    value = plan.get("payment_hash")
    return value.strip().lower() if isinstance(value, str) else ""


def _dedup_keys(journal: PaymentJournal) -> frozenset[str]:
    """Jeder Rail-Schluessel, den der Control Plane je abgeschickt hat.

    Auch die terminalen: die Frage lautet "fuehren BEIDE Buecher diese
    Zahlung?", und ein erledigter Vorgang zaehlt dabei genauso wie ein offener.
    """
    return frozenset(
        key
        for intent_id in journal.index.all_intents()
        if (key := journal.index.dedup_key(intent_id)) is not None
    )


__all__ = [
    "LEGACY_PROVEN_STATES",
    "LEGACY_UNPROVEN_STATE",
    "dual_journal_pass",
    "legacy_journal_path",
]
