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
    bib = {r[0]: r[4] for r in wb["Prix du DPGF" if "Prix du DPGF" in wb.sheetnames else "Bibliothèque de prix"].iter_rows(min_row=2, values_only=True) if r[0]}
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


def ajuster(corps):
    d = j("/api/ajuster", corps)
    if "erreur" in d:
        print("     (réponse : %s)" % d["erreur"])
    return d.get("estimation") or d


def ajust():
    print("== Ajustements manuels (sans IA)")
    j("/api/cle", {"cle": "sk-test-1234567890"})
    m = tache("/api/metre?demo=ifc")
    verifier(m.get("nb_unites") == 118 and m.get("empreinte"), "118 objets chiffrables un par un")
    e0 = tache("/api/chiffrer", {"hauteur": 2.7})
    L0 = lignes(e0)
    voile = next(g for g in m["groupes"] if "Voile BA 20" in g["type"])
    objets = j("/api/unites?groupe=" + voile["id"])
    verifier(len(objets) == voile["nombre"] and all(o["codes"] == ["GO-01"] for o in objets), "%d voiles listés, tous en GO-01" % len(objets))
    deux = [o["id"] for o in objets[:2]]
    surf2 = sum(o["surface"] for o in objets[:2])

    e1 = ajuster({"action": "affecter", "unites": deux, "code": "GO-02", "mode": "remplacer"})
    L1 = lignes(e1)
    verifier(abs(L1["GO-01"]["quantite"] - (L0["GO-01"]["quantite"] - surf2)) < 0.01, "GO-01 perd exactement les 2 voiles (%.2f m²)" % surf2)
    verifier(abs(L1["GO-02"]["quantite"] - (L0.get("GO-02", {}).get("quantite", 0) + surf2)) < 0.01, "GO-02 les reçoit")
    src = [x for x in L1["GO-02"]["sources"] if x.get("unites")]
    verifier(src and sorted(src[0]["unites"]) == sorted(deux) and src[0]["origine"] == "manuel", "source « 2 objets » tracée (manuel)")
    s1 = next(x for x in L1["GO-01"]["sources"] if x["g"] == voile["id"])
    verifier(sorted(s1["sauf"]) == sorted(deux), "la visionneuse saura exclure ces 2 objets de GO-01")
    verifier(L1["GO-02"]["confiance"] == "manuelle" and L1["GO-02"]["origine"] in ("manuel", "mixte"), "ligne marquée manuelle")

    e2 = ajuster({"action": "retirer", "unites": deux, "code": "GO-02"})
    verifier(len(e2["exclus"]) == 1 and e2["exclus"][0]["nombre"] == 2, "retirés de leur seule ligne : 2 objets exclus du chiffrage")
    e3 = ajuster({"action": "annuler"}); e4 = ajuster({"action": "annuler"})
    verifier(e4["total_ht"] == e0["total_ht"] and e4["historique"]["retablir"] == 2, "annuler × 2 : retour au total initial (%s €)" % e4["total_ht"])
    e5 = ajuster({"action": "retablir"})
    verifier(e5["total_ht"] == e1["total_ht"], "rétablir : %s €" % e5["total_ht"])

    dallage = next(g for g in m["groupes"] if "Dallage" in g["type"])
    e6 = ajuster({"action": "affecter", "groupes": [dallage["id"]], "code": "RS-01", "mode": "ajouter"})
    L6 = lignes(e6)
    verifier(dallage["id"] in L6["RS-01"]["groupes"] and dallage["id"] in L6["GO-06"]["groupes"], "type entier ajouté à une 2e ligne (GO-06 + RS-01)")
    verifier(j("/api/etat").get("corrections") == 1, "correction retenue pour les prochains projets")

    e7 = ajuster({"action": "quantite", "code": "GO-06", "valeur": "123,5"})
    l7 = lignes(e7)["GO-06"]
    verifier(l7["quantite"] == 123.5 and l7["qte_manuelle"] and l7["quantite_calculee"], "quantité forcée à 123,5 (calculée gardée : %s)" % l7["quantite_calculee"])
    e8 = ajuster({"action": "supplement", "code": "GO-09", "valeur": 28, "libelle": "Fondations (CCTP 2.1)"})
    verifier(lignes(e8)["GO-09"]["quantite"] == 28 and abs(e8["total_ht"] - e7["total_ht"] - 28 * 320) < 0.01, "ouvrage non modélisé saisi : +28 m³ (+8 960 €)")
    e9 = ajuster({"action": "valider", "code": "PL-01"})
    verifier(lignes(e9)["PL-01"]["confiance"] == "validée" and not lignes(e9)["PL-01"]["a_verifier"], "ligne validée")
    e10 = ajuster({"action": "retirer", "code": "FP-02"})
    sanit = next(g for g in m["groupes"] if g["type"] == "Sanitaires")
    verifier("FP-02" not in lignes(e10) and sanit["id"] in lignes(e10)["RS-01"]["groupes"], "ligne FP-02 supprimée, les sanitaires gardent leur carrelage")
    err = j("/api/ajuster", {"action": "affecter", "groupes": ["G99"], "code": "GO-01"})
    verifier("erreur" in err, "groupe inconnu refusé")
    err = j("/api/ajuster", {"action": "affecter", "groupes": [dallage["id"]], "code": "=HYPERLINK()"})
    verifier("erreur" in err, "code de prix inconnu refusé")
    verifier(len(e10["journal"]) >= 6, "journal des modifications (%d entrées)" % len(e10["journal"]))

    r, chemin = excel_recalcule("ajuste.xlsx")
    verifier(r["total_errors"] == 0 and abs(total_ht(chemin) - e10["total_ht"]) < 0.01, "Excel = estimation ajustée (%s €)" % e10["total_ht"])
    from openpyxl import load_workbook
    wb = load_workbook(chemin)
    verifier("Modifications" in wb.sheetnames, "onglet « Modifications » dans l'Excel")

    print("== Reprise des ajustements")
    m2 = tache("/api/metre?demo=ifc")
    verifier(m2.get("ajustements_repris"), "même maquette rechargée : ajustements retrouvés")
    e11 = tache("/api/chiffrer", {"hauteur": 2.7})
    verifier(e11["total_ht"] == e10["total_ht"], "même total après rechargement (%s €)" % e11["total_ht"])
    autre = os.path.join(TMP, "maquette_mm.ifc")
    subprocess.run([sys.executable, os.path.join(APP, "outils", "generer_maquette.py"), autre, "--mm"], check=True, stdout=subprocess.DEVNULL)
    tache("/api/metre?nom=maquette_mm.ifc", brut=open(autre, "rb").read())
    e12 = tache("/api/chiffrer", {"hauteur": 2.7})
    verifier(e12.get("corrections_reprises") == 2 and dallage["id"] in lignes(e12)["RS-01"]["groupes"] and "FP-02" not in lignes(e12),
             "autre projet : corrections reprises (dallage + sanitaires : %s)" % e12.get("corrections_reprises"))
    e13 = ajuster({"action": "reinitialiser", "groupes": [dallage["id"]]})
    verifier(dallage["id"] not in lignes(e13).get("RS-01", {"groupes": []})["groupes"] and j("/api/etat")["corrections"] == 1, "retour à l'IA : correction du dallage oubliée")

    print("== Ajustements sur un plan DXF (blocs, locaux, calques)")
    md = tache("/api/metre?demo=dxf")
    ed = tache("/api/chiffrer", {"hauteur": 2.7})
    porte = next(g for g in md["groupes"] if g["type"] == "P83")
    objets = j("/api/unites?groupe=" + porte["id"])
    verifier(len(objets) == 8 and all(o["id"].startswith("i") for o in objets), "8 portes P83 chiffrables une par une")
    e1 = ajuster({"action": "affecter", "unites": [o["id"] for o in objets[:2]], "code": "MI-03", "mode": "remplacer"})
    c0 = ed["affectations"][porte["id"]]["codes"][0]
    verifier(lignes(e1)[c0]["quantite"] == lignes(ed)[c0]["quantite"] - 2 and lignes(e1)["MI-03"]["quantite"] == lignes(ed)["MI-03"]["quantite"] + 2,
             "2 portes passées en coupe-feu : %s −2, MI-03 +2" % c0)
    accueil = next(g for g in md["groupes"] if g["genre"] == "local" and g["nombre"] == 1)
    uid = j("/api/unites?groupe=" + accueil["id"])[0]["id"]
    e2 = ajuster({"action": "affecter", "unites": [uid], "code": "RS-02", "mode": "remplacer"})
    verifier(e2["affectations"][accueil["id"]]["manuel"] and uid not in e2["unites_modifiees"], "tous les objets d'un groupe = choix sur le type entier")
    calque = next(g for g in md["groupes"] if g["genre"] == "calque" and g["longueur"] and "CLOISON" in g["type"])
    e3 = ajuster({"action": "nature", "groupes": [calque["id"]], "nature": "lineaire"})
    l3 = next(l for l in e3["lignes"] if calque["id"] in l["groupes"])
    src = next(x for x in l3["sources"] if x["g"] == calque["id"])
    verifier(e3["natures"][calque["id"]] == "lineaire" and l3["unite"] == "m2" and src["quantite"] is None and l3["qte_manquante"],
             "calque forcé en « linéaire » : plus de surface de mur déduite (quantité à saisir)")
    e4 = ajuster({"action": "nature", "groupes": [calque["id"]], "nature": ""})
    verifier(e4["total_ht"] == e2["total_ht"], "retour à la nature proposée par l'IA")


