"""
Tests de bout en bout du POC, sans clé API ni réseau : un faux DeepSeek local (faux_deepseek.py)
imite les réponses du modèle, y compris ses défauts (réponses tronquées, groupes oubliés, paramètre refusé).

Usage (depuis la racine du dépôt) :
    python3 tests/test_bout_en_bout.py            # tous les scénarios
    python3 tests/test_bout_en_bout.py base vis   # certains scénarios seulement

Dépendance supplémentaire : aucune (openpyxl, déjà requis par l'application).
La mémoire des correspondances utilisée pendant les tests est un fichier temporaire :
la mémoire réelle de l'application n'est jamais modifiée.
"""
import http.client, json, os, re, subprocess, sys, tempfile, time, urllib.request

S = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(S)
PORT = int(os.environ.get("PORT_TEST", "8799"))
PORT_FAUX = int(os.environ.get("PORT_FAUX", "9911"))
B = "http://127.0.0.1:%d" % PORT
TMP = tempfile.mkdtemp(prefix="poc_tests_")
RESULTATS = []


def verifier(cond, texte):
    RESULTATS.append((bool(cond), texte))
    print(("  OK    " if cond else "  ÉCHEC ") + texte)


def req(chemin, corps=None, brut=None, entetes=None):
    donnees = brut if brut is not None else (json.dumps(corps).encode() if corps is not None else None)
    r = urllib.request.Request(B + chemin, data=donnees, method="POST" if donnees is not None else "GET", headers=entetes or {})
    try:
        with urllib.request.urlopen(r, timeout=120) as f:
            return f.status, f.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def j(chemin, corps=None, brut=None):
    code, b = req(chemin, corps, brut)
    return json.loads(b)


def tache(chemin, corps=None, brut=None):
    d = j(chemin, corps, b"" if corps is None and brut is None else brut)
    if "erreur" in d:
        return d
    while True:
        t = j("/api/tache?id=" + d["tache"])
        if t["etat"] == "fini":
            return t["resultat"]
        if t["etat"] == "erreur":
            return {"erreur": t["erreur"]}
        time.sleep(0.3)


def lignes(est):
    return {l["code"]: l for l in est["lignes"]}


def evaluer_estimation(chemin):
    """Petit calculateur des formules de l'onglet Estimation (celles que produit l'application),
    pour vérifier les montants sans Excel ni LibreOffice. Renvoie (valeurs, nb_formules, nb_erreurs)."""
    from openpyxl import load_workbook
    wb = load_workbook(chemin)
    bib = {r[0]: r[4] for r in wb["Bibliothèque de prix"].iter_rows(min_row=2, values_only=True) if r[0]}
    ws = wb["Estimation"]
    brut = {c.coordinate: c.value for ligne in ws.iter_rows() for c in ligne}
    cache, erreurs, nb = {}, [0], [0]

    def val(ref):
        if ref in cache:
            return cache[ref]
        v = brut.get(ref)
        if isinstance(v, str) and v.startswith("="):
            nb[0] += 1
            f = v[1:]
            try:
                if f.startswith("IFERROR(INDEX("):
                    code = val("B" + re.search(r"MATCH\(B(\d+)", f).group(1))
                    r = bib.get(code, 0)
                elif f.startswith("SUM("):
                    a, b = re.match(r"SUM\(H(\d+):H(\d+)\)", f).groups()
                    r = sum(val("H%d" % i) or 0 for i in range(int(a), int(b) + 1))
                elif "*" in f:
                    x, y = f.split("*")
                    r = (val(x) or 0) * (val(y) or 0)
                else:
                    r = sum(val(x) or 0 for x in f.split("+"))
            except Exception:
                erreurs[0] += 1
                r = None
            v = r
        cache[ref] = v
        return v

    valeurs = {ref: val(ref) for ref in brut}
    return valeurs, nb[0], erreurs[0]


