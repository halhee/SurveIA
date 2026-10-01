"""Test de l'interface dans un vrai navigateur (Playwright, facultatif) : les boutons font ce qu'ils annoncent.
Il aurait détecté la disparition du bouton « Télécharger l'estimation Excel » (les autres tests passent par l'API).

Usage :  python3 tests/test_interface.py        (nécessite : pip install playwright, et un Chromium installé)
Sans Playwright, le test est simplement ignoré.
"""
import os, subprocess, sys, tempfile, time, http.client

S = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(S)
PORT, PORT_FAUX = 8798, 9910
RESULTATS = []


def verifier(cond, texte):
    RESULTATS.append(bool(cond))
    print(("  OK    " if cond else "  ÉCHEC ") + texte)


def attendre(port, delai=15):
    fin = time.time() + delai
    while time.time() < fin:
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/")
            return True
        except OSError:
            time.sleep(0.2)
    return False


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright absent : test de l'interface ignoré.")
        return 0
    tmp = tempfile.mkdtemp(prefix="poc_ui_")
    faux = subprocess.Popen([sys.executable, os.path.join(S, "faux_deepseek.py")], env=dict(os.environ, PORT_FAUX=str(PORT_FAUX)),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env = dict(os.environ, DEEPSEEK_BASE_URL="http://127.0.0.1:%d" % PORT_FAUX, CHIFFRAGE_MEMOIRE=os.path.join(tmp, "memoire.json"),
               CHIFFRAGE_DONNEES=os.path.join(tmp, "donnees"))
    appli = subprocess.Popen([sys.executable, os.path.join(APP, "chiffrage_ia.py"), "--sans-navigateur", "--port", str(PORT)],
                             env=env, cwd=APP, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    erreurs_js = []
    try:
        if not (attendre(PORT) and attendre(PORT_FAUX)):
            verifier(False, "démarrage de l'application")
            return 1
        with sync_playwright() as p:
            nav = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
            pg = nav.new_page(viewport={"width": 1500, "height": 1000}, accept_downloads=True)
            pg.on("pageerror", lambda e: erreurs_js.append(str(e)))
            pg.goto("http://127.0.0.1:%d/" % PORT)
            pg.fill("#cle", "sk-test-1234567890"); pg.click("#btn-cle")
            pg.wait_for_selector("#e2:not(.verrouillee)")
            print("== Interface : maquette, CCTP, DPGF")
            pg.click("#btn-dpgf-demo")
            pg.wait_for_selector("#dpgf-info:has-text('26 articles')")
            verifier(pg.locator("#dpgf-modes input:checked").get_attribute("value") == "aucun", "DPGF chargé, mode « quantités seulement » proposé")
            pg.click("#dpgf-lecture summary")
            pg.wait_for_selector("#dpgf-apercu table")
            verifier(pg.locator("#dpgf-apercu tr.g-article").count() == 26, "lecture du DPGF vérifiable : 26 articles surlignés")
            pg.click("#btn-cctp-demo"); pg.wait_for_selector("#cctp-info:has-text('articles repérés')")
            pg.click("#btn-demo"); pg.wait_for_selector("#e3:not(.verrouillee)", timeout=60000)
            pg.click("#btn-chiffrer"); pg.wait_for_selector("#e4:not(.verrouillee)", timeout=90000)
            pg.wait_for_selector("#t4 tr.chap")
            verifier(pg.locator("#t4 tr.cliquable").count() == 26 and pg.locator("#t4 tr.opt").count() == 1, "estimation par articles du DPGF, option à part")
            verifier(pg.locator("#t4 .art.non-verifie").count() == 1, "citation du CCTP non retrouvée signalée en rouge")
            with pg.expect_download() as d:
                pg.click("#btn-excel")
            verifier(d.value.suggested_filename.endswith(".xlsx"), "bouton « Télécharger l'estimation Excel » : %s" % d.value.suggested_filename)
            with pg.expect_download() as d:
                pg.click("#btn-dpgf-rempli")
            verifier(d.value.suggested_filename.startswith("DPGF_renseigne"), "bouton « Télécharger le DPGF renseigné » : %s" % d.value.suggested_filename)
            pg.click("#dpgf-modes label:nth-child(2) input")
            pg.wait_for_selector("#e4.verrouillee", state="attached")
            verifier(True, "changer le mode de prix invalide l'estimation (à relancer)")
            pg.click("#btn-dpgf-retirer")
            pg.wait_for_selector("#btn-dpgf-retirer.cache", state="attached")
            verifier(not erreurs_js, "aucune erreur JavaScript dans la page %s" % erreurs_js[:2])
            nav.close()
    finally:
        appli.terminate(); faux.terminate()
        appli.wait(10); faux.wait(10)
        if "Traceback" in appli.stderr.read().decode(errors="replace"):
            verifier(False, "erreur Python dans l'application")
    print("\nRÉSULTAT : %d/%d" % (sum(RESULTATS), len(RESULTATS)))
    return 0 if all(RESULTATS) else 1


if __name__ == "__main__":
    sys.exit(main())