def dpgf():
    from openpyxl import Workbook, load_workbook
    import hashlib
    j("/api/cle", {"cle": "sk-test-1234567890"})
    print("== DPGF Excel : trame du maître d'œuvre (sans prix) + CCTP")
    orig = open(os.path.join(APP, "DPGF_demo.xlsx"), "rb").read()
    d = j("/api/dpgf?demo=1", brut=b"")
    verifier(d.get("nb_articles") == 26 and d.get("nb_lots") == 9, "26 articles et 9 lots lus (%s / %s)" % (d.get("nb_articles"), d.get("nb_lots")))
    verifier(d.get("nb_pm") == 1 and d.get("nb_options") == 1, "1 article « pour mémoire » et 1 option repérés")
    verifier(d.get("mode_prix") == "aucun" and d.get("mode_conseille") == "aucun", "DPGF sans prix : mode « quantités seulement » proposé")
    ap = j("/api/dpgf/apercu")
    verifier(len(ap["articles"]) == 26 and ap["feuilles"][0]["lignes"], "aperçu ligne par ligne disponible")
    j("/api/cctp?demo=1", brut=b"")
    tache("/api/metre?demo=ifc")
    e = tache("/api/chiffrer", {"hauteur": 2.7})
    L = lignes(e)
    verifier(e["dpgf"] and "2.2.1" in L and "GO-06" not in L, "les lignes chiffrées sont les articles du DPGF")
    verifier(L["2.2.1"]["quantite"] > 0 and L["2.2.1"]["unite"] == "m2", "dallage 2.2.1 : %s m² issus de la maquette" % L["2.2.1"]["quantite"])
    verifier(e["lignes"][0]["code"] == "2.1.1" and [l["ordre"] for l in e["lignes"]] == sorted(l["ordre"] for l in e["lignes"]), "ordre du DPGF respecté")
    verifier(len(e["lignes"]) == 26, "les 26 articles figurent, même sans élément dans la maquette (%d)" % len(e["lignes"]))
    verifier(L["2.1.1"]["confiance"] == "à établir" and L["2.1.1"]["qte_manquante"] and L["2.1.1"]["a_verifier"], "semelles 2.1.1 : « à établir »")
    verifier("quantité indiquée au CCTP" in L["2.1.1"]["remarque"].lower() or "28" in L["2.1.1"]["remarque"], "info du CCTP (28 m³) portée sur l'article")
    verifier(L["11.1.3"]["pm"] and not L["11.1.3"]["a_verifier"], "article PM : pas de relance inutile")
    verifier(L["9.3.1"]["option"] and e["total_ht"] == 0 and "total_options" in e, "option comptée à part")
    verifier(not any(a.get("code") for a in e["absents"]), "ouvrages du CCTP liés au DPGF : plus de doublon « à compléter »")
    verifier(any(not a.get("code") for a in e["absents"]), "ouvrage du CCTP sans article au DPGF signalé (raccordements)")
    cite = L["8.1.1"]["articles"][0] if L["8.1.1"]["articles"] else {}
    verifier(cite.get("verifie") is False and "non vérifiée" in L["8.1.1"]["remarque"] and L["8.1.1"]["confiance"] != "haute", "citation inventée du CCTP détectée (garde-corps)")
    verifier(L["2.3.1"]["articles"] and L["2.3.1"]["articles"][0]["verifie"] is True, "citation exacte du CCTP vérifiée (voiles, art. %s)" % L["2.3.1"]["articles"][0]["numero"])

    print("== Mode de prix : bibliothèque (rapprochement automatique)")
    d2 = j("/api/dpgf_reglages", {"mode_prix": "bibliotheque"})
    verifier(d2["mode_prix"] == "bibliotheque" and j("/api/etat")["dpgf"]["mode_prix"] == "bibliotheque", "mode changé sans recharger le fichier")
    e2 = tache("/api/chiffrer", {"hauteur": 2.7})
    verifier(e2["appels"] == 0, "changer le mode de prix ne redemande rien à l'IA")
    L2 = lignes(e2)
    verifier(L2["2.2.1"]["pu"] == 58 and "GO-06" in L2["2.2.1"]["pu_source"], "dallage : prix 58 € repris de la bibliothèque (GO-06)")
    verifier(L2["6.1.1"]["pu"] == 390 and "à confirmer" in L2["6.1.1"]["remarque"], "rapprochement douteux (âme pleine / alvéolaire) signalé « à confirmer »")
    verifier(e2["total_ht"] > 0 and e2["prix_fictifs"], "total calculé avec les prix d'exemple, signalés fictifs : %s €" % e2["total_ht"])
    r, chemin = excel_recalcule("dpgf_bib.xlsx")
    wbx = load_workbook(chemin)
    verifier(r["total_errors"] == 0 and abs(total_ht(chemin) - e2["total_ht"]) < 0.01, "SurveIA Excel recalculé = estimation (%s €)" % e2["total_ht"])
    verifier("Prix du DPGF" in wbx.sheetnames, "onglet « Prix du DPGF »")
    texte = " ".join(str(c) for row in wbx["Estimation"].iter_rows(values_only=True) for c in row if c)
    verifier("LOT 02" in texte and "2.1 Fondations" in texte and "Options et prestations" in texte, "Excel organisé par lots, chapitres et options du DPGF")
    verifier("empreinte" in texte and "DPGF_demo.xlsx" in texte, "fichiers CCTP et DPGF identifiés dans l'Excel")
    code, rempli = req("/api/dpgf/rempli")
    chemin_r = os.path.join(TMP, "dpgf_rempli.xlsx"); open(chemin_r, "wb").write(rempli)
    wo, wr = load_workbook(__import__("io").BytesIO(orig)), load_workbook(chemin_r)
    formules_ok = all(wr[ws.title][c.coordinate].value == c.value for ws in wo.worksheets for row in ws.iter_rows() for c in row
                      if c.data_type == "f")
    verifier(formules_ok and sum(1 for ws in wo.worksheets for row in ws.iter_rows() for c in row if c.data_type == "f") > 3, "formules du DPGF d'origine conservées")
    ws_o, ws_r = wo["DPGF"], wr["DPGF"]
    verifier(ws_r.column_dimensions["B"].width == ws_o.column_dimensions["B"].width and ws_r.merged_cells.ranges == ws_o.merged_cells.ranges, "mise en page conservée")
    trouve = [(row[0].row, row) for row in ws_r.iter_rows() if any(c.value == "2.2.1" for c in row)]
    verifier(trouve and any(isinstance(c.value, (int, float)) and abs(c.value - L2["2.2.1"]["quantite"]) < 0.01 for c in trouve[0][1]), "quantité du dallage écrite dans la colonne Quantité du DPGF")
    verifier(any(c.comment and "SurveIA" in c.comment.text for c in trouve[0][1]), "commentaire de traçabilité sur la cellule")
    verifier(any(isinstance(c.value, (int, float)) and c.value == 58 for c in trouve[0][1]), "prix de bibliothèque écrit (le DPGF n'en avait pas)")
    verifier("SurveIA - traçabilité" in wr.sheetnames, "onglet de traçabilité ajouté")
    verifier(hashlib.sha1(orig).hexdigest() == hashlib.sha1(open(os.path.join(APP, "DPGF_demo.xlsx"), "rb").read()).hexdigest(), "fichier DPGF d'origine inchangé sur le disque")

    print("== Ajustements sur un article du DPGF")
    def ajuster(corps):
        r = j("/api/ajuster", corps)
        return r["estimation"] if "estimation" in r else r
    ea = ajuster({"action": "supplement", "code": "2.1.1", "valeur": 28, "libelle": "Fondations (CCTP 2.1)"})
    verifier(not isinstance(ea, dict) or "erreur" not in ea, "ajustement accepté sur un article du DPGF")
    verifier(lignes(ea)["2.1.1"]["quantite"] == 28 and lignes(ea)["2.1.1"]["confiance"] == "manuelle", "28 m³ saisis sur l'article 2.1.1")

    print("== DPGF d'entreprise : autre structure, prix et quantités déjà renseignés")
    d3 = j("/api/dpgf?demo=entreprise", brut=b"")
    verifier(d3["nb_articles"] == 15 and d3["mode_prix"] == "dpgf" and d3["part_prix"] == 1.0, "autre structure lue : 15 articles, prix du DPGF utilisés")
    e3 = tache("/api/chiffrer", {"hauteur": 2.7})
    L3 = lignes(e3)
    verifier(L3["Lot 02 GO l.7"]["pu"] == 61 and L3["Lot 02 GO l.7"]["quantite"] > 0, "dallage chiffré au prix du DPGF (61 €)")
    verifier(not e3["prix_fictifs"], "aucun prix fictif en mode DPGF")
    sem = L3["Lot 02 GO l.13"]
    verifier(sem.get("qte_reprise_dpgf") and sem["quantite"] == 28, "semelles : aucun élément maquette, quantité du DPGF reprise (28 m³)")
    verifier(any("Aucun article du DPGF" in n["raison"] for n in e3["non_chiffres"]), "éléments sans article au DPGF laissés non chiffrés (pas d'article inventé)")
    code, rempli = req("/api/dpgf/rempli")
    chemin_r = os.path.join(TMP, "dpgf_rempli2.xlsx"); open(chemin_r, "wb").write(rempli)
    wr = load_workbook(chemin_r)
    cellule = next(c for ws in wr.worksheets if ws.title.startswith("Lot 02") for row in ws.iter_rows() for c in row if c.value == 240)
    verifier(cellule.value == 240, "quantité 240 du DPGF jamais écrasée")
    verifier(cellule.comment is not None and "SurveIA" in cellule.comment.text, "mais commentée avec le métré SurveIA")

    print("== Autres structures de DPGF")
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active; ws.title = "Bordereau"
    for ligne in (["Art.", "Libellé", "U", "Qté", "P.U. HT", "Montant HT"], ["LOT 01 - TERRASSEMENTS"],
                  ["01.01", "Décapage terre végétale ép. 30 cm", "m2"], ["01.02", "Fouilles en rigole", "m3"],
                  ["", "Total lot 01", None, None, None, "=SUM(F3:F4)"], ["LOT 02 - GROS OEUVRE"],
                  ["02.01.001", "Semelles filantes BA", "m3"], ["02.01.002", "Voile BA 20 cm", "m2"]):
        ws.append(ligne)
    tampon = __import__("io").BytesIO(); wb.save(tampon)
    da = j("/api/dpgf?nom=bordereau.xlsx", brut=tampon.getvalue())
    verifier(da.get("nb_articles") == 4 and da.get("nb_lots") == 2, "numérotation 01.01 / lots « LOT 0x » : 4 articles, 2 lots (total ignoré)")
    wb = Workbook(); ws = wb.active; ws.title = "Offre prix"
    ws["A2"] = "Société XYZ – cadre de décomposition"
    for col, t in zip("ABCDE", ("Désignation des ouvrages", "Unité", "Quantité", "PU", "Total")):
        ws[col + "12"] = t
    r = 13
    for titre, items in (("Structure", [("Dalle béton armé 20 cm", "m2"), ("Poteaux BA", "m3")]), ("Cloisons", [("Cloison 72/48", "m2"), ("Porte 83", "u")])):
        ws.cell(row=r, column=1, value=titre).font = Font(bold=True); r += 1
        for des, u in items:
            ws.cell(row=r, column=1, value=des); ws.cell(row=r, column=2, value=u); r += 1
    tampon = __import__("io").BytesIO(); wb.save(tampon)
    db = j("/api/dpgf?nom=offre_prix.xlsx", brut=tampon.getvalue())
    verifier(db.get("nb_articles") == 4 and db.get("nb_lots") == 2, "sans numéros, en-tête en ligne 12, titres en gras : 4 articles, 2 lots")

    print("== DPGF sans en-tête et texte piégé")
    wb = Workbook(); ws = wb.active; ws.title = "Offre"
    ws.append(["Mon bordereau"]); ws.append(["1", "Dallage béton armé ép. 15 cm", "m2", 100, 55])
    ws.append(["2", "Voile béton armé ép. 20 cm", "m2", 40, 160])
    ws.append(["3", "Poteaux", "m3", 3, 900])
    ws.cell(row=2, column=2).value = "=cmd|' /C calc'!A0 Dallage béton armé ép. 15 cm"
    ws.cell(row=2, column=2).data_type = "s"
    tampon = __import__("io").BytesIO(); wb.save(tampon)
    d4 = j("/api/dpgf?nom=offre.xlsx", brut=tampon.getvalue())
    verifier(d4.get("nb_articles") == 3, "DPGF sans en-tête lu d'après son contenu (%s articles)" % d4.get("nb_articles"))
    e4 = tache("/api/chiffrer", {"hauteur": 2.7})
    code, b = req("/api/excel")
    chemin = os.path.join(TMP, "piege.xlsx"); open(chemin, "wb").write(b)
    wbp = load_workbook(chemin)
    piege = [c for w in wbp.worksheets for row in w.iter_rows() for c in row if "cmd|" in str(c.value)]
    verifier(piege and all(c.data_type != "f" for c in piege), "texte « =cmd… » du DPGF présent mais neutralisé dans l'Excel (%d cellule(s))" % len(piege))
    err = j("/api/dpgf?nom=notes.txt", brut=b"abc")
    verifier("erreur" in err, "fichier qui n'est pas un Excel refusé")
    err = j("/api/dpgf?nom=vide.xlsx", brut=b"")
    verifier("erreur" in err, "fichier vide refusé")
    j("/api/dpgf_retirer", {})
    verifier(j("/api/etat")["dpgf"] is None, "DPGF retiré : retour à la bibliothèque")


SCENARIOS = {  # nom : (fonction, variables d'environnement du faux DeepSeek)
    "vis": (vis, {}), "base": (base, {}), "tronque": (tronque, {"TRONQUER": "5"}), "oublie": (oublie, {"OUBLIER": "1"}),
    "repli": (repli, {"REFUSER_THINKING": "1"}), "ajust": (ajust, {}), "dpgf": (dpgf, {}), "gros": (gros, {}),
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
    env_app = dict(os.environ, DEEPSEEK_BASE_URL="http://127.0.0.1:%d" % PORT_FAUX, CHIFFRAGE_MEMOIRE=memoire,
                   CHIFFRAGE_DONNEES=os.path.join(TMP, "donnees_%s" % nom))
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