def total_ht(chemin):
    valeurs, _, _ = evaluer_estimation(chemin)
    ligne = next(k[1:] for k, v in valeurs.items() if k.startswith("G") and v == "Total HT")
    return valeurs["H" + ligne]


def excel_recalcule(nom):
    code, b = req("/api/excel")
    chemin = os.path.join(TMP, nom)
    open(chemin, "wb").write(b)
    _, nb, err = evaluer_estimation(chemin)
    return {"status": "success" if err == 0 else "errors_found", "total_errors": err, "total_formulas": nb}, chemin


def base():
    print("== Clé et modèles")
    d = j("/api/cle", {"cle": "sk-test-1234567890", "modele": "deepseek-flash"})
    verifier(d.get("ok"), "clé acceptée")
    verifier("deepseek-v5-preview" in d.get("libelles", {}), "modèle inconnu du code proposé depuis l'API : %s" % list(d.get("libelles", {})))
    verifier(d["libelles"].get("deepseek-flash", "").startswith("DeepSeek V4.1"), "libellé français conservé pour les modèles connus")

    print("== Sécurité")
    c = http.client.HTTPConnection("127.0.0.1", PORT)
    c.request("GET", "/api/etat", headers={"Host": "evil.example:%d" % PORT}); verifier(c.getresponse().status == 403, "Host étranger refusé (DNS rebinding)")
    c = http.client.HTTPConnection("127.0.0.1", PORT)
    c.request("POST", "/api/question", body=b"{}", headers={"Origin": "https://site-malveillant.com"}); verifier(c.getresponse().status == 403, "Origin étranger refusé")
    c = http.client.HTTPConnection("127.0.0.1", PORT)
    c.request("GET", "/api/etat", headers={"Origin": "http://localhost:%d" % PORT, "Host": "localhost:%d" % PORT}); verifier(c.getresponse().status == 200, "localhost accepté")

    print("== IFC sans CCTP (locaux + sanitaires)")
    j("/api/memoire_vider", {})
    m = tache("/api/metre?demo=ifc")
    verifier(m.get("nb_locaux") == 18, "18 locaux IfcSpace lus (%s)" % m.get("nb_locaux"))
    e1 = tache("/api/chiffrer", {"hauteur": 2.7})
    L = lignes(e1)
    verifier(L.get("PL-01", {}).get("quantite") == 6 and L.get("PL-02", {}).get("quantite") == 4, "6 WC et 4 lavabos chiffrés")
    verifier(abs(L.get("RS-01", {}).get("quantite", 0) - (32.07 + 55.35 + 28.8)) < 0.05, "carrelage = accueil + circulations + sanitaires (%s m²)" % L.get("RS-01", {}).get("quantite"))
    verifier("FP-01" in L and "FP-02" in L, "faux plafonds chiffrés depuis les locaux")
    verifier(e1["appels"] >= 2 and e1["depuis_memoire"] == 0, "%d appels (lots de 20 groupes), rien en mémoire" % e1["appels"])
    verifier(any("Local technique" in n["element"] for n in e1["non_chiffres"]), "local technique signalé non chiffré")

    print("== Mémoire des correspondances")
    t0 = time.time(); e2 = tache("/api/chiffrer", {"hauteur": 2.7}); dt = time.time() - t0
    verifier(e2["appels"] == 0 and e2["depuis_memoire"] == e2["nb_groupes"], "2e calcul : 0 appel, %d/%d groupes en mémoire (%.2f s)" % (e2["depuis_memoire"], e2["nb_groupes"], dt))
    verifier(e2["total_ht"] == e1["total_ht"], "même total : %s €" % e2["total_ht"])
    e3 = tache("/api/chiffrer", {"hauteur": 2.7, "forcer": True})
    verifier(e3["appels"] >= 2 and e3["depuis_memoire"] == 0, "« Relancer » ignore la mémoire (%d appels)" % e3["appels"])
    verifier(e3["jetons_cache"] > 0, "cache DeepSeek exploité grâce au préfixe commun (%d jetons)" % e3["jetons_cache"])
    verifier(os.path.exists(os.environ["CHIFFRAGE_MEMOIRE"]), "mémoire enregistrée sur disque")

    print("== Question")
    q = tache("/api/question", {"question": "Top ?"})
    verifier("postes principaux" in q.get("reponse", ""), "réponse reçue")

    print("== IFC + CCTP")
    j("/api/cctp?demo=1", brut=b"")
    e4 = tache("/api/chiffrer", {"hauteur": 2.7})
    verifier(len(e4["absents"]) == 3, "3 ouvrages du CCTP non modélisés (dont 1 hors bibliothèque)")
    verifier(any(l["ecarts"] for l in e4["lignes"]), "écarts maquette / CCTP signalés")
    r, chemin = excel_recalcule("v4_ifc_cctp.xlsx")
    verifier(r.get("status") == "success" and r.get("total_errors") == 0, "Excel recalculé sans erreur (%s formules)" % r.get("total_formulas"))
    from openpyxl import load_workbook
    ws = load_workbook(chemin)["Estimation"]
    lignes_xl = [[c.value for c in row] for row in ws.iter_rows()]
    a_compl = [l for l in lignes_xl if l[1] in ("GO-09", "ET-02")]
    verifier(len(a_compl) == 2 and all(isinstance(l[7], str) and l[7].startswith("=F") for l in a_compl), "ouvrages CCTP absents = lignes chiffrables avec formules")
    verifier(any(isinstance(l[0], str) and "sans ligne dans la bibliothèque" in l[0] for l in lignes_xl), "ouvrage hors bibliothèque listé à part")
    verifier(any(isinstance(l[0], str) and "prix de démonstration fictifs" in l[0] for l in lignes_xl), "mention « prix fictifs » avec la bibliothèque d'exemple")
    ws.cell(row=[i for i, l in enumerate(lignes_xl, 1) if l[1] == "GO-09"][0], column=6, value=28)
    wb = ws.parent; wb.save(chemin)
    tot = total_ht(chemin)
    verifier(abs(tot - (e4["total_ht"] + 28 * 320)) < 0.01, "saisie de 28 m³ de fondations → total HT %s € (+8 960 €)" % tot)

    print("== Plan DXF + CCTP : changement de hauteur sans IA")
    tache("/api/metre?demo=dxf")
    e5 = tache("/api/chiffrer", {"hauteur": 3.0})
    e6 = tache("/api/chiffrer", {"hauteur": 2.5})
    q5, q6 = lignes(e5)["GO-01"]["quantite"], lignes(e6)["GO-01"]["quantite"]
    verifier(e6["appels"] == 0, "hauteur 3,00 → 2,50 m : aucun appel à DeepSeek")
    verifier(abs(q6 - q5 * 2.5 / 3.0) < 0.01, "voiles recalculés : %s → %s m²" % (q5, q6))

    print("== Bibliothèque personnelle")
    csv = open(os.path.join(APP, "bibliotheque_prix.csv"), "rb").read()
    j("/api/bibliotheque?nom=mes_prix.csv", brut=csv)
    tache("/api/metre?demo=ifc")
    tache("/api/chiffrer", {"hauteur": 2.7})
    r, chemin = excel_recalcule("v4_perso.xlsx")
    texte = " ".join(str(c) for row in load_workbook(chemin)["Estimation"].iter_rows(values_only=True) for c in row if c)
    verifier("fictifs" not in texte and "bibliothèque fournie" in texte, "plus de mention « fictifs » avec une bibliothèque personnelle")


