"""
Lecture d'un DPGF (décomposition du prix global et forfaitaire) au format Excel, quelle que soit sa présentation.

Un DPGF n'a pas de format normalisé : chaque cabinet a le sien. Ce module le comprend par le code :
  1. dans chaque feuille, il cherche la ligne d'en-tête (n°, désignation, unité, quantité, prix unitaire, montant),
     y compris sur deux lignes, et à défaut devine les colonnes d'après leur contenu ;
  2. il range chaque ligne : titre (lot, chapitre, sous-chapitre), article chiffrable (il a une unité),
     description qui complète l'article précédent, sous-total ou total (ignorés) ;
  3. il déduit la hiérarchie de la numérotation (2 / 2.1 / 2.1.3, « LOT 02 »), sinon de la mise en forme
     (gras, majuscules) ou du nom de la feuille quand chaque lot a sa feuille ;
  4. il relève les quantités et prix déjà présents, les « PM » (pour mémoire) et les options (PSE, variantes).

Quand la lecture est douteuse (aucune en-tête trouvée), le serveur peut demander à l'IA de désigner la ligne
d'en-tête et les colonnes (analyser(..., aide_ia=...)), et l'utilisateur peut toujours corriger les colonnes.

Formats : .xlsx et .xlsm (openpyxl, déjà requis). .xls ancien : nécessite xlrd (facultatif).
"""

import io
import re
import unicodedata
import zipfile

LIGNES_MAX = 20000      # par feuille
FEUILLES_MAX = 60
TAILLE_DECOMPRESSEE_MAX = 300 * 10**6
ROLES = ("numero", "designation", "unite", "quantite", "pu", "total", "cctp")
LIBELLES_ROLES = {"numero": "N° / repère", "designation": "Désignation", "unite": "Unité", "quantite": "Quantité",
                  "pu": "Prix unitaire", "total": "Montant", "cctp": "Réf. CCTP"}

# Unités reconnues (forme normalisée -> forme affichée). Une ligne avec l'une d'elles est un article chiffrable.
UNITES = {
    "m2": "m2", "m²": "m2", "mc": "m2", "m 2": "m2",
    "m3": "m3", "m³": "m3", "m 3": "m3",
    "ml": "ml", "m": "ml", "mètre": "ml", "metre": "ml", "m.l": "ml", "mlin": "ml",
    "u": "u", "un": "u", "unité": "u", "unite": "u", "pce": "u", "pièce": "u", "piece": "u", "nb": "u", "no": "u", "p": "u",
    "ens": "ens", "ensemble": "ens", "ft": "ft", "f": "ft", "fft": "ft", "forfait": "ft", "forf": "ft",
    "kg": "kg", "t": "t", "tonne": "t", "h": "h", "heure": "h", "j": "j", "jour": "j", "mois": "mois", "l": "l", "litre": "l",
}
MOTIF_PM = re.compile(r"^\s*(p\.?\s?m\.?|pour m[ée]moire|inclus|compris|n[ée]ant|sans objet|s\.?o\.?)\s*$", re.I)
MOTIF_TOTAL = re.compile(r"^\s*(sous[- ]?total|total|montant|report|à reporter|a reporter|tva|t\.?v\.?a|"
                         r"t\.?t\.?c|h\.?t\.?\s*$|net à payer|récapitulatif|recapitulatif)", re.I)
# « PSE » seul désigne aussi le polystyrène expansé : une option s'écrit « PSE 1 », « PSE n° 2 » ou en toutes lettres
MOTIF_OPTION = re.compile(r"\b(options?|pse\s*(?:n[°o]\s*)?\d{1,2}(?![\d,.]|\s*(?:mm|cm|m)\b)|prestations? suppl[ée]mentaires?|variantes?|tranche optionnelle)\b", re.I)
MOTIF_NUMERO_TEXTE = re.compile(r"^\s*(\d{1,3}(?:[.\-]\d{1,3}){0,5}|[A-Z](?:[.\-]\d{1,3}){0,5}(?=[.)]))[.)]?\s+[-–:]?\s*(\S.*)$")
MOTIF_LOT = re.compile(r"^\s*lot\s*(?:n[°o]\s*)?(\d{1,3}[a-z]?)\b\s*[-–:.]?\s*(.*)$", re.I)
MOTIF_ROMAIN = re.compile(r"^\s*([IVX]{1,5})[.)]\s+(\S.*)$")


