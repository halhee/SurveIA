"""
POC « De la maquette au chiffrage » avec DeepSeek
=================================================

Lancement :  python chiffrage_ia.py
Puis suivre les étapes dans le navigateur qui s'ouvre (la clé API est demandée en premier).

Principe :
  1. Le code lit la maquette IFC ou le plan DXF/DWG et calcule le métré (surfaces, volumes, longueurs, nombres).
  2. DeepSeek associe chaque type d'élément à une ou plusieurs lignes de la bibliothèque de prix,
     en s'appuyant sur le CCTP s'il est fourni (articles cités, écarts signalés, ouvrages non modélisés).
     L'IA ne calcule AUCUNE quantité et n'invente AUCUN prix : elle ne fait que la correspondance.
  3. Le code calcule l'estimation (quantité x prix unitaire) et produit un fichier Excel.

Dépendances : openpyxl (pip install openpyxl) ; pypdf, seulement pour lire un CCTP en PDF (pip install pypdf)
La clé API reste en mémoire le temps de la session : elle n'est jamais écrite sur le disque.
"""

import csv
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import uuid
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
import urllib.request
import webbrowser
from collections import Counter, defaultdict, namedtuple
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import geometrie_ifc
import lecture_cctp
import lecture_dxf

warnings.filterwarnings("ignore", message=".*ARC4.*")  # avertissement sans conséquence de pypdf
DOSSIER = os.path.dirname(os.path.abspath(__file__))
URL_API = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODELES = {
    "deepseek-flash": "DeepSeek V4.1 Flash (rapide, économique)",
    "deepseek-v4-pro": "DeepSeek V4 Pro (plus puissant)",
}


# =============================================================================
# 1. LECTURE DU FICHIER IFC (format texte STEP, sans dépendance externe)
# =============================================================================

class Ref(int):
    """Référence vers une autre entité (#123)."""


class Enum(str):
    """Valeur énumérée (.STANDARD.)."""


class Typed:
    """Valeur typée, par ex. IFCAREAMEASURE(12.5)."""

    def __init__(self, nom, valeurs):
        self.nom = nom
        self.valeurs = valeurs

    @property
    def valeur(self):
        return self.valeurs[0] if self.valeurs else None


# Un seul motif, parcouru par findall (boucle en C) : chaque correspondance renvoie un tuple
# dont un seul élément est non vide : chaîne | référence | énumération | nombre | mot-clé | binaire | ponctuation.
_TOKEN = re.compile(
    r"('(?:[^']|'')*')|#(\d+)|\.([A-Za-z0-9_]+)\.|([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
    r"|([A-Za-z][A-Za-z0-9_]*)|(\"[0-9A-Fa-f]*\")|([(),$*])"
)


