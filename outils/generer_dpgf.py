"""Génère des DPGF Excel de démonstration pour le petit immeuble de bureaux fictif (même bâtiment que la maquette,
le plan DXF et le CCTP de démo).

Usage :
  python generer_dpgf.py DPGF_demo.xlsx                 # trame de DCE : une feuille, numérotation 2.1.1, sans prix
  python generer_dpgf.py DPGF_entreprise.xlsx --entreprise
      # présentation très différente : une feuille par lot, page de garde et récapitulatif, aucune numérotation,
      # en-tête sur deux lignes, colonnes dans un autre ordre, prix d'une entreprise et quelques quantités

Les deux fichiers servent à vérifier que SurveIA lit un DPGF quelle que soit sa présentation.
Ils sont reproductibles à l'octet près (dates fixées).
"""
import io
import re
import sys
import zipfile
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

DATE_FIXE = datetime(2026, 9, 30, 12, 0, 0)

# (numéro, désignation, unité, description) ; unité None = titre
TRAME = [
    ("LOT 02", "GROS ŒUVRE", None, None),
    ("2.1", "Fondations", None, None),
    ("2.1.1", "Semelles filantes en béton armé C25/30", "m3", "Section 60 x 40 cm sous voiles périphériques, y compris terrassements en rigole."),
    ("2.2", "Dallage", None, None),
    ("2.2.1", "Dallage béton armé ép. 15 cm sur terre-plein", "m2", "Y compris film polyane et isolant sous dallage selon étude thermique."),
    ("2.3", "Voiles", None, None),
    ("2.3.1", "Voiles béton armé ép. 20 cm, coffrage deux faces", "m2", None),
    ("2.4", "Planchers", None, None),
    ("2.4.1", "Dalle pleine béton armé ép. 20 cm (plancher haut RDC)", "m2", None),
    ("2.4.2", "Dalle pleine béton armé ép. 22 cm (toiture-terrasse)", "m2", None),
    ("2.5", "Poteaux", None, None),
    ("2.5.1", "Poteaux béton armé 25 x 25 cm coulés en place", "m3", None),
    ("LOT 03", "ÉTANCHÉITÉ", None, None),
    ("3.1.1", "Complexe d'étanchéité bicouche SBS sur isolant PSE 120 mm", "m2", None),
    ("3.2.1", "Relevés d'étanchéité sur acrotères", "ml", None),
    ("LOT 05", "MENUISERIES EXTÉRIEURES", None, None),
    ("5.1", "Fenêtres aluminium à rupture de pont thermique", None, None),
    ("5.1.1", "Fenêtre aluminium 120 x 135, double vitrage 4/16/4", "u", None),
    ("5.1.2", "Fenêtre aluminium 240 x 135, double vitrage 4/16/4", "u", None),
    ("5.2.1", "Ensemble porte d'entrée aluminium vitrée deux vantaux 140 x 220", "ens", None),
    ("LOT 06", "MENUISERIES INTÉRIEURES", None, None),
    ("6.1.1", "Bloc-porte bois âme pleine 83 x 204, huisserie bois", "u", None),
    ("6.2.1", "Bloc-porte coupe-feu EI30 93 x 204 avec ferme-porte", "u", None),
    ("LOT 07", "CLOISONS – DOUBLAGES", None, None),
    ("7.1.1", "Cloisons de distribution 72/48, une plaque BA13 par face", "m2", None),
    ("7.2.1", "Cloisons acoustiques 98/48, deux plaques BA13 par face", "m2", None),
    ("7.3.1", "Cloisons des sanitaires 98/48 hydrofuges BA13 H", "m2", None),
    ("7.4.1", "Doublage des façades laine minérale 100 mm sur ossature", "m2", None),
    ("LOT 08", "SERRURERIE", None, None),
    ("8.1.1", "Garde-corps verre feuilleté 44.2 sur profil aluminium", "ml", None),
    ("LOT 09", "REVÊTEMENTS DE SOLS", None, None),
    ("9.1.1", "Carrelage grès cérame 60 x 60 collé U4P3E3C2", "m2", None),
    ("9.2.1", "Sol PVC en lés U3P3", "m2", None),
    ("9.3", "Prestation supplémentaire éventuelle n° 1 (PSE)", None, None),
    ("9.3.1", "Plinthes carrelage assorties", "ml", None),
    ("LOT 10", "FAUX PLAFONDS", None, None),
    ("10.1.1", "Faux plafond dalles minérales 600 x 600 sur ossature apparente", "m2", None),
    ("10.1.2", "Faux plafond plaques de plâtre hydrofuges (sanitaires)", "m2", None),
    ("LOT 11", "PLOMBERIE – SANITAIRES", None, None),
    ("11.1.1", "WC suspendu avec bâti-support et plaque de commande", "u", None),
    ("11.1.2", "Lavabo sur colonne, robinetterie temporisée", "u", None),
    ("11.1.3", "Raccordements aux réseaux EU / EV / EF", "ft", "PM"),
]