def sans_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normaliser(s):
    s = sans_accents(str(s or "")).lower().replace("\n", " ")
    s = re.sub(r"[^a-z0-9%/ ]+", " ", s)
    return " ".join(s.split())


def unite_normalisee(v):
    if v is None:
        return None
    s = str(v).strip().lower().replace(" ", " ")
    if not s or len(s) > 12:
        return None
    s = s.rstrip(".")
    return UNITES.get(s) or UNITES.get(s.replace(" ", "")) or UNITES.get(s.replace(".", ""))


def nombre(v):
    """Valeur numérique d'une cellule (nombre ou texte « 1 234,50 € »), sinon None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(" ", "").replace(" ", "").replace("€", "").replace("EUR", "")
        if re.fullmatch(r"-?\d+(?:[.,]\d+)?", s):
            return float(s.replace(",", "."))
        if re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?", s):  # 1.234,56 ou 1,234.56
            dec = s[-3] if len(s) > 3 and s[-3] in ",." else None
            if dec:
                entier, frac = s[:-3], s[-2:]
                return float(re.sub(r"[.,]", "", entier) + "." + frac)
            return float(re.sub(r"[.,]", "", s))
    return None


def lettre(col):
    """Index de colonne (0 = A) -> lettre Excel."""
    s, n = "", col + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def index_lettre(l):
    n = 0
    for c in l.strip().upper():
        if not "A" <= c <= "Z":
            raise ValueError("Colonne invalide : %s" % l)
        n = n * 26 + ord(c) - 64
    return n - 1


# --------------------------------------------------------------------------------------------------
# Lecture brute du classeur : chaque feuille devient une grille de cellules (valeur, gras, italique)
# --------------------------------------------------------------------------------------------------

class Cellule:
    __slots__ = ("v", "gras", "italique", "formule")

    def __init__(self, v, gras=False, italique=False, formule=False):
        self.v, self.gras, self.italique, self.formule = v, gras, italique, formule


def lire_classeur(contenu, nom_fichier):
    ext = nom_fichier.lower().rsplit(".", 1)[-1] if "." in nom_fichier else ""
    if ext in ("xlsx", "xlsm"):
        return lire_xlsx(contenu)
    if ext == "xls":
        return lire_xls(contenu)
    if ext in ("csv", "ods", "pdf", "docx"):
        raise ValueError("Format de DPGF non pris en charge pour l'instant : enregistrez-le en Excel (.xlsx).")
    raise ValueError("Format de DPGF non pris en charge : utilisez un fichier Excel .xlsx (ou .xls).")


def lire_xlsx(contenu):
    from openpyxl import load_workbook
    try:
        archive = zipfile.ZipFile(io.BytesIO(contenu))
        if sum(i.file_size for i in archive.infolist()) > TAILLE_DECOMPRESSEE_MAX:
            raise ValueError("Classeur anormalement volumineux une fois décompressé.")
    except zipfile.BadZipFile:
        raise ValueError("Fichier Excel illisible : réenregistrez-le au format .xlsx.")
    try:
        valeurs = load_workbook(io.BytesIO(contenu), data_only=True)
        formules = load_workbook(io.BytesIO(contenu), data_only=False)
    except Exception as e:
        raise ValueError("Fichier Excel illisible (%s)." % type(e).__name__)
    feuilles = []
    for ws in valeurs.worksheets[:FEUILLES_MAX]:
        wf = formules[ws.title]
        n_lignes = min(ws.max_row or 0, LIGNES_MAX)
        n_cols = min(ws.max_column or 0, 40)
        grille = []
        for r in range(1, n_lignes + 1):
            ligne = []
            for c in range(1, n_cols + 1):
                cel = ws.cell(row=r, column=c)
                f = wf.cell(row=r, column=c).value
                est_formule = isinstance(f, str) and f.startswith("=")
                v = cel.value
                if isinstance(v, str):
                    v = v.strip()
                ligne.append(Cellule(v, bool(cel.font and cel.font.b), bool(cel.font and cel.font.i), est_formule))
            grille.append(ligne)
        masquee = ws.sheet_state != "visible"
        feuilles.append({"nom": ws.title, "grille": grille, "masquee": masquee})
    return feuilles


def lire_xls(contenu):
    try:
        import xlrd
    except ImportError:
        raise ValueError("Ce DPGF est au format Excel ancien (.xls). Enregistrez-le en .xlsx depuis Excel ou Numbers, "
                         "ou installez xlrd :  python3 -m pip install xlrd")
    try:
        livre = xlrd.open_workbook(file_contents=contenu, formatting_info=True)
    except Exception as e:
        raise ValueError("Fichier .xls illisible (%s) : enregistrez-le en .xlsx." % type(e).__name__)
    feuilles = []
    for sh in livre.sheets()[:FEUILLES_MAX]:
        grille = []
        for r in range(min(sh.nrows, LIGNES_MAX)):
            ligne = []
            for c in range(min(sh.ncols, 40)):
                v = sh.cell_value(r, c)
                if v == "":
                    v = None
                elif isinstance(v, str):
                    v = v.strip()
                gras = italique = False
                try:
                    police = livre.font_list[livre.xf_list[sh.cell_xf_index(r, c)].font_index]
                    gras, italique = bool(police.bold), bool(police.italic)
                except Exception:
                    pass
                ligne.append(Cellule(v, gras, italique, False))
            grille.append(ligne)
        feuilles.append({"nom": sh.name, "grille": grille, "masquee": bool(getattr(sh, "visibility", 0))})
    return feuilles


# --------------------------------------------------------------------------------------------------
# Repérage de la ligne d'en-tête et des colonnes
# --------------------------------------------------------------------------------------------------

def role_libelle(libelle):
    """Rôle d'une cellule d'en-tête d'après son texte (ou None), et la force de la reconnaissance."""
    t = normaliser(libelle)
    if not t or len(t) > 60:
        return None, 0
    mots = t.split()
    if "cctp" in mots or t.startswith("ref cctp") or t.startswith("art cctp"):
        return "cctp", 3
    if re.search(r"\b(montant|prix total|total|p t|pt|somme)\b", t) and "unitaire" not in t:
        return "total", 3
    if re.search(r"\b(prix unitaire|p u|pu|pu ht|unitaire|prix u|prix unit)\b", t) or t in ("prix", "prix ht"):
        return "pu", 3 if "unit" in t or t.startswith("p u") or t.startswith("pu") else 1
    if t in ("u", "un", "unite", "unites", "unit", "u m", "um") or t.startswith("unite"):
        return "unite", 3
    if re.search(r"\b(qte|qtes|quantite|quantites|quant|qt|qte s)\b", t) or t in ("nombre", "nb"):
        return "quantite", 3
    if re.search(r"\b(designation|libelle|description|descriptif|intitule|prestation|nature des (ouvrages|travaux)|ouvrages?)\b", t):
        return "designation", 3
    if t in ("n", "no", "n o", "num", "numero", "art", "article", "ref", "reference", "repere", "code", "item", "poste",
             "index", "n art", "n d article", "n article", "numero d article", "rep") or t.startswith("n "):
        return "numero", 2
    return None, 0


