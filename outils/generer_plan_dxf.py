"""Génère le plan RDC de démonstration en DXF R12 (compatible avec la plupart des logiciels).
Même bâtiment que maquette_demo.ifc, dessiné en 2D, unités : millimètres."""
import math
import sys

L = []


def p(code, val):
    L.append("%3d" % code if code < 1000 else str(code))
    L.append(str(val))


def entete():
    p(0, "SECTION"); p(2, "HEADER")
    p(9, "$ACADVER"); p(1, "AC1009")
    p(9, "$DWGCODEPAGE"); p(3, "ANSI_1252")
    p(9, "$EXTMIN"); p(10, -3000.0); p(20, -3000.0); p(30, 0.0)
    p(9, "$EXTMAX"); p(10, 23000.0); p(20, 15000.0); p(30, 0.0)
    p(0, "ENDSEC")


CALQUES = [("0", 7), ("A-MUR-BA20", 1), ("A-DOUBLAGE", 30), ("A-CLOISON-72", 3), ("A-CLOISON-98-ACOU", 4),
           ("A-CLOISON-72-HYDRO", 5), ("A-POTEAU", 1), ("A-LOCAUX", 8), ("A-PORTE", 6), ("A-FENETRE", 140),
           ("A-MOBILIER", 9), ("A-SANITAIRE", 150), ("A-COTATION", 2), ("A-TEXTE", 7)]


def tables():
    p(0, "SECTION"); p(2, "TABLES")
    p(0, "TABLE"); p(2, "LTYPE"); p(70, 1)
    p(0, "LTYPE"); p(2, "CONTINUOUS"); p(70, 0); p(3, "Solid line"); p(72, 65); p(73, 0); p(40, 0.0)
    p(0, "ENDTAB")
    p(0, "TABLE"); p(2, "LAYER"); p(70, len(CALQUES))
    for nom, couleur in CALQUES:
        p(0, "LAYER"); p(2, nom); p(70, 0); p(62, couleur); p(6, "CONTINUOUS")
    p(0, "ENDTAB")
    p(0, "TABLE"); p(2, "STYLE"); p(70, 1)
    p(0, "STYLE"); p(2, "STANDARD"); p(70, 0); p(40, 0.0); p(41, 1.0); p(50, 0.0); p(71, 0); p(42, 250.0); p(3, "txt"); p(4, "")
    p(0, "ENDTAB")
    p(0, "ENDSEC")


def ligne(cal, x1, y1, x2, y2):
    p(0, "LINE"); p(8, cal); p(10, float(x1)); p(20, float(y1)); p(30, 0.0); p(11, float(x2)); p(21, float(y2)); p(31, 0.0)


def arc(cal, cx, cy, r, a0, a1):
    p(0, "ARC"); p(8, cal); p(10, float(cx)); p(20, float(cy)); p(30, 0.0); p(40, float(r)); p(50, float(a0)); p(51, float(a1))


def polyligne(cal, pts, fermee=True):
    p(0, "POLYLINE"); p(8, cal); p(66, 1); p(10, 0.0); p(20, 0.0); p(30, 0.0); p(70, 1 if fermee else 0)
    for x, y in pts:
        p(0, "VERTEX"); p(8, cal); p(10, float(x)); p(20, float(y)); p(30, 0.0)
    p(0, "SEQEND"); p(8, cal)


def rect(cal, x1, y1, x2, y2):
    polyligne(cal, [(x1, y1), (x2, y1), (x2, y2), (x1, y2)])


def texte(cal, x, y, t, h=250.0):
    import unicodedata  # textes sans accents : certains lecteurs DXF ignorent la page de code
    t = "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c)).replace("²", "2")
    p(0, "TEXT"); p(8, cal); p(10, float(x)); p(20, float(y)); p(30, 0.0); p(40, h); p(1, t)


def insert(cal, bloc, x, y, rot=0.0):
    p(0, "INSERT"); p(8, cal); p(2, bloc); p(10, float(x)); p(20, float(y)); p(30, 0.0)
    if rot:
        p(50, float(rot))


