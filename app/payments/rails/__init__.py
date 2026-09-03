"""Rail-Adapter (ADR 0018 §7).

Jeder Adapter uebersetzt genau EIN fremdes Protokoll in das Domaenenmodell und
kennt vom Control Plane nur :mod:`app.payments.rail`. Bewusst ohne
Re-Exports: ein ``__init__``, das alle Rails laedt, zwingt jeden Import des
kleinsten Adapters dazu, auch die Abhaengigkeiten aller anderen zu ziehen.
"""

from __future__ import annotations