def colonnes_de_ligne(grille, r):
    """Colonnes reconnues sur la ligne r (seule, puis combinée avec la suivante pour les en-têtes sur deux lignes)."""
    meilleur = ({}, 0)
    for combiner in (False, True):
        roles, score = {}, 0
        if combiner and (r + 1 >= len(grille) or sum(1 for c in grille[r] if isinstance(c.v, str) and c.v) < 2
                         or not any(isinstance(c.v, str) and c.v for c in grille[r + 1])):
            continue  # une en-tête sur deux lignes : deux lignes de libellés, l'une sous l'autre
        for c, cel in enumerate(grille[r]):
            texte = cel.v if isinstance(cel.v, str) else ""
            if combiner:
                bas = grille[r + 1][c].v if c < len(grille[r + 1]) else None
                if isinstance(bas, (int, float)):
                    continue  # la ligne suivante contient déjà des données
                texte = " ".join(x for x in (texte, bas if isinstance(bas, str) else "") if x)
            role, force = role_libelle(texte)
            if role and (role not in roles or force > roles[role][1]):
                roles[role] = (c, force)
                score += force
        if score > meilleur[1]:
            meilleur = ({k: v[0] for k, v in roles.items()}, score)
    return meilleur


def trouver_entete(grille):
    """(ligne d'en-tête, colonnes {rôle: index}, confiance 0-1) ; ligne = None si rien de convaincant."""
    meilleur, meilleur_score = (None, {}, 0.0, False), 0
    for r in range(min(len(grille), 80)):
        roles, score = colonnes_de_ligne(grille, r)
        if "designation" not in roles:
            continue
        utiles = sum(1 for k in ("unite", "quantite", "pu", "total") if k in roles)
        if not utiles:
            continue
        confiance = min(1.0, (score + 2 * utiles) / 16)
        if score + 2 * utiles > meilleur_score:
            deux = colonnes_de_ligne_simple(grille, r) != roles
            meilleur, meilleur_score = (r, roles, confiance, deux), score + 2 * utiles
    r, roles, conf, deux = meilleur
    if r is not None and deux:
        r += 1  # en-tête sur deux lignes : les données commencent après la seconde
    return r, roles, conf