def decoder_chaine(s):
    """Décode les caractères spéciaux STEP (\\X2\\00E9\\X0\\ -> é, etc.)."""
    s = s.replace("''", "'")
    if "\\" not in s:
        return s

    def x2(m):
        h = m.group(1)
        return "".join(chr(int(h[i:i + 4], 16)) for i in range(0, len(h) - 3, 4))

    def x4(m):
        h = m.group(1)
        return "".join(chr(int(h[i:i + 8], 16)) for i in range(0, len(h) - 7, 8))

    s = re.sub(r"\\X2\\([0-9A-Fa-f]*)\\X0\\", x2, s)
    s = re.sub(r"\\X4\\([0-9A-Fa-f]*)\\X0\\", x4, s)
    s = re.sub(r"\\X\\([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("latin-1"), s)
    s = re.sub(r"\\S\\(.)", lambda m: chr(ord(m.group(1)) + 128), s)
    s = re.sub(r"\\P[A-I]\\", "", s)
    return s.replace("\\\\", "\\")


def analyser_arguments(texte):
    """Transforme la liste d'arguments STEP en structures Python."""
    racine = []
    pile = [racine]
    courant = racine
    en_attente_type = None
    for chaine, ref, enum, num, mot, binaire, ponct in _TOKEN.findall(texte):
        if ponct:
            if ponct == ",":
                continue
            if ponct == "(":
                if en_attente_type:
                    nouvelle = Typed(en_attente_type, [])
                    courant.append(nouvelle)
                    courant = nouvelle.valeurs
                    en_attente_type = None
                else:
                    nouvelle = []
                    courant.append(nouvelle)
                    courant = nouvelle
                pile.append(courant)
            elif ponct == ")":
                if len(pile) > 1:
                    pile.pop()
                    courant = pile[-1]
            elif ponct == "$":
                courant.append(None)
            else:  # « * » : attribut dérivé
                courant.append("*")
        elif ref:
            courant.append(Ref(int(ref)))
        elif num:
            courant.append(float(num) if ("." in num or "e" in num or "E" in num) else int(num))
        elif chaine:
            courant.append(decoder_chaine(chaine[1:-1]))
        elif enum:
            courant.append(Enum(enum.upper()))
        elif mot:
            en_attente_type = mot.upper()
        elif binaire:
            courant.append(binaire)
    return racine


class LecteurIFC:
    def __init__(self, contenu: bytes):
        try:
            texte = contenu.decode("utf-8")
        except UnicodeDecodeError:
            texte = contenu.decode("latin-1")
        m = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", texte, re.I)
        self.schema = m.group(1).upper() if m else "inconnu"
        debut = re.search(r"\bDATA\s*;", texte)
        if not debut:
            raise ValueError("Ce fichier ne ressemble pas à un IFC (section DATA introuvable).")
        data = texte[debut.end():]
        self.brut = {}          # id -> (TYPE, texte des arguments)
        self.par_type = defaultdict(list)
        self._cache = {}
        # Une instruction = « #id = TYPE( ... ); ». Les chaînes entre apostrophes peuvent contenir
        # des « ; » : le motif les saute. findall parcourt tout le fichier en une passe (en C).
        motif = re.compile(r"#(\d+)\s*=\s*([A-Za-z0-9_]+)\s*\(((?:[^;']|'(?:[^']|'')*')*)\)\s*;")
        brut, par_type = self.brut, self.par_type
        for ident, t, arguments in motif.findall(data):
            t = t.upper()
            i = int(ident)
            brut[i] = (t, arguments)
            par_type[t].append(i)
        if not self.brut:
            raise ValueError("Aucune entité lue dans le fichier IFC.")

    def type_de(self, i):
        return self.brut.get(i, (None, None))[0]

    def args(self, i):
        if i not in self._cache:
            self._cache[i] = analyser_arguments(self.brut[i][1]) if i in self.brut else []
        return self._cache[i]

    def tous(self, *types):
        for t in types:
            for i in self.par_type.get(t, []):
                yield i


# =============================================================================
# 2. MÉTRÉ : regroupement des éléments par type et calcul des quantités
# =============================================================================

CLASSES = {
    "IFCWALL": ("IfcWall", "Mur"), "IFCWALLSTANDARDCASE": ("IfcWall", "Mur"), "IFCWALLELEMENTEDCASE": ("IfcWall", "Mur"),
    "IFCCURTAINWALL": ("IfcCurtainWall", "Mur-rideau"),
    "IFCSLAB": ("IfcSlab", "Dalle / plancher"), "IFCSLABSTANDARDCASE": ("IfcSlab", "Dalle / plancher"), "IFCSLABELEMENTEDCASE": ("IfcSlab", "Dalle / plancher"),
    "IFCROOF": ("IfcRoof", "Toiture"),
    "IFCCOLUMN": ("IfcColumn", "Poteau"), "IFCCOLUMNSTANDARDCASE": ("IfcColumn", "Poteau"),
    "IFCBEAM": ("IfcBeam", "Poutre"), "IFCBEAMSTANDARDCASE": ("IfcBeam", "Poutre"),
    "IFCFOOTING": ("IfcFooting", "Fondation"), "IFCPILE": ("IfcPile", "Pieu"),
    "IFCDOOR": ("IfcDoor", "Porte"), "IFCDOORSTANDARDCASE": ("IfcDoor", "Porte"),
    "IFCWINDOW": ("IfcWindow", "Fenêtre"), "IFCWINDOWSTANDARDCASE": ("IfcWindow", "Fenêtre"),
    "IFCSTAIR": ("IfcStair", "Escalier"), "IFCSTAIRFLIGHT": ("IfcStairFlight", "Volée d'escalier"),
    "IFCRAMP": ("IfcRamp", "Rampe"), "IFCRAILING": ("IfcRailing", "Garde-corps"),
    "IFCCOVERING": ("IfcCovering", "Revêtement"), "IFCPLATE": ("IfcPlate", "Plaque"),
    "IFCMEMBER": ("IfcMember", "Membrure / montant"),
    "IFCBUILDINGELEMENTPROXY": ("IfcBuildingElementProxy", "Élément générique"),
    # Équipements comptés à l'unité (IFC4 : IfcSanitaryTerminal ; IFC2x3 : IfcFlowTerminal)
    "IFCSANITARYTERMINAL": ("IfcSanitaryTerminal", "Appareil sanitaire"),
    "IFCFLOWTERMINAL": ("IfcFlowTerminal", "Équipement terminal"),
    # Locaux : leur surface de plancher sert aux revêtements de sol et aux plafonds
    "IFCSPACE": ("IfcSpace", "Local"),
}

PREFIXES = {None: 1.0, "MILLI": 1e-3, "CENTI": 1e-2, "DECI": 1e-1, "KILO": 1e3, "DECA": 10.0, "HECTO": 100.0}

# Ordre de préférence des quantités (noms standards IFC et exports courants)
Q_SURFACE = ["NetSideArea", "NetArea", "GrossSideArea", "GrossArea", "Area", "NetSurfaceArea",
             "GrossSurfaceArea", "NetFootprintArea", "GrossFootprintArea", "Surface", "Aire"]
Q_VOLUME = ["NetVolume", "GrossVolume", "Volume"]
Q_LONGUEUR = ["Length", "NetLength", "GrossLength", "Longueur"]
Q_EPAISSEUR = ["Width", "Thickness", "Depth", "Épaisseur", "Epaisseur"]
# Pour les locaux (IfcSpace) : surface de plancher d'abord (Qto_SpaceBaseQuantities, propriétés Revit)
Q_SURFACE_LOCAL = ["NetFloorArea", "GrossFloorArea", "Area", "NetArea", "GrossArea", "Surface", "Aire"]


def facteurs_unites(ifc):
    """Facteurs de conversion vers m, m², m³ d'après IfcUnitAssignment."""
    f = {"LENGTHUNIT": 1.0, "AREAUNIT": 1.0, "VOLUMEUNIT": 1.0}

    def facteur(ref, profondeur=0):
        t = ifc.type_de(ref)
        a = ifc.args(ref)
        if t == "IFCSIUNIT":
            return a[1], PREFIXES.get(a[2], 1.0) ** {"AREAUNIT": 2, "VOLUMEUNIT": 3}.get(a[1], 1)
        if t == "IFCCONVERSIONBASEDUNIT" and profondeur < 3:
            mesure = ifc.args(a[3])  # IfcMeasureWithUnit
            val = mesure[0].valeur if isinstance(mesure[0], Typed) else mesure[0]
            _, base = facteur(mesure[1], profondeur + 1) if isinstance(mesure[1], Ref) else (None, 1.0)
            return a[1], float(val) * base
        return None, 1.0

    for ua in ifc.tous("IFCUNITASSIGNMENT"):
        for ref in ifc.args(ua)[0]:
            if isinstance(ref, Ref):
                try:
                    genre, val = facteur(ref)
                    if genre in f:
                        f[genre] = val
                except Exception:
                    pass
    return f


def nom_materiau(ifc, ref, profondeur=0):
    if not isinstance(ref, Ref) or profondeur > 4:
        return None
    t, a = ifc.type_de(ref), ifc.args(ref)
    if t == "IFCMATERIAL":
        return a[0]
    if t in ("IFCMATERIALLAYERSETUSAGE", "IFCMATERIALPROFILESETUSAGE", "IFCMATERIALPROFILESETUSAGETAPERING"):
        return nom_materiau(ifc, a[0], profondeur + 1)
    if t == "IFCMATERIALLAYERSET":
        couches = [nom_materiau(ifc, ifc.args(c)[0], profondeur + 1) for c in a[0] if isinstance(c, Ref)]
        couches = [c for c in couches if c]
        nom = a[1] if len(a) > 1 and isinstance(a[1], str) else None
        return (nom + " (" + " + ".join(couches) + ")") if nom and couches else (nom or " + ".join(couches) or None)
    if t == "IFCMATERIALLAYER":
        return nom_materiau(ifc, a[0], profondeur + 1)
    if t == "IFCMATERIALLIST":
        noms = [nom_materiau(ifc, m, profondeur + 1) for m in a[0]]
        return " + ".join(n for n in noms if n) or None
    if t == "IFCMATERIALCONSTITUENTSET":
        noms = [nom_materiau(ifc, ifc.args(c)[2], profondeur + 1) for c in (a[2] or []) if isinstance(c, Ref)]
        return a[0] or " + ".join(n for n in noms if n) or None
    if t == "IFCMATERIALPROFILESET":
        noms = [nom_materiau(ifc, ifc.args(p)[2], profondeur + 1) for p in (a[2] or []) if isinstance(p, Ref)]
        return a[0] or " + ".join(n for n in noms if n) or None
    return None


def extraire_metre(contenu: bytes, nom_fichier="maquette.ifc"):
    t0 = time.time()
    ifc = LecteurIFC(contenu)
    fu = facteurs_unites(ifc)

    elements = {i: CLASSES[ifc.type_de(i)] for i in ifc.tous(*CLASSES.keys())}
    quantites = defaultdict(dict)   # id -> {nom: valeur en unités SI}
    proprietes = defaultdict(dict)  # repli : propriétés Revit « Area », « Volume »...
    type_elt, mat_elt, niveau_elt = {}, {}, {}
    mat_type = {}

    def ajouter_quantite(cible, q):
        t, a = ifc.type_de(q), ifc.args(q)
        if len(a) < 4 or not isinstance(a[3], (int, float)):
            return
        facteur = {"IFCQUANTITYLENGTH": fu["LENGTHUNIT"], "IFCQUANTITYAREA": fu["AREAUNIT"],
                   "IFCQUANTITYVOLUME": fu["VOLUMEUNIT"], "IFCQUANTITYCOUNT": 1.0}.get(t)
        if facteur is not None:
            cible.setdefault(a[0], a[3] * facteur)

    for rel in ifc.tous("IFCRELDEFINESBYPROPERTIES"):
        a = ifc.args(rel)
        objets, definition = a[4], a[5]
        objets = objets if isinstance(objets, list) else [objets]
        cibles = [o for o in objets if o in elements]
        if not cibles or not isinstance(definition, Ref):
            continue
        td, ad = ifc.type_de(definition), ifc.args(definition)
        if td == "IFCELEMENTQUANTITY":
            for o in cibles:
                for q in ad[5] or []:
                    ajouter_quantite(quantites[o], q)
        elif td == "IFCPROPERTYSET":
            for p in ad[4] or []:
                if ifc.type_de(p) != "IFCPROPERTYSINGLEVALUE":
                    continue
                pa = ifc.args(p)
                v = pa[2]
                if isinstance(v, Typed) and isinstance(v.valeur, (int, float)):
                    facteur = {"IFCAREAMEASURE": fu["AREAUNIT"], "IFCVOLUMEMEASURE": fu["VOLUMEUNIT"],
                               "IFCLENGTHMEASURE": fu["LENGTHUNIT"], "IFCPOSITIVELENGTHMEASURE": fu["LENGTHUNIT"]}.get(v.nom)
                    if facteur:
                        for o in cibles:
                            proprietes[o].setdefault(pa[0], v.valeur * facteur)

    for rel in ifc.tous("IFCRELDEFINESBYTYPE"):
        a = ifc.args(rel)
        nom = ifc.args(a[5])[2] if isinstance(a[5], Ref) else None
        for o in a[4] or []:
            if o in elements and nom:
                type_elt[o] = nom

    types_ids = {}
    for rel in ifc.tous("IFCRELDEFINESBYTYPE"):
        a = ifc.args(rel)
        for o in a[4] or []:
            types_ids[o] = a[5]

    for rel in ifc.tous("IFCRELASSOCIATESMATERIAL"):
        a = ifc.args(rel)
        nom = nom_materiau(ifc, a[5])
        for o in a[4] or []:
            if o in elements:
                mat_elt[o] = nom
            else:
                mat_type[o] = nom

    # Locaux rattachés à un niveau (IfcRelAggregates niveau -> locaux)
    niveau_local = {}
    for rel in ifc.tous("IFCRELAGGREGATES"):
        a = ifc.args(rel)
        if ifc.type_de(a[4]) == "IFCBUILDINGSTOREY":
            nom_niveau = ifc.args(a[4])[2] or "?"
            for e in a[5] or []:
                if ifc.type_de(e) == "IFCSPACE":
                    niveau_local[e] = nom_niveau
                    if e in elements:
                        niveau_elt[e] = nom_niveau

    for rel in ifc.tous("IFCRELCONTAINEDINSPATIALSTRUCTURE"):
        a = ifc.args(rel)
        structure = a[5] if isinstance(a[5], Ref) else None
        nom = ifc.args(structure)[2] if structure else None
        if structure and ifc.type_de(structure) == "IFCSPACE":
            nom = niveau_local.get(structure, nom)  # élément rangé dans un local : on remonte au niveau
        for o in a[4] or []:
            if o in elements:
                niveau_elt[o] = nom or "?"

    # Éléments décomposés (mur-rideau, toiture, escalier...) : on évite de compter deux fois.
    exclus = set()
    for rel in ifc.tous("IFCRELAGGREGATES"):
        a = ifc.args(rel)
        parent, enfants = a[4], [e for e in (a[5] or []) if e in elements]
        if parent in elements and enfants:
            q = {**proprietes[parent], **quantites[parent]}
            if any(k in q for k in Q_SURFACE + Q_VOLUME):
                exclus.update(enfants)
            else:
                exclus.add(parent)
                for e in enfants:
                    niveau_elt.setdefault(e, niveau_elt.get(parent, "?"))

    groupes = {}
    for i, (classe, categorie) in elements.items():
        if i in exclus:
            continue
        a = ifc.args(i)
        nom_type = type_elt.get(i)
        nom_local = None
        if classe == "IfcSpace":
            # Revit : Name = numéro (« 101 »), LongName = usage (« Bureau »). On regroupe par usage.
            nom_long = a[7] if len(a) > 7 and isinstance(a[7], str) and a[7].strip() else None
            nom_court = a[2] if isinstance(a[2], str) else ""
            nom_local = " ".join(x for x in (nom_court, nom_long) if x) or "(local sans nom)"
            base = nom_long or nom_court or "(local sans nom)"
            nom_type = re.sub(r"[\s\-_]*\d+[a-zA-Z]?$", "", base).strip() or base
        if not nom_type:
            objet = a[4] if len(a) > 4 and isinstance(a[4], str) else None
            nom = a[2] if len(a) > 2 and isinstance(a[2], str) else None
            nom_type = objet or (re.sub(r":\d+$", "", nom) if nom else None) or "(sans type)"
        materiau = mat_elt.get(i) or mat_type.get(types_ids.get(i)) or ""
        cle = (classe, nom_type)
        g = groupes.setdefault(cle, {
            "classe": classe, "categorie": categorie, "type": nom_type, "materiaux": Counter(),
            "nombre": 0, "surface": 0.0, "n_surface": 0, "volume": 0.0, "n_volume": 0,
            "longueur": 0.0, "n_longueur": 0, "epaisseurs": [], "niveaux": Counter(), "noms": [], "membres": [],
        })
        g["nombre"] += 1
        g["membres"].append((i, nom_local or (a[2] if isinstance(a[2], str) else "") or nom_type, niveau_elt.get(i, "?")))
        if nom_local:
            g["noms"].append(nom_local)
        if materiau:
            g["materiaux"][materiau] += 1
        g["niveaux"][niveau_elt.get(i, "?")] += 1

        q = {**proprietes[i], **quantites[i]}  # les quantités IFC priment sur les propriétés
        surface = next((q[k] for k in (Q_SURFACE_LOCAL if classe == "IfcSpace" else Q_SURFACE) if k in q), None)
        if surface is None and classe in ("IfcDoor", "IfcWindow") and len(a) > 9:
            h, w = a[8], a[9]
            if isinstance(h, (int, float)) and isinstance(w, (int, float)):
                surface = h * w * fu["LENGTHUNIT"] ** 2
        volume = next((q[k] for k in Q_VOLUME if k in q), None)
        longueur = next((q[k] for k in Q_LONGUEUR if k in q), None)
        epaisseur = next((q[k] for k in Q_EPAISSEUR if k in q), None)
        if surface is not None:
            g["surface"] += surface; g["n_surface"] += 1
        if volume is not None:
            g["volume"] += volume; g["n_volume"] += 1
        if longueur is not None:
            g["longueur"] += longueur; g["n_longueur"] += 1
        if epaisseur is not None and classe in ("IfcWall", "IfcSlab", "IfcCovering", "IfcPlate"):
            g["epaisseurs"].append(epaisseur)

    ordre = ["IfcFooting", "IfcPile", "IfcSlab", "IfcWall", "IfcColumn", "IfcBeam", "IfcRoof", "IfcStair",
             "IfcStairFlight", "IfcRamp", "IfcCurtainWall", "IfcWindow", "IfcDoor", "IfcRailing", "IfcCovering",
             "IfcPlate", "IfcMember", "IfcSanitaryTerminal", "IfcFlowTerminal", "IfcBuildingElementProxy", "IfcSpace"]
    liste = sorted(groupes.values(), key=lambda g: (ordre.index(g["classe"]) if g["classe"] in ordre else 99, g["type"]))
    resultat = []
    for n, g in enumerate(liste, 1):
        ep = g["epaisseurs"]
        resultat.append({
            "id": "G%02d" % n,
            "source": "ifc",
            "genre": "element",
            "classe": g["classe"],
            "categorie": g["categorie"],
            "type": g["type"],
            "materiau": g["materiaux"].most_common(1)[0][0] if g["materiaux"] else "",
            "nombre": g["nombre"],
            "surface": round(g["surface"], 2) if g["n_surface"] else None,
            "volume": round(g["volume"], 3) if g["n_volume"] else None,
            "longueur": round(g["longueur"], 2) if g["n_longueur"] else None,
            "n_surface": g["n_surface"], "n_volume": g["n_volume"], "n_longueur": g["n_longueur"],
            "epaisseur": round(sum(ep) / len(ep), 3) if ep else None,
            "niveaux": dict(g["niveaux"]),
            "detail": ", ".join(sorted(set(g["noms"])))[:200] if g["noms"] else "",
        })
    niveaux = sorted({n for g in resultat for n in g["niveaux"]})
    # Pour la visionneuse : chaque élément mesuré, avec son groupe (clés « _ » : jamais envoyées au navigateur)
    elements_visionneuse = [{"id": i, "g": r["id"], "classe": g["classe"], "niveau": niv, "nom": nom}
                            for g, r in zip(liste, resultat) for i, nom, niv in g["membres"]]
    return {
        "_lecteur": ifc,
        "_facteur_longueur": fu["LENGTHUNIT"],
        "_elements": elements_visionneuse,
        "fichier": nom_fichier,
        "source": "ifc",
        "schema": ifc.schema,
        "nb_entites": len(ifc.brut),
        "nb_elements": sum(g["nombre"] for g in resultat if g["classe"] != "IfcSpace"),
        "nb_locaux": sum(g["nombre"] for g in resultat if g["classe"] == "IfcSpace"),
        "niveaux": niveaux,
        "groupes": resultat,
        "unite_longueur": "mm" if abs(fu["LENGTHUNIT"] - 1e-3) < 1e-9 else ("m" if fu["LENGTHUNIT"] == 1.0 else "%g m" % fu["LENGTHUNIT"]),
        "duree": round(time.time() - t0, 2),
    }


# =============================================================================
# 3. BIBLIOTHÈQUE DE PRIX
# =============================================================================

def lire_bibliotheque(contenu: bytes):
    texte = contenu.decode("utf-8-sig", errors="replace")
    separateur = ";" if texte.count(";") >= texte.count(",") else ","
    lignes = list(csv.DictReader(io.StringIO(texte), delimiter=separateur))
    if not lignes:
        raise ValueError("Bibliothèque de prix vide.")

    def champ(ligne, *noms):
        for cle, v in ligne.items():
            c = (cle or "").strip().lower()
            if any(c.startswith(n) for n in noms):
                return (v or "").strip()
        return ""

    prix = []
    for ligne in lignes:
        code = champ(ligne, "code")
        if not code:
            continue
        pu = champ(ligne, "prix", "pu").replace(" ", "").replace(" ", "").replace("€", "").replace(",", ".")
        try:
            pu = float(pu)
        except ValueError:
            raise ValueError("Prix unitaire illisible pour le code %s : %r" % (code, pu))
        prix.append({"code": code, "lot": champ(ligne, "lot") or "Divers",
                     "designation": champ(ligne, "designation", "désignation", "libell"),
                     "unite": champ(ligne, "unit") or "u", "pu": pu})
    if not prix:
        raise ValueError("Aucune ligne de prix valide (colonnes attendues : code, lot, designation, unite, prix_unitaire_ht).")
    return prix


def nature_unite(unite):
    u = unite.strip().lower().replace("²", "2").replace("³", "3").replace(".", "")
    if u in ("m2", "m 2", "mc", "m carré", "m carre"):
        return "surface"
    if u in ("m3", "m 3", "m cube"):
        return "volume"
    if u in ("ml", "m", "mètre linéaire", "metre lineaire", "ml "):
        return "longueur"
    if u in ("u", "un", "unité", "unite", "pce", "pièce", "piece", "nb", "ens", "ensemble"):
        return "nombre"
    if u in ("ft", "forfait", "fft"):
        return "forfait"
    return None


PARAMS_DEFAUT = {"hauteur": 2.70, "double_trait": True}
NATURES_PLAN = ("mur", "surface_sol", "lineaire", "element_vertical", "comptage", "ignorer")
LIBELLES_NATURE = {"mur": "mur", "surface_sol": "surface de sol", "lineaire": "linéaire",
                   "element_vertical": "élément vertical", "comptage": "comptage", "ignorer": "hors chiffrage"}


def fr(v, d=2):
    return ("%.*f" % (d, v)).replace(".", ",")


def quantite_pour(groupe, unite, nature_plan=None, params=None):
    """Quantité d'un groupe dans l'unité d'un prix.
    Renvoie (quantité ou None, remarque à vérifier, hypothèse de calcul)."""
    if groupe.get("source") == "dxf":
        return quantite_plan(groupe, unite, nature_plan, params or PARAMS_DEFAUT)
    nature = nature_unite(unite)
    if nature == "nombre":
        return float(groupe["nombre"]), "", ""
    if nature == "forfait":
        return 1.0, "", "Forfait : quantité fixée à 1"
    if nature in ("surface", "volume", "longueur"):
        v = groupe[nature]
        n_connu = groupe["n_" + nature]
        if v is None:
            return None, "%s absente de la maquette : quantité à saisir" % nature.capitalize(), ""
        if n_connu < groupe["nombre"]:
            return v, "%s connue pour %d élément(s) sur %d : à compléter" % (nature.capitalize(), n_connu, groupe["nombre"]), ""
        return v, "", ""
    return None, "Unité « %s » non déductible de la maquette : quantité à saisir" % unite, ""


def quantite_plan(g, unite, nature_plan, params):
    """Règles de métré pour un plan 2D. L'IA a seulement choisi la nature (mur, sol...) ;
    les formules ci-dessous sont fixes et affichées comme hypothèses."""
    u = nature_unite(unite)
    h = float(params.get("hauteur") or PARAMS_DEFAUT["hauteur"])
    if u == "forfait":
        return 1.0, "", "Forfait : quantité fixée à 1"
    if g["genre"] == "bloc":
        if u == "nombre":
            return float(g["nombre"]), "", "Nombre d'insertions du bloc « %s »" % g["type"]
        return None, "Un bloc ne donne qu'un nombre : %s à saisir (nomenclature, CCTP...)" % unite, ""
    if g["genre"] == "local":
        if u == "surface":
            return g["surface"], "", "Surface des contours des locaux « %s »" % g["type"]
        if u == "nombre":
            return float(g["nombre"]), "", ""
        return None, "Unité « %s » non déductible des contours de locaux : à saisir" % unite, ""
    # Calque
    L_dev, S = g["longueur"] or 0.0, g["surface"] or 0.0
    if nature_plan == "mur":
        double = params.get("double_trait", True)
        lm = L_dev / 2 if double else L_dev
        base = ("longueur de mur = longueur dessinée %s m ÷ 2 (double trait) = %s m" % (fr(L_dev), fr(lm)) if double
                else "longueur de mur = longueur dessinée %s m (simple trait)" % fr(L_dev))
        if u == "surface":
            return round(lm * h, 2), "", base + " ; × hauteur %s m ; ouvertures non déduites" % fr(h)
        if u == "longueur":
            return round(lm, 2), "", base
        if u == "volume":
            if S:
                return round(S * h, 3), "", "surface en plan %s m² × hauteur %s m" % (fr(S), fr(h))
            return None, "Volume non déductible (ni hachure ni contour fermé) : à saisir", base
    elif nature_plan == "surface_sol":
        if u == "surface" and S:
            return S, "", "Surface des contours fermés et hachures du calque"
    elif nature_plan == "lineaire":
        if u == "longueur" and L_dev:
            return round(L_dev, 2), "", "Longueur dessinée du calque"
    elif nature_plan == "element_vertical":
        if u == "volume" and S:
            return round(S * h, 3), "", "surface en plan %s m² × hauteur %s m" % (fr(S), fr(h))
        if u == "nombre" and g["nombre"]:
            return float(g["nombre"]), "", "Nombre de contours fermés"
        if u == "longueur" and g["nombre"]:
            return round(g["nombre"] * h, 2), "", "%d élément(s) × hauteur %s m" % (g["nombre"], fr(h))
    elif nature_plan == "comptage":
        if u == "nombre" and g["nombre"]:
            return float(g["nombre"]), "", "Nombre de contours fermés"
    return None, "Quantité en %s non déductible de ce calque : à saisir" % unite, ""


# =============================================================================
# 4. APPELS À L'API DEEPSEEK
# =============================================================================

class ErreurAPI(Exception):
    pass


def appel_api(cle, chemin, corps=None, delai=420):
    req = urllib.request.Request(
        URL_API + chemin,
        data=json.dumps(corps).encode("utf-8") if corps is not None else None,
        headers={"Authorization": "Bearer " + cle, "Content-Type": "application/json"},
        method="POST" if corps is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=delai) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        messages = {401: "Clé API refusée (401). Vérifiez qu'elle est complète et active.",
                    402: "Solde insuffisant sur le compte DeepSeek (402). Rechargez le compte sur platform.deepseek.com.",
                    429: "Trop de requêtes (429). Patientez quelques secondes puis réessayez."}
        raise ErreurAPI(messages.get(e.code, "Erreur API %d : %s" % (e.code, detail)), e.code, detail)
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e.reason):
            # Cas classique sur Mac avec le Python de python.org : les certificats ne sont pas installés.
            raise ErreurAPI("Python ne trouve pas les certificats de sécurité pour joindre DeepSeek. Sur Mac, ouvrez le dossier "
                            "Applications > Python 3.x et double-cliquez sur « Install Certificates.command », puis relancez.", 0, "")
        raise ErreurAPI("Impossible de joindre %s (%s). Vérifiez votre connexion Internet." % (URL_API, e.reason), 0, "")
    except TimeoutError:
        raise ErreurAPI("DeepSeek n'a pas répondu à temps. Réessayez, ou décochez la réflexion approfondie.", 0, "")
    except (ConnectionError, OSError) as e:
        raise ErreurAPI("La connexion à DeepSeek a été interrompue (%s). Réessayez." % e, 0, "")


