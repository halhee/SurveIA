"""Faux serveur DeepSeek pour tester le POC hors ligne, sans clé ni réseau (réponses imitant le modèle).
Variables d'environnement :
  DELAI=s          durée de chaque réponse
  REFUSER_THINKING=1   renvoie une erreur 400 si le paramètre « thinking » est présent
  TRONQUER=n       coupe (finish_reason=length) toute réponse de correspondance de plus de n groupes
  OUBLIER=1        oublie le dernier groupe de chaque paquet (au 1er passage seulement)
"""
import json, os, re, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAI = float(os.environ.get("DELAI", "0"))
REFUSER_THINKING = os.environ.get("REFUSER_THINKING") == "1"
TRONQUER = int(os.environ.get("TRONQUER", "0"))
OUBLIER = os.environ.get("OUBLIER") == "1"
JOURNAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "faux_params.log")
EN_COURS = [0, 0]
VERROU = threading.Lock()
PREFIXES_VUS = set()
DEJA_OUBLIES = set()

REGLES_IFC = [
    ("(IfcSpace) | type « Bureau", [("RS-02", "moyenne", "Bureau : sol souple."), ("FP-01", "moyenne", "Faux plafond dalles.")]),
    ("(IfcSpace) | type « Salle de réunion", [("RS-02", "moyenne", "Salle de réunion : sol souple."), ("FP-01", "moyenne", "Faux plafond dalles.")]),
    ("(IfcSpace) | type « Accueil", [("RS-01", "moyenne", "Accueil : carrelage."), ("FP-01", "moyenne", "Faux plafond dalles.")]),
    ("(IfcSpace) | type « Circulation", [("RS-01", "moyenne", "Circulation : carrelage."), ("FP-01", "moyenne", "Faux plafond dalles.")]),
    ("(IfcSpace) | type « Sanitaires", [("RS-01", "haute", "Sanitaires : carrelage."), ("FP-02", "moyenne", "Plafond hydrofuge.")]),
    ("(IfcSpace) | type « Local technique", []),
    ("WC suspendu", [("PL-01", "haute", "WC suspendu.")]),
    ("Lavabo", [("PL-02", "haute", "Lavabo sur colonne.")]),
    ("Dallage", [("GO-06", "haute", "Dallage BA 15 cm sur terre-plein.")]),
    ("toiture", [("GO-05", "haute", "Dalle BA 22 cm."), ("ET-01", "moyenne", "Étanchéité mentionnée dans le type ; isolant non précisé.")]),
    ("Dalle BA 20", [("GO-04", "haute", "Dalle pleine BA 20 cm.")]),
    ("hydro", [("CL-03", "haute", "Cloison 72/48 hydrofuge.")]),
    ("acoustique", [("CL-02", "haute", "Cloison 98/48 acoustique.")]),
    ("Cloison 72/48", [("CL-01", "haute", "Cloison 72/48 BA13.")]),
    ("Doublage", [("CL-04", "haute", "Doublage PSE 10+100.")]),
    ("Voile BA 20", [("GO-01", "haute", "Voile BA épaisseur 20 cm.")]),
    ("Poteau", [("GO-07", "haute", "Poteau béton armé, chiffré au m³.")]),
    ("Fenêtre alu", [("ME-01", "haute", "Fenêtre aluminium.")]),
    ("coupe-feu", [("MI-03", "haute", "Porte EI30 93x204.")]),
    ("Porte bois", [("MI-01", "moyenne", "Porte bois 83x204 ; âme non précisée dans la maquette.")]),
    ("entrée", [("ME-03", "haute", "Porte d'entrée alu vitrée.")]),
    ("Garde-corps", [("SR-02", "moyenne", "Garde-corps vitré.")]),
]
REGLES_PLAN = [
    ("Local « Bureau", "surface_sol", [("RS-02", "moyenne", "Bureaux : sol souple probable, à confirmer.")]),
    ("Circulation", "surface_sol", [("RS-01", "moyenne", "Circulation : carrelage probable.")]),
    ("Accueil", "surface_sol", [("RS-01", "moyenne", "Accueil : carrelage probable.")]),
    ("reunion", "surface_sol", [("RS-02", "moyenne", "Salle de réunion : sol souple probable.")]),
    ("Sanitaires", "surface_sol", [("RS-01", "haute", "Sanitaires : carrelage.")]),
    ("Local technique", "surface_sol", []),
    ("A-MUR-BA20", "mur", [("GO-01", "haute", "Calque de voiles BA 20.")]),
    ("HYDRO", "mur", [("CL-03", "haute", "Cloison 72 hydrofuge.")]),
    ("98-ACOU", "mur", [("CL-02", "haute", "Cloison 98 acoustique.")]),
    ("A-CLOISON-72", "mur", [("CL-01", "haute", "Cloison 72/48.")]),
    ("A-DOUBLAGE", "mur", [("CL-04", "moyenne", "Doublage, type d'isolant non précisé.")]),
    ("A-POTEAU", "element_vertical", [("GO-07", "haute", "Poteaux BA dessinés en contour.")]),
    ("A-COTATION", "ignorer", []),
    ("« PCF93", "comptage", [("MI-03", "haute", "Bloc porte coupe-feu 93.")]),
    ("« P83", "comptage", [("MI-01", "moyenne", "Porte 83, âme non précisée.")]),
    ("« PENTREE140", "comptage", [("ME-03", "haute", "Porte d'entrée 140.")]),
    ("« F120", "comptage", [("ME-01", "moyenne", "Fenêtre 120, matériau non précisé.")]),
    ("« F240", "comptage", [("ME-01", "moyenne", "Fenêtre 240, matériau non précisé.")]),
    ("BUREAU_1P", "ignorer", []), ("TABLE_REUNION", "ignorer", []),
    ("« WC", "comptage", [("PL-01", "moyenne", "WC.")]), ("« LAVABO", "comptage", [("PL-02", "moyenne", "Lavabo.")]),
]
ARTICLES = {"GO-01": "2.3", "GO-04": "2.4", "GO-05": "2.4", "GO-06": "2.2", "GO-07": "2.5", "ET-01": "3.1",
            "ME-01": "5.1", "ME-03": "5.2", "MI-02": "6.1", "MI-03": "6.2", "CL-01": "7.1", "CL-02": "7.2",
            "CL-03": "7.3", "CL-05": "7.4", "CL-06": "7.3", "SR-02": "8.1", "RS-01": "9.1", "RS-02": "9.2",
            "FP-01": "10.1", "FP-02": "10.1", "PL-01": "11.1", "PL-02": "11.1"}
