"""
Lecture des plans DXF (et DWG via conversion) pour le métré 2D.
Aucune dépendance externe.

Ce que le code mesure, par calque et par bloc :
  - nombre d'insertions de chaque bloc (portes, fenêtres, équipements...)
  - longueur développée des traits (lignes, arcs, polylignes)
  - surface des contours fermés et des hachures
  - locaux : contours fermés nommés par le texte placé à l'intérieur

Il ne décide PAS ce que représente un calque : c'est l'IA qui dira ensuite
si c'est un mur, un sol, un comptage, ou un élément à ignorer (cotation, mobilier...).
"""

import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict

UNITES_INSUNITS = {1: ("pouce", 0.0254), 2: ("pied", 0.3048), 4: ("mm", 0.001), 5: ("cm", 0.01), 6: ("m", 1.0)}
UNITES_CHOIX = {"mm": 0.001, "cm": 0.01, "m": 1.0}


# ----------------------------------------------------------------------------- géométrie

def arc_bulge(p1, p2, bulge):
    """Longueur et aire (signée selon le bulge) d'un segment courbe de polyligne."""
    corde = math.dist(p1, p2)
    if not bulge or corde == 0:
        return corde, 0.0
    theta = 4 * math.atan(abs(bulge))
    rayon = corde / (2 * math.sin(theta / 2))
    longueur = rayon * theta
    aire_segment = rayon * rayon / 2 * (theta - math.sin(theta))
    return longueur, math.copysign(aire_segment, bulge)


def mesurer_polyligne(points, bulges, fermee):
    """Renvoie (longueur, aire) ; aire = 0 si la polyligne est ouverte."""
    n = len(points)
    if n < 2:
        return 0.0, 0.0
    longueur, aire_courbes, shoelace = 0.0, 0.0, 0.0
    segments = n if fermee else n - 1
    for i in range(segments):
        a, b = points[i], points[(i + 1) % n]
        l, s = arc_bulge(a, b, bulges[i] if i < len(bulges) else 0.0)
        longueur += l
        aire_courbes += s
        shoelace += a[0] * b[1] - b[0] * a[1]
    aire = abs(shoelace / 2 + aire_courbes) if fermee and (n >= 3 or any(bulges)) else 0.0
    return longueur, aire


def point_dans_polygone(p, poly):
    x, y = p
    dedans = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            dedans = not dedans
        j = i
    return dedans


# ----------------------------------------------------------------------------- lecture brute