def verifier_cle(cle):
    rep = appel_api(cle, "/models", delai=30)
    return [m.get("id") for m in rep.get("data", [])]


Reponse = namedtuple("Reponse", "texte entree sortie cache fin")

SYSTEME = ("Tu es économiste de la construction (métreur) et tu travailles en français. "
           "Tu t'appuies uniquement sur les documents fournis et tu n'inventes jamais ni quantité ni prix.")


def discuter(cle, modele, messages, json_attendu=False, reflexion=False, max_jetons=None):
    """Appel au modèle. Par défaut, DeepSeek active son « mode réflexion » (lent) :
    on le désactive sauf demande explicite, ce qui divise nettement le temps de réponse."""
    corps = {"model": modele, "messages": messages, "stream": False,
             "thinking": {"type": "enabled" if reflexion else "disabled"}}
    if not reflexion:
        corps["temperature"] = 0.1 if json_attendu else 0.4
    if json_attendu:
        corps["response_format"] = {"type": "json_object"}
    if max_jetons:
        corps["max_tokens"] = max_jetons
    # Si le modèle refuse un paramètre facultatif (erreur 400), on le retire et on réessaie,
    # un paramètre à la fois : d'abord celui cité dans le message d'erreur, sinon dans cet ordre.
    for _ in range(5):
        try:
            rep = appel_api(cle, "/chat/completions", corps)
            break
        except ErreurAPI as e:
            facultatifs = [k for k in ("thinking", "max_tokens", "temperature", "response_format") if k in corps]
            if not (len(e.args) > 1 and e.args[1] == 400 and facultatifs):
                raise
            cite = [k for k in facultatifs if k in str(e.args[2]).lower()]
            corps.pop((cite or facultatifs)[0])
    choix = (rep.get("choices") or [{}])[0]
    usage = rep.get("usage") or {}
    return Reponse((choix.get("message") or {}).get("content") or "", usage.get("prompt_tokens", 0),
                   usage.get("completion_tokens", 0), usage.get("prompt_cache_hit_tokens", 0),
                   choix.get("finish_reason") or "")


def lire_json(texte):
    texte = re.sub(r"^```(?:json)?|```$", "", texte.strip(), flags=re.M).strip()
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        debut, fin = texte.find("{"), texte.rfind("}")
        if debut >= 0 and fin > debut:
            return json.loads(texte[debut:fin + 1])
        raise


# Toutes les demandes commencent par le même préfixe (CCTP puis bibliothèque de prix) et les consignes
# viennent APRÈS : DeepSeek met ce préfixe en cache et facture les appels suivants beaucoup moins cher.
def prefixe_commun(cctp, biblio_txt):
    return entete_cctp(cctp) + "BIBLIOTHÈQUE DE PRIX (code | lot | désignation | unité)\n" + biblio_txt + "\n\n"


def message_ia(prefixe, consigne, donnees):
    return [{"role": "system", "content": SYSTEME},
            {"role": "user", "content": prefixe + "=== CONSIGNES ===\n" + consigne + "\n\n=== DONNÉES ===\n" + donnees}]


