"""Liaison entre le DPGF lu (lecture_dpgf.py) et le chiffrage.

- Les articles du DPGF deviennent les « lignes de prix » du chiffrage : l'IA associe les éléments de la maquette
  aux articles du DPGF, exactement comme elle le fait avec une bibliothèque de prix.
- Trois modes de prix :
    "dpgf"          prix unitaires du DPGF seulement (un article sans prix reste à 0 et est signalé) ;
    "bibliotheque"  prix du DPGF s'il y en a, sinon prix de la bibliothèque fournie, par rapprochement
                    automatique de la désignation (code, sans IA) ;
    "aucun"         quantités seulement.
- Vérification des citations du CCTP faites par l'IA : l'article doit exister et l'extrait doit se retrouver
  mot pour mot dans le texte du CCTP, sinon la citation est signalée et la confiance plafonnée.
Le DPGF et le CCTP ne sont jamais modifiés ici.
"""
import re

from lecture_dpgf import normaliser

MODES_PRIX = ("dpgf", "bibliotheque", "aucun")
LIBELLES_MODE = {"dpgf": "prix du DPGF", "bibliotheque": "prix du DPGF, complétés par la bibliothèque",
                 "aucun": "quantités seulement (sans prix)"}
CHAMPS_DPGF = ("chapitre", "description", "pm", "option", "cctp_ref", "ordre", "quantite_dpgf", "pu_source",
               "sans_prix", "prix_remarque", "feuille", "ligne_dpgf")

# Unités équivalentes pour rapprocher un article d'une ligne de bibliothèque
FAMILLE_UNITE = {"u": "u", "ens": "u", "ft": "ft", "m2": "m2", "m3": "m3", "ml": "ml"}
MOTS_VIDES = set("de du des la le les un une et en sur sous avec pour par au aux a d l y compris fourniture pose "
                 "fourni pose posee prix ouvrage ouvrages type dont".split())


def _racine(m):
    return m[:-1] if len(m) > 4 and m[-1] in "sx" and not m[-2].isdigit() else m


def mots(texte):
    t = re.sub(r"(\d)\s*[x×]\s*(\d)", r"\1 \2", normaliser(texte).replace("×", "x"))
    t = re.sub(r"(\d)\s*[x×]\s*(\d)", r"\1 \2", t)  # 60 x 60 x 10 : deux passes pour les chevauchements
    return {_racine(m) for m in t.split() if m not in MOTS_VIDES and (len(m) > 1 or m.isdigit())}


def famille(unite):
    u = normaliser(unite).replace(" ", "")
    return FAMILLE_UNITE.get({"m²": "m2", "m³": "m3", "mc": "m2"}.get(u, u), u)


def rapprocher(article, bibliotheque):
    """Ligne de la bibliothèque qui décrit le mieux l'article du DPGF : (ligne, score) ou (None, 0).
    Même famille d'unité exigée ; score = recouvrement des mots (nombres compris : épaisseurs, sections)."""
    a = mots(article["designation"] + " " + (article.get("description") or ""))
    if not a:
        return None, 0.0
    fa = famille(article["unite"])
    meilleur, second, score_max = None, None, 0.0
    for p in bibliotheque or []:
        if famille(p["unite"]) != fa:
            continue
        b = mots(p["designation"])
        if not b:
            continue
        commun = a & b
        nombres_a = {m for m in a if any(c.isdigit() for c in m)}
        nombres_b = {m for m in b if any(c.isdigit() for c in m)}
        if nombres_a and nombres_b and not (nombres_a & nombres_b):
            continue  # épaisseurs / sections différentes : ce n'est pas la même ligne
        s = 2 * len(commun) / (len(a) + len(b))
        if s > score_max:
            meilleur, second, score_max = p, meilleur, s
    if meilleur is None or score_max < 0.45:
        return None, 0.0
    return meilleur, round(score_max, 2)