# Variante « entreprise » : feuille, puis (titre en gras | article), unité, prix, quantité éventuelle, réf. CCTP
ENTREPRISE = {
    "Lot 02 GO": [
        ("Planchers", None, None, None, None),
        ("Dallage BA 15 cm sur terre-plein, compris polyane", "M2", 61.0, None, "2.2"),
        ("Dalle BA 20 cm", "M2", 99.0, None, "2.4"),
        ("Dalle BA 22 cm toiture", "M2", 108.0, 240, "2.4"),
        ("Structure", None, None, None, None),
        ("Voile BA 20 cm 2 faces coffrées", "M2", 170.0, None, "2.3"),
        ("Poteaux BA 25x25", "M3", 1010.0, None, "2.5"),
        ("Fondations superficielles (semelles)", "M3", 335.0, 28, "2.1"),
    ],
    "Lot 07 Cloisons": [
        ("Cloisons", None, None, None, None),
        ("Cloison 72/48 BA13", "M2", 44.0, None, "7.1"),
        ("Cloison acoustique 98/48 2xBA13", "M2", 61.0, None, "7.2"),
        ("Cloison hydro 98/48 BA13H", "M2", 66.0, None, "7.3"),
        ("Doublages", None, None, None, None),
        ("Doublage laine minérale sur ossature", "M2", 46.0, None, "7.4"),
    ],
    "Lot 09 Sols": [
        ("Carrelage", None, None, None, None),
        ("Grès cérame 60x60", "M2", 75.0, 120, "9.1"),
        ("Sols souples", None, None, None, None),
        ("PVC en lés", "M2", 47.0, None, "9.2"),
    ],
    "Lot 11 Plomberie": [
        ("Appareils", None, None, None, None),
        ("WC suspendu complet", "U", 795.0, None, "11.1"),
        ("Lavabo colonne", "U", 535.0, None, "11.1"),
        ("Essais, mise en service", "ENS", 650.0, 1, ""),
    ],
}


def reproductible(octets):
    """Réécrit l'archive avec des dates fixes : mêmes octets à chaque génération."""
    source = zipfile.ZipFile(io.BytesIO(octets))
    sortie = io.BytesIO()
    with zipfile.ZipFile(sortie, "w", zipfile.ZIP_DEFLATED) as z:
        for info in source.infolist():
            nouveau = zipfile.ZipInfo(info.filename, date_time=DATE_FIXE.timetuple()[:6])
            nouveau.compress_type = zipfile.ZIP_DEFLATED
            nouveau.external_attr = info.external_attr
            donnees = source.read(info.filename)
            if info.filename == "docProps/core.xml":  # openpyxl y écrit l'heure d'enregistrement
                donnees = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>" + DATE_FIXE.strftime("%Y-%m-%dT%H:%M:%SZ").encode(), donnees)
            z.writestr(nouveau, donnees)
    return sortie.getvalue()


