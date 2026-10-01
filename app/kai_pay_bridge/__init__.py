"""KAI -> KAI-Pay: signierte Zahlungsvorschlaege (ADR 0021 E5/I5, D-297).

KAI erzeugt hier nur Vorschlaege (``kaiprop1``) fuer die selbstverwahrte Wallet KAI-Pay
(Strang B). Die Wallet prueft Signatur, Laufzeit, Wiederholung, erlaubte Empfaenger und Budget
lokal; bezahlt wird nur nach Bestaetigung durch den Nutzer in der Wallet. Dieses Paket sendet
kein Geld, haelt keinen Zahlungszustand und importiert nichts aus ``app.payments``, ``app.pay``
oder ``app.lightning`` (ADR 0021 I3; ein Test haelt das fest).
"""