def lire_paires(contenu: bytes):
    if contenu[:22] == b"AutoCAD Binary DXF\r\n\x1a\x00"[:22]:
        raise ValueError("DXF binaire non pris en charge : réenregistrez le plan en DXF ASCII (texte).")
    try:
        texte = contenu.decode("utf-8")
    except UnicodeDecodeError:
        texte = contenu.decode("cp1252", errors="replace")
    # Avant 2007, AutoCAD écrit les caractères accentués sous la forme \U+00E9 : on les décode partout
    # (noms de calques, de blocs, textes).
    if "\\U+" in texte:
        texte = re.sub(r"\\U\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), texte)
    lignes = texte.splitlines()
    paires = []
    for i in range(0, len(lignes) - 1, 2):
        code = lignes[i].strip()
        if not code.lstrip("-").isdigit():
            raise ValueError("Ce fichier ne ressemble pas à un DXF (ligne %d inattendue)." % (i + 1))
        paires.append((int(code), lignes[i + 1].strip()))
    return paires


def decouper_entites(paires):
    """Découpe en (section, bloc courant, type d'entité, liste de paires)."""
    section, bloc = None, None
    courant = None
    for code, val in paires:
        if code == 0:
            if courant:
                yield courant
                courant = None
            if val == "SECTION":
                section = "?"
                continue
            if val == "ENDSEC":
                section = None
                continue
            if val == "EOF":
                break
            if val == "BLOCK":
                courant = (section, None, "BLOCK", [])
                continue
            if val == "ENDBLK":
                bloc = None
                continue
            courant = (section, bloc, val, [])
            continue
        if section == "?" and code == 2:
            section = val
            continue
        if courant:
            courant[3].append((code, val))
            if courant[2] == "BLOCK" and code == 2:
                bloc = val
                courant = (section, val, "BLOCK", courant[3])
    if courant:
        yield courant


def valeur(paires, code, defaut=None, conv=str):
    for c, v in paires:
        if c == code:
            try:
                return conv(v)
            except ValueError:
                return defaut
    return defaut


def texte_mtext(brut):
    t = re.sub(r"\\[A-Za-z][^;\\]*;", "", brut)
    t = t.replace("\\P", " ").replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", t).strip()


def points_lwpolyligne(paires):
    points, bulges = [], []
    x = None
    for c, v in paires:
        if c == 10:
            x = float(v)
        elif c == 20 and x is not None:
            points.append((x, float(v)))
            bulges.append(0.0)
            x = None
        elif c == 42 and bulges:
            bulges[-1] = float(v)
    return points, bulges


def aire_hachure(paires):
    """Aire d'une hachure à partir de ses contours (polylignes et bords droits / arcs)."""
    i, n = 0, len(paires)
    while i < n and paires[i][0] != 91:
        i += 1
    if i >= n:
        return 0.0
    nb_contours = int(paires[i][1])
    i += 1
    total = 0.0
    for _ in range(nb_contours):
        while i < n and paires[i][0] != 92:
            i += 1
        if i >= n:
            break
        drapeau = int(paires[i][1])
        i += 1
        points, bulges = [], []
        if drapeau & 2:  # contour de type polyligne
            a_bulge = 0
            nb = 0
            while i < n and paires[i][0] in (72, 73, 93):
                if paires[i][0] == 72:
                    a_bulge = int(paires[i][1])
                if paires[i][0] == 93:
                    nb = int(paires[i][1])
                i += 1
            for _ in range(nb):
                while i < n and paires[i][0] != 10:
                    i += 1
                if i + 1 >= n:
                    break
                x = float(paires[i][1])
                y = float(paires[i + 1][1]) if paires[i + 1][0] == 20 else 0.0
                i += 2
                b = 0.0
                if a_bulge and i < n and paires[i][0] == 42:
                    b = float(paires[i][1])
                    i += 1
                points.append((x, y))
                bulges.append(b)
        else:  # contour fait d'arêtes
            while i < n and paires[i][0] != 93:
                i += 1
            nb_aretes = int(paires[i][1]) if i < n else 0
            i += 1
            for _ in range(nb_aretes):
                while i < n and paires[i][0] != 72:
                    i += 1
                if i >= n:
                    break
                genre = int(paires[i][1])
                i += 1
                vals = {}
                while i < n and paires[i][0] not in (72, 92, 97, 75, 76):
                    vals.setdefault(paires[i][0], float(paires[i][1]) if paires[i][0] not in (73,) else int(paires[i][1]))
                    i += 1
                if genre == 1:
                    points.append((vals.get(10, 0.0), vals.get(20, 0.0)))
                    bulges.append(0.0)
                elif genre == 2:  # arc : on l'approche par des cordes
                    cx, cy, r = vals.get(10, 0.0), vals.get(20, 0.0), vals.get(40, 0.0)
                    a0, a1 = math.radians(vals.get(50, 0.0)), math.radians(vals.get(51, 360.0))
                    if not vals.get(73, 1):
                        a0, a1 = -a0, -a1
                    if a1 < a0:
                        a1 += 2 * math.pi
                    for k in range(17):
                        a = a0 + (a1 - a0) * k / 16
                        points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
                        bulges.append(0.0)
        _, aire = mesurer_polyligne(points, bulges, True)
        total += aire if (drapeau & 1 or drapeau & 16 or total == 0) else -aire
    return abs(total)


# ----------------------------------------------------------------------------- dessin (visionneuse 2D)

def points_arc(cx, cy, r, a0_deg, a1_deg):
    ouverture = (a1_deg - a0_deg) % 360 or 360
    n = max(4, int(ouverture / 10))
    return [(cx + r * math.cos(math.radians(a0_deg + ouverture * k / n)),
             cy + r * math.sin(math.radians(a0_deg + ouverture * k / n))) for k in range(n + 1)]


def points_polyligne(pts, bulges, fermee):
    """Points d'une polyligne, arcs (bulges) compris."""
    res = []
    n = len(pts)
    for i in range(n if fermee else n - 1):
        a, b = pts[i], pts[(i + 1) % n]
        res.append(a)
        bu = bulges[i] if i < len(bulges) else 0.0
        if bu:
            theta = 4 * math.atan(bu)
            corde = math.dist(a, b)
            if corde > 0:
                r = corde / (2 * math.sin(abs(theta) / 2))
                mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                d = r * math.cos(abs(theta) / 2)
                ux, uy = (b[0] - a[0]) / corde, (b[1] - a[1]) / corde
                sgn = 1 if bu > 0 else -1
                cx, cy = mx - sgn * uy * d, my + sgn * ux * d
                a0 = math.atan2(a[1] - cy, a[0] - cx)
                k = max(2, int(abs(theta) / (math.pi / 16)))
                for j in range(1, k):
                    ang = a0 + theta * j / k
                    res.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    if n and not fermee:
        res.append(pts[-1])
    return res


class Dessin:
    """Mémorise la géométrie du plan (espace objet + définitions de blocs) pour la dessiner dans le navigateur."""
    LIMITE = 400000  # primitives au plus (protection du navigateur)

    def __init__(self):
        self.modele = []          # (calque, points, fermee)
        self.textes = []          # (x, y, hauteur, rotation, texte, calque)
        self.inserts = []         # (nom, x, y, sx, sy, rot, calque, miroir)
        self.blocs = defaultdict(lambda: {"base": (0.0, 0.0), "prims": [], "inserts": []})
        self.poly_bloc = {}       # POLYLINE en cours dans un bloc

    @staticmethod
    def miroir(p):
        return valeur(p, 230, 1.0, float) < 0  # extrusion (0,0,-1) : symétrie en X (blocs « retournés »)

    def primitive(self, genre, p):
        """Convertit une entité simple en (calque, points, fermée), ou None."""
        cal = valeur(p, 8, "0")
        m = -1 if self.miroir(p) else 1
        if genre == "LINE":
            return cal, [(valeur(p, 10, 0.0, float), valeur(p, 20, 0.0, float)),
                         (valeur(p, 11, 0.0, float), valeur(p, 21, 0.0, float))], False
        if genre == "ARC":
            pts = points_arc(valeur(p, 10, 0.0, float), valeur(p, 20, 0.0, float), valeur(p, 40, 0.0, float),
                             valeur(p, 50, 0.0, float), valeur(p, 51, 0.0, float))
            return cal, [(m * x, y) for x, y in pts], False
        if genre == "CIRCLE":
            pts = points_arc(valeur(p, 10, 0.0, float), valeur(p, 20, 0.0, float), valeur(p, 40, 0.0, float), 0, 360)
            return cal, [(m * x, y) for x, y in pts], True
        if genre == "LWPOLYLINE":
            pts, bul = points_lwpolyligne(p)
            fermee = bool(valeur(p, 70, 0, int) & 1)
            return cal, [(m * x, y) for x, y in points_polyligne(pts, bul, fermee)], fermee
        return None

    def ajouter_bloc(self, bloc, genre, p):
        b = self.blocs[bloc]
        if genre == "BLOCK":
            b["base"] = (valeur(p, 10, 0.0, float), valeur(p, 20, 0.0, float))
        elif genre == "INSERT":
            b["inserts"].append(self._insert(p))
        elif genre == "POLYLINE":
            drapeaux = valeur(p, 70, 0, int)
            self.poly_bloc[bloc] = None if drapeaux & (16 | 64) else (valeur(p, 8, "0"), [], [], bool(drapeaux & 1))
        elif genre == "VERTEX" and self.poly_bloc.get(bloc):
            self.poly_bloc[bloc][1].append((valeur(p, 10, 0.0, float), valeur(p, 20, 0.0, float)))
            self.poly_bloc[bloc][2].append(valeur(p, 42, 0.0, float))
        elif genre == "SEQEND" and self.poly_bloc.get(bloc):
            cal, pts, bul, fermee = self.poly_bloc.pop(bloc)
            b["prims"].append((cal, points_polyligne(pts, bul, fermee), fermee))
        else:
            prim = self.primitive(genre, p)
            if prim:
                b["prims"].append(prim)

    def _insert(self, p):
        return (valeur(p, 2, ""), valeur(p, 10, 0.0, float), valeur(p, 20, 0.0, float),
                valeur(p, 41, 1.0, float), valeur(p, 42, 1.0, float), valeur(p, 50, 0.0, float),
                valeur(p, 8, "0"), self.miroir(p))

    def ajouter_modele(self, cal, pts, fermee):
        self.modele.append((cal, pts, fermee))
        return len(self.modele) - 1

    def ajouter_insert(self, p):
        self.inserts.append(self._insert(p))

    def ajouter_texte(self, p, texte):
        self.textes.append((valeur(p, 10, 0.0, float), valeur(p, 20, 0.0, float), valeur(p, 40, 1.0, float),
                            valeur(p, 50, 0.0, float), texte, valeur(p, 8, "0")))

    def exporter(self, resoudre_nom, groupes_dxf):
        """Construit le JSON de la visionneuse 2D. resoudre_nom : *U12 -> nom du bloc d'origine."""
        calques, index_calque = [], {}
        blocs, index_bloc = [], {}

        def ic(nom):
            if nom not in index_calque:
                index_calque[nom] = len(calques)
                calques.append(nom)
            return index_calque[nom]

        def ib(nom):
            nom = resoudre_nom(nom)
            if nom not in index_bloc:
                index_bloc[nom] = len(blocs)
                blocs.append(nom)
            return index_bloc[nom]

        prims = []

        def plat(pts):
            return [round(c, 3) for pt in pts for c in pt]

        for cal, pts, fermee in self.modele:
            prims.append([ic(cal), -1, [], plat(pts), 1 if fermee else 0])

        def developper(nom, transfo, calque_parent, numero, chaine, profondeur):
            if profondeur > 6 or len(prims) > self.LIMITE or nom not in self.blocs:
                return
            b = self.blocs[nom]
            chaine = chaine + [ib(nom)]
            for cal, pts, fermee in b["prims"]:
                cal = calque_parent if cal == "0" else cal
                prims.append([ic(cal), numero, chaine, plat(transfo(pt) for pt in pts), 1 if fermee else 0])
            for ins in b["inserts"]:
                developper(ins[0], composer_transfo(transfo, ins, self.blocs[ins[0]]["base"] if ins[0] in self.blocs else (0, 0)),
                           calque_parent if ins[6] == "0" else ins[6], numero, chaine, profondeur + 1)

        emprises = []
        for numero, ins in enumerate(self.inserts):
            if ins[0].startswith("*") and ins[0][:2].upper() != "*U":
                continue  # cotations et blocs techniques
            debut = len(prims)
            base = self.blocs[ins[0]]["base"] if ins[0] in self.blocs else (0.0, 0.0)
            developper(ins[0], composer_transfo(lambda q: q, ins, base), ins[6], numero, [], 0)
            coords = [c for pr in prims[debut:] for c in pr[3]]
            if coords:
                emprises.append([numero, ib(ins[0]), min(coords[0::2]), min(coords[1::2]), max(coords[0::2]), max(coords[1::2])])

        textes = [[round(x, 3), round(y, 3), round(h, 3), round(rot, 1), t[:80], ic(cal)] for x, y, h, rot, t, cal in self.textes[:20000]]

        # Emprise « utile » : on écarte les objets isolés très loin du plan (quantiles 2 % – 98 %)
        xs = sorted(pr[3][0] for pr in prims if pr[3])
        ys = sorted(pr[3][1] for pr in prims if pr[3])
        if xs:
            q = lambda v, f: v[min(len(v) - 1, max(0, int(f * len(v))))]
            x0, x1, y0, y1 = q(xs, 0.02), q(xs, 0.98), q(ys, 0.02), q(ys, 0.98)
            marge = max(x1 - x0, y1 - y0, 1e-6) * 0.08
            bornes = [x0 - marge, y0 - marge, x1 + marge, y1 + marge]
        else:
            bornes = [0, 0, 1, 1]

        # Correspondance groupes du métré -> primitives / blocs / calques
        correspondance = {}
        for g in groupes_dxf:
            if g["genre"] == "calque":
                correspondance[g["id"]] = {"calque": index_calque.get(g["type"], -1)}
            elif g["genre"] == "bloc":
                correspondance[g["id"]] = {"bloc": index_bloc.get(g["type"], -1)}
            else:
                correspondance[g["id"]] = {"prims": g.get("_prims", [])}
        return {"calques": calques, "blocs": blocs, "prims": prims, "textes": textes, "emprises": emprises,
                "bornes": bornes, "groupes": correspondance, "tronque": len(prims) > self.LIMITE}


def composer_transfo(parent, ins, base):
    """Transformation d'un point du bloc vers le repère parent : insertion, échelle, rotation, miroir."""
    _, x, y, sx, sy, rot, _, miroir = ins
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))

    def t(p):
        u, v = (p[0] - base[0]) * sx, (p[1] - base[1]) * sy
        px, py = x + c * u - s * v, y + s * u + c * v
        if miroir:
            px = -px
        return parent((px, py))
    return t