CONSIGNE_CORRESPONDANCE = """On te fournit des groupes d'éléments issus d'une maquette BIM (IFC) ou d'un plan 2D (DXF/DWG).
La bibliothèque de prix figure plus haut. Pour chaque groupe, choisis la ou les lignes de la bibliothèque
qui décrivent l'ouvrage à chiffrer.

Règles :
- Utilise UNIQUEMENT des codes présents dans la bibliothèque. N'invente jamais de code ni de prix.
- Tu ne calcules aucune quantité : le métré est fait par ailleurs.
- Un groupe peut nécessiter plusieurs lignes (3 au maximum) quand l'ouvrage en combine plusieurs,
  par exemple une dalle de toiture = plancher béton + étanchéité. N'ajoute pas de ligne sans lien clair.
- Choisis des lignes dont l'unité est cohérente avec les quantités disponibles du groupe.
- Si aucune ligne ne convient, renvoie une liste vide et explique pourquoi dans "remarque".
- Confiance : "haute" si type, matériau et dimensions concordent clairement ; "moyenne" si c'est plausible
  mais qu'une caractéristique diffère ou manque ; "faible" sinon.
- Justification : une phrase courte, factuelle.
- Réponds pour CHAQUE groupe de la liste, sans en oublier, dans le même ordre.

Groupes « Local » (IfcSpace dans une maquette, ou locaux d'un plan 2D) : ce sont des pièces. Leur surface est la
surface de plancher. Associe-les aux revêtements de sol et aux plafonds qui s'y appliquent (d'après le CCTP s'il est
fourni, sinon d'après l'usage du local), jamais aux ouvrages de structure, de cloisons ou de menuiseries.

Groupes issus d'un plan 2D (identifiants D01, D02...) : ce sont des calques, des blocs ou des locaux.
Déduis ce qu'ils représentent de leur nom (conventions de calques type A-MUR, A-CLOISON, blocs P83, F120...).
Pour chacun, indique en plus "nature", parmi :
  "mur" (murs, cloisons, doublages dessinés en traits), "surface_sol" (sols, dalles, locaux, hachures de surface),
  "lineaire" (éléments au mètre : garde-corps, plinthes...), "element_vertical" (poteaux et éléments dessinés
  en contour dont on chiffre la hauteur), "comptage" (objets à compter), "ignorer" (cotations, textes,
  mobilier, habillage graphique, tout ce qui ne se chiffre pas dans un marché de travaux).
Si "ignorer", renvoie une liste d'ouvrages vide et une remarque courte.
Pour les groupes de maquette IFC (G01, G02...), mets "nature": "".

Réponds uniquement avec un objet JSON de la forme :
{"correspondances": [{"groupe": "G01", "nature": "", "ouvrages": [{"code": "GO-01", "confiance": "haute", "justification": "..."}], "remarque": ""}]}"""

CONSIGNE_CCTP = """

Un CCTP du projet est fourni au début du message. Il est contractuel : en cas de contradiction avec la maquette
ou le plan, c'est le CCTP qui prévaut pour choisir la ligne de prix.
- Pour chaque ouvrage retenu, ajoute "article_cctp" (numéro de l'article qui le décrit, ex. "7.3", ou "" si aucun)
  et "extrait_cctp" (quelques mots recopiés de l'article, 20 mots au plus).
- Si la maquette ou le plan contredit le CCTP (matériau, épaisseur, type, performance...), décris l'écart en une phrase
  dans "ecart_cctp" au niveau du groupe (sinon ""). Choisis alors la ligne qui correspond au CCTP si elle existe,
  avec une confiance "moyenne" au plus.
- La confiance "haute" suppose que le CCTP confirme l'ouvrage.
- Si le CCTP donne une quantité (nombre, longueur...) utile pour un élément dont la quantité manque, signale-la
  dans "remarque" sans la reporter ailleurs : la saisie reste à l'économiste.
Format d'un groupe avec CCTP :
{"groupe": "G01", "nature": "", "ecart_cctp": "", "ouvrages": [{"code": "GO-01", "confiance": "haute", "justification": "...", "article_cctp": "2.3", "extrait_cctp": "..."}], "remarque": ""}"""

CONSIGNE_ABSENTS = """Le CCTP du projet et la bibliothèque de prix figurent plus haut ; les éléments trouvés dans la maquette
ou le plan sont listés dans les données. Liste les ouvrages décrits au CCTP qui n'ont AUCUN élément correspondant
dans la maquette ou le plan, et qui devraient donc être chiffrés à part (fondations non modélisées, étanchéité des relevés,
faux plafonds, équipements...). Ignore les généralités et les prestations hors travaux.
Pour chacun : "lot", "article" (numéro), "ouvrage" (courte description), "code" (ligne de la bibliothèque qui conviendrait,
ou "" s'il n'y en a pas), "info_quantite" (quantité indiquée au CCTP, recopiée telle quelle, ou ""), "remarque" (courte).
N'invente aucune quantité. Réponds uniquement en JSON : {"absents": [{"lot": "", "article": "", "ouvrage": "", "code": "", "info_quantite": "", "remarque": ""}]}"""

CONSIGNE_QUESTION = """Réponds à la question de l'utilisateur en français, de façon concise et concrète.
Tu disposes, dans les données, du métré de la maquette ou du plan et de l'estimation calculée ; la bibliothèque
de prix (et le CCTP s'il y en a un) figure plus haut. Appuie-toi sur ces chiffres, montre tes calculs quand tu en fais,
et signale clairement toute hypothèse ou tout prix qui ne vient pas de la bibliothèque.
Quand tu t'appuies sur le CCTP, cite le numéro d'article.%s"""


def decrire_groupe(g, avec_id=True):
    ident = (g["id"] + " | ") if avec_id else ""
    if g.get("source") == "dxf":
        if g["genre"] == "bloc":
            return "%sBloc « %s » | %s | %d insertion(s) | quantités disponibles : nombre" % (
                ident, g["type"], g["detail"], g["nombre"])
        if g["genre"] == "local":
            return "%sLocal « %s » (calque %s) | %d contour(s) : %s | surface %s m² | quantités disponibles : nombre, surface" % (
                ident, g["type"], g["calque"], g["nombre"], g["detail"], fr(g["surface"]))
        dispo = []
        if g["longueur"]:
            dispo.append("longueur dessinée %s m" % fr(g["longueur"]))
        if g["surface"]:
            dispo.append("surface en plan %s m²" % fr(g["surface"]))
        return "%sCalque « %s » | %s | %s" % (ident, g["type"], g["detail"], ", ".join(dispo) or "aucune mesure")
    dispo = ["nombre"]
    for k in ("surface", "volume", "longueur"):
        if g[k] is not None:
            dispo.append(k)
    morceaux = ["%s%s (%s) | type « %s »" % (ident, g["categorie"], g["classe"], g["type"])]
    if g["materiau"]:
        morceaux.append("matériau « %s »" % g["materiau"])
    if g["epaisseur"]:
        morceaux.append("épaisseur %.2f m" % g["epaisseur"])
    morceaux.append("%d élément(s)" % g["nombre"])
    if g.get("detail"):
        morceaux.append("locaux : " + g["detail"])
    morceaux.append("quantités disponibles : " + ", ".join(dispo))
    return " | ".join(morceaux)


def libelle_groupe(g):
    if g.get("source") == "dxf":
        nom = {"bloc": "Bloc", "local": "Locaux", "calque": "Calque"}[g["genre"]]
        return "%s « %s »" % (nom, g["type"]) + (" (%d)" % g["nombre"] if g["genre"] != "calque" else "")
    return "%s « %s » (%d)" % (g["categorie"], g["type"], g["nombre"])


def entete_cctp(cctp):
    return "CCTP DU PROJET (fichier « %s »)\n<<<\n%s\n>>>\n\n" % (cctp["fichier"], cctp["texte"]) if cctp else ""


def lire_absents(texte, codes):
    absents = []
    for a in lire_json(texte).get("absents", []):
        if isinstance(a, dict) and a.get("ouvrage"):
            code = str(a.get("code") or "").strip()
            absents.append({
                "lot": str(a.get("lot") or ""), "article": str(a.get("article") or ""),
                "ouvrage": str(a["ouvrage"]), "code": code if code in codes else "",
                "designation": codes[code]["designation"] if code in codes else "",
                "unite": codes[code]["unite"] if code in codes else "",
                "info_quantite": str(a.get("info_quantite") or ""), "remarque": str(a.get("remarque") or ""),
            })
    return absents


# -----------------------------------------------------------------------------
# Mémoire des correspondances : une réponse de l'IA pour un groupe donné (même description, même
# bibliothèque, même CCTP, même modèle) est réutilisée au lieu d'être redemandée. Changer la hauteur
# des murs ou relancer le calcul devient instantané et gratuit. Le fichier ne contient aucune clé API.
# -----------------------------------------------------------------------------

VERSION_CONSIGNES = "v4.1"


class Memoire:
    TAILLE_MAX = 5000

    def __init__(self, chemin):
        self.chemin = chemin
        self.verrou = threading.Lock()
        self.donnees = None

    def _charger(self):
        if self.donnees is None:
            try:
                with open(self.chemin, encoding="utf-8") as f:
                    d = json.load(f)
                self.donnees = d if isinstance(d, dict) and d.get("version") == VERSION_CONSIGNES else {}
            except (OSError, ValueError):
                self.donnees = {}
            self.donnees.setdefault("version", VERSION_CONSIGNES)
            self.donnees.setdefault("entrees", {})
        return self.donnees["entrees"]

    def lire(self, cle):
        with self.verrou:
            return self._charger().get(cle)

    def ecrire(self, valeurs):
        if not valeurs:
            return
        with self.verrou:
            entrees = self._charger()
            entrees.update(valeurs)
            while len(entrees) > self.TAILLE_MAX:
                entrees.pop(next(iter(entrees)))
            try:
                temporaire = self.chemin + ".tmp"
                with open(temporaire, "w", encoding="utf-8") as f:
                    json.dump(self.donnees, f, ensure_ascii=False)
                os.replace(temporaire, self.chemin)
            except OSError:
                pass  # dossier en lecture seule : la mémoire reste valable pour la session

    def vider(self):
        with self.verrou:
            self.donnees = {"version": VERSION_CONSIGNES, "entrees": {}}
            try:
                os.remove(self.chemin)
            except OSError:
                pass

    def taille(self):
        with self.verrou:
            return len(self._charger())


# Emplacement modifiable (variable CHIFFRAGE_MEMOIRE) : les tests utilisent un fichier temporaire
# et ne touchent jamais à la mémoire réelle de l'utilisateur.
MEMOIRE = Memoire(os.environ.get("CHIFFRAGE_MEMOIRE") or os.path.join(DOSSIER, "memoire_correspondances.json"))


def empreinte(*morceaux):
    return hashlib.sha1("\x1f".join(str(m) for m in morceaux).encode("utf-8")).hexdigest()