ECARTS = [
    ("Doublage", "CL-05", "Maquette : doublage PSE collé ; le CCTP impose de la laine minérale sur ossature."),
    ("ydro", "CL-06", "Maquette : cloison 72/48 ; le CCTP demande du 98/48 hydrofuge."),
    ("Porte bois", "MI-02", "Le CCTP impose l'âme pleine ; la bibliothèque n'a qu'un bloc-porte âme pleine en 93x204."),
    ("« P83", "MI-02", "Le CCTP impose l'âme pleine ; la bibliothèque n'a qu'un bloc-porte âme pleine en 93x204."),
]


def avec_cctp(entree, ligne):
    ecart = ""
    for motcle, code, texte in ECARTS:
        if motcle in ligne:
            entree["ouvrages"] = [{"code": code, "confiance": "moyenne", "justification": "Choix conforme au CCTP."}]
            ecart = texte
            break
    for o in entree["ouvrages"]:
        o["article_cctp"] = ARTICLES.get(o["code"], "")
        o["extrait_cctp"] = "extrait de l'article %s" % o["article_cctp"] if o["article_cctp"] else ""
    entree["ecart_cctp"] = ecart
    if "Garde-corps" in ligne:
        entree["remarque"] = "Le CCTP (8.1) indique 6,00 ml."
    return entree