def colonnes_de_ligne_simple(grille, r):
    roles = {}
    for c, cel in enumerate(grille[r]):
        role, force = role_libelle(cel.v if isinstance(cel.v, str) else "")
        if role and role not in roles:
            roles[role] = c
    return roles


def deviner_colonnes(grille):
    """Sans en-tête : colonnes déduites du contenu (une colonne d'unités, la plus longue colonne de texte,
    puis les colonnes de nombres à droite des unités : quantité, prix unitaire, montant)."""
    n_cols = max((len(l) for l in grille), default=0)
    unites, textes, nombres = [0] * n_cols, [0] * n_cols, [0] * n_cols
    for ligne in grille[:2000]:
        for c, cel in enumerate(ligne):
            if unite_normalisee(cel.v) and isinstance(cel.v, str):
                unites[c] += 1
            elif isinstance(cel.v, str) and len(cel.v) > 15:
                textes[c] += len(cel.v)
            elif nombre(cel.v) is not None:
                nombres[c] += 1
    if not n_cols or max(unites) < 3:
        return None, {}, 0.0
    cu = unites.index(max(unites))
    cd = max(range(n_cols), key=lambda c: textes[c] if c != cu else -1)
    roles = {"unite": cu, "designation": cd}
    a_droite = [c for c in range(cu + 1, n_cols) if nombres[c] >= 2]
    for role, c in zip(("quantite", "pu", "total"), a_droite):
        roles[role] = c
    if cd > 0:
        gauche = [c for c in range(cd) if any(isinstance(l[c].v, (str, int, float)) and l[c].v not in ("", None)
                                              for l in grille[:200] if c < len(l))]
        if gauche:
            roles["numero"] = gauche[-1]
    debut = next((r for r, l in enumerate(grille) if cu < len(l) and unite_normalisee(l[cu].v)), 0)
    return max(0, debut - 1), roles, 0.35


# --------------------------------------------------------------------------------------------------
# Lecture des lignes : titres, articles, descriptions, totaux
# --------------------------------------------------------------------------------------------------

def niveau_numero(numero):
    """« 2 » -> 1, « 2.1 » -> 2, « 2.1.3 » -> 3 ; « LOT 02 » -> 1."""
    if not numero:
        return None
    if MOTIF_LOT.match(numero):
        return 1
    parties = [p for p in re.split(r"[.\-]", numero) if p]
    return len(parties) if parties else None