def catalogue(dpgf, mode_prix, bibliotheque=None):
    """Lignes de prix tirées du DPGF, au même format que la bibliothèque, avec les champs propres au DPGF."""
    if mode_prix not in MODES_PRIX:
        raise ValueError("Mode de prix inconnu : %s" % mode_prix)
    lignes = []
    for a in dpgf["articles"]:
        pu, source, sans_prix, remarque = 0.0, "", False, ""
        if mode_prix == "aucun":
            source, sans_prix = "Sans prix (quantités seulement)", True
        elif a.get("pu_dpgf") is not None:
            pu, source = float(a["pu_dpgf"]), "Prix du DPGF"
        elif a.get("pm"):
            source = "Pour mémoire"
        elif mode_prix == "bibliotheque" and bibliotheque:
            p, s = rapprocher(a, bibliotheque)
            if p is not None:
                pu, source = float(p["pu"]), "Bibliothèque : %s (%s), rapprochement automatique (%d %%)" % (
                    p["code"], p["designation"], round(s * 100))
                if s < 0.85:
                    remarque = "Prix rapproché automatiquement de la bibliothèque (%s, %d %% de ressemblance) : à confirmer" % (
                        p["code"], round(s * 100))
            else:
                sans_prix, source = True, "Aucun prix dans le DPGF ni dans la bibliothèque"
        else:
            sans_prix, source = True, "Aucun prix dans le DPGF"
        lignes.append({
            "code": a["code"], "lot": a["lot"] or "Divers", "designation": a["designation"],
            "unite": a["unite"], "pu": pu, "dpgf": True,
            "chapitre": a.get("chapitre") or "", "description": a.get("description") or "",
            "pm": bool(a.get("pm")), "option": bool(a.get("option")), "cctp_ref": a.get("cctp_ref") or "",
            "ordre": a.get("ordre", 0), "quantite_dpgf": a.get("quantite_dpgf"),
            "pu_source": source, "sans_prix": sans_prix and not a.get("pm"), "prix_remarque": remarque,
            "feuille": a.get("feuille"), "ligne_dpgf": a.get("ligne"),
        })
    return lignes


def texte_pour_ia(lignes):
    """Articles du DPGF présentés à l'IA : code | lot › chapitre | désignation | unité | description | réf. CCTP."""
    sortie = []
    for p in lignes:
        lot = p["lot"] + (" › " + p["chapitre"] if p.get("chapitre") else "")
        marques = (" [POUR MÉMOIRE]" if p.get("pm") else "") + (" [OPTION]" if p.get("option") else "")
        morceaux = [p["code"], lot, p["designation"] + marques, p["unite"]]
        if p.get("description"):
            morceaux.append(" ".join(p["description"].split())[:300])
        if p.get("cctp_ref"):
            morceaux.append("réf. CCTP " + p["cctp_ref"])
        sortie.append(" | ".join(morceaux))
    return "\n".join(sortie)


def numeros_cites(texte):
    """Numéros d'articles contenus dans une cellule « Réf. CCTP » (« art. 2.3 », « 7.1 et 7.2 »...)."""
    return re.findall(r"\d{1,3}(?:\.\d{1,3}){0,5}", str(texte or ""))


def _numero(n):
    return re.sub(r"^(?:art(?:icle)?\.?\s*)", "", str(n or "").strip().lower()).strip(" .")


def texte_normalise(cctp):
    if "_norm" not in cctp:
        cctp["_norm"] = " " + normaliser(cctp.get("texte", "")) + " "
    return cctp["_norm"]


def verifier_citation(cctp, numero, extrait):
    """(ok, motif) : l'article cité existe-t-il dans le CCTP, et l'extrait y figure-t-il mot pour mot ?"""
    if not cctp:
        return True, ""
    numero = _numero(numero)
    numeros = {_numero(a["numero"]) for a in cctp.get("articles") or []}
    if numero and numeros and numero not in numeros:
        return False, "l'article %s n'existe pas dans le CCTP" % numero
    morceaux = [normaliser(m) for m in re.split(r"…|\.{3}|\[\.\.\.\]", str(extrait or ""))]
    morceaux = [m for m in morceaux if len(m.split()) >= 2]
    if not morceaux:
        return (True, "") if numero and numeros else (False, "aucun extrait du CCTP fourni pour la justifier")
    texte = texte_normalise(cctp)
    for m in morceaux:
        if (" " + m + " ") not in texte:
            return False, "l'extrait cité ne figure pas mot pour mot dans le CCTP"
    return True, ""