def associer_prix(cle, modele, metre, bibliotheque, params=None, cctp=None, reflexion=False, progres=None,
                  forcer=False, taille_lot=20):
    """Demande à l'IA les correspondances manquantes (en parallèle, par paquets), puis calcule l'estimation."""
    progres = progres or (lambda texte: None)
    codes = {p["code"]: p for p in bibliotheque}
    biblio_txt = "\n".join("%s | %s | %s | %s" % (p["code"], p["lot"], p["designation"], p["unite"]) for p in bibliotheque)
    prefixe = prefixe_commun(cctp, biblio_txt)
    groupes = metre["groupes"]
    origine = "DU PLAN 2D" if metre.get("source") == "dxf" else "DE LA MAQUETTE"
    consigne = CONSIGNE_CORRESPONDANCE + (CONSIGNE_CCTP if cctp else "")
    max_jetons = 32000 if reflexion else 8000
    contexte_memoire = (VERSION_CONSIGNES, modele, reflexion, empreinte(cctp["texte"]) if cctp else "", empreinte(biblio_txt))
    cles = {g["id"]: empreinte(*contexte_memoire, decrire_groupe(g, avec_id=False)) for g in groupes}
    presents = "\n".join(decrire_groupe(g) for g in groupes)
    cle_absents = empreinte(*contexte_memoire, "absents", presents) if cctp else None

    correspondances, absents = {}, None
    if not forcer:
        for g in groupes:
            c = MEMOIRE.lire(cles[g["id"]])
            if c:
                correspondances[g["id"]] = {**c, "groupe": g["id"]}
        if cle_absents:
            absents = MEMOIRE.lire(cle_absents)
    depuis_memoire = len(correspondances)
    a_demander = [g for g in groupes if g["id"] not in correspondances]

    stats = {"appels": 0, "entree": 0, "sortie": 0, "cache": 0, "decoupages": 0, "relances": 0}
    verrou_stats = threading.Lock()

    def compter(rep):
        with verrou_stats:
            stats["appels"] += 1
            stats["entree"] += rep.entree
            stats["sortie"] += rep.sortie
            stats["cache"] += rep.cache

    def demander_paquet(paquet, profondeur=0):
        """Un appel pour un paquet de groupes. Si la réponse est tronquée ou illisible, le paquet est
        coupé en deux et redemandé (jusqu'à 4 fois), au lieu de faire échouer tout le chiffrage."""
        donnees_txt = "GROUPES D'ÉLÉMENTS " + origine + "\n" + "\n".join(decrire_groupe(g) for g in paquet)
        rep = discuter(cle, modele, message_ia(prefixe, consigne, donnees_txt), True, reflexion, max_jetons)
        compter(rep)
        donnees = None
        if rep.fin != "length":
            try:
                donnees = lire_json(rep.texte)
            except ValueError:
                donnees = None
        if not isinstance(donnees, dict):
            if len(paquet) > 1 and profondeur < 4:
                with verrou_stats:
                    stats["decoupages"] += 1
                milieu = len(paquet) // 2
                resultat = demander_paquet(paquet[:milieu], profondeur + 1)
                resultat.update(demander_paquet(paquet[milieu:], profondeur + 1))
                return resultat
            return {}
        ids = {g["id"] for g in paquet}
        return {str(c["groupe"]).strip(): c for c in donnees.get("correspondances", [])
                if isinstance(c, dict) and str(c.get("groupe", "")).strip() in ids}

    def demander_absents():
        rep = discuter(cle, modele, message_ia(prefixe, CONSIGNE_ABSENTS, "ÉLÉMENTS PRÉSENTS DANS " +
                                                origine.replace("DU ", "LE ").replace("DE LA ", "LA ") + "\n" + presents),
                       True, reflexion, max_jetons)
        compter(rep)
        try:
            return lire_absents(rep.texte, codes)
        except ValueError:
            return [{"lot": "", "article": "", "ouvrage": "Analyse des ouvrages absents illisible : relancez la correspondance.",
                     "code": "", "designation": "", "unite": "", "info_quantite": "", "remarque": ""}]

    taches = [("paquet", a_demander[i:i + taille_lot]) for i in range(0, len(a_demander), taille_lot)]
    if cctp and absents is None:
        taches.append(("absents", None))
    nouvelles = {}

    def memoriser():
        MEMOIRE.ecrire({cles[i]: {k: v for k, v in c.items() if k != "groupe"} for i, c in nouvelles.items()})

    try:
        if taches:
            progres("%d groupe(s) repris de la mémoire, %d à demander à DeepSeek (%d demande(s))…" % (
                depuis_memoire, len(a_demander), len(taches)) if depuis_memoire else
                "Envoi de %d demande(s) à DeepSeek%s…" % (len(taches), " (dont l'analyse du CCTP)" if cctp and absents is None else ""))
            # Avec un gros CCTP, un premier appel seul amorce le cache de DeepSeek ; les suivants,
            # lancés ensuite en parallèle, relisent le préfixe commun à tarif réduit.
            amorce = []
            if cctp and len(taches) >= 3 and len(cctp["texte"]) > 20000:
                amorce, taches = taches[:1], taches[1:]
            faits, total = 0, len(amorce) + len(taches)
            for vague in (amorce, taches):
                if not vague:
                    continue
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futurs = {pool.submit(demander_paquet if genre == "paquet" else demander_absents,
                                          *([contenu] if genre == "paquet" else [])): genre for genre, contenu in vague}
                    for futur in as_completed(futurs):
                        resultat = futur.result()  # une erreur d'API remonte ici (après mémorisation du reste)
                        faits += 1
                        if futurs[futur] == "absents":
                            absents = resultat
                            MEMOIRE.ecrire({cle_absents: absents})
                        else:
                            nouvelles.update(resultat)
                        progres("Réponses de DeepSeek : %d/%d%s…" % (
                            faits, total, " (analyse du CCTP comprise)" if cctp and futurs[futur] == "absents" else ""))
            # Groupes oubliés par l'IA : une seule relance, groupée
            oublies = [g for g in a_demander if g["id"] not in nouvelles]
            if oublies:
                progres("Relance de %d groupe(s) oublié(s) par l'IA…" % len(oublies))
                stats["relances"] = len(oublies)
                nouvelles.update(demander_paquet(oublies))
    finally:
        memoriser()
    correspondances.update(nouvelles)

    progres("Calcul de l'estimation…")
    est = calculer_estimation(metre, bibliotheque, correspondances, absents or [], params, cctp)
    est.update({
        "appels": stats["appels"], "jetons_entree": stats["entree"], "jetons_sortie": stats["sortie"],
        "jetons_cache": stats["cache"], "depuis_memoire": depuis_memoire, "nb_groupes": len(groupes),
        "decoupages": stats["decoupages"], "relances": stats["relances"],
    })
    return est


def calculer_estimation(metre, bibliotheque, correspondances, absents, params=None, cctp=None):
    """Partie déterministe : quantités du métré × prix de la bibliothèque, selon les correspondances."""
    params = {**PARAMS_DEFAUT, **(params or {})}
    codes = {p["code"]: p for p in bibliotheque}
    groupes = metre["groupes"]
    lignes, non_chiffres, ignores = {}, [], []
    natures = {}
    for g in groupes:
        c = correspondances.get(g["id"])
        if c is None:
            non_chiffres.append({"groupe": g["id"], "element": libelle_groupe(g), "nombre": g["nombre"],
                                 "raison": "L'IA n'a pas renvoyé de correspondance pour ce groupe, même après relance."})
            continue
        nature = str(c.get("nature") or "").strip().lower()
        nature = nature if nature in NATURES_PLAN else ""
        natures[g["id"]] = nature
        ouvrages = [o for o in (c.get("ouvrages") or []) if isinstance(o, dict)]
        valides = [o for o in ouvrages if str(o.get("code", "")).strip() in codes][:3]
        inconnus = [str(o.get("code")) for o in ouvrages if str(o.get("code", "")).strip() not in codes]
        if not valides:
            if nature == "ignorer":
                ignores.append({"groupe": g["id"], "element": libelle_groupe(g),
                                "raison": c.get("remarque") or "Hors chiffrage"})
                continue
            raison = c.get("remarque") or ("Codes inconnus proposés : " + ", ".join(inconnus) if inconnus else
                                          "Aucune ligne de prix proposée par l'IA")
            non_chiffres.append({"groupe": g["id"], "element": libelle_groupe(g), "nombre": g["nombre"], "raison": raison})
            continue
        for o in valides:
            p = codes[str(o["code"]).strip()]
            qte, remarque, hypothese = quantite_pour(g, p["unite"], nature, params)
            conf = str(o.get("confiance", "moyenne")).lower()
            conf = conf if conf in ("haute", "moyenne", "faible") else "moyenne"
            ligne = lignes.setdefault(p["code"], {
                "code": p["code"], "lot": p["lot"], "designation": p["designation"], "unite": p["unite"],
                "pu": p["pu"], "quantite": 0.0, "qte_manquante": False, "groupes": [], "elements": [],
                "confiance": "haute", "justifications": [], "remarques": [], "hypotheses": [],
                "articles": [], "ecarts": [],
            })
            article = str(o.get("article_cctp") or "").strip()
            if cctp and article:
                extrait = " ".join(str(o.get("extrait_cctp") or "").split()[:25])
                if not any(a["numero"] == article for a in ligne["articles"]):
                    ligne["articles"].append({"numero": article, "extrait": extrait})
            ecart = str(c.get("ecart_cctp") or "").strip()
            if cctp and ecart and ecart not in ligne["ecarts"]:
                ligne["ecarts"].append(ecart)
            if qte is None:
                ligne["qte_manquante"] = True
            else:
                ligne["quantite"] += qte
            ligne["groupes"].append(g["id"])
            ligne["elements"].append(libelle_groupe(g) + (" — " + LIBELLES_NATURE.get(nature, nature) if nature else ""))
            rang = {"haute": 0, "moyenne": 1, "faible": 2}
            if rang[conf] > rang[ligne["confiance"]]:
                ligne["confiance"] = conf
            if o.get("justification"):
                ligne["justifications"].append(str(o["justification"]).strip())
            if remarque:
                ligne["remarques"].append(remarque)
            info_ia = str(c.get("remarque") or "").strip()
            if info_ia:
                ligne["remarques"].append("Note de l'IA : " + info_ia)
            if hypothese:
                ligne["hypotheses"].append(hypothese)

    resultat = []
    for l in lignes.values():
        l["quantite"] = round(l["quantite"], 3)
        l["total"] = round(l["quantite"] * l["pu"], 2)
        l["justification"] = " ".join(dict.fromkeys(l.pop("justifications")))
        l["remarque"] = " ; ".join(dict.fromkeys(l.pop("remarques")))
        l["hypothese"] = " ; ".join(dict.fromkeys(l.pop("hypotheses")))
        l["a_verifier"] = l["confiance"] != "haute" or l["qte_manquante"] or bool(l["remarque"]) or bool(l["ecarts"])
        resultat.append(l)
    resultat.sort(key=lambda l: (l["lot"], l["code"]))
    return {
        "source": metre.get("source", "ifc"),
        "cctp": {k: cctp[k] for k in ("fichier", "nb_pages", "nb_articles", "tronque")} if cctp else None,
        "absents": absents,
        "lignes": resultat,
        "non_chiffres": non_chiffres,
        "ignores": ignores,
        "natures": natures,
        "params": params if metre.get("source") == "dxf" else None,
        "total_ht": round(sum(l["total"] for l in resultat), 2),
        "tva": 0.20,
    }


def repondre_question(cle, modele, question, metre, estimation, bibliotheque, cctp=None, prix_fictifs=False, reflexion=False):
    biblio_txt = "\n".join("%s | %s | %s | %s" % (p["code"], p["lot"], p["designation"], p["unite"]) for p in bibliotheque)
    contexte = {
        "maquette_ou_plan": metre["fichier"],
        "source": metre.get("source"),
        "prix_unitaires": {p["code"]: p["pu"] for p in bibliotheque},
        "metre": [{k: g.get(k) for k in ("id", "categorie", "type", "materiau", "nombre", "surface", "volume", "longueur")}
                  for g in metre["groupes"]],
        "estimation": [{k: l.get(k) for k in ("lot", "code", "designation", "unite", "quantite", "pu", "total", "confiance", "remarque", "hypothese")}
                       for l in estimation["lignes"]],
        "non_chiffres": estimation["non_chiffres"],
        "hors_chiffrage": estimation.get("ignores", []),
        "total_ht": estimation["total_ht"],
    }
    if estimation.get("absents"):
        contexte["ouvrages_du_cctp_absents_de_la_maquette"] = estimation["absents"]
    consigne = CONSIGNE_QUESTION % (" Les prix de la bibliothèque sont fictifs (démonstration) : rappelle-le si tu donnes un montant."
                                    if prix_fictifs else "")
    rep = discuter(cle, modele, message_ia(prefixe_commun(cctp, biblio_txt), consigne,
                                           "CONTEXTE (JSON) :\n" + json.dumps(contexte, ensure_ascii=False) + "\n\nQUESTION : " + question),
                   reflexion=reflexion, max_jetons=32000 if reflexion else 4000)
    return {"reponse": rep.texte.strip(), "jetons_entree": rep.entree, "jetons_sortie": rep.sortie, "jetons_cache": rep.cache}


# =============================================================================
# 5. EXPORT EXCEL
# =============================================================================

# Formules écrites par le programme lui-même (prix unitaire, totaux, TVA). Tout autre texte commençant par
# « = » vient d'un fichier (nom d'objet IFC, calque, CCTP, bibliothèque) ou de l'IA : on le force en texte,
# sinon Excel l'exécuterait à l'ouverture (« injection de formule »).
FORMULES_AUTORISEES = re.compile(
    r"^=(IFERROR\(INDEX\('Bibliothèque de prix'!\$E\$2:\$E\$\d+,MATCH\(B\d+,'Bibliothèque de prix'!\$A\$2:\$A\$\d+,0\)\),0\)"
    r"|F\d+\*G\d+|SUM\(H\d+:H\d+\)|H\d+(\+H\d+)*|H\d+\*H\d+)$")


def neutraliser_formules(wb):
    for ws in wb.worksheets:
        for ligne in ws.iter_rows():
            for c in ligne:
                if isinstance(c.value, str) and c.value.startswith("=") and not FORMULES_AUTORISEES.match(c.value):
                    c.value = c.value  # réaffectation puis type forcé en texte
                    c.data_type = "s"


