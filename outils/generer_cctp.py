"""Génère le CCTP de démonstration (PDF) de l'immeuble de bureaux fictif."""
import sys
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, PageBreak

import glob
import os


def trouver_police(nom):
    """DejaVu si elle est installée (Linux, ou copiée à côté du script) ; sinon police intégrée Helvetica."""
    for motif in (os.path.join(os.path.dirname(os.path.abspath(__file__)), nom),
                  "/usr/share/fonts/truetype/dejavu/" + nom, "/Library/Fonts/" + nom,
                  os.path.expanduser("~/Library/Fonts/") + nom, "C:/Windows/Fonts/" + nom):
        trouves = glob.glob(motif)
        if trouves:
            return trouves[0]
    return None


if trouver_police("DejaVuSans.ttf") and trouver_police("DejaVuSans-Bold.ttf"):
    pdfmetrics.registerFont(TTFont("DV", trouver_police("DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DVB", trouver_police("DejaVuSans-Bold.ttf")))
    POLICE, POLICE_GRAS = "DV", "DVB"
else:
    POLICE, POLICE_GRAS = "Helvetica", "Helvetica-Bold"
corps = ParagraphStyle("c", fontName=POLICE, fontSize=9.5, leading=13, spaceAfter=5)
titre = ParagraphStyle("t", fontName=POLICE_GRAS, fontSize=17, leading=22, spaceAfter=6)
lot = ParagraphStyle("l", fontName=POLICE_GRAS, fontSize=12.5, leading=16, spaceBefore=12, spaceAfter=6)
art = ParagraphStyle("a", fontName=POLICE_GRAS, fontSize=10, leading=14, spaceBefore=6, spaceAfter=3)
note = ParagraphStyle("n", fontName=POLICE, fontSize=8.5, leading=11, textColor="#666666")

CONTENU = [
    ("LOT 00 – GÉNÉRALITÉS", [
        ("0.1 Objet", "Le présent Cahier des Clauses Techniques Particulières (CCTP) décrit les ouvrages de la construction "
         "d'un immeuble de bureaux en R+1 d'environ 480 m² de surface de plancher. Document fictif établi pour une "
         "démonstration : il ne correspond à aucun projet réel."),
        ("0.2 Documents de référence", "Les travaux seront exécutés conformément aux DTU en vigueur, aux normes NF, "
         "à la réglementation incendie applicable aux ERP de 5e catégorie et à la RE2020."),
        ("0.3 Coordination BIM", "La maquette numérique IFC et les plans DXF servent de base au métré. En cas de "
         "contradiction entre la maquette et le présent CCTP, le CCTP prévaut."),
    ]),
    ("LOT 02 – GROS ŒUVRE", [
        ("2.1 Fondations", "Semelles filantes en béton armé C25/30 sous voiles périphériques, section 60 x 40 cm, "
         "et semelles isolées 100 x 100 x 50 cm sous poteaux. Volume indicatif : 28 m³. Les fondations ne sont pas modélisées."),
        ("2.2 Dallage", "Dallage en béton armé ép. 15 cm sur terre-plein compacté, film polyane et isolant sous dallage selon étude thermique."),
        ("2.3 Voiles de façade", "Voiles en béton armé C25/30 ép. 20 cm, coffrage deux faces, parement soigné côté extérieur destiné à recevoir un enduit."),
        ("2.4 Planchers", "Plancher haut du RDC en dalle pleine béton armé ép. 20 cm. Plancher de toiture-terrasse en dalle pleine ép. 22 cm, "
         "avec acrotère béton de 50 cm de hauteur sur tout le périmètre (64 ml)."),
        ("2.5 Poteaux", "Poteaux intérieurs en béton armé 25 x 25 cm coulés en place, hauteur d'étage 3,00 m."),
    ]),
    ("LOT 03 – ÉTANCHÉITÉ", [
        ("3.1 Toiture-terrasse inaccessible", "Complexe d'étanchéité bicouche élastomère SBS sur isolant PSE ép. 120 mm, "
         "pare-vapeur, protection gravillons. Surface : ensemble de la toiture-terrasse."),
        ("3.2 Relevés", "Relevés d'étanchéité sur acrotères, hauteur 20 cm au-dessus de la protection, avec bande de solin, sur tout le périmètre."),
    ]),
    ("LOT 05 – MENUISERIES EXTÉRIEURES", [
        ("5.1 Fenêtres", "Fenêtres en aluminium à rupture de pont thermique, ouvrant à la française, double vitrage 4/16/4 "
         "faiblement émissif avec argon, Uw ≤ 1,4 W/m².K. Nomenclature par niveau : 10 fenêtres de 120 x 135 cm "
         "et 3 fenêtres de 240 x 135 cm."),
        ("5.2 Porte d'entrée", "Ensemble de porte d'entrée en aluminium vitré à deux vantaux, 140 x 220 cm, "
         "vitrage feuilleté de sécurité, ferme-porte et contrôle d'accès (hors lot)."),
    ]),
    ("LOT 06 – MENUISERIES INTÉRIEURES", [
        ("6.1 Blocs-portes des bureaux", "Blocs-portes en bois à âme pleine, 83 x 204 cm, huisserie bois, "
         "serrure bec-de-cane à condamnation. Performance acoustique Rw ≥ 30 dB."),
        ("6.2 Portes coupe-feu", "Blocs-portes coupe-feu EI30 de 93 x 204 cm avec ferme-porte, pour les locaux techniques "
         "et le recoupement des circulations, procès-verbal de classement exigé."),
    ]),
    ("LOT 07 – CLOISONS – DOUBLAGES", [
        ("7.1 Cloisons de distribution", "Cloisons en plaques de plâtre 72/48, une plaque BA13 par face, isolant laine minérale 45 mm."),
        ("7.2 Cloisons de la salle de réunion", "Cloisons acoustiques 98/48, deux plaques BA13 par face, laine minérale 45 mm, "
         "affaiblissement Rw ≥ 45 dB."),
        ("7.3 Cloisons des sanitaires", "Les cloisons des sanitaires sont de type 98/48 avec plaques hydrofuges BA13 H "
         "(deux plaques par face) pour permettre la fixation des équipements suspendus."),
        ("7.4 Doublages des façades", "Doublage des voiles de façade par complexe laine minérale 100 mm sur ossature "
         "métallique et plaque BA13, R ≥ 3,15 m².K/W. Les doublages collés en polystyrène ne sont pas admis."),
    ]),
    ("LOT 08 – SERRURERIE", [
        ("8.1 Garde-corps", "Garde-corps en verre feuilleté 44.2 sur profil aluminium, hauteur 1,00 m, en rive de la "
         "terrasse du R+1 : deux éléments de 3,00 ml, soit 6,00 ml."),
    ]),
    ("LOT 09 – REVÊTEMENTS DE SOLS", [
        ("9.1 Carrelage", "Carrelage grès cérame 60 x 60 cm collé, classement U4P3E3C2, dans l'accueil, les circulations et les sanitaires."),
        ("9.2 Sols souples", "Sol PVC en lés U3P3, dans les bureaux et la salle de réunion, avec remontées en plinthes."),
    ]),
    ("LOT 10 – FAUX PLAFONDS", [
        ("10.1 Plafonds démontables", "Faux plafonds en dalles minérales 600 x 600 sur ossature apparente dans l'ensemble des locaux, "
         "absorption acoustique αw ≥ 0,90."),
    ]),
    ("LOT 11 – PLOMBERIE – SANITAIRES", [
        ("11.1 Appareils sanitaires", "Par niveau : 3 WC suspendus avec bâti-support et 2 lavabos sur colonne, robinetterie temporisée."),
    ]),
]


def construire(chemin):
    doc = SimpleDocTemplate(chemin, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm, bottomMargin=18 * mm,
                            title="CCTP - Immeuble de bureaux (demo)", author="POC Chiffrage IA")
    elems = [Paragraph("CCTP – Immeuble de bureaux R+1", titre),
             Paragraph("Cahier des Clauses Techniques Particulières – document fictif de démonstration", corps),
             Paragraph("Ce document a été rédigé pour tester le POC de chiffrage. Toute ressemblance avec un projet existant "
                       "serait fortuite.", note), Spacer(1, 8)]
    for i, (titre_lot, articles) in enumerate(CONTENU):
        if i == 5:
            elems.append(PageBreak())
        elems.append(Paragraph(titre_lot, lot))
        for t, texte in articles:
            elems.append(Paragraph(t, art))
            elems.append(Paragraph(texte, corps))

    def pied(canvas, doc):
        canvas.setFont(POLICE, 8)
        canvas.drawRightString(A4[0] - 22 * mm, 10 * mm, "CCTP démo – page %d" % doc.page)

    doc.build(elems, onFirstPage=pied, onLaterPages=pied)


if __name__ == "__main__":
    construire(sys.argv[1])
    print("Écrit :", sys.argv[1])
