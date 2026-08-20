"""Ein gepoolter Estimand, ein p-Wert — mit cluster-robustem Standardfehler.

Der Runner wertet heute **pro Symbol** aus: ``run_symbol_search`` ruft je Symbol
``search_hypotheses`` und erhaelt einen eigenen p-Wert. Ueber 34 Assets waeren
das 34 Tests derselben Hypothese — und die Forschungsfrage waere unbemerkt
verrutscht von

    "Hat diese eine Regel ueber das versiegelte Universum einen OOS-Edge?"

zu

    "Auf welchem der 34 Assets funktioniert sie?"

Das Zweite ist Discovery, nicht Konfirmation, und es bricht ``m = 1``. Dieses
Modul liefert stattdessen genau **einen** Schaetzwert und genau **einen**
p-Wert ueber alle Symbole hinweg; per-Symbol-Zahlen bleiben ``DIAGNOSTIC``.

**Der Estimand, vor T0 festgelegt:**

    mean net bps per valid signal, gepoolt ueber das versiegelte Universum

Bewusst *pro Signal* und nicht *pro Episode*: die Frage lautet, was ein
einzelnes Signal im Mittel wert ist. Ueberlappende Signale zu einer Episode
zusammenzufassen waere statistisch ebenfalls sauber, wuerde den Estimand aber
zu "Edge pro Signal-Episode" verschieben — eine andere Frage, und deshalb eine,
die man vorher entscheidet und nicht hinterher.

**Die Abhaengigkeit wird im Standardfehler behandelt, nicht im Estimand.**
``se = std / sqrt(n)`` unterstellt Unabhaengigkeit. Bei 34 korrelierten Assets
und ``horizon = 4`` ist das falsch: gleichzeitige Signale sind naeherungsweise
ein Marktimpuls, aufeinanderfolgende teilen Haltekerzen. Der Cluster-Sandwich
(Liang-Zeger, CR1) korrigiert genau das::

    V = G/(G-1) * (1/n^2) * sum_g ( sum_{i in g} (x_i - xbar) )^2
    t = xbar / sqrt(V)          df = G - 1

Freiheitsgrade zaehlen **Cluster**, nicht Beobachtungen — das ist der Punkt.
100 Signale in 12 Clustern tragen die Evidenz von ungefaehr 12, nicht 100.

Fail-closed wie ``app/research/stats.py``: nicht-endliche Werte, weniger als
zwei Cluster oder eine entartete Streuung liefern ``p = 1.0``. Ein
konfirmatorischer Test, der bei kaputtem Input Signifikanz behauptet, ist
schlimmer als keiner.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.analysis.student_t import student_t_sf


@dataclass(frozen=True)
class ClusterRobustSummary:
    """Der eine gatende Wert — plus alles, was seine Herkunft nachvollziehbar macht."""

    n: int
    n_clusters: int
    mean_bps: float
    se_bps: float  # cluster-robust
    naive_se_bps: float  # std/sqrt(n), nur zum Vergleich
    t_stat: float
    dof: int
    p_value: float  # einseitig, H0: mean <= 0

    @property
    def variance_inflation(self) -> float:
        """Wie stark der i.i.d.-Fehler den wahren unterschaetzt haette.

        1,0 = die Signale waren tatsaechlich unabhaengig. 4,0 = der naive
        Standardfehler war halb so gross wie der richtige.
        """
        if self.naive_se_bps <= 0.0:
            return 1.0
        return (self.se_bps / self.naive_se_bps) ** 2


def cluster_robust_mean(
    values: Sequence[float],
    cluster_ids: Sequence[int],
) -> ClusterRobustSummary:
    """Gepoolter Mittelwert mit cluster-robustem einseitigem Test auf ``mean > 0``.

    Args:
        values: net-bps je Signal, gepoolt ueber das gesamte Universum.
        cluster_ids: Cluster-Zugehoerigkeit je Signal, gleiche Laenge und
            Reihenfolge (siehe ``signal_clusters.assign_clusters``).

    Returns:
        ClusterRobustSummary. ``p_value = 1.0``, wenn kein Test moeglich ist.

    Raises:
        ValueError: Laengen stimmen nicht ueberein.
    """
    if len(values) != len(cluster_ids):
        raise ValueError("values and cluster_ids must have equal length")

    n = len(values)
    if n == 0:
        return ClusterRobustSummary(0, 0, 0.0, 0.0, 0.0, 0.0, 0, 1.0)

    if not all(math.isfinite(x) for x in values):
        # Wie in stats.summarize_net_bps: ein verunreinigtes Sample darf nie
        # ueber inf->mean>0 oder NaN->p=0 einen Edge vortaeuschen.
        return ClusterRobustSummary(n, 0, 0.0, 0.0, 0.0, 0.0, 0, 1.0)

    mean = sum(values) / n

    groups: dict[int, float] = {}
    for value, cluster in zip(values, cluster_ids, strict=True):
        groups[cluster] = groups.get(cluster, 0.0) + (value - mean)

    n_clusters = len(groups)

    naive_se = 0.0
    if n >= 2:
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        naive_se = math.sqrt(variance) / math.sqrt(n)

    if n_clusters < 2:
        # Ein einziger Cluster heisst: alle Signale haengen zusammen. Daraus
        # laesst sich keine Streuung zwischen unabhaengigen Einheiten schaetzen.
        return ClusterRobustSummary(n, n_clusters, mean, 0.0, naive_se, 0.0, 0, 1.0)

    correction = n_clusters / (n_clusters - 1)
    meat = sum(residual_sum**2 for residual_sum in groups.values())
    var_mean = correction * meat / (n * n)
    se = math.sqrt(var_mean)

    if se <= 0.0:
        # Entartet: jeder Cluster hat exakt den Mittelwert. Signifikant genau
        # dann, wenn der Mittelwert strikt positiv ist.
        return ClusterRobustSummary(
            n,
            n_clusters,
            mean,
            0.0,
            naive_se,
            math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0),
            n_clusters - 1,
            0.0 if mean > 0 else 1.0,
        )

    t_stat = mean / se
    dof = n_clusters - 1
    p_value = min(1.0, max(0.0, student_t_sf(t_stat, float(dof))))
    return ClusterRobustSummary(n, n_clusters, mean, se, naive_se, t_stat, dof, p_value)