def tronque():
    print("== Réponses tronquées (TRONQUER=5)")
    j("/api/cle", {"cle": "sk-test-1234567890"})
    tache("/api/metre?demo=ifc")
    e = tache("/api/chiffrer", {"hauteur": 2.7, "forcer": True})
    verifier("erreur" not in e, "pas d'échec global : %s" % e.get("erreur", "ok"))
    verifier(e.get("decoupages", 0) > 0, "%s paquet(s) redécoupé(s)" % e.get("decoupages"))
    manquants = [n for n in e.get("non_chiffres", []) if "même après relance" in n["raison"]]
    verifier(not manquants, "aucun groupe perdu (%d manquants)" % len(manquants))
    verifier(len(e.get("lignes", [])) >= 20, "%d lignes chiffrées" % len(e.get("lignes", [])))


def oublie():
    print("== Groupes oubliés (OUBLIER=1)")
    j("/api/cle", {"cle": "sk-test-1234567890"})
    tache("/api/metre?demo=ifc")
    e = tache("/api/chiffrer", {"hauteur": 2.7, "forcer": True})
    verifier(e.get("relances", 0) > 0, "%s groupe(s) oublié(s) redemandé(s)" % e.get("relances"))
    manquants = [n for n in e.get("non_chiffres", []) if "même après relance" in n["raison"]]
    verifier(not manquants, "aucun groupe perdu après relance")