# ----------------------------------------------------------------------------- métré

def extraire_metre_dxf(contenu: bytes, nom_fichier="plan.dxf", unite="auto"):
    import time
    t0 = time.time()
    paires = lire_paires(contenu)

    insunits = None
    for i, (c, v) in enumerate(paires):
        if c == 9 and v == "$INSUNITS" and i + 1 < len(paires):
            insunits = int(paires[i + 1][1])
            break

    calques = defaultdict(lambda: {"longueur": 0.0, "surface_contours": 0.0, "nb_contours": 0,
                                   "surface_hachures": 0.0, "nb_traits": 0})
    blocs_modele = defaultdict(lambda: {"nombre": 0, "calques": defaultdict(int), "dynamiques": 0})
    # Blocs dynamiques : quand on modifie un paramètre (largeur de porte...), AutoCAD insère une copie
    # anonyme « *U12 ». La table BLOCK_RECORD indique de quel bloc d'origine elle provient.
    nom_par_handle, origine_anonyme = {}, {}
    inserts_dans_blocs = defaultdict(list)   # bloc -> [blocs insérés dedans]
    textes = []                              # (calque, texte, x, y)
    contours = []                            # (calque, points, aire)
    xs, ys = [], []

    polyligne = None  # POLYLINE ancienne génération en cours
    dessin = Dessin()

    def fin_polyligne():
        nonlocal polyligne
        if polyligne:
            cal, pts, bul, fermee = polyligne
            ajouter_polyligne(cal, pts, bul, fermee)
        polyligne = None

    def ajouter_polyligne(cal, pts, bul, fermee, miroir=False):
        longueur, aire = mesurer_polyligne(pts, bul, fermee)
        trace = points_polyligne(pts, bul, fermee)
        indice = dessin.ajouter_modele(cal, [(-x, y) for x, y in trace] if miroir else trace, fermee)
        if fermee and aire > 0:
            calques[cal]["surface_contours"] += aire
            calques[cal]["nb_contours"] += 1
            calques[cal]["longueur"] += longueur
            contours.append((cal, pts, aire, longueur, indice))
        else:
            calques[cal]["longueur"] += longueur
            calques[cal]["nb_traits"] += 1
        for x, y in pts:
            xs.append(x); ys.append(y)

    for section, bloc, genre, p in decouper_entites(paires):
        if section == "TABLES" and genre == "BLOCK_RECORD":
            nom_rec, handle = valeur(p, 2, ""), valeur(p, 5, "")
            if handle:
                nom_par_handle[handle.upper()] = nom_rec
            for k, (c, v) in enumerate(p):
                if c == 1001 and v == "AcDbBlockRepBTag":
                    cible = next((v2 for c2, v2 in p[k + 1:k + 5] if c2 == 1005), None)
                    if cible:
                        origine_anonyme[nom_rec] = cible.upper()
                    break
            continue
        if section == "BLOCKS" and bloc:
            if genre == "INSERT":
                inserts_dans_blocs[bloc].append(valeur(p, 2, ""))
            dessin.ajouter_bloc(bloc, genre, p)
            continue
        if section != "ENTITIES":
            continue
        if genre != "VERTEX" and genre != "SEQEND" and polyligne:
            fin_polyligne()
        if valeur(p, 67, 0, int) == 1:  # espace papier : on ignore
            continue
        cal = valeur(p, 8, "0")
        if genre in ("LINE", "ARC", "CIRCLE"):
            dessin.ajouter_modele(*dessin.primitive(genre, p))
        if genre == "LINE":
            x1, y1, x2, y2 = (valeur(p, c, 0.0, float) for c in (10, 20, 11, 21))
            calques[cal]["longueur"] += math.dist((x1, y1), (x2, y2))
            calques[cal]["nb_traits"] += 1
            xs += [x1, x2]; ys += [y1, y2]
        elif genre == "ARC":
            r = valeur(p, 40, 0.0, float)
            a0, a1 = valeur(p, 50, 0.0, float), valeur(p, 51, 0.0, float)
            ouverture = (a1 - a0) % 360 or 360
            calques[cal]["longueur"] += r * math.radians(ouverture)
            calques[cal]["nb_traits"] += 1
        elif genre == "CIRCLE":
            r = valeur(p, 40, 0.0, float)
            calques[cal]["longueur"] += 2 * math.pi * r
            calques[cal]["nb_traits"] += 1
        elif genre == "LWPOLYLINE":
            pts, bul = points_lwpolyligne(p)
            ajouter_polyligne(cal, pts, bul, bool(valeur(p, 70, 0, int) & 1), Dessin.miroir(p))
        elif genre == "POLYLINE":
            drapeaux = valeur(p, 70, 0, int)
            polyligne = None if drapeaux & (16 | 64) else (cal, [], [], bool(drapeaux & 1))  # on ignore maillages 3D
        elif genre == "VERTEX" and polyligne:
            polyligne[1].append((valeur(p, 10, 0.0, float), valeur(p, 20, 0.0, float)))
            polyligne[2].append(valeur(p, 42, 0.0, float))
        elif genre == "SEQEND":
            fin_polyligne()
        elif genre == "HATCH":
            aire = aire_hachure(p)
            calques[cal]["surface_hachures"] += aire
        elif genre == "INSERT":
            dessin.ajouter_insert(p)
            nom = valeur(p, 2, "")
            if nom and (not nom.startswith("*") or nom[:2].upper() == "*U"):
                n = max(1, valeur(p, 70, 1, int)) * max(1, valeur(p, 71, 1, int))  # MINSERT
                blocs_modele[nom]["nombre"] += n
                blocs_modele[nom]["calques"][cal] += n
            xs.append(valeur(p, 10, 0.0, float)); ys.append(valeur(p, 20, 0.0, float))
        elif genre in ("TEXT", "MTEXT"):
            brut = "".join(v for c, v in p if c in (3, 1))
            t = texte_mtext(brut) if genre == "MTEXT" else brut.strip()
            if t:
                textes.append((cal, t, valeur(p, 10, 0.0, float), valeur(p, 20, 0.0, float)))
                dessin.ajouter_texte(p, t)
    fin_polyligne()

    # Blocs imbriqués (ex. : un bloc « sanitaire type » qui contient des lavabos)
    def compter_imbriques(nom, multiplicateur, profondeur=0):
        if profondeur > 5:
            return
        for enfant in inserts_dans_blocs.get(nom, []):
            if enfant and (not enfant.startswith("*") or enfant[:2].upper() == "*U"):
                blocs_modele[enfant]["nombre"] += multiplicateur
                blocs_modele[enfant]["calques"]["(dans le bloc %s)" % nom] += multiplicateur
                compter_imbriques(enfant, multiplicateur, profondeur + 1)

    for nom, info in list(blocs_modele.items()):
        if not any(k.startswith("(dans") for k in info["calques"]):
            compter_imbriques(nom, info["nombre"])

    # Rattachement des copies anonymes (*U..) à leur bloc dynamique d'origine
    for nom in [n for n in blocs_modele if n[:2].upper() == "*U"]:
        info = blocs_modele.pop(nom)
        origine = nom_par_handle.get(origine_anonyme.get(nom, ""))
        cible = blocs_modele[origine or "(blocs dynamiques non identifiés)"]
        cible["nombre"] += info["nombre"]
        cible["dynamiques"] += info["nombre"]
        for cal, n in info["calques"].items():
            cible["calques"][cal] += n

    # Unités
    etendue = max((max(xs) - min(xs)) if xs else 0, (max(ys) - min(ys)) if ys else 0)
    if unite in UNITES_CHOIX:
        nom_unite, f = unite, UNITES_CHOIX[unite]
        origine_unite = "choisie"
    elif insunits in UNITES_INSUNITS:
        nom_unite, f = UNITES_INSUNITS[insunits]
        origine_unite = "lue dans le fichier"
    else:
        nom_unite, f = ("mm", 0.001) if etendue > 5000 else (("cm", 0.01) if etendue > 300 else ("m", 1.0))
        origine_unite = "devinée d'après la taille du dessin"

    # Locaux : chaque texte est rattaché au plus petit contour fermé qui le contient,
    # sur un calque de locaux (nom évocateur) ou sur le même calque que le texte.
    indice_locaux = re.compile(r"LOCA|PIECE|PIÈCE|ROOM|SURF|ESPACE|ZONE|SPACE", re.I)
    locaux = defaultdict(lambda: {"nombre": 0, "surface": 0.0, "noms": [], "calque": ""})
    nom_contour = {}
    for (ct, t, x, y) in textes:
        if re.fullmatch(r"[\d\s.,m²2]+", t):
            continue  # une surface écrite (« 12,5 m² ») n'est pas un nom de local
        candidats = [(aire, idx) for idx, (cal, pts, aire, _l, _i) in enumerate(contours)
                     if (cal == ct or indice_locaux.search(cal)) and point_dans_polygone((x, y), pts)]
        if candidats:
            idx = min(candidats)[1]
            nom_contour.setdefault(idx, t)
    contours_nommes = set(nom_contour)
    infos_locaux = {}  # primitive du dessin -> (nom, surface m², catégorie)
    for idx, nom in nom_contour.items():
        cal, pts, aire, _l, indice = contours[idx]
        categorie = re.sub(r"[\s\-_]*\d+[a-zA-Z]?$", "", nom).strip() or nom
        infos_locaux[indice] = (nom, aire, (cal, categorie))
        locaux[(cal, categorie)].setdefault("prims", []).append(indice)
        locaux[(cal, categorie)]["nombre"] += 1
        locaux[(cal, categorie)]["surface"] += aire
        locaux[(cal, categorie)]["noms"].append(nom)
        locaux[(cal, categorie)]["calque"] = cal

    # Retrait des contours nommés de leur calque (pour ne pas les compter deux fois)
    for idx in contours_nommes:
        cal, pts, aire, longueur, _i = contours[idx]
        calques[cal]["surface_contours"] -= aire
        calques[cal]["nb_contours"] -= 1
        calques[cal]["longueur"] -= longueur

    groupes = []
    n = 0
    for (cal, categorie), info in sorted(locaux.items()):
        n += 1
        groupes.append({
            "id": "D%02d" % n, "source": "dxf", "genre": "local", "classe": "Local", "categorie": "Local",
            "type": categorie, "calque": cal, "materiau": "", "nombre": info["nombre"],
            "surface": round(info["surface"] * f * f, 2), "volume": None, "longueur": None,
            "n_surface": info["nombre"], "n_volume": 0, "n_longueur": 0, "epaisseur": None,
            "detail": ", ".join(sorted(set(info["noms"])))[:200], "niveaux": {nom_fichier: info["nombre"]},
            "_prims": info.get("prims", []),
        })
    for nom, info in sorted(blocs_modele.items()):
        if info["nombre"] == 0:
            continue
        n += 1
        cals = ", ".join(sorted(info["calques"]))
        groupes.append({
            "id": "D%02d" % n, "source": "dxf", "genre": "bloc", "classe": "Bloc", "categorie": "Bloc",
            "type": nom, "calque": cals, "materiau": "", "nombre": info["nombre"],
            "surface": None, "volume": None, "longueur": None, "n_surface": 0, "n_volume": 0, "n_longueur": 0,
            "epaisseur": None,
            "detail": "calque(s) : " + cals + (" ; dont %d variante(s) de bloc dynamique" % info["dynamiques"] if info["dynamiques"] else ""),
            "niveaux": {nom_fichier: info["nombre"]},
        })
    for cal, info in sorted(calques.items()):
        surface = info["surface_contours"] + info["surface_hachures"]
        if info["longueur"] < 1e-9 and surface < 1e-9:
            continue
        n += 1
        groupes.append({
            "id": "D%02d" % n, "source": "dxf", "genre": "calque", "classe": "Calque", "categorie": "Calque",
            "type": cal, "calque": cal, "materiau": "",
            "nombre": info["nb_contours"],
            "surface": round(surface * f * f, 2) if surface > 1e-9 else None,
            "volume": None,
            "longueur": round(info["longueur"] * f, 2) if info["longueur"] > 1e-9 else None,
            "n_surface": info["nb_contours"] or (1 if surface else 0), "n_volume": 0, "n_longueur": 1,
            "surface_hachures": round(info["surface_hachures"] * f * f, 2),
            "epaisseur": None,
            "detail": "%d trait(s), %d contour(s) fermé(s)%s" % (
                info["nb_traits"], info["nb_contours"],
                ", hachures %.2f m²" % (info["surface_hachures"] * f * f) if info["surface_hachures"] else ""),
            "niveaux": {nom_fichier: 1},
        })
    # Dessin pour la visionneuse 2D
    def resoudre_nom(nom):
        if nom[:2].upper() != "*U":
            return nom
        return nom_par_handle.get(origine_anonyme.get(nom, "")) or "(blocs dynamiques non identifiés)"

    plan = dessin.exporter(resoudre_nom, groupes)
    groupe_local = {("Local", g["type"], g["calque"]): g["id"] for g in groupes if g["genre"] == "local"}
    plan["locaux"] = {str(i): [nom, round(aire * f * f, 2), groupe_local.get(("Local", cat[1], cat[0]), "")]
                      for i, (nom, aire, cat) in infos_locaux.items()}
    plan["unite"] = nom_unite
    plan["facteur"] = f
    for g in groupes:
        g.pop("_prims", None)
    return {
        "_dessin": plan,
        "fichier": nom_fichier, "source": "dxf", "schema": "DXF",
        "nb_entites": sum(1 for c, _ in paires if c == 0),
        "nb_elements": sum(g["nombre"] for g in groupes if g["genre"] != "calque") + sum(1 for g in groupes if g["genre"] == "calque"),
        "niveaux": [nom_fichier], "groupes": groupes,
        "unite_longueur": nom_unite, "origine_unite": origine_unite,
        "duree": round(time.time() - t0, 2),
    }