def correspondre(ligne, cctp):
    m = re.match(r"(D\d+) \|", ligne)
    if m:
        nat, ouv = "", []
        for motcle, n, lst in REGLES_PLAN:
            if motcle in ligne:
                nat, ouv = n, [{"code": c, "confiance": k, "justification": j} for c, k, j in lst]
                break
        e = {"groupe": m.group(1), "nature": nat, "ouvrages": ouv,
             "remarque": "" if ouv else ("Mobilier / cotation : hors marché de travaux." if nat == "ignorer" else "Aucune ligne de prix adaptée.")}
        return avec_cctp(e, ligne) if cctp and ouv else e
    m = re.match(r"(G\d+) \|", ligne)
    if not m:
        return None
    ouv = []
    for motcle, lst in REGLES_IFC:
        if motcle.lower() in ligne.lower():
            ouv = [{"code": c, "confiance": k, "justification": j} for c, k, j in lst]
            break
    e = {"groupe": m.group(1), "nature": "", "ouvrages": ouv, "remarque": "" if ouv else "Rien dans la bibliothèque."}
    return avec_cctp(e, ligne) if cctp and ouv else e


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, d):
        b = json.dumps(d).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def auth(self):
        if self.headers.get("Authorization") != "Bearer sk-test-1234567890":
            self.send(401, {"error": {"message": "Authentication Fails"}})
            return False
        return True

    def do_GET(self):
        if self.path == "/stats":
            return self.send(200, {"max_simultanes": EN_COURS[1]})
        time.sleep(DELAI / 2)
        if self.auth():
            self.send(200, {"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}, {"id": "deepseek-v5-preview"}]})

    def do_POST(self):
        if not self.auth():
            return
        corps = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = corps["messages"]
        tout = "\n".join(m["content"] for m in msgs)
        user = msgs[-1]["content"]
        prefixe = user.split("=== CONSIGNES ===")[0]
        with VERROU:
            cache = len(prefixe) // 4 if prefixe in PREFIXES_VUS else 0
            PREFIXES_VUS.add(prefixe)
        with open(JOURNAL, "a") as f:
            f.write(json.dumps({"thinking": corps.get("thinking"), "temperature": corps.get("temperature"),
                                "json": bool(corps.get("response_format")), "max_tokens": corps.get("max_tokens"),
                                "systeme_commun": msgs[0]["content"][:30]}) + "\n")
        if REFUSER_THINKING and "thinking" in corps:
            return self.send(400, {"error": {"message": "unknown parameter: thinking"}})
        with VERROU:
            EN_COURS[0] += 1; EN_COURS[1] = max(EN_COURS[1], EN_COURS[0])
        time.sleep(DELAI)
        with VERROU:
            EN_COURS[0] -= 1
        fin = "stop"
        if corps.get("response_format") and "Liste les ouvrages décrits au CCTP" in tout:
            plan = "DU PLAN 2D" in tout or "LE PLAN 2D" in tout
            absents = [
                {"lot": "02 Gros œuvre", "article": "2.1", "ouvrage": "Fondations : semelles filantes et isolées", "code": "GO-09", "info_quantite": "Volume indicatif : 28 m³", "remarque": "Non modélisées."},
                {"lot": "03 Étanchéité", "article": "3.2", "ouvrage": "Relevés d'étanchéité sur acrotères", "code": "ET-02", "info_quantite": "64 ml (périmètre)", "remarque": ""},
            ]
            if plan:
                absents.append({"lot": "10 Faux plafonds", "article": "10.1", "ouvrage": "Faux plafonds en dalles minérales 600x600", "code": "FP-01", "info_quantite": "", "remarque": ""})
            absents.append({"lot": "12 VRD", "article": "12.1", "ouvrage": "Raccordements aux réseaux", "code": "", "info_quantite": "", "remarque": "Lot absent de la bibliothèque."})
            contenu = json.dumps({"absents": absents}, ensure_ascii=False)
        elif corps.get("response_format"):
            cctp = "Un CCTP du projet est fourni" in tout
            lignes = [l for l in user.split("GROUPES D'ÉLÉMENTS")[1].splitlines() if re.match(r"[GD]\d+ \|", l)]
            sortie = [e for e in (correspondre(l, cctp) for l in lignes) if e]
            if OUBLIER and len(sortie) > 1:
                cle = sortie[-1]["groupe"]
                if cle not in DEJA_OUBLIES:
                    DEJA_OUBLIES.add(cle)
                    sortie = sortie[:-1]
            contenu = "```json\n" + json.dumps({"correspondances": sortie}, ensure_ascii=False) + "\n```"
            if TRONQUER and len(lignes) > TRONQUER:
                contenu, fin = contenu[:len(contenu) // 2], "length"
        else:
            bloc = user.split("CONTEXTE (JSON) :\n", 1)[1]
            ctx = json.JSONDecoder().raw_decode(bloc)[0]
            top = sorted(ctx["estimation"], key=lambda l: -l["total"])[:3]
            contenu = "Les 3 postes principaux :\n" + "\n".join("- **%s** : %.0f €" % (l["designation"], l["total"]) for l in top)
        self.send(200, {"choices": [{"message": {"role": "assistant", "content": contenu}, "finish_reason": fin}],
                        "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": len(contenu) // 4,
                                  "prompt_cache_hit_tokens": cache}})


ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT_FAUX", "9911"))), H).serve_forever()