def fabriquer_gros_ifc(copies=700):
    """Maquette volumineuse (≈ 80 Mo) : la maquette de démo recopiée 700 fois."""
    chemin = os.path.join(TMP, "gros.ifc")
    if os.path.exists(chemin):
        return chemin
    src = open(os.path.join(APP, "maquette_demo.ifc")).read()
    tete, donnees = src.split("DATA;\n", 1)
    donnees = donnees.rsplit("ENDSEC;", 1)[0]
    lignes = [l for l in donnees.splitlines() if l.strip()]
    maxi = max(int(re.match(r"#(\d+)", l).group(1)) for l in lignes)
    sortie = list(lignes)
    for k in range(1, copies):
        d = k * maxi
        sortie += [re.sub(r"#(\d+)", lambda m: "#%d" % (int(m.group(1)) + d), l) for l in lignes
                   if not l.split("=", 1)[1].startswith(("IFCPROJECT", "IFCUNITASSIGNMENT", "IFCSIUNIT"))]
    open(chemin, "w").write(tete + "DATA;\n" + "\n".join(sortie) + "\nENDSEC;\nEND-ISO-10303-21;\n")
    return chemin


def gros():
    print("== Gros IFC (≈ 80 Mo) en tâche de fond")
    j("/api/cle", {"cle": "sk-test-1234567890"})
    d = j("/api/metre?nom=gros.ifc", brut=open(fabriquer_gros_ifc(), "rb").read())
    verifier("tache" in d, "réponse immédiate avec un identifiant de tâche")
    t0 = time.time()
    while True:
        t = j("/api/tache?id=" + d["tache"])
        if t["etat"] != "en_cours":
            break
        time.sleep(0.5)
    verifier(t["etat"] == "fini" and t["resultat"]["nb_elements"] == 70000, "métré terminé en %.1f s (%s éléments)" % (time.time() - t0, t["resultat"]["nb_elements"] if t["resultat"] else t))


def repli():
    print("== Paramètre refusé (REFUSER_THINKING=1)")
    j("/api/cle", {"cle": "sk-test-1234567890"})
    tache("/api/metre?demo=ifc")
    e = tache("/api/chiffrer", {"hauteur": 2.7, "forcer": True})
    verifier("erreur" not in e and len(e["lignes"]) > 10, "repli automatique sans « thinking » : %s lignes" % len(e.get("lignes", [])))