def texte_cellule(cel):
    v = cel.v
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def lire_feuille(feuille, entete, colonnes, multi_feuilles):
    grille = feuille["grille"]
    col = colonnes

    def val(ligne, role):
        c = col.get(role)
        return ligne[c] if c is not None and c < len(ligne) else Cellule(None)

    elements, pile = [], []  # pile de titres : [(niveau, texte, numero)]
    niveaux_forme = {}
    dernier_article, dernier = None, None
    for r in range(entete + 1, len(grille)):
        ligne = grille[r]
        cel_des = val(ligne, "designation")
        des = texte_cellule(cel_des)
        textes = [texte_cellule(c) for c in ligne if isinstance(c.v, str) and c.v.strip()]
        if not des and textes:
            # titre fusionné sur plusieurs colonnes, ou écrit dans la colonne du numéro
            des = max(textes, key=len) if len(textes) > 1 else textes[0]
        numero = texte_cellule(val(ligne, "numero"))
        if numero and numero == des:
            numero = ""
        unite_brute = val(ligne, "unite").v
        unite = unite_normalisee(unite_brute)
        q_cel, pu_cel, tot_cel = val(ligne, "quantite"), val(ligne, "pu"), val(ligne, "total")
        pm = any(isinstance(c.v, str) and MOTIF_PM.match(c.v) for c in (q_cel, pu_cel, tot_cel))
        if not des and not numero:
            continue
        gras = cel_des.gras or any(c.gras for c in ligne if isinstance(c.v, str) and c.v.strip())
        # numéro écrit au début de la désignation : « 2.1.1 Semelles filantes »
        if not numero:
            m = MOTIF_LOT.match(des)
            if m and not unite:
                numero = "LOT %s" % m.group(1)
            else:
                m = MOTIF_NUMERO_TEXTE.match(des) or MOTIF_ROMAIN.match(des)
                if m and (unite or gras or len(m.group(1)) <= 8) and not re.fullmatch(r"\d+", m.group(2).split()[0] or ""):
                    numero, des = m.group(1).rstrip("."), m.group(2).strip()
        if MOTIF_TOTAL.match(des) and not unite:
            elements.append({"type": "total", "ligne": r + 1, "texte": des})
            dernier = "total"
            continue
        if unite or (pm and numero):
            if re.fullmatch(r"\d+(?:\.\d+)+", numero):
                # « 5.2.1 » n'appartient pas au chapitre « 5.1 » même si aucun titre « 5.2 » ne le précède
                while pile and re.fullmatch(r"[\d.]+", pile[-1][2] or "") and not numero.startswith(pile[-1][2] + "."):
                    pile.pop()
            dernier_article = {
                "type": "article", "ligne": r + 1, "numero": numero, "designation": des, "description": "",
                "unite": unite or "u", "unite_brute": str(unite_brute or "").strip(),
                "quantite_dpgf": nombre(q_cel.v), "pu_dpgf": nombre(pu_cel.v), "total_dpgf": nombre(tot_cel.v),
                "formule_quantite": q_cel.formule, "formule_pu": pu_cel.formule, "formule_total": tot_cel.formule,
                "pm": pm, "option": bool(MOTIF_OPTION.search(des)) or any(MOTIF_OPTION.search(t[1]) for t in pile),
                "chemin": [t[1] for t in pile], "numeros_titres": [t[2] for t in pile],
                "cctp_ref": texte_cellule(val(ligne, "cctp")),
            }
            elements.append(dernier_article)
            dernier = "article"
            continue
        # Ligne sans unité : titre, ou description de l'article précédent
        est_titre = bool(numero) or gras or (des.isupper() and len(des) > 3) or MOTIF_LOT.match(des)
        if not est_titre and dernier == "article" and dernier_article is not None:
            dernier_article["description"] = (dernier_article["description"] + " " + des).strip()[:600]
            continue
        if not est_titre and dernier in ("titre", None) and pile:
            continue  # texte d'introduction d'un chapitre : sans effet sur le chiffrage
        niveau = niveau_numero(numero)
        if niveau is None:
            # titre sans numéro : son niveau dépend de sa mise en forme ; une même mise en forme = un même niveau
            signature = (des.isupper(), gras, cel_des.italique)
            if signature not in niveaux_forme:
                base = 1 if (des.isupper() or MOTIF_LOT.match(des)) else (2 if gras else 3)
                numerotes = [t for t in pile if t[2]]
                niveaux_forme[signature] = max(base, numerotes[-1][0] + 1) if numerotes else base
            niveau = niveaux_forme[signature]
        while pile and pile[-1][0] >= niveau:
            pile.pop()
        titre = ((numero + " ") if numero and not numero.upper().startswith("LOT") else "") + des
        if numero.upper().startswith("LOT") and not des.upper().startswith("LOT"):
            titre = "%s – %s" % (numero, des)
        pile.append((niveau, titre.strip(), numero))
        elements.append({"type": "titre", "ligne": r + 1, "niveau": niveau, "numero": numero, "texte": titre.strip(),
                         "option": bool(MOTIF_OPTION.search(des))})
        dernier = "titre"
    return elements