def debut_bloc(nom):
    p(0, "BLOCK"); p(8, "0"); p(2, nom); p(70, 0); p(10, 0.0); p(20, 0.0); p(30, 0.0); p(3, nom)


def fin_bloc():
    p(0, "ENDBLK"); p(8, "0")


def blocs():
    p(0, "SECTION"); p(2, "BLOCKS")
    for nom, larg in (("P83", 830), ("PCF93", 930), ("PENTREE140", 1400)):
        debut_bloc(nom)
        ligne("0", 0, 0, 0, larg)          # vantail ouvert
        arc("0", 0, 0, larg, 0, 90)        # débattement
        if nom == "PCF93":
            texte("0", 100, -250, "CF", 150)
        fin_bloc()
    for nom, larg in (("F120", 1200), ("F240", 2400)):
        debut_bloc(nom)
        for dy in (0, 60, 140, 200):
            ligne("0", 0, dy, larg, dy)
        ligne("0", 0, 0, 0, 200); ligne("0", larg, 0, larg, 200)
        fin_bloc()
    debut_bloc("BUREAU_1P"); rect("0", 0, 0, 1600, 800); arc("0", 800, 1100, 250, 0, 360); fin_bloc()
    debut_bloc("TABLE_REUNION"); rect("0", 0, 0, 3600, 1200); fin_bloc()
    debut_bloc("WC"); rect("0", 0, 0, 400, 200); arc("0", 200, 450, 200, 0, 360); fin_bloc()
    debut_bloc("LAVABO"); rect("0", 0, 0, 600, 450); fin_bloc()
    p(0, "ENDSEC")


def double_trait(cal, x1, y1, x2, y2, ep):
    """Mur en double trait : x1,y1 -> x2,y2 est l'une des faces."""
    if y1 == y2:
        ligne(cal, x1, y1, x2, y2); ligne(cal, x1, y1 + ep, x2, y2 + ep)
    else:
        ligne(cal, x1, y1, x2, y2); ligne(cal, x1 + ep, y1, x2 + ep, y2)


