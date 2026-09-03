"""Import-Richtung des Payment Control Plane — mechanisch, nicht als Absicht.

ADR 0018 §2: ``payments -> lightning``, nie umgekehrt. Ohne diesen Test ist die
Richtung eine Zusage im Dokument; mit ihm ist sie ein Merge-Gate.

Zwei getrennte Aussagen, weil sie zwei verschiedene Defekte fangen:

1. **Richtung** (jeder Import, auch der verzoegerte): ``app.lightning`` darf
   ``app.payments`` nicht kennen. Ein Rail-Adapter, den sein eigener Rail
   importiert, ist kein Adapter mehr, sondern eine Schleife.
2. **Zyklusfreiheit auf Paketebene** (nur Top-Level-Importe): der Bestand hat
   heute den SCC ``audit -> lightning -> truth -> audit``
   (``app/audit/input_contract_rejections.py`` importierte
   ``app.lightning.input_contract_rejections``). Der Umzug nach
   ``app/payments/input_rejections.py`` dreht genau diese Kante.

Warum nur Top-Level fuer (2): ein Import im Funktionsrumpf existiert zur
Importzeit nicht und kann deshalb keinen Importzyklus ausloesen. Ein
``if TYPE_CHECKING:``-Import existiert zur Laufzeit ueberhaupt nicht.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: Die vier Pakete, deren Verflechtung ADR 0018 §2 aufloest.
WATCHED_PACKAGES = frozenset({"app.payments", "app.lightning", "app.audit", "app.truth"})

#: Ausnahmen der Richtungsregel — beide strukturell abgesichert, nicht behauptet:
#:
#: * ``rail``/``models``: reine Typvertraege. Sie stehen hier, damit ein bewusster
#:   Protocol-Import nicht heimlich zu einem Laufzeit-Import auswaechst.
#: * ``input_rejections``: der Nebenstrom, den ``ops_ledger`` schreibt, wenn es
#:   einen Plan ablehnt. Er ist ein BLATT (importiert nichts aus ``app.*``,
#:   siehe :func:`test_payments_input_rejections_is_a_leaf_module`) und kann
#:   deshalb keinen Paketzyklus schliessen — die Kante ``lightning -> payments``
#:   hat hier keine Rueckkante, ueber die sie zurueckfinden koennte.
PAYMENT_TYPE_ONLY_MODULES = frozenset(
    {"app.payments.rail", "app.payments.models", "app.payments.input_rejections"}
)


def _package_of(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "app":
        return ".".join(parts[:2])
    return None


def _is_type_checking_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _imported_modules(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Import):
            out.update(alias.name for alias in sub.names)
        elif isinstance(sub, ast.ImportFrom) and sub.level == 0 and sub.module:
            out.add(sub.module)
    return out


def _all_imports(tree: ast.Module) -> set[str]:
    """Jeder Import im Modul — Top-Level, verzoegert, TYPE_CHECKING."""
    return _imported_modules(tree)


def _toplevel_runtime_imports(tree: ast.Module) -> set[str]:
    """Nur Importe, die beim Modulimport wirklich ausgefuehrt werden."""
    out: set[str] = set()
    for node in tree.body:
        if _is_type_checking_guard(node):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.If, ast.Try, ast.With)):
            out |= _imported_modules(node)
    return out


def _python_files(package_dir: Path) -> list[Path]:
    return sorted(p for p in package_dir.rglob("*.py"))


def _package_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {pkg: set() for pkg in WATCHED_PACKAGES}
    for pkg in WATCHED_PACKAGES:
        pkg_dir = APP_ROOT / pkg.split(".", 1)[1]
        if not pkg_dir.is_dir():
            continue
        for path in _python_files(pkg_dir):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for imported in _toplevel_runtime_imports(tree):
                target = _package_of(imported)
                if target in WATCHED_PACKAGES and target != pkg:
                    graph[pkg].add(target)
    return graph


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[set[str]]:
    """Tarjan, iterativ — vier Knoten, aber ohne Rekursionsfallen."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[set[str]] = []
    counter = 0

    for root in sorted(graph):
        if root in index:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(graph.get(root, set())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, successors = work[-1]
            if successors:
                nxt = successors.pop(0)
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, sorted(graph.get(nxt, set()))))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: set[str] = set()
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.add(member)
                    if member == node:
                        break
                result.append(component)
    return result


def test_lightning_never_imports_payments() -> None:
    """Der Rail kennt den Control Plane nicht — auch nicht verzoegert."""
    offenders: list[str] = []
    for path in _python_files(APP_ROOT / "lightning"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for imported in _all_imports(tree):
            if not imported.startswith("app.payments"):
                continue
            if imported in PAYMENT_TYPE_ONLY_MODULES:
                continue
            offenders.append(f"{path.relative_to(APP_ROOT.parent).as_posix()} -> {imported}")
    assert not offenders, (
        "app/lightning importiert app.payments — ADR 0018 §2 verlangt die Gegenrichtung: "
        + ", ".join(sorted(offenders))
    )


def test_no_toplevel_cycle_between_payment_packages() -> None:
    """payments/lightning/audit/truth bilden keinen Import-SCC mehr."""
    graph = _package_graph()
    components = _strongly_connected_components(graph)
    cycles = [sorted(component) for component in components if len(component) > 1]
    edges = {key: sorted(value) for key, value in sorted(graph.items()) if value}
    assert not cycles, (
        f"Top-Level-Importzyklus zwischen den Payment-Paketen: {cycles} — Kanten: {edges}"
    )


def test_payments_input_rejections_is_a_leaf_module() -> None:
    """Die Ausnahme in :data:`PAYMENT_TYPE_ONLY_MODULES` traegt sich selbst.

    ``app.lightning`` darf dieses eine Modul importieren — aber nur, solange es
    seinerseits nichts aus ``app.*`` kennt. Sonst waere die Ausnahme ein Loch
    statt einer Grenze.
    """
    path = APP_ROOT / "payments" / "input_rejections.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    app_imports = sorted(i for i in _all_imports(tree) if i.startswith("app."))
    assert not app_imports, (
        "app/payments/input_rejections.py muss ein Blatt bleiben, sonst wird die "
        f"Ausnahme fuer app/lightning zu einem Zyklus: {app_imports}"
    )


def test_audit_reads_the_rejection_stream_from_payments() -> None:
    """Der Umzug ist vollzogen, nicht nur angekuendigt (ADR 0018 §2)."""
    source = (APP_ROOT / "audit" / "input_contract_rejections.py").read_text(encoding="utf-8")
    assert "app.payments.input_rejections" in source
    assert "app.lightning.input_contract_rejections" not in source


def test_legacy_rejection_module_is_a_re_export_only() -> None:
    """Der alte Pfad bleibt 7 Tage lesbar — aber ohne eigene Logik."""
    path = APP_ROOT / "lightning" / "input_contract_rejections.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    assert not defined, f"Re-Export-Schicht darf nichts definieren, fand: {defined}"
    assert "app.payments.input_rejections" in source
