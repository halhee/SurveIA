"""
Lecture d'un CCTP (PDF, Word .docx ou texte) pour donner du contexte à l'IA.

Le CCTP ne sert PAS à mesurer : il aide l'IA à choisir la bonne ligne de prix,
à citer l'article concerné, à signaler les écarts entre la maquette et le CCTP,
et à repérer les ouvrages décrits au CCTP mais absents de la maquette.

PDF : nécessite la bibliothèque pypdf (python -m pip install pypdf).
Word et texte : aucune dépendance.
"""

import io
import re
import zipfile
from xml.etree import ElementTree

TAILLE_MAX = 300_000  # caractères envoyés à l'IA (environ 80 000 jetons)
PAGES_MAX = 800  # un CCTP dépasse rarement quelques centaines de pages
PYPDF_MINIMUM = "6.19.0"  # versions antérieures : failles de déni de service sur des PDF piégés


def pypdf_a_jour():
    try:
        import pypdf
        actuelle = tuple(int(x) for x in re.findall(r"\d+", pypdf.__version__)[:3])
        return actuelle >= tuple(int(x) for x in PYPDF_MINIMUM.split("."))
    except Exception:
        return False

# Titres d'articles : « 7.2 Cloisons... », « Article 3.1 - ... », « LOT 07 - CLOISONS »
MOTIF_ARTICLE = re.compile(r"^\s*(?:ARTICLE\s+|Art\.?\s*)?(\d{1,2}(?:\.\d{1,2}){1,3})\.?\s+[-–:]?\s*(\S.{2,120})$", re.I)
MOTIF_LOT = re.compile(r"^\s*LOT\s*(?:N°|n°|No)?\s*(\d{1,2})\s*[-–:.]?\s*(.{2,80})$", re.I)


def texte_pdf(contenu):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("Pour lire un CCTP en PDF, installez pypdf :  python -m pip install pypdf  "
                         "(ou fournissez le CCTP en Word .docx ou en texte).")
    try:
        lecteur = PdfReader(io.BytesIO(contenu))
        _ = len(lecteur.pages)
    except Exception:
        raise ValueError("PDF illisible ou endommagé : réenregistrez-le, ou fournissez le CCTP en .docx.")
    if lecteur.is_encrypted:
        try:
            lecteur.decrypt("")
        except Exception:
            raise ValueError("Ce PDF est protégé par un mot de passe.")
    if len(lecteur.pages) > PAGES_MAX:
        raise ValueError("PDF de plus de %d pages : fournissez uniquement le CCTP (pas le dossier complet)." % PAGES_MAX)
    pages = []
    for page in lecteur.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n".join(pages), len(pages)


def texte_docx(contenu):
    try:
        archive = zipfile.ZipFile(io.BytesIO(contenu))
        if archive.getinfo("word/document.xml").file_size > 200 * 10**6:
            raise ValueError("Document Word anormalement volumineux une fois décompressé.")
        xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("Fichier Word illisible (seul le format .docx est pris en charge, pas .doc).")
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    racine = ElementTree.fromstring(xml)
    paragraphes = []
    for p in racine.iter(ns + "p"):
        morceaux = []
        for n in p.iter():
            if n.tag == ns + "t" and n.text:
                morceaux.append(n.text)
            elif n.tag == ns + "tab":
                morceaux.append("\t")
        paragraphes.append("".join(morceaux))
    return "\n".join(paragraphes), None


def lire_cctp(contenu: bytes, nom_fichier: str):
    ext = nom_fichier.lower().rsplit(".", 1)[-1] if "." in nom_fichier else ""
    if ext == "pdf":
        texte, nb_pages = texte_pdf(contenu)
    elif ext == "docx":
        texte, nb_pages = texte_docx(contenu)
    elif ext in ("txt", "md"):
        try:
            texte = contenu.decode("utf-8-sig")
        except UnicodeDecodeError:
            texte = contenu.decode("cp1252", errors="replace")
        nb_pages = None
    elif ext == "doc":
        raise ValueError("Le format Word ancien (.doc) n'est pas pris en charge : enregistrez le CCTP en .docx ou en PDF.")
    else:
        raise ValueError("Format de CCTP non pris en charge : utilisez un PDF, un .docx ou un .txt.")

    texte = re.sub(r"[ \t ]+", " ", texte)
    texte = re.sub(r"\n\s*\n+", "\n", texte).strip()
    if len(texte) < 200:
        raise ValueError("Presque aucun texte n'a pu être extrait. S'il s'agit d'un PDF scanné (image), "
                         "il faudrait d'abord le passer en reconnaissance de caractères (OCR).")

    articles, lots = [], []
    for ligne in texte.splitlines():
        m = MOTIF_LOT.match(ligne)
        if m:
            lots.append("Lot %s – %s" % (m.group(1).zfill(2), m.group(2).strip()))
            continue
        m = MOTIF_ARTICLE.match(ligne)
        if m and not re.search(r"\d{2,}[.,]\d{2}\s*(€|m|ml)", ligne):
            articles.append({"numero": m.group(1), "titre": m.group(2).strip()[:90]})

    tronque = len(texte) > TAILLE_MAX
    return {
        "fichier": nom_fichier,
        "texte": texte[:TAILLE_MAX],
        "nb_caracteres": len(texte),
        "nb_mots": len(texte.split()),
        "nb_pages": nb_pages,
        "lots": list(dict.fromkeys(lots))[:30],
        "articles": articles[:400],
        "nb_articles": len(articles),
        "tronque": tronque,
    }