# ----------------------------------------------------------------------------- DWG

def trouver_oda():
    candidats = [shutil.which("ODAFileConverter"), shutil.which("ODAFileConverter.exe")]
    for base in (r"C:\Program Files\ODA", r"C:\Program Files (x86)\ODA"):
        if os.path.isdir(base):
            for d in sorted(os.listdir(base), reverse=True):
                candidats.append(os.path.join(base, d, "ODAFileConverter.exe"))
    candidats += ["/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
                  "/usr/bin/ODAFileConverter", "/usr/local/bin/ODAFileConverter"]
    chemin_perso = os.environ.get("ODA_CONVERTER")
    if chemin_perso:
        candidats.insert(0, chemin_perso)
    return next((c for c in candidats if c and os.path.isfile(c)), None)


MESSAGE_SANS_ODA = ("Pour lire un DWG, il faut le convertir en DXF. Deux solutions : "
                    "1) dans AutoCAD, Fichier > Enregistrer sous > format DXF, puis chargez le DXF ; "
                    "2) installez le logiciel gratuit « ODA File Converter » (opendesign.com) : "
                    "la conversion se fera alors automatiquement.")


def convertir_dwg(contenu: bytes, nom_fichier="plan.dwg"):
    oda = trouver_oda()
    if not oda:
        raise ValueError(MESSAGE_SANS_ODA)
    with tempfile.TemporaryDirectory() as entree, tempfile.TemporaryDirectory() as sortie:
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(nom_fichier))[0]) or "plan"
        nom = base + ".DWG"  # extension en majuscules : cohérente avec le filtre « *.DWG » d'ODA
        with open(os.path.join(entree, nom), "wb") as f:
            f.write(contenu)
        try:
            subprocess.run([oda, entree, sortie, "ACAD2018", "DXF", "0", "1", "*.DWG"],
                           check=False, timeout=180, capture_output=True)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ValueError("La conversion DWG → DXF a échoué (%s). Enregistrez le plan en DXF depuis AutoCAD." % e)
        dxf = [f for f in os.listdir(sortie) if f.lower().endswith(".dxf")]
        if not dxf:
            raise ValueError("ODA File Converter n'a produit aucun DXF. Le fichier DWG est peut-être protégé ou corrompu.")
        with open(os.path.join(sortie, dxf[0]), "rb") as f:
            return f.read()