def exporter_excel(metre, estimation, bibliotheque, modele):
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    POLICE = "Arial"
    bleu = Font(name=POLICE, color="0000FF")
    vert = Font(name=POLICE, color="008000")
    normal = Font(name=POLICE)
    gras = Font(name=POLICE, bold=True)
    entete_font = Font(name=POLICE, bold=True, color="FFFFFF")
    entete_fill = PatternFill("solid", fgColor="1F3A5F")
    lot_fill = PatternFill("solid", fgColor="DCE6F0")
    total_fill = PatternFill("solid", fgColor="F2F2F2")
    jaune = PatternFill("solid", fgColor="FFFF00")
    conf_fill = {"haute": PatternFill("solid", fgColor="D9EAD3"), "moyenne": PatternFill("solid", fgColor="FCE5CD"),
                 "faible": PatternFill("solid", fgColor="F4CCCC")}
    fin = Side(style="thin", color="BFBFBF")
    bord = Border(left=fin, right=fin, top=fin, bottom=fin)
    EUR = '#,##0.00 "€";-#,##0.00 "€";"-"'
    QTE = '#,##0.00;-#,##0.00;"-"'
    haut = Alignment(vertical="top", wrap_text=True)

    wb = Workbook()

    # ---- Onglet Bibliothèque de prix (source des prix unitaires) ----
    wbib = wb.active
    wbib.title = "Bibliothèque de prix"
    en_tetes = ["Code", "Lot", "Désignation", "Unité", "PU HT (€)"]
    wbib.append(en_tetes)
    for p in bibliotheque:
        wbib.append([p["code"], p["lot"], p["designation"], p["unite"], p["pu"]])
    for c in wbib[1]:
        c.font, c.fill, c.border = entete_font, entete_fill, bord
    for ligne in wbib.iter_rows(min_row=2):
        for c in ligne:
            c.font, c.border = normal, bord
        ligne[4].font, ligne[4].number_format = bleu, EUR
    for col, larg in zip("ABCDE", (10, 30, 60, 8, 14)):
        wbib.column_dimensions[col].width = larg
    wbib.freeze_panes = "A2"
    n_bib = len(bibliotheque) + 1
    wbib.cell(row=n_bib + 2, column=1, value=("Prix fictifs, à usage de démonstration uniquement. " if estimation.get("prix_fictifs") else "")
              + "Modifiez les prix en bleu : l'estimation se met à jour automatiquement.").font = Font(name=POLICE, italic=True, color="7F7F7F")

    # ---- Onglet Estimation ----
    ws = wb.create_sheet("Estimation", 0)
    ws["A1"] = "Estimation sommaire — " + metre["fichier"]
    ws["A1"].font = Font(name=POLICE, bold=True, size=14)
    ws["A2"] = ("Générée le %s par le POC Chiffrage IA (correspondance des prix : %s). "
                "Métré calculé par le code ; %s.%s" % (
                    datetime.now().strftime("%d/%m/%Y %H:%M"), modele,
                    "prix de démonstration fictifs" if estimation.get("prix_fictifs") else "prix issus de la bibliothèque fournie",
                    (" CCTP pris en compte : %s." % estimation["cctp"]["fichier"]) if estimation.get("cctp") else ""))
    ws["A2"].font = Font(name=POLICE, italic=True, color="7F7F7F")
    ws["A3"] = ("Légende : texte bleu = valeur modifiable ; texte vert = prix lu dans l'onglet « Bibliothèque de prix » ; "
                "fond jaune = quantité à saisir ; colonne « Confiance IA » : fiabilité de la correspondance proposée.")
    ws["A3"].font = Font(name=POLICE, size=9, color="595959")

    cols = ["Lot", "Code", "Désignation", "Éléments sources", "Unité", "Quantité", "PU HT (€)", "Total HT (€)",
            "Confiance IA", "Justification IA, articles du CCTP, points à vérifier"]
    r = 5
    for j, t in enumerate(cols, 1):
        c = ws.cell(row=r, column=j, value=t)
        c.font, c.fill, c.border = entete_font, entete_fill, bord
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A6"
    r += 1

    sous_totaux = []
    par_lot = defaultdict(list)
    for l in estimation["lignes"]:
        par_lot[l["lot"]].append(l)
    for lot in sorted(par_lot):
        ws.cell(row=r, column=1, value=lot).font = gras
        for j in range(1, 11):
            ws.cell(row=r, column=j).fill = lot_fill
            ws.cell(row=r, column=j).border = bord
        r += 1
        premiere = r
        for l in par_lot[lot]:
            valeurs = [lot, l["code"], l["designation"], "\n".join(l["elements"]), l["unite"], None, None, None,
                       l["confiance"], "\n".join(x for x in (
                           l["justification"],
                           "\n".join("CCTP art. %s : « %s »" % (a["numero"], a["extrait"]) for a in l.get("articles", [])),
                           "\n".join("⚠ Écart avec le CCTP : " + e for e in l.get("ecarts", [])),
                           ("⚠ " + l["remarque"]) if l["remarque"] else "",
                           ("Calcul : " + l["hypothese"]) if l.get("hypothese") else "") if x)]
            for j, v in enumerate(valeurs, 1):
                c = ws.cell(row=r, column=j, value=v)
                c.font, c.border, c.alignment = normal, bord, haut
            q = ws.cell(row=r, column=6, value=round(l["quantite"], 3) if not l["qte_manquante"] or l["quantite"] else 0)
            q.font, q.number_format = bleu, QTE
            if l["qte_manquante"] and not l["quantite"]:
                q.fill = jaune
                q.comment = Comment("Quantité absente de la maquette : à saisir.", "POC Chiffrage IA")
            pu = ws.cell(row=r, column=7, value="=IFERROR(INDEX('Bibliothèque de prix'!$E$2:$E$%d,MATCH(B%d,'Bibliothèque de prix'!$A$2:$A$%d,0)),0)" % (n_bib, r, n_bib))
            pu.font, pu.number_format = vert, EUR
            tot = ws.cell(row=r, column=8, value="=F%d*G%d" % (r, r))
            tot.font, tot.number_format = normal, EUR
            ws.cell(row=r, column=9).fill = conf_fill[l["confiance"]]
            r += 1
        ws.cell(row=r, column=7, value="Sous-total").font = gras
        st = ws.cell(row=r, column=8, value="=SUM(H%d:H%d)" % (premiere, r - 1))
        st.font, st.number_format = gras, EUR
        for j in range(1, 11):
            ws.cell(row=r, column=j).fill = total_fill
            ws.cell(row=r, column=j).border = bord
        sous_totaux.append("H%d" % r)
        r += 2

    # Ouvrages du CCTP absents de la maquette mais présents dans la bibliothèque : de vraies lignes,
    # quantité à 0 sur fond jaune. L'économiste la saisit et le total se met à jour.
    a_completer = [a for a in estimation.get("absents") or [] if a.get("code")]
    if a_completer:
        ws.cell(row=r, column=1, value="À compléter : ouvrages du CCTP non modélisés (quantités à saisir)").font = Font(name=POLICE, bold=True, color="C55A11")
        for j in range(1, 11):
            ws.cell(row=r, column=j).fill = PatternFill("solid", fgColor="FCE4D6")
            ws.cell(row=r, column=j).border = bord
        r += 1
        premiere = r
        for a in a_completer:
            valeurs = [a["lot"], a["code"], a["designation"],
                       "CCTP art. %s : %s" % (a["article"], a["ouvrage"]) if a["article"] else a["ouvrage"],
                       a["unite"], None, None, None, "à établir",
                       " ".join(x for x in (("Quantité indiquée au CCTP : " + a["info_quantite"]) if a["info_quantite"] else "",
                                            a["remarque"], "Non modélisé : quantité à métrer.") if x)]
            for j, v in enumerate(valeurs, 1):
                c = ws.cell(row=r, column=j, value=v)
                c.font, c.border, c.alignment = normal, bord, haut
            q = ws.cell(row=r, column=6, value=0)
            q.font, q.number_format, q.fill = bleu, QTE, jaune
            q.comment = Comment("Ouvrage décrit au CCTP mais absent de la maquette : saisissez la quantité.", "POC Chiffrage IA")
            pu = ws.cell(row=r, column=7, value="=IFERROR(INDEX('Bibliothèque de prix'!$E$2:$E$%d,MATCH(B%d,'Bibliothèque de prix'!$A$2:$A$%d,0)),0)" % (n_bib, r, n_bib))
            pu.font, pu.number_format = vert, EUR
            tot = ws.cell(row=r, column=8, value="=F%d*G%d" % (r, r))
            tot.font, tot.number_format = normal, EUR
            r += 1
        ws.cell(row=r, column=7, value="Sous-total").font = gras
        st = ws.cell(row=r, column=8, value="=SUM(H%d:H%d)" % (premiere, r - 1))
        st.font, st.number_format = gras, EUR
        for j in range(1, 11):
            ws.cell(row=r, column=j).fill = total_fill
            ws.cell(row=r, column=j).border = bord
        sous_totaux.append("H%d" % r)
        r += 2

    ligne_ht = r
    ws.cell(row=r, column=7, value="Total HT").font = gras
    c = ws.cell(row=r, column=8, value="=" + "+".join(sous_totaux) if sous_totaux else 0)
    c.font, c.number_format = gras, EUR
    r += 1
    ws.cell(row=r, column=7, value="Taux de TVA").font = normal
    c = ws.cell(row=r, column=8, value=estimation.get("tva", 0.2))
    c.font, c.number_format, c.fill = bleu, "0.0%", jaune
    ligne_taux = r
    r += 1
    ws.cell(row=r, column=7, value="TVA").font = normal
    c = ws.cell(row=r, column=8, value="=H%d*H%d" % (ligne_ht, ligne_taux))
    c.font, c.number_format = normal, EUR
    r += 1
    ws.cell(row=r, column=7, value="Total TTC").font = gras
    c = ws.cell(row=r, column=8, value="=H%d+H%d" % (ligne_ht, r - 1))
    c.font, c.number_format = gras, EUR
    r += 2

    if estimation["non_chiffres"]:
        ws.cell(row=r, column=1, value="Éléments non chiffrés (à traiter)").font = Font(name=POLICE, bold=True, color="C00000")
        r += 1
        for n in estimation["non_chiffres"]:
            ws.cell(row=r, column=2, value=n["groupe"]).font = normal
            ws.cell(row=r, column=4, value=n["element"]).font = normal
            c = ws.cell(row=r, column=10, value=n["raison"])
            c.font, c.alignment = normal, haut
            r += 1
        r += 1
    hors_biblio = [a for a in estimation.get("absents") or [] if not a.get("code")]
    if hors_biblio:
        ws.cell(row=r, column=1, value="Ouvrages décrits au CCTP, absents de la maquette et sans ligne dans la bibliothèque"
                ).font = Font(name=POLICE, bold=True, color="C55A11")
        r += 1
        for a in hors_biblio:
            ws.cell(row=r, column=1, value=a["lot"]).font = normal
            ws.cell(row=r, column=2, value=a["code"] or "—").font = normal
            c = ws.cell(row=r, column=3, value=a["designation"] or "Pas de ligne dans la bibliothèque")
            c.font, c.alignment = normal, haut
            c = ws.cell(row=r, column=4, value="CCTP art. %s : %s" % (a["article"], a["ouvrage"]) if a["article"] else a["ouvrage"])
            c.font, c.alignment = normal, haut
            ws.cell(row=r, column=5, value=a["unite"]).font = normal
            c = ws.cell(row=r, column=10, value=" ".join(x for x in (("Quantité indiquée au CCTP : " + a["info_quantite"]) if a["info_quantite"] else "", a["remarque"]) if x))
            c.font, c.alignment = normal, haut
            r += 1
        r += 1
    if estimation.get("ignores"):
        ws.cell(row=r, column=1, value="Écartés par l'IA (hors chiffrage)").font = Font(name=POLICE, bold=True, color="7F7F7F")
        r += 1
        for n in estimation["ignores"]:
            ws.cell(row=r, column=2, value=n["groupe"]).font = normal
            ws.cell(row=r, column=4, value=n["element"]).font = normal
            c = ws.cell(row=r, column=10, value=n["raison"])
            c.font, c.alignment = normal, haut
            r += 1

    for col, larg in zip("ABCDEFGHIJ", (22, 9, 42, 40, 7, 12, 13, 15, 12, 70)):
        ws.column_dimensions[col].width = larg
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    # ---- Onglet Métré maquette ----
    wm = wb.create_sheet("Métré", 1)
    plan = metre.get("source") == "dxf"
    wm.append(["Groupe", "Catégorie", "Classe IFC" if not plan else "Nature (IA)", "Type" if not plan else "Nom",
               "Matériau" if not plan else "Détail", "Nombre", "Surface (m²)", "Volume (m³)",
               "Longueur (m)" if not plan else "Longueur dessinée (m)", "Épaisseur moy. (m)",
               "Répartition par niveau" if not plan else "Fichier", "Codes de prix associés"])
    natures = estimation.get("natures", {})
    codes_par_groupe = defaultdict(list)
    for l in estimation["lignes"]:
        for gid in l["groupes"]:
            codes_par_groupe[gid].append(l["code"])
    for g in metre["groupes"]:
        wm.append([g["id"], g["categorie"],
                   LIBELLES_NATURE.get(natures.get(g["id"]), "") if plan else g["classe"],
                   g["type"], g.get("detail", "") if plan else g["materiau"], g["nombre"], g["surface"],
                   g["volume"], g["longueur"], g["epaisseur"],
                   ", ".join("%s : %d" % kv for kv in sorted(g["niveaux"].items())),
                   ", ".join(codes_par_groupe.get(g["id"], [])) or "non chiffré"])
    for c in wm[1]:
        c.font, c.fill, c.border = entete_font, entete_fill, bord
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for ligne in wm.iter_rows(min_row=2):
        for c in ligne:
            c.font, c.border = normal, bord
        ligne[6].number_format = ligne[8].number_format = QTE
        ligne[7].number_format = '#,##0.000;-#,##0.000;"-"'
        ligne[9].number_format = "0.000"
    for j, larg in enumerate((8, 18, 22, 34, 34, 9, 13, 13, 13, 12, 22, 22), 1):
        wm.column_dimensions[get_column_letter(j)].width = larg
    wm.freeze_panes = "A2"
    r = wm.max_row + 2
    if plan:
        reg = estimation.get("params") or PARAMS_DEFAUT
        note = ("Source : %s (plan 2D, unités %s, %s). Mesures faites par le code sur les calques, blocs et contours ; "
                "la nature de chaque calque est proposée par l'IA. Hypothèses : hauteur des murs %s m, murs %s, "
                "ouvertures non déduites." % (metre["fichier"], metre["unite_longueur"], metre.get("origine_unite", ""),
                                              fr(reg["hauteur"]), "en double trait" if reg["double_trait"] else "en simple trait"))
    else:
        note = ("Source : %s (schéma %s, longueurs en %s). Quantités lues dans les jeux de quantités IFC "
                "(Qto_*) ou, à défaut, dans les propriétés de l'élément." % (metre["fichier"], metre["schema"], metre["unite_longueur"]))
    wm.cell(row=r, column=1, value=note).font = Font(name=POLICE, italic=True, color="7F7F7F")

    neutraliser_formules(wb)
    tampon = io.BytesIO()
    wb.save(tampon)
    return tampon.getvalue()


