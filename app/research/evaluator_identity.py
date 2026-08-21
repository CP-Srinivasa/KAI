"""Beweisen, dass der laufende Code der versiegelte ist — nicht nur behaupten.

``activation.evaluator_sha256`` war bisher ein wohlgeformter 64-Hex-String und
sonst nichts. Die Auswertung lief anschliessend mit der gerade importierten
Implementierung. Das ist derselbe Fehler wie „Code auf Platte != Code im
Prozess", nur eine Ebene hoeher: das Artefakt behauptet eine Identitaet, die
niemand nachrechnet.

Zwei Bindungen, beide fail-closed vor jeder Performance-Rechnung::

    git HEAD                 == activation.research_code_sha
    evaluator bundle SHA256  == activation.evaluator_sha256

**Was im Bundle steckt und warum genau das.** Die Auswertungsmodule sind klein
und ausschliesslich fuer diesen Zweck da — ihre Bytes gehoeren vollstaendig
hinein. ``runner.py`` dagegen ist gross und aendert sich aus Gruenden, die den
Primaertest nichts angehen; es vollstaendig zu hashen wuerde den Seal bei jeder
unbeteiligten Aenderung brechen. Vom Runner geht deshalb nur der **Quelltext des
versiegelten Deciders** ein — genau die Regel, um die es geht, und sonst nichts.

Der Bundle-Hash ist damit praezise: er aendert sich, wenn sich die Regel oder die
Auswertung aendert, und nicht, wenn jemand anderswo eine Zeile Kommentar
verschiebt.
"""

from __future__ import annotations

import hashlib
import inspect
import subprocess
from collections.abc import Callable
from pathlib import Path

BUNDLE_SPEC_VERSION = "kai/evaluator-bundle/v1"

# Die Module, die das Verdikt tatsaechlich erzeugen. Vollstaendig gehasht.
EVALUATOR_BUNDLE_MODULES: tuple[str, ...] = (
    "app/analysis/student_t.py",
    "app/research/frozen_dataset.py",
    "app/research/frozen_input.py",
    "app/research/pooled_inference.py",
    "app/research/prereg_candidate.py",
    "app/research/prereg_evaluation.py",
    "app/research/prereg_window.py",
    "app/research/prereg_window_state.py",
    "app/research/primary_confirmatory.py",
    "app/research/samples.py",
    "app/research/signal_clusters.py",
)


class EvaluatorIdentityError(RuntimeError):
    """Der laufende Code ist nicht der versiegelte — Abbruch vor der Auswertung."""


def evaluator_bundle_sha256(repo_root: Path, *, decider_name: str) -> str:
    """Hash ueber die Auswertungsmodule PLUS den Quelltext des Deciders.

    Args:
        repo_root: Wurzel des Checkouts.
        decider_name: der versiegelte Hypothesenname.

    Raises:
        EvaluatorIdentityError: ein Modul fehlt oder der Decider ist unbekannt.
    """
    from app.research.prereg_evaluation import resolve_decider

    parts: list[bytes] = [BUNDLE_SPEC_VERSION.encode("utf-8")]
    for relative in EVALUATOR_BUNDLE_MODULES:
        path = repo_root / relative
        if not path.is_file():
            raise EvaluatorIdentityError(f"Evaluator-Modul fehlt: {relative}")
        # Zeilenenden normalisieren: derselbe Code darf auf Windows und Linux
        # nicht zwei verschiedene Identitaeten haben.
        body = path.read_bytes().replace(b"\r\n", b"\n")
        parts.append(relative.encode("utf-8"))
        parts.append(hashlib.sha256(body).hexdigest().encode("utf-8"))

    source = inspect.getsource(resolve_decider(decider_name)).replace("\r\n", "\n")
    parts.append(f"decider:{decider_name}".encode())
    parts.append(hashlib.sha256(source.encode("utf-8")).hexdigest().encode("utf-8"))

    return hashlib.sha256(b"\n".join(parts)).hexdigest()


def git_head(repo_root: Path) -> str:
    """Der Commit, mit dem tatsaechlich gearbeitet wird."""
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - git fehlt
        raise EvaluatorIdentityError(f"git nicht aufrufbar: {exc}") from exc
    if completed.returncode != 0:
        raise EvaluatorIdentityError(f"git rev-parse HEAD scheiterte: {completed.stderr.strip()}")
    return completed.stdout.strip()


def assert_runtime_matches(
    *,
    repo_root: Path,
    research_code_sha: str,
    evaluator_sha256: str,
    decider_name: str,
    head_provider: Callable[[Path], str] = git_head,
) -> None:
    """Beide Bindungen pruefen. Abweichung = Abbruch, nicht Warnung.

    Ein Verdikt, das unter anderem Code entstanden ist als versiegelt, ist kein
    schwaecheres Verdikt — es ist ein anderes Experiment.
    """
    head = head_provider(repo_root)
    if head != research_code_sha:
        raise EvaluatorIdentityError(
            f"git HEAD ist {head[:12]}…, versiegelt wurde {research_code_sha[:12]}… — "
            "der laufende Code ist nicht der praeregistrierte."
        )
    bundle = evaluator_bundle_sha256(repo_root, decider_name=decider_name)
    if bundle != evaluator_sha256:
        raise EvaluatorIdentityError(
            f"Evaluator-Bundle ist {bundle[:12]}…, versiegelt wurde "
            f"{evaluator_sha256[:12]}… — die Auswertung wurde seit T0 veraendert."
        )