def analyser(contenu, nom_fichier, forcer=None, aide_ia=None):
    """Lit un DPGF. forcer = {nom de feuille: {"entete": n° de ligne (1 = première), "colonnes": {rôle: "B"}}}
    pour imposer une lecture corrigée par l'utilisateur. aide_ia(feuille, extrait_tsv) -> (ligne, {rôle: lettre})
    est appelée pour une feuille sans en-tête reconnaissable."""
    feuilles = lire_classeur(contenu, nom_fichier)
    forcer = forcer or {}
    lectures, avertissements = [], []
    for f in feuilles:
        grille = f["grille"]
        if not grille or f["masquee"]:
            continue
        methode = "code"
        if f["nom"] in forcer:
            entete = int(forcer[f["nom"]]["entete"]) - 1
            colonnes = {k: index_lettre(v) for k, v in forcer[f["nom"]]["colonnes"].items() if v and k in ROLES}
            confiance, methode = 1.0, "manuel"
        else:
            entete, colonnes, confiance = trouver_entete(grille)
            if entete is None:
                entete, colonnes, confiance = deviner_colonnes(grille)
                methode = "contenu"
            if (entete is None or confiance < 0.5) and aide_ia:
                extrait = "\n".join("%d\t" % (r + 1) + "\t".join("%s=%s" % (lettre(c), texte_cellule(cel)[:40])
                                                                  for c, cel in enumerate(l) if texte_cellule(cel))
                                    for r, l in enumerate(grille[:45]))
                try:
                    r_ia, cols_ia = aide_ia(f["nom"], extrait)
                    if r_ia and cols_ia.get("designation") and cols_ia.get("unite"):
                        entete = int(r_ia) - 1
                        colonnes = {k: index_lettre(v) for k, v in cols_ia.items() if v and k in ROLES}
                        confiance, methode = 0.7, "ia"
                except Exception as e:
                    avertissements.append("Feuille « %s » : l'IA n'a pas pu aider à la lecture (%s)." % (f["nom"], e))
        if entete is None or "designation" not in colonnes or "unite" not in colonnes:
            continue
        elements = lire_feuille(f, entete, colonnes, len(feuilles) > 1)
        articles = [e for e in elements if e["type"] == "article"]
        if len(articles) < 1:
            continue
        lectures.append({"feuille": f["nom"], "entete": entete + 1, "colonnes": colonnes, "confiance": round(confiance, 2),
                         "methode": methode, "elements": elements, "nb_lignes": len(grille)})
    if not lectures:
        raise ValueError("Aucun tableau de DPGF trouvé : il faut au moins une colonne de désignations et une colonne "
                         "d'unités (m2, ml, u, ens…). Vérifiez le fichier, ou indiquez les colonnes à la main.")

    multi = len(lectures) > 1
    articles, titres = [], []
    vus = {}
    for i, lec in enumerate(lectures):
        for e in lec["elements"]:
            if e["type"] == "titre":
                titres.append({**e, "feuille": lec["feuille"]})
            if e["type"] != "article":
                continue
            chemin = ([lec["feuille"]] if multi else []) + e["chemin"]
            lot = chemin[0] if chemin else (lec["feuille"] if multi else "DPGF")
            code = e["numero"] or ("%s l.%d" % (lec["feuille"], e["ligne"]) if multi else "l.%d" % e["ligne"])
            if code in vus:
                vus[code] += 1
                code = "%s (%d)" % (code, vus[code])
            else:
                vus[code] = 1
            articles.append({
                "code": code, "numero": e["numero"], "feuille": lec["feuille"], "ligne": e["ligne"],
                "lot": lot[:120], "chapitre": " › ".join(chemin[1:])[:200], "chemin": chemin,
                "designation": e["designation"][:300], "description": e["description"],
                "unite": e["unite"], "unite_brute": e["unite_brute"],
                "quantite_dpgf": e["quantite_dpgf"], "pu_dpgf": e["pu_dpgf"], "total_dpgf": e["total_dpgf"],
                "formule_quantite": e["formule_quantite"], "formule_pu": e["formule_pu"], "formule_total": e["formule_total"],
                "pm": e["pm"], "option": e["option"], "cctp_ref": e["cctp_ref"],
                "numeros_titres": [n for n in e["numeros_titres"] if n], "ordre": len(articles),
            })
    nb = len(articles)
    avec_prix = sum(1 for a in articles if a["pu_dpgf"])
    avec_qte = sum(1 for a in articles if a["quantite_dpgf"])
    lots = list(dict.fromkeys(a["lot"] for a in articles))
    for lec in lectures:
        if lec["methode"] == "contenu":
            avertissements.append("Feuille « %s » : pas d'en-tête reconnue, colonnes devinées d'après leur contenu. "
                                  "Vérifiez la lecture." % lec["feuille"])
        manquantes = [LIBELLES_ROLES[k] for k in ("quantite", "pu") if k not in lec["colonnes"]]
        if manquantes:
            avertissements.append("Feuille « %s » : pas de colonne %s." % (lec["feuille"], " ni ".join(manquantes)))
    return {
        "fichier": nom_fichier,
        "feuilles": [{k: lec[k] for k in ("feuille", "entete", "confiance", "methode", "nb_lignes")}
                     | {"colonnes": {k: lettre(v) for k, v in lec["colonnes"].items()}} for lec in lectures],
        "feuilles_ignorees": [f["nom"] for f in feuilles if f["nom"] not in {l["feuille"] for l in lectures}],
        "articles": articles,
        "titres": titres,
        "nb_articles": nb,
        "nb_lots": len(lots),
        "lots": lots[:60],
        "nb_titres": len(titres),
        "part_prix": round(avec_prix / nb, 2) if nb else 0,
        "part_quantites": round(avec_qte / nb, 2) if nb else 0,
        "nb_pm": sum(1 for a in articles if a["pm"]),
        "nb_options": sum(1 for a in articles if a["option"]),
        "avertissements": avertissements,
    }