# =============================================================================
# 6. SERVEUR WEB LOCAL (interface de démonstration)
# =============================================================================

ETAT = {"cle": None, "modele": "deepseek-flash", "metre": None, "estimation": None, "cctp": None,
        "bibliotheque": None, "nom_biblio": None}
VERROU = threading.Lock()
TACHES = {}  # calculs longs lancés en arrière-plan : id -> état
TAILLE_MAX_ENVOI = 2 * 10**9  # 2 Go : au-delà, même une grosse maquette est suspecte


def message_erreur(e):
    if isinstance(e, ErreurAPI):
        return e.args[0]
    if isinstance(e, ValueError):
        return str(e)
    return "Erreur inattendue : %s: %s" % (type(e).__name__, e)


def lancer_tache(genre, fonction, cle_unique=None):
    """Lance un calcul long dans un fil séparé et renvoie son identifiant.
    Le navigateur interroge ensuite /api/tache?id=... : aucune requête ne reste
    ouverte longtemps, donc plus de délai dépassé côté navigateur."""
    with VERROU:
        if cle_unique:
            for i, t in TACHES.items():
                if t.get("cle_unique") == cle_unique and t["etat"] == "en_cours":
                    return i  # même calcul déjà en cours (double clic) : on ne le relance pas
        ident = uuid.uuid4().hex[:12]
        TACHES[ident] = {"genre": genre, "cle_unique": cle_unique, "etat": "en_cours", "progression": "Démarrage…",
                         "debut": time.time(), "resultat": None, "erreur": None}

    def progres(texte):
        TACHES[ident]["progression"] = texte

    def executer():
        try:
            TACHES[ident]["resultat"] = fonction(progres)
            TACHES[ident]["etat"] = "fini"
        except Exception as e:
            TACHES[ident]["erreur"] = message_erreur(e)
            TACHES[ident]["etat"] = "erreur"

    threading.Thread(target=executer, daemon=True).start()
    return ident


def prix_fictifs():
    """Vrai tant que c'est la bibliothèque d'exemple (prix inventés) qui est chargée."""
    return ETAT.get("biblio_exemple", False)


def charger_bibliotheque_defaut():
    with open(os.path.join(DOSSIER, "bibliotheque_prix.csv"), "rb") as f:
        ETAT["bibliotheque"] = lire_bibliotheque(f.read())
    ETAT["nom_biblio"] = "bibliotheque_prix.csv (exemple, prix fictifs)"
    ETAT["biblio_exemple"] = True


def resume_cctp():
    c = ETAT.get("cctp")
    if not c:
        return None
    return {k: c[k] for k in ("fichier", "nb_pages", "nb_mots", "nb_articles", "lots", "articles", "tronque")}


CONNEXION_COUPEE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class Serveur(ThreadingHTTPServer):
    daemon_threads = True  # Ctrl+C arrête l'application même si un calcul est en cours

    def handle_error(self, request, client_address):
        # Le navigateur a fermé la connexion (onglet fermé, page rechargée...) : sans importance.
        if isinstance(sys.exc_info()[1], CONNEXION_COUPEE):
            return
        super().handle_error(request, client_address)


class Gestionnaire(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # console silencieuse

    def _json(self, donnees, code=200):
        corps = json.dumps(donnees, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)
        except CONNEXION_COUPEE:
            pass  # le navigateur n'attend plus la réponse

    def _erreur(self, message, code=400):
        self._json({"erreur": message}, code)

    def _corps(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("Requête invalide (taille inconnue).")
        if n < 0 or n > TAILLE_MAX_ENVOI:
            raise ValueError("Fichier trop volumineux (plus de %d Mo)." % (TAILLE_MAX_ENVOI // 10**6))
        return self.rfile.read(n) if n else b""

    def end_headers(self):
        # Protections du navigateur : la page ne peut charger ni contacter que l'application elle-même,
        # ne peut pas être intégrée dans un autre site, et les types de fichiers ne sont pas devinés.
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _appel_autorise(self):
        """Refuse les requêtes venues d'un autre site ouvert dans le navigateur (qui pourrait sinon
        utiliser l'application, donc votre crédit DeepSeek) et les attaques par « DNS rebinding »."""
        port = self.server.server_address[1]
        hotes = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        if self.headers.get("Host", "") not in hotes:
            return False
        origine = self.headers.get("Origin")
        if origine and origine not in {"http://" + h for h in hotes}:
            return False
        return True

    def _exiger_cle(self):
        if not ETAT["cle"]:
            self._erreur("Renseignez d'abord votre clé API DeepSeek (étape 1).", 401)
            return False
        return True

    def do_GET(self):
        if not self._appel_autorise():
            return self._erreur("Accès refusé : ouvrez l'application depuis l'adresse affichée dans le Terminal.", 403)
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            with open(os.path.join(DOSSIER, "interface.html"), "rb") as f:
                corps = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)
        elif url.path == "/api/etat":
            self._json({"cle_ok": bool(ETAT["cle"]), "modele": ETAT["modele"], "modeles": MODELES,
                        "bibliotheque": {"nom": ETAT["nom_biblio"], "nb": len(ETAT["bibliotheque"] or [])},
                        "cctp": resume_cctp(), "memoire": MEMOIRE.taille()})
        elif url.path in ("/visionneuse.js",):
            with open(os.path.join(DOSSIER, url.path.lstrip("/")), "rb") as f:
                corps = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)
        elif url.path == "/api/visionneuse/geometrie":
            v = ETAT.get("visionneuse") or {}
            if v.get("source") != "ifc" or not v.get("geometrie"):
                return self._erreur("Géométrie non préparée.", 404)
            corps = v["geometrie"]
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)
        elif url.path == "/api/visionneuse/dessin":
            v = ETAT.get("visionneuse") or {}
            if v.get("source") != "dxf":
                return self._erreur("Aucun plan chargé.", 404)
            self._json(v["dessin"])
        elif url.path == "/api/tache":
            t = TACHES.get(parse_qs(url.query).get("id", [""])[0])
            if not t:
                return self._erreur("Calcul introuvable : l'application a peut-être été relancée. Relancez l'opération.", 404)
            self._json({"etat": t["etat"], "progression": t["progression"], "erreur": t["erreur"],
                        "duree": round(time.time() - t["debut"]), "resultat": t["resultat"] if t["etat"] == "fini" else None})
        elif url.path == "/api/excel":
            if not ETAT["estimation"]:
                return self._erreur("Aucune estimation à exporter.")
            corps = exporter_excel(ETAT["metre"], ETAT["estimation"], ETAT["bibliotheque"], ETAT["modele"])
            nom = "Estimation_" + re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(ETAT["metre"]["fichier"])[0]) + ".xlsx"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % nom)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)
        else:
            self._erreur("Introuvable", 404)

    def do_POST(self):
        if not self._appel_autorise():
            return self._erreur("Accès refusé : ouvrez l'application depuis l'adresse affichée dans le Terminal.", 403)
        url = urlparse(self.path)
        params = parse_qs(url.query)
        try:
            if url.path == "/api/cle":
                d = json.loads(self._corps() or b"{}")
                cle = (d.get("cle") or "").strip()
                modele = d.get("modele") or "deepseek-flash"
                if not cle:
                    return self._erreur("Collez votre clé API DeepSeek.")
                modeles = [m for m in verifier_cle(cle) if m]
                if modeles and modele not in modeles:
                    modele = next((m for m in MODELES if m in modeles), modeles[0])
                with VERROU:
                    ETAT["cle"], ETAT["modele"] = cle, modele
                return self._json({"ok": True, "modele": modele, "modeles_disponibles": modeles,
                                   "libelles": {m: MODELES.get(m, m) for m in (modeles or list(MODELES))},
                                   "apercu": cle[:3] + "…" + cle[-4:] if len(cle) > 8 else "…"})

            if url.path == "/api/oublier_cle":
                with VERROU:
                    ETAT["cle"] = None
                return self._json({"ok": True})

            if url.path == "/api/modele":
                d = json.loads(self._corps() or b"{}")
                ETAT["modele"] = d.get("modele") or ETAT["modele"]
                return self._json({"ok": True, "modele": ETAT["modele"]})

            if not self._exiger_cle():
                return

            if url.path == "/api/bibliotheque":
                contenu = self._corps()
                if params.get("defaut"):
                    charger_bibliotheque_defaut()
                else:
                    ETAT["bibliotheque"] = lire_bibliotheque(contenu)
                    ETAT["nom_biblio"] = params.get("nom", ["bibliotheque.csv"])[0]
                    ETAT["biblio_exemple"] = False
                ETAT["estimation"] = None
                return self._json({"nom": ETAT["nom_biblio"], "nb": len(ETAT["bibliotheque"])})

            if url.path == "/api/cctp":
                if params.get("demo"):
                    with open(os.path.join(DOSSIER, "CCTP_demo.pdf"), "rb") as f:
                        contenu, nom = f.read(), "CCTP_demo.pdf"
                else:
                    contenu, nom = self._corps(), params.get("nom", ["cctp.pdf"])[0]
                cctp = lecture_cctp.lire_cctp(contenu, nom)
                with VERROU:
                    ETAT["cctp"], ETAT["estimation"] = cctp, None
                return self._json(resume_cctp())

            if url.path == "/api/cctp_retirer":
                with VERROU:
                    ETAT["cctp"], ETAT["estimation"] = None, None
                return self._json({"ok": True})

            if url.path == "/api/metre":
                demo = params.get("demo", [""])[0]
                if demo:
                    nom = "plan_RDC_demo.dxf" if demo == "dxf" else "maquette_demo.ifc"
                    with open(os.path.join(DOSSIER, nom), "rb") as f:
                        contenu = f.read()
                else:
                    contenu, nom = self._corps(), params.get("nom", ["maquette.ifc"])[0]
                if not contenu:
                    return self._erreur("Fichier vide.")
                extension = os.path.splitext(nom)[1].lower()
                unite = params.get("unite", ["auto"])[0]
                if extension not in (".dwg", ".dxf", ".ifc"):
                    return self._erreur("Format non pris en charge : utilisez un fichier .ifc, .dxf ou .dwg.")
                taille = len(contenu) / 1e6
                with VERROU:
                    ETAT["numero_chargement"] = numero = ETAT.get("numero_chargement", 0) + 1

                def mesurer(progres):
                    # Un gros IFC peut demander une minute : on travaille en arrière-plan.
                    nonlocal contenu
                    if extension == ".dwg":
                        progres("Conversion du DWG en DXF…")
                        contenu = lecture_dxf.convertir_dwg(contenu, nom)
                    progres("Lecture de %s (%.1f Mo) et calcul du métré…" % (nom, taille))
                    if extension == ".ifc":
                        metre = extraire_metre(contenu, nom)
                    else:
                        metre = lecture_dxf.extraire_metre_dxf(contenu, nom, unite)
                    # Les données de la visionneuse (lecteur IFC, dessin DXF) restent sur le serveur
                    if metre["source"] == "ifc":
                        visionneuse = {"source": "ifc", "lecteur": metre.pop("_lecteur"), "facteur": metre.pop("_facteur_longueur"),
                                       "elements": metre.pop("_elements"), "geometrie": None}
                    else:
                        visionneuse = {"source": "dxf", "dessin": metre.pop("_dessin")}
                    with VERROU:
                        if ETAT["numero_chargement"] != numero:
                            raise ValueError("Chargement remplacé par un fichier chargé plus récemment.")
                        ETAT["metre"], ETAT["estimation"], ETAT["visionneuse"] = metre, None, visionneuse
                    return metre

                return self._json({"tache": lancer_tache("metre", mesurer)})

            if url.path == "/api/chiffrer":
                if not ETAT["metre"]:
                    return self._erreur("Chargez d'abord une maquette (étape 2).")
                d = json.loads(self._corps() or b"{}")
                reglages = {"hauteur": float(d.get("hauteur") or PARAMS_DEFAUT["hauteur"]),
                            "double_trait": bool(d.get("double_trait", True))}
                if not 1.0 <= reglages["hauteur"] <= 20.0:
                    return self._erreur("Hauteur des murs peu plausible : indiquez une valeur entre 1 et 20 m.")
                reflexion = bool(d.get("reflexion"))
                forcer = bool(d.get("forcer"))
                cle, modele, metre, biblio, cctp = ETAT["cle"], ETAT["modele"], ETAT["metre"], ETAT["bibliotheque"], ETAT["cctp"]

                def chiffrer(progres):
                    t0 = time.time()
                    est = associer_prix(cle, modele, metre, biblio, reglages, cctp, reflexion, progres, forcer)
                    est["prix_fictifs"] = prix_fictifs()
                    est["duree"] = round(time.time() - t0, 1)
                    est["modele"] = modele + (" (réflexion)" if reflexion else "")
                    with VERROU:
                        if ETAT["metre"] is not metre:
                            raise ValueError("La maquette ou le plan a changé pendant le calcul : relancez la correspondance.")
                        ETAT["estimation"] = est
                    return est

                cle_calcul = "chiffrage-%d-%s-%s-%s" % (id(metre), reflexion, forcer, json.dumps(reglages, sort_keys=True))
                return self._json({"tache": lancer_tache("chiffrage", chiffrer, cle_calcul)})

            if url.path == "/api/question":
                if not ETAT["estimation"]:
                    return self._erreur("Lancez d'abord le chiffrage (étape 4).")
                d = json.loads(self._corps() or b"{}")
                q = (d.get("question") or "").strip()
                if not q:
                    return self._erreur("Question vide.")
                cle, modele, metre, est = ETAT["cle"], ETAT["modele"], ETAT["metre"], ETAT["estimation"]
                biblio, cctp, fictifs = ETAT["bibliotheque"], ETAT["cctp"], prix_fictifs()
                reflexion = bool(d.get("reflexion"))

                def repondre(progres):
                    progres("DeepSeek rédige sa réponse…")
                    return repondre_question(cle, modele, q, metre, est, biblio, cctp, fictifs, reflexion)

                return self._json({"tache": lancer_tache("question", repondre)})

            if url.path == "/api/visionneuse/preparer":
                v = ETAT.get("visionneuse")
                if not v:
                    return self._erreur("Chargez d'abord une maquette ou un plan.")
                if v["source"] == "dxf" or v.get("geometrie"):
                    return self._json({"pret": True, "source": v["source"]})

                def preparer(progres):
                    # Géométrie calculée une seule fois par maquette, puis gardée en mémoire
                    donnees = geometrie_ifc.construire(v["lecteur"], v["facteur"], v["elements"], progres)
                    v["geometrie"] = donnees
                    return {"pret": True, "source": "ifc", "taille": len(donnees)}

                return self._json({"tache": lancer_tache("geometrie", preparer, "geometrie-%d" % id(v))})

            if url.path == "/api/memoire_vider":
                MEMOIRE.vider()
                return self._json({"ok": True, "memoire": 0})

            self._erreur("Introuvable", 404)
        except CONNEXION_COUPEE:
            pass  # le navigateur a fermé la connexion : rien à lui répondre
        except ErreurAPI as e:
            self._erreur(e.args[0], 502)
        except ValueError as e:
            self._erreur(str(e))
        except Exception as e:  # dernier filet : on affiche l'erreur dans l'interface
            self._erreur(message_erreur(e), 500)