def vis():
    import struct
    print("== Visionneuse")
    j("/api/cle", {"cle": "sk-test-1234567890"})
    m = tache("/api/metre?demo=ifc")
    verifier("_lecteur" not in m and "_elements" not in m, "données internes non envoyées au navigateur")
    r = j("/api/visionneuse/preparer", brut=b"")
    if "tache" in r:
        while j("/api/tache?id=" + r["tache"])["etat"] == "en_cours":
            time.sleep(0.2)
    code, b = req("/api/visionneuse/geometrie")
    n = struct.unpack("<I", b[:4])[0]
    h = json.loads(b[4:4 + n])
    verifier(code == 200 and len(h["elements"]) == 118 and h["sans_geometrie"] == 0, "géométrie 3D : 118 éléments, tous avec une forme")
    groupes_3d = {e[1] for e in h["elements"]}
    verifier(groupes_3d == {g["id"] for g in m["groupes"]}, "chaque groupe du métré a ses objets en 3D")
    taille = 4 + n + h["nb_sommets"] * 16 + h["nb_indices"] * 4
    verifier(len(b) == taille, "binaire cohérent (%d octets)" % len(b))
    r2 = j("/api/visionneuse/preparer", brut=b"")
    verifier(r2.get("pret") is True, "géométrie gardée en mémoire (2e demande immédiate)")
    tache("/api/metre?demo=dxf")
    code, b = req("/api/visionneuse/geometrie")
    verifier(code == 404, "plus de 3D après chargement d'un plan")
    d = j("/api/visionneuse/dessin")
    verifier(len(d["prims"]) == 182 and len(d["emprises"]) == 42, "dessin 2D : 182 traits, 42 blocs")
    blocs = {d["blocs"][c["bloc"]] for c in d["groupes"].values() if "bloc" in c}
    verifier({"P83", "PCF93", "F120", "F240", "WC", "LAVABO"} <= blocs, "blocs du métré retrouvés dans le dessin")
    locaux = [c for c in d["groupes"].values() if "prims" in c]
    verifier(sum(len(c["prims"]) for c in locaux) == 9, "9 locaux cliquables")


SCENARIOS = {  # nom : (fonction, variables d'environnement du faux DeepSeek)
    "vis": (vis, {}), "base": (base, {}), "tronque": (tronque, {"TRONQUER": "5"}), "oublie": (oublie, {"OUBLIER": "1"}),
    "repli": (repli, {"REFUSER_THINKING": "1"}), "gros": (gros, {}),
}


def attendre(port, delai=15):
    fin = time.time() + delai
    while time.time() < fin:
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/")
            return True
        except OSError:
            time.sleep(0.2)
    return False


def executer(nom):
    fonction, env_faux = SCENARIOS[nom]
    env = dict(os.environ, PORT_FAUX=str(PORT_FAUX), **env_faux)
    faux = subprocess.Popen([sys.executable, os.path.join(S, "faux_deepseek.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    memoire = os.path.join(TMP, "memoire_%s.json" % nom)
    os.environ["CHIFFRAGE_MEMOIRE"] = memoire
    env_app = dict(os.environ, DEEPSEEK_BASE_URL="http://127.0.0.1:%d" % PORT_FAUX, CHIFFRAGE_MEMOIRE=memoire)
    appli = subprocess.Popen([sys.executable, os.path.join(APP, "chiffrage_ia.py"), "--sans-navigateur", "--port", str(PORT)],
                             env=env_app, cwd=APP, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        if not (attendre(PORT) and attendre(PORT_FAUX)):
            verifier(False, "démarrage de l'application ou du faux DeepSeek (ports %d / %d libres ?)" % (PORT, PORT_FAUX))
            return
        fonction()
    finally:
        appli.terminate(); faux.terminate()
        appli.wait(10); faux.wait(10)
        erreurs = appli.stderr.read().decode(errors="replace")
        if "Traceback" in erreurs:
            verifier(False, "erreur Python dans l'application :\n" + erreurs[-1500:])


if __name__ == "__main__":
    for nom in sys.argv[1:] or list(SCENARIOS):
        executer(nom)
    print("\nRÉSULTAT : %d/%d" % (sum(ok for ok, _ in RESULTATS), len(RESULTATS)))
    sys.exit(0 if all(ok for ok, _ in RESULTATS) else 1)