def apercu(dpgf, contenu, nom_fichier, n_max=400):
    """Lecture ligne par ligne, pour que l'utilisateur vérifie ce que le code a compris."""
    feuilles = {f["nom"]: f for f in lire_classeur(contenu, nom_fichier)}
    sortie = []
    for lec in dpgf["feuilles"]:
        f = feuilles.get(lec["feuille"])
        if not f:
            continue
        cols = {k: index_lettre(v) for k, v in lec["colonnes"].items()}
        elements = {e["ligne"]: e for e in lire_feuille(f, lec["entete"] - 1, cols, len(dpgf["feuilles"]) > 1)}
        lignes = []
        n_cols = max((len(l) for l in f["grille"]), default=0)
        for r, l in enumerate(f["grille"][:n_max]):
            e = elements.get(r + 1)
            genre = "entete" if r + 1 == lec["entete"] else (e["type"] if e else ("" if r + 1 > lec["entete"] else "avant"))
            lignes.append({"n": r + 1, "genre": genre, "niveau": e.get("niveau") if e else None,
                           "cellules": [texte_cellule(c)[:80] for c in l]})
        sortie.append({**lec, "lettres": [lettre(c) for c in range(n_cols)], "lignes": lignes,
                       "description_lignes": "description ajoutée à l'article précédent"})
    return sortie


# --------------------------------------------------------------------------------------------------
# Écriture : le DPGF d'origine, rempli (mise en page et formules conservées)
# --------------------------------------------------------------------------------------------------