def diagnostic():
    """Vérifie l'installation sans clé API : python3 chiffrage_ia.py --diagnostic"""
    problemes = []

    def ligne(ok, texte, conseil=""):
        print(("  [OK]     " if ok else "  [ÉCHEC]  ") + texte)
        if not ok:
            problemes.append(texte + (" → " + conseil if conseil else ""))

    print("Diagnostic de SurveIA\n")
    print("  Python %s (%s)" % (sys.version.split()[0], sys.executable))
    ligne(sys.version_info >= (3, 9), "Version de Python ≥ 3.9", "installez un Python récent (python.org)")
    try:
        import openpyxl
        ligne(True, "openpyxl %s (export Excel)" % openpyxl.__version__)
    except ImportError:
        ligne(False, "openpyxl absent (export Excel)", "python3 -m pip install openpyxl")
    try:
        import pypdf
        ok = lecture_cctp.pypdf_a_jour()
        ligne(ok, "pypdf %s (CCTP en PDF)%s" % (pypdf.__version__, "" if ok else " : version vulnérable"),
              "python3 -m pip install --upgrade \"pypdf>=%s\" (failles corrigées)" % lecture_cctp.PYPDF_MINIMUM)
    except ImportError:
        ligne(False, "pypdf absent (CCTP en PDF)", "python3 -m pip install pypdf")

    try:
        charger_bibliotheque_defaut()
        ligne(True, "Bibliothèque de prix d'exemple : %d lignes" % len(ETAT["bibliotheque"]))
        m = extraire_metre(open(os.path.join(DOSSIER, "maquette_demo.ifc"), "rb").read(), "maquette_demo.ifc")
        ligne(True, "Maquette IFC de démo : %d éléments, %d locaux, %d groupes" % (m["nb_elements"], m.get("nb_locaux", 0), len(m["groupes"])))
        d = lecture_dxf.extraire_metre_dxf(open(os.path.join(DOSSIER, "plan_RDC_demo.dxf"), "rb").read(), "plan_RDC_demo.dxf")
        ligne(True, "Plan DXF de démo : %d groupes (unités %s)" % (len(d["groupes"]), d["unite_longueur"]))
        geo = geometrie_ifc.construire(m["_lecteur"], m["_facteur_longueur"], m["_elements"])
        ligne(len(geo) > 1000, "Géométrie 3D de la visionneuse (%d Ko)" % (len(geo) // 1024))
        ligne(len(d["_dessin"]["prims"]) > 0, "Dessin 2D de la visionneuse : %d traits" % len(d["_dessin"]["prims"]))
        est = calculer_estimation(m, ETAT["bibliotheque"], {}, [], None, None)
        taille = len(exporter_excel(m, est, ETAT["bibliotheque"], "diagnostic"))
        ligne(taille > 1000, "Génération d'un fichier Excel (%d octets)" % taille)
    except Exception as e:
        ligne(False, "Lecture des fichiers de démo : %s: %s" % (type(e).__name__, e), "fichiers du dossier incomplets ? re-décompressez le zip")
    try:
        c = lecture_cctp.lire_cctp(open(os.path.join(DOSSIER, "CCTP_demo.pdf"), "rb").read(), "CCTP_demo.pdf")
        ligne(True, "CCTP de démo : %d articles repérés" % c["nb_articles"])
    except Exception as e:
        ligne(False, "CCTP de démo : %s" % e)

    oda = lecture_dxf.trouver_oda()
    print("  [info]   Conversion DWG : %s" % (("ODA File Converter trouvé (%s)" % oda) if oda else
                                           "ODA File Converter non installé (facultatif : sinon, enregistrez les plans en DXF)"))
    try:
        essai = os.path.join(DOSSIER, ".essai_ecriture")
        open(essai, "w").close()
        os.remove(essai)
        ligne(True, "Écriture possible dans le dossier (mémoire des correspondances)")
    except OSError:
        ligne(False, "Dossier en lecture seule : la mémoire des correspondances ne sera pas conservée", "déplacez le dossier (ex. Bureau)")

    # Réseau : sans clé, DeepSeek doit répondre « 401 non autorisé » ; c'est la preuve que la connexion
    # et les certificats de sécurité fonctionnent.
    try:
        appel_api("diagnostic-sans-cle", "/models", delai=20)
        ligne(True, "Connexion à %s" % URL_API)
    except ErreurAPI as e:
        if len(e.args) > 1 and e.args[1] in (401, 403):
            ligne(True, "Connexion à %s et certificats de sécurité" % URL_API)
        else:
            ligne(False, "Connexion à %s : %s" % (URL_API, e.args[0]))
    print("  [info]   Mémoire des correspondances : %d entrée(s)" % MEMOIRE.taille())

    print()
    if problemes:
        print("À corriger :")
        for p in problemes:
            print("  - " + p)
        return 1
    print("Tout est prêt. Lancez l'application avec :  python3 chiffrage_ia.py")
    return 0


def main():
    if "--diagnostic" in sys.argv:
        sys.exit(diagnostic())
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("Il manque la bibliothèque openpyxl. Installez-la avec :  python -m pip install openpyxl")
        sys.exit(1)
    try:
        import pypdf  # noqa: F401
    except ImportError:
        print("Remarque : pypdf n'est pas installé, les CCTP en PDF ne pourront pas être lus "
              "(python -m pip install pypdf). Les CCTP en .docx et .txt fonctionnent.")
    else:
        if not lecture_cctp.pypdf_a_jour():
            print("ATTENTION : votre version de pypdf a des failles connues (PDF piégés). Mettez-la à jour :\n"
                  "  python3 -m pip install --upgrade \"pypdf>=%s\"" % lecture_cctp.PYPDF_MINIMUM)
    charger_bibliotheque_defaut()
    port = 8765
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    serveur = None
    for p in range(port, port + 20):
        try:
            serveur = Serveur(("127.0.0.1", p), Gestionnaire)
            port = p
            break
        except OSError:
            continue
    if not serveur:
        print("Aucun port libre trouvé.")
        sys.exit(1)
    adresse = "http://127.0.0.1:%d" % port
    print("SurveIA lancé : " + adresse)
    print("Ouvrez cette adresse dans votre navigateur si elle ne s'ouvre pas toute seule.")
    print("Pour arrêter : Ctrl+C dans cette fenêtre.")
    if "--sans-navigateur" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(adresse)).start()
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
