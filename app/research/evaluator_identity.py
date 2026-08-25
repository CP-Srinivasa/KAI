"""Beweisen, dass der laufende Code der versiegelte ist — nicht nur behaupten.

``activation.evaluator_sha256`` war anfangs ein wohlgeformter 64-Hex-String und
sonst nichts; die Auswertung lief mit der gerade importierten Implementierung.
Das ist derselbe Fehler wie "Code auf Platte != Code im Prozess", nur eine Ebene
hoeher: das Artefakt behauptet eine Identitaet, die niemand nachrechnet.

Zwei Bindungen, beide fail-closed vor jeder Performance-Rechnung::

    git HEAD                 == activation.research_code_sha
    evaluator bundle SHA256  == activation.evaluator_sha256

**Warum der Bundle-Hash die eigentliche Verteidigung ist.** ``git rev-parse
HEAD`` sieht uncommittete Aenderungen im Arbeitsbaum ueberhaupt nicht. Wer eine
Datei editiert und nicht committet, passiert die HEAD-Pruefung mit wehenden
Fahnen. Nur der Bundle-Hash liest Bytes.

**Was im Bundle steckt — und warum nicht weniger.** Am 2026-08-25 hashte eine
fruehere Fassung aus ``runner.py`` nur den Quelltext des Deciders
(``inspect.getsource``). Gemessen::

    RSI_REENTRY_LOW = 30.0 -> 15.0    Bundle-Hash unveraendert  !!
    VOLUME_SPIKE_Z  = 2.0  -> 1.0     Bundle-Hash unveraendert  !!

Beide Konstanten definieren die Regel, werden aber zur Auswertungszeit aus
Modulen gelesen, die nicht im Bundle lagen — und ``getsource`` einer Funktion
enthaelt die Konstanten nicht, die sie liest. Die Regel liegt deshalb jetzt in
``app/research/sealed_hypothesis.py``, allein in ihrer eigenen Datei, und jede
beteiligte Datei geht VOLLSTAENDIG ein.

Die Auswahl folgt einer Frage: *koennte eine Aenderung dieser Datei aus DEMSELBEN
eingefrorenen Datenschnitt ein anderes Verdikt erzeugen?* Deshalb sind
``forward_returns`` und ``rsi`` NICHT dabei — sie erzeugen den Datenschnitt,
laufen bei der Auswertung aber nicht mehr; ihr Ergebnis ist ueber
``dataset_sha256`` gebunden. ``volume_z`` dagegen ist dabei, weil die Regel
``VOLUME_SPIKE_Z`` zur Laufzeit liest.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from pathlib import Path

BUNDLE_SPEC_VERSION = "kai/evaluator-bundle/v1"

# Die Module, die das Verdikt tatsaechlich erzeugen. Vollstaendig gehasht.
EVALUATOR_BUNDLE_MODULES: tuple[str, ...] = (
    # Die Regel selbst — eigene Datei, damit sie vollstaendig hashbar ist.
    "app/research/sealed_hypothesis.py",
    # Die Konstante, die sie liest. Ohne diese Zeile bliebe VOLUME_SPIKE_Z
    # aenderbar, ohne den Seal zu brechen (gemessen 2026-08-25).
    "app/analysis/indicators/volume_z.py",
    # Der FeatureRow-Vertrag: panels_from_frozen rekonstruiert daraus die
    # Zeilen des Artefakts; ein geaendertes Feldset aendert die Auswertung.
    "app/analysis/features/feature_matrix.py",
    # Die Auswertungskette vom Artefakt zum Verdikt.
    "app/analysis/student_t.py",
    "app/research/frozen_dataset.py",
    "app/research/frozen_input.py",
    "app/research/pooled_inference.py",
    "app/research/prereg_candidate.py",
    "app/research/prereg_evaluation.py",
    "app/research/prereg_storage.py",
    "app/research/prereg_window.py",
    "app/research/prereg_window_state.py",
    "app/research/primary_confirmatory.py",
    "app/research/samples.py",
    "app/research/signal_clusters.py",
    # Die Kerzenlaenge. ``interval_to_ms`` uebersetzt den versiegelten
    # Timeframe in Millisekunden und bestimmt damit Cluster-Grenzen und
    # Haltefenster — aus DEMSELBEN Artefakt kaeme mit anderen Millisekunden
    # ein anderes Verdikt.
    "app/market_data/kline_windows.py",
    # Der Lock, unter dem publiziert und journalisiert wird.
    "app/research/exclusive_lock.py",
    # Der Waechter selbst. Schuetzt nicht gegen einen entschlossenen Angreifer,
    # macht aber eine versehentliche Aufweichung sichtbar.
    "app/research/evaluator_identity.py",
)


class EvaluatorIdentityError(RuntimeError):
    """Der laufende Code ist nicht der versiegelte — Abbruch vor der Auswertung."""


def evaluator_bundle_sha256(repo_root: Path, *, decider_name: str) -> str:
    """Hash ueber die VOLLSTAENDIGEN Bytes jeder beteiligten Datei.

    Args:
        repo_root: Wurzel des Checkouts.
        decider_name: der versiegelte Hypothesenname. Er geht in den Hash ein,
            damit ein Wechsel der gewerteten Regel den Seal bricht — auch wenn
            beide Regeln in derselben Datei staenden.

    Raises:
        EvaluatorIdentityError: ein Modul fehlt.
    """
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

    parts.append(f"decider:{decider_name}".encode())
    return hashlib.sha256(b"\n".join(parts)).hexdigest()


def assert_worktree_clean(repo_root: Path) -> None:
    """Der Checkout darf keine unversionierten Aenderungen an Code tragen.

    ``git rev-parse HEAD`` sieht uncommittete Aenderungen NICHT. Ohne diese
    Pruefung waere ``research_code_sha == HEAD`` eine Aussage ueber die
    Historie, nicht ueber die Bytes, die gerade laufen — und der
    Producer-Code (Features, Labels, Universum), der bewusst NICHT im
    Evaluator-Bundle liegt, waere ueberhaupt nicht gebunden.

    Geprueft werden ausschliesslich TRACKED Dateien: unversionierte Artefakte
    unter ``artifacts/`` entstehen im Normalbetrieb und sind kein Befund.
    """
    completed = _git(repo_root, "status", "--porcelain", "--untracked-files=no")
    dirty = [line for line in completed.splitlines() if line.strip()]
    if dirty:
        raise EvaluatorIdentityError(
            "der Checkout traegt unversionierte Aenderungen — research_code_sha "
            "waere dann eine Aussage ueber die Historie, nicht ueber die "
            "laufenden Bytes: " + "; ".join(dirty[:10])
        )


def assert_modules_load_from(repo_root: Path) -> None:
    """Die IMPORTIERTEN Module muessen aus genau diesem Checkout stammen.

    ``evaluator_bundle_sha256`` liest Dateien unter ``repo_root``; die
    Auswertung laeuft mit bereits importierten Modulen. Ein Prozess koennte
    Module aus Checkout A geladen haben und ``repo_root`` auf den sauberen
    Checkout B zeigen lassen — dann wird B gehasht und A ausgefuehrt.
    """
    import importlib

    for relative in EVALUATOR_BUNDLE_MODULES:
        module_name = relative.removesuffix(".py").replace("/", ".")
        module = importlib.import_module(module_name)
        origin = getattr(module, "__file__", None)
        if origin is None:  # pragma: no cover - reine Namespace-Pakete
            raise EvaluatorIdentityError(f"{module_name} hat keinen Dateipfad")
        loaded = Path(origin).resolve()
        expected = (repo_root / relative).resolve()
        if loaded != expected:
            raise EvaluatorIdentityError(
                f"{module_name} ist aus {loaded} geladen, gehasht wird {expected} — "
                "es wuerde anderer Code laufen als geprueft."
            )


def _git(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo_root), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - git fehlt
        raise EvaluatorIdentityError(f"git nicht aufrufbar: {exc}") from exc
    if completed.returncode != 0:
        raise EvaluatorIdentityError(f"git {' '.join(args)} scheiterte: {completed.stderr.strip()}")
    return completed.stdout


def git_head(repo_root: Path) -> str:
    """Der Commit, mit dem tatsaechlich gearbeitet wird."""
    return _git(repo_root, "rev-parse", "HEAD").strip()


def assert_runtime_matches(
    *,
    repo_root: Path,
    research_code_sha: str,
    evaluator_sha256: str,
    decider_name: str,
    head_provider: Callable[[Path], str] = git_head,
    worktree_check: Callable[[Path], None] = assert_worktree_clean,
    module_check: Callable[[Path], None] = assert_modules_load_from,
) -> None:
    """Beide Bindungen pruefen. Abweichung = Abbruch, nicht Warnung.

    Vier Bindungen, alle fail-closed:

    * der Checkout traegt keine unversionierten Aenderungen
    * die importierten Module stammen aus genau diesem Checkout
    * ``git HEAD`` ist der versiegelte Commit
    * das Evaluator-Bundle hat den versiegelten Hash

    Ein Verdikt, das unter anderem Code entstanden ist als versiegelt, ist kein
    schwaecheres Verdikt — es ist ein anderes Experiment.
    """
    # Reihenfolge ist Absicht: erst die billigen, umfassenden Pruefungen, dann
    # die teuren. Die beiden Rueckrufe existieren, damit Tests JEDE Bindung
    # einzeln pruefen koennen — in Produktion bleiben die Vorgaben.
    worktree_check(repo_root)
    module_check(repo_root)

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
