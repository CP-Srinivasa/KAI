#!/usr/bin/env python3
"""Verifiziere Attestierungen mit dem Code, der sie GESIEGELT hat.

**Warum das Skript existiert.** ``verify_canonical_edge_seq`` rechnet mit dem
Code von *heute* nach. Am 2026-09-01 verifizierten deshalb nur **17 von 64**
canonical-edge-Zeilen — die uebrigen 47 meldeten ``hash_mismatch`` bei
intakten Eingaben-Pins. Das liess zwei Deutungen zu, die nicht weiter
auseinanderliegen koennen: gebrochene Siegel, oder ein Pruefwerkzeug, das
aeltere Zeilen nicht reproduzieren kann.

Mit diesem Skript wurde die Frage entschieden: **47 von 47 verifizieren mit
ihrem eigenen Commit, kein Siegel ist gebrochen.** Der Befund gehoert nicht in
eine einmalige Shell-Sitzung, sondern in ein Werkzeug — sonst muss ihn beim
naechsten Mal jemand neu erarbeiten.

**Die Falle, die dabei zuschnappte, ist hier eingebaut:** ein Pruefskript, das
NICHT im geprueften Baum liegt, prueft etwas anderes, als es behauptet. Python
setzt ``sys.path[0]`` auf das **Skriptverzeichnis**; liegt dort kein ``app/``,
gewinnt die editierbare Installation aus einem beliebigen anderen Checkout.
Genau das liess drei Gruppen faelschlich als Fehlschlag erscheinen. Deshalb
prueft ``main`` beim Start, aus welchem Baum ``app`` tatsaechlich kommt, und
bricht ab, statt ein falsches Ergebnis zu liefern.

Aufruf (IM Worktree des gesiegelten Commits):

    git worktree add --detach /tmp/seal <commit>
    cp scripts/verify_attestation_with_sealed_code.py /tmp/seal/
    cd /tmp/seal && python verify_attestation_with_sealed_code.py \
        --commit <commit> --ledger <pfad> --root <artefakt-wurzel>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _module_tree(module_file: str) -> Path:
    """Wurzel des Baums, aus dem ``app`` geladen wurde.

    ``<root>/app/observability/edge_attestation.py`` -> ``<root>``: das sind
    DREI Ebenen hinauf, also ``parents[2]``. Der erste Wurf griff eine zu weit
    (``parents[3]``); das Werkzeug haette damit immer abgebrochen und nie
    gemessen — gefunden vom Test, nicht vom Auge.
    """
    return Path(module_file).resolve().parents[2]


def sealed_seqs(ledger: Path, commit_prefix: str, kind: str) -> list[int]:
    """Alle Ledger-Sequenzen, die unter ``commit_prefix`` gesiegelt wurden."""
    out: list[int] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("kind") != kind:
            continue
        code = record.get("payload", {}).get("code") or {}
        if str(code.get("commit", "")).startswith(commit_prefix):
            seq = record.get("seq")
            if isinstance(seq, int):
                out.append(seq)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attestierungen mit ihrem eigenen Code pruefen")
    parser.add_argument("--commit", required=True, help="Praefix des gesiegelten Commits")
    parser.add_argument("--ledger", required=True, help="Pfad zum attestation_ledger.jsonl")
    parser.add_argument("--root", required=True, help="Wurzel, gegen die die Pins aufloesen")
    parser.add_argument("--json", action="store_true", help="maschinenlesbare Ausgabe")
    args = parser.parse_args(argv)

    from app.observability import edge_attestation as ea

    tree = _module_tree(ea.__file__)
    if tree.resolve() != Path.cwd().resolve():
        print(
            "ABBRUCH: 'app' wurde aus einem ANDEREN Baum geladen als dem "
            f"aktuellen Verzeichnis.\n  geladen aus: {tree}\n  cwd:         {Path.cwd()}\n"
            "Ein Pruefskript ausserhalb des geprueften Baums prueft den falschen Code "
            "(sys.path[0] ist das Skriptverzeichnis). Skript IN den Worktree kopieren.",
            file=sys.stderr,
        )
        return 2

    ledger = Path(args.ledger)
    seqs = sealed_seqs(ledger, args.commit, ea.CANONICAL_EDGE_KIND)
    results = [
        (seq, ea.verify_canonical_edge_seq(seq, ledger_path=ledger, root=Path(args.root)))
        for seq in seqs
    ]
    ok = [seq for seq, r in results if r.ok]
    bad = [(seq, r.reason) for seq, r in results if not r.ok]

    if args.json:
        print(
            json.dumps(
                {
                    "commit": args.commit,
                    "code_tree": str(tree),
                    "sealed": len(seqs),
                    "verified": len(ok),
                    "failures": [{"seq": s, "reason": why} for s, why in bad],
                },
                indent=2,
            )
        )
    else:
        print(f"{args.commit}: {len(ok)}/{len(seqs)} verifizieren mit dem gesiegelten Code")
        for seq, why in bad:
            print(f"  FAIL seq={seq} reason={why}")
    return 0 if not bad else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
