"""Fixture built from the real PETIT BAO EM 'Rapport Jour New' tab (25/08/2026).

Columns: A B C=label D=MIDI E=W-1 F=SOIR G=W-1 H=TOTAL I=WTD J=WTD-1 K=delta L=%
"""

W = 13


def row(label="", midi="", w1m="", soir="", w1s="", total="", wtd="", wtdp="",
        delta="", pct=""):
    r = ["", "", label, midi, w1m, soir, w1s, total, wtd, wtdp, delta, pct, ""]
    return r + [""] * (W - len(r))


GRID = [
    row("25/08/2026"),
    ["", "", "", "RAPPORT JOURNALIER PETIT BAO EM"] + [""] * 9,
    row("mardi 25 août 2026"),
    row("", "MIDI", "W-1", "SOIR", "W-1", "TOTAL", "Current Week-to-Date (WTD)"),
    row("CA TTC", "1 371,00 €", "-23,73%", "3 215,40 €", "11,76%", "4 586,40 €",
        "9 474,45 €", "8 663,40 €", "811,05 €", "9,36%"),
    row("CA HT", "1 246,00 €", "-23,64%", "2 914,13 €", "12,13%", "4 160,13 €",
        "8 593,16 €", "7 841,75 €", "751,41 €", "9,58%"),
    row("CA HT ON SITE", "987,82 €", "-32,21%", "2 426,95 €", "3,34%", "718,18 €",
        "7 286,84 €", "6 972,30 €", "314,55 €", "4,51%"),
    row("CA HT TAKE AWAY", "258,18 €", "47,92%", "460,00 €", "83,67%", "718,18 €",
        "1 211,95 €", "822,27 €", "389,68 €", "47,39%"),
    row("CA HT DELIVERY", "0,00 €", "", "27,18 €", "", "27,18 €"),
    row("COUVERTS ON SITE", "48", "-27,27%", "108", "12,50%", "156", "328", "308"),
    row("NOMBRE TAKE AWAY", "17", "41,67%", "17", "41,67%", "34", "58", "42"),
    row("NOMBRE LIVRAISON", "0", "", "1", "", "1", "2", "1"),
    row("TM HT ON SITE", "20,58 €", "", "22,47 €"),
    row("PANIER OUTSIDE", "15,19 €", "", "27,07 €"),
    row("STAFF", "MIDI", "MIDI", "SOIR", "SOIR"),
    row("MANAGER:", "Jeremy", "Jeremy", "Jeremy & capucine", "Jeremy & capucine"),
    row("PASS MASTER:", "Dorjee", "Dorjee", "Dorjee", "Dorjee"),
    row("STAFF:", "Capucine", "Capucine", "Daphnée - Capucine", "Daphnée - Capucine"),
    row("EVENEMENT / METEO", "Soleil", "Soleil", "Soleil", "Soleil"),
    row("BRIEFING TOPICS", "Step de service", "", "Step de service"),
    row("TOP 3", "", "", "Reprise d'activité", "Important"),
    row("TOP 3", "", "", "Pas mal de groupes", "Important"),
    row("TOP 3", "", "", "", ""),
    row("GENERAL",
        "Service à 2 en postes flottants, service assez calme. Aucun problème.",
        "",
        "Service assez mouvementé, bcp de groupes. Bonne coordination."),
    row("FOH", "RAS", "", "Daphnée : BAR / Jerem & capucine : ACCUEIL"),
    row("BOH", "RAS", "",
        "Une commande de shanghai noodle était assez longue : 2 tickets imprimés."),
    row("GLITCH", "RAS", "", ""),
    row("COMMENTAIRES", "DOOKA : 10  5*: 10  Pas de commentaires", "",
        "DOOKA : 26  4*: 2  5*: 24  Excellent restaurant, coup de cœur."),
    row("RECEPTION DE MARCHANDISES - OK", "", "", ""),
    row("RECEPTION DE MARCHANDISES - BAD", "", "", ""),
    row("RECEPTION DE MARCHANDISES -  IF BAD, WHY", "", "", ""),
    row("QUALITE FOOD ", "RAS", "", "RAS"),
    row("QUALITE FOOD - IF QAS", "", "", ""),
    row("#RESA", "", "", "//"),
    row("#WALKOUTS", "", "", ""),
    row("BESOIN", "", "", ""),
    row("RUPTURES FOOD & DRINKS", "", "", ""),
    row("REMISE", " - ", "", " - 0"),
    row("PERTE", " - ", "", ""),
    row("ECART DE CAISSE", "0,00 €", "", "0,00 €"),
]