def trame_dce():
    wb = Workbook()
    ws = wb.active
    ws.title = "DPGF"
    gras, titre = Font(name="Arial", bold=True), Font(name="Arial", bold=True, size=14)
    fin = Side(style="thin", color="999999")
    bord = Border(left=fin, right=fin, top=fin, bottom=fin)
    ws["A1"] = "Immeuble de bureaux R+1 — projet fictif de démonstration"
    ws["A1"].font = titre
    ws["A2"] = "DÉCOMPOSITION DU PRIX GLOBAL ET FORFAITAIRE (DPGF)"
    ws["A2"].font = gras
    ws["A3"] = "Phase DCE — indice A — tous corps d'état"
    ws["A5"] = "Le candidat complète les quantités et les prix. Les quantités sont données à titre indicatif ; l'entreprise reste responsable de son métré."
    ws["A5"].font = Font(name="Arial", italic=True, size=9)
    r = 7
    for c, t in enumerate(["N°", "Désignation des ouvrages", "U", "Qté", "P.U. HT\n(€)", "Montant HT\n(€)"], 1):
        cel = ws.cell(row=r, column=c, value=t)
        cel.font = Font(name="Arial", bold=True, color="FFFFFF")
        cel.fill = PatternFill("solid", fgColor="44546A")
        cel.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cel.border = bord
    r += 1
    debut_lot, sous_totaux = None, []

    def fermer_lot(r, nom):
        ws.cell(row=r, column=2, value="Sous-total %s" % nom).font = gras
        ws.cell(row=r, column=6, value="=SUM(F%d:F%d)" % (debut_lot, r - 1)).font = gras
        sous_totaux.append(r)
        return r + 2

    lot_courant = None
    for numero, texte, unite, description in TRAME:
        if numero.startswith("LOT"):
            if lot_courant:
                r = fermer_lot(r, lot_courant)
            lot_courant = "%s – %s" % (numero, texte)
            ws.cell(row=r, column=1, value=lot_courant).font = Font(name="Arial", bold=True, size=12)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            ws.cell(row=r, column=1).fill = PatternFill("solid", fgColor="D9E1F2")
            r += 1
            debut_lot = r
            continue
        if unite is None:
            ws.cell(row=r, column=1, value=numero).font = gras
            ws.cell(row=r, column=2, value=texte).font = gras
            r += 1
            continue
        ws.cell(row=r, column=1, value=numero)
        ws.cell(row=r, column=2, value=texte).alignment = Alignment(wrap_text=True)
        ws.cell(row=r, column=3, value=unite).alignment = Alignment(horizontal="center")
        if description == "PM":
            ws.cell(row=r, column=4, value="PM")
        ws.cell(row=r, column=6, value="=IF(ISNUMBER(D%d),D%d*E%d,0)" % (r, r, r))
        for c in range(1, 7):
            ws.cell(row=r, column=c).border = bord
        r += 1
        if description and description != "PM":
            ws.cell(row=r, column=2, value=description).font = Font(name="Arial", italic=True, size=9)
            r += 1
    r = fermer_lot(r, lot_courant)
    ws.cell(row=r, column=2, value="TOTAL HT").font = gras
    ws.cell(row=r, column=6, value="=" + "+".join("F%d" % x for x in sous_totaux)).font = gras
    ws.cell(row=r + 1, column=2, value="TVA 20 %")
    ws.cell(row=r + 1, column=6, value="=F%d*0.2" % r)
    ws.cell(row=r + 2, column=2, value="TOTAL TTC").font = gras
    ws.cell(row=r + 2, column=6, value="=F%d+F%d" % (r, r + 1))
    for col, larg in zip("ABCDEF", (9, 62, 6, 11, 12, 15)):
        ws.column_dimensions[col].width = larg
    ws.freeze_panes = "A8"
    return wb


def entreprise():
    wb = Workbook()
    garde = wb.active
    garde.title = "Page de garde"
    garde["B3"] = "ENTREPRISE GÉNÉRALE FICTIVE"
    garde["B3"].font = Font(bold=True, size=16)
    garde["B5"] = "Offre de prix — Immeuble de bureaux R+1 (démonstration)"
    garde["B6"] = "Prix fermes et non révisables, valables 90 jours."
    recap = wb.create_sheet("Récapitulatif")
    recap["A1"] = "Récapitulatif par lot"
    recap["A1"].font = Font(bold=True)
    recap.append([])
    recap.append(["Lot", "Montant HT"])
    for nom, lignes in ENTREPRISE.items():
        ws = wb.create_sheet(nom)
        ws["A1"] = nom.upper()
        ws["A1"].font = Font(bold=True, size=13)
        ws["A2"] = "Offre entreprise — à rapprocher du CCTP"
        # en-tête sur deux lignes, colonnes dans un ordre inhabituel
        for c, (haut, bas) in enumerate([("Désignation", ""), ("Unité", ""), ("Quantité", ""), ("Prix", "unitaire HT"),
                                          ("Total", "HT"), ("Réf.", "CCTP")], 1):
            ws.cell(row=4, column=c, value=haut).font = Font(bold=True)
            if bas:
                ws.cell(row=5, column=c, value=bas).font = Font(bold=True)
        r = 6
        for texte, unite, pu, qte, cctp in lignes:
            if unite is None:
                ws.cell(row=r, column=1, value=texte).font = Font(bold=True, underline="single")
                r += 1
                continue
            ws.cell(row=r, column=1, value=texte)
            ws.cell(row=r, column=2, value=unite)
            if qte is not None:
                ws.cell(row=r, column=3, value=qte)
            ws.cell(row=r, column=4, value=pu)
            ws.cell(row=r, column=5, value="=C%d*D%d" % (r, r))
            ws.cell(row=r, column=6, value=cctp)
            r += 1
        ws.cell(row=r + 1, column=1, value="Total %s" % nom).font = Font(bold=True)
        ws.cell(row=r + 1, column=5, value="=SUM(E6:E%d)" % (r - 1)).font = Font(bold=True)
        recap.append([nom, "='%s'!E%d" % (nom, r + 1)])
        for col, larg in zip("ABCDEF", (48, 8, 10, 12, 12, 8)):
            ws.column_dimensions[col].width = larg
    return wb


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    wb = entreprise() if "--entreprise" in sys.argv else trame_dce()
    wb.properties.created = wb.properties.modified = DATE_FIXE
    wb.properties.creator = "SurveIA (démonstration)"
    tampon = io.BytesIO()
    wb.save(tampon)
    open(sys.argv[1], "wb").write(reproductible(tampon.getvalue()))
    print("Écrit : %s" % sys.argv[1])


if __name__ == "__main__":
    main()