def entites():
    p(0, "SECTION"); p(2, "ENTITIES")
    X, Y, E = 20000, 12000, 200
    # Voiles de façade (double trait continu)
    rect_lignes = lambda cal, a, b, c, d: [ligne(cal, a, b, c, b), ligne(cal, c, b, c, d), ligne(cal, c, d, a, d), ligne(cal, a, d, a, b)]
    rect_lignes("A-MUR-BA20", 0, 0, X, Y)
    rect_lignes("A-MUR-BA20", E, E, X - E, Y - E)
    # Doublages sur les longs pans
    double_trait("A-DOUBLAGE", E, E, X - E, E, 110)
    double_trait("A-DOUBLAGE", E, Y - E - 110, X - E, Y - E - 110, 110)
    # Cloisons
    c72, c98, hy = "A-CLOISON-72", "A-CLOISON-98-ACOU", "A-CLOISON-72-HYDRO"
    double_trait(c72, 310, 4000, X - 310, 4000, 72)            # nord du couloir... sud
    double_trait(c72, 310, 5500, X - 310, 5500, 72)
    for x in (5000, 10000):
        double_trait(c72, x, 5572, x, Y - 310, 72)
    double_trait(c72, 15000, 5572, 15000, 8500, 72)
    double_trait(hy, 15000, 8572, 15000, Y - 310, 72)
    double_trait(hy, 15072, 8500, X - 310, 8500, 72)
    for x in (9000, 15000):
        double_trait(c98, x, 310, x, 4000, 98)
    # Poteaux
    for x, y in ((5000, 4750), (10000, 4750), (15000, 4750), (7500, 2000)):
        rect("A-POTEAU", x - 125, y - 125, x + 125, y + 125)
    # Locaux (contours intérieurs) + noms
    loc = [
        ("Bureau 1", 310, 5572, 5000, Y - 310), ("Bureau 2", 5072, 5572, 10000, Y - 310),
        ("Bureau 3", 10072, 5572, 15000, Y - 310), ("Local technique", 15072, 5572, X - 310, 8500),
        ("Sanitaires", 15072, 8572, X - 310, Y - 310), ("Circulation", 310, 4072, X - 310, 5500),
        ("Accueil", 310, 310, 9000, 4000), ("Salle de réunion", 9098, 310, 15000, 4000),
        ("Bureau 4", 15098, 310, X - 310, 4000),
    ]
    for nom, x1, y1, x2, y2 in loc:
        rect("A-LOCAUX", x1, y1, x2, y2)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        texte("A-LOCAUX", cx - 900, cy + 150, nom)
        texte("A-LOCAUX", cx - 900, cy - 250, ("%.2f m²" % ((x2 - x1) * (y2 - y1) / 1e6)).replace(".", ","), 180)
    # Portes : 8 bureaux/locaux en bois, 2 coupe-feu, 1 entrée
    for x, y, rot in ((1500, 5572, 0), (6500, 5572, 0), (11500, 5572, 0), (16000, 5572, 0),
                      (3000, 4000, 180), (12000, 4000, 180), (17000, 4000, 180), (16000, 8572, 0)):
        insert("A-PORTE", "P83", x, y, rot)
    insert("A-PORTE", "PCF93", 18500, 5572, 0)
    insert("A-PORTE", "PCF93", 700, 4750, 270)
    insert("A-PORTE", "PENTREE140", 4000, 0, 0)
    # Fenêtres : 5 petites + 1 grande par long pan, 1 grande sur un pignon
    for i in range(5):
        insert("A-FENETRE", "F120", 1000 + i * 3200, 0)
        insert("A-FENETRE", "F120", 1000 + i * 3200, Y - E)
    insert("A-FENETRE", "F240", 16500, 0)
    insert("A-FENETRE", "F240", 16500, Y - E)
    insert("A-FENETRE", "F240", E, 5000, 90)
    # Mobilier et sanitaires (hors chiffrage attendu)
    for x0 in (800, 5600, 10600):
        for k in range(3):
            insert("A-MOBILIER", "BUREAU_1P", x0 + (k % 2) * 1900, 7000 + (k // 2) * 2200)
    for k in range(3):
        insert("A-MOBILIER", "BUREAU_1P", 15600 + (k % 2) * 1900, 1000 + (k // 2) * 1500)
    insert("A-MOBILIER", "TABLE_REUNION", 10200, 1400)
    for k in range(3):
        insert("A-SANITAIRE", "WC", 16200 + k * 1100, Y - 600, 180)
    for k in range(2):
        insert("A-SANITAIRE", "LAVABO", 16400 + k * 1000, 8800)
    # Cotations (simples lignes de cote)
    ligne("A-COTATION", 0, -1200, X, -1200); ligne("A-COTATION", 0, -1400, 0, -1000); ligne("A-COTATION", X, -1400, X, -1000)
    texte("A-COTATION", X / 2 - 400, -1100, "20,00", 200)
    ligne("A-COTATION", X + 1200, 0, X + 1200, Y); ligne("A-COTATION", X + 1000, 0, X + 1400, 0); ligne("A-COTATION", X + 1000, Y, X + 1400, Y)
    texte("A-COTATION", X + 1300, Y / 2, "12,00", 200)
    texte("A-TEXTE", 0, Y + 1200, "PLAN DU REZ-DE-CHAUSSÉE - Immeuble de bureaux (démo) - 1/100", 350)
    p(0, "ENDSEC")


if __name__ == "__main__":
    entete(); tables(); blocs(); entites()
    p(0, "EOF")
    with open(sys.argv[1], "w", encoding="cp1252", newline="\r\n") as f:
        f.write("\n".join(L) + "\n")
    print("Écrit :", sys.argv[1])
