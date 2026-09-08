"""KAI PAY v0.1 — die Produktschicht ueber der versiegelten Payment Fabric.

``app/payments`` ist versiegelt (D-CORE-005): es entscheidet ueber Geld und
wird nur noch gewartet. ``app/pay`` ist das Produkt darueber — anfordern,
bezahlen lassen, Eingang bestaetigen. Die Richtung ist ``pay -> payments`` und
niemals umgekehrt (``tests/unit/test_payment_dependency_direction.py``).

Keine Zeile hier trifft eine Aussage ueber Geld. Betrag, Zeitpunkt und Beweis
kommen aus dem Rail-Lookup und dem hash-verketteten Journal des Kerns;
gespeichert wird hier nur, was zur Anzeige und zur Zuordnung noetig ist.
"""

from __future__ import annotations

__all__: list[str] = []
