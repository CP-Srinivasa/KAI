"""Die Grenze mechanisch, nicht als Absicht (Operator-Schaerfung D-CORE-006).

Der Kurs des Operators lautet: die Produktschicht darf **kein zweiter
Reconciler** und **kein zweiter autoritativer Payment-State** werden. Als
Dokumentationssatz ist das ein Vorsatz; hier wird er ein Merge-Gate.

Vier Aussagen, alle per AST gegen den Quelltext — nicht per Textsuche, sonst
wuerde schon ein Kommentar den Test kippen oder ihn taeuschen:

1. ``app/pay`` importiert den schreibenden Zugang zum Geld-Journal nicht.
2. ``app/pay`` ruft nirgends ``journal.append`` oder ``journal.transaction``.
3. Es gibt genau EINEN Schreiber von ``receivable_settled`` im ganzen Repo, und
   der steht im Kern (``app/payments/receivables.py``).
4. Beide Takte — Reconcile-Timer und Produktschicht — laufen durch DIESEN
   einen Durchgang, und der Kern kennt die Produktschicht nicht.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PAY_DIR = REPO_ROOT / "app" / "pay"
APP_DIR = REPO_ROOT / "app"

#: Der schreibende Zugang zum Geld-Journal. Ein Import davon in ``app/pay``
#: waere der erste Schritt zu einem zweiten Schreiber.
FORBIDDEN_IMPORTS = frozenset({"PaymentJournal", "JournalTransaction", "record_invoice"})

SETTLED_EVENT_NAMES = frozenset({"SETTLED_EVENT", "receivable_settled"})


def _trees(directory: Path) -> dict[str, ast.Module]:
    return {
        path.relative_to(REPO_ROOT).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(directory.rglob("*.py"))
    }


def _imported_names(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            out.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            out.update(alias.asname or alias.name for alias in node.names)
    return out


def test_app_pay_importiert_den_schreibzugang_nicht() -> None:
    offenders = [
        f"{rel} -> {sorted(_imported_names(tree) & FORBIDDEN_IMPORTS)}"
        for rel, tree in _trees(PAY_DIR).items()
        if _imported_names(tree) & FORBIDDEN_IMPORTS
    ]
    assert not offenders, (
        "app/pay darf das Geld-Journal nicht direkt anfassen — der einzige Weg zu einem "
        f"receivable_settled fuehrt durch app/payments/receivables.py: {offenders}"
    )


def test_app_pay_ruft_nirgends_journal_append_oder_transaction() -> None:
    """Auch nicht ueber ein durchgereichtes Objekt."""
    offenders: list[str] = []
    for rel, tree in _trees(PAY_DIR).items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            target = ast.unparse(node.func.value).lower()
            if node.func.attr == "transaction" or (
                node.func.attr == "append" and "journal" in target
            ):
                offenders.append(f"{rel}:{node.lineno} -> {ast.unparse(node.func)}()")
    assert not offenders, offenders


def _writes_settled_event(tree: ast.Module) -> bool:
    """True, wenn dieses Modul einen ``receivable_settled``-Record ANHAENGT."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "append":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and arg.value in SETTLED_EVENT_NAMES:
                return True
            if isinstance(arg, ast.Name) and arg.id in SETTLED_EVENT_NAMES:
                return True
    return False


def test_genau_ein_modul_schreibt_receivable_settled() -> None:
    writers = sorted(rel for rel, tree in _trees(APP_DIR).items() if _writes_settled_event(tree))
    assert writers == ["app/payments/receivables.py"], (
        "Ein zweiter Schreiber von receivable_settled waere ein zweiter Reconciler "
        f"mit eigener Meinung: {writers}"
    )


def test_beide_takte_benutzen_denselben_durchgang() -> None:
    """Timer und Produktschicht rufen ``settle_receivable`` — nicht je einen eigenen."""
    calls: dict[str, list[str]] = {}
    for rel in ("app/payments/reconcile_passes.py", "app/pay/service.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        calls[rel] = [
            ast.unparse(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
    assert "settle_receivable" in calls["app/payments/reconcile_passes.py"]
    assert "settle_receivable" in calls["app/pay/service.py"]


def test_app_pay_befragt_den_rail_nicht_selbst() -> None:
    """Sonst entstuende neben dem Kern-Durchgang eine zweite Settlement-Logik."""
    offenders = [
        f"{rel}:{node.lineno}"
        for rel, tree in _trees(PAY_DIR).items()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"invoice_status", "lookup", "list_payments", "pay"}
    ]
    assert not offenders, offenders


def test_die_richtung_bleibt_pay_nach_payments() -> None:
    """``app/payments`` kennt ``app.pay`` nicht — der Kern bleibt versiegelt."""
    offenders = [
        f"{rel} -> {node.module}"
        for rel, tree in _trees(REPO_ROOT / "app" / "payments").items()
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("app.pay")
        and not (node.module or "").startswith("app.payments")
    ]
    assert not offenders, f"app/payments importiert die Produktschicht: {offenders}"