def remplir(contenu, nom_fichier, dpgf, valeurs, tracabilite, mode_prix):
    """valeurs = {code article: {"quantite": float|None, "pu": float|None, "commentaire": str}}.
    Renvoie les octets d'un .xlsx : le classeur d'origine avec les quantités (et les prix si demandés)
    écrits dans ses propres colonnes, un commentaire sur chaque cellule remplie, et un onglet de traçabilité."""
    from openpyxl import Workbook, load_workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Font, PatternFill

    ext = nom_fichier.lower().rsplit(".", 1)[-1]
    if ext in ("xlsx", "xlsm"):
        wb = load_workbook(io.BytesIO(contenu))  # les macros d'un .xlsm ne sont pas conservées (le résultat est un .xlsx)
    else:  # .xls : on recopie les valeurs dans un classeur neuf (la mise en forme d'origine est perdue)
        wb = Workbook()
        wb.remove(wb.active)
        for f in lire_classeur(contenu, nom_fichier):
            ws = wb.create_sheet(f["nom"][:31])
            for r, l in enumerate(f["grille"], 1):
                for c, cel in enumerate(l, 1):
                    if cel.v is not None:
                        ws.cell(row=r, column=c, value=cel.v).font = Font(bold=cel.gras, italic=cel.italique)
    jaune = PatternFill("solid", fgColor="FFF2CC")
    colonnes = {f["feuille"]: {k: index_lettre(v) + 1 for k, v in f["colonnes"].items()} for f in dpgf["feuilles"]}
    ecrits = 0
    for a in dpgf["articles"]:
        v = valeurs.get(a["code"])
        if v is None or a["feuille"] not in wb.sheetnames:
            continue
        ws, cols, r = wb[a["feuille"]], colonnes[a["feuille"]], a["ligne"]
        cq, cp, ct = cols.get("quantite"), cols.get("pu"), cols.get("total")
        if cq and not a["formule_quantite"]:
            cel = ws.cell(row=r, column=cq)
            if a["quantite_dpgf"] is not None:
                pass  # la quantité du DPGF (celle du maître d'œuvre) n'est jamais écrasée : on commente seulement
            elif v.get("quantite") is not None:
                cel.value = round(v["quantite"], 3)
                ecrits += 1
            else:
                cel.fill = jaune
            if v.get("commentaire"):
                cel.comment = Comment(v["commentaire"][:1500], "SurveIA", width=420, height=180)
        if cp and mode_prix == "bibliotheque" and v.get("pu") is not None and not a["formule_pu"] and a["pu_dpgf"] is None:
            ws.cell(row=r, column=cp).value = v["pu"]
            if v.get("source_prix"):
                ws.cell(row=r, column=cp).comment = Comment(v["source_prix"][:500], "SurveIA", width=320, height=90)
        if ct and cq and cp and ws.cell(row=r, column=ct).value in (None, "") and not a["formule_total"]:
            ws.cell(row=r, column=ct).value = "=%s%d*%s%d" % (lettre(cq - 1), r, lettre(cp - 1), r)
    # Onglet de traçabilité : d'où vient chaque quantité
    nom = "SurveIA - traçabilité"
    if nom in wb.sheetnames:
        del wb[nom]
    wt = wb.create_sheet(nom)
    entetes = ["Feuille", "Ligne", "N°", "Désignation", "Unité", "Quantité SurveIA", "Quantité d'origine",
               "Prix unitaire", "Source du prix", "Éléments de la maquette", "Statut"]
    wt.append(entetes)
    for c in wt[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    for t in tracabilite:
        wt.append([t.get(k) for k in ("feuille", "ligne", "numero", "designation", "unite", "quantite", "quantite_dpgf",
                                     "pu", "source_prix", "sources", "statut")])
    for ligne in wt.iter_rows(min_row=2):
        for c in ligne:
            if isinstance(c.value, str) and c.value.startswith("="):
                c.data_type = "s"  # texte venu d'un fichier ou de l'IA : jamais une formule
    for col, larg in zip("ABCDEFGHIJK", (16, 7, 10, 50, 7, 14, 14, 12, 34, 70, 22)):
        wt.column_dimensions[col].width = larg
    wt.freeze_panes = "A2"
    tampon = io.BytesIO()
    wb.save(tampon)
    return tampon.getvalue(), ecrits
