"""Payment Control Plane (ADR 0018).

Eine Kette fuer jede Wertbewegung: ``PaymentIntent -> Policy -> Authorization ->
Rail-Execution -> Settlement -> Reconciliation -> Proof``. Dieses Paket besitzt
das Domaenenmodell; Rails (Lightning zuerst) sind Adapter darunter und kennen
``app.payments`` nicht — die Richtung wird in
``tests/unit/test_payment_dependency_direction.py`` mechanisch erzwungen.

Bewusst ohne Re-Exports: ein Paket-``__init__``, das Domaenentypen
weiterreicht, zieht beim Import des kleinsten Untermoduls den ganzen Baum nach
und macht genau die Zyklen wieder moeglich, die ADR 0018 §2 aufloest.
"""

from __future__ import annotations
