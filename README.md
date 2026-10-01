# SurveIA

**De la maquette BIM au chiffrage, en quelques minutes.** Une preuve de concept (POC) qui lit une maquette IFC ou un plan DXF/DWG, calcule le métré, puis confie à une IA (DeepSeek) la partie fastidieuse du travail de l'économiste : associer chaque ouvrage à la bonne ligne d'une bibliothèque de prix, en s'appuyant sur le CCTP. Une visionneuse 3D/2D permet de localiser chaque ligne de prix dans la maquette.

> **Principe clé : l'IA ne calcule aucune quantité et n'invente aucun prix.** Le métré est fait par le code, à partir des quantités IFC ou de la géométrie du plan. Les prix viennent uniquement de la bibliothèque fournie. Chaque calcul est affiché et traçable, et l'estimation est à relire par un économiste.

![Estimation et visionneuse 3D](docs/captures/estimation_et_3d.png)

## Ce que fait l'application

1. **Clé API DeepSeek** : demandée au lancement, gardée en mémoire seulement, jamais écrite sur le disque.
2. **Sources** :
   - une maquette **IFC** (IFC2x3 ou IFC4) ou un plan **DXF / DWG** ;
   - un **CCTP** facultatif (PDF, Word `.docx` ou texte) ;
   - un **DPGF** facultatif (Excel `.xlsx`, de structure libre) ;
   - une **bibliothèque de prix** (CSV).
3. **Métré**, calculé par le code sans IA.
   - **IFC** : jeux de quantités (`Qto_*`) regroupés par type, locaux (IfcSpace) et appareils sanitaires.
   - **Plans** : longueurs par calque, surfaces des contours et hachures, nombre de blocs (y compris imbriqués et dynamiques), locaux repérés par leur nom.
4. **Estimation** : DeepSeek choisit la ou les lignes de prix de chaque ouvrage, avec un niveau de confiance et une justification. S'il y a un CCTP, il cite les articles et signale les **écarts maquette / CCTP**. Il liste aussi les **ouvrages décrits au CCTP mais non modélisés**.
5. **Export Excel** : lots, sous-totaux, TVA et TTC, avec les formules conservées. Les prix se modifient dans l'onglet bibliothèque, et les quantités manquantes sont à saisir sur fond jaune.
6. **Questions libres** sur le chiffrage : « Quel impact si on passe les fenêtres en PVC ? », etc.
7. **Visionneuse** : cliquez une ligne pour voir les objets concernés, ou un objet pour voir sa fiche et ses prix.
8. **Ajustements en direct** : vous corrigez l'IA en quelques clics, par catégorie, par type ou objet par objet (voir ci-dessous). Le total se recalcule instantanément, sans appel à l'IA.
9. **DPGF et CCTP du projet** : le DPGF fourni structure le chiffrage (voir ci-dessous).

## Avec un DPGF et un CCTP

Joignez le **DPGF** du projet (celui du maître d'œuvre, ou celui de l'entreprise) et son **CCTP** : le chiffrage suit alors leur organisation, **quelle qu'en soit la structure**.

- **Lecture libre d'un DPGF Excel** : le code retrouve la ligne d'en-tête (même sur deux lignes), le rôle de chaque colonne (désignation, unité, quantité, prix, renvoi au CCTP…) et la hiérarchie (lots, chapitres) d'après la numérotation (`2.1.1`, `01.02.003`…), la mise en forme (gras, majuscules) ou les noms de feuilles. Il repère les lignes « pour mémoire » et les **options** (PSE, variantes), ignore les totaux, rattache les descriptions à leur article. Si l'en-tête est introuvable, l'IA aide ; sinon vous corrigez les colonnes dans « Vérifier ce que le code a compris ».
- **Les articles du DPGF sont les lignes du chiffrage** : l'IA associe chaque élément de la maquette à un article du DPGF, jamais à un article inventé. Un élément sans article reste « non chiffré ». Un article sans élément dans la maquette reste dans l'estimation, « à établir » (avec la quantité du CCTP si elle y figure, ou celle du DPGF).
- **Prix, au choix** : ceux du DPGF ; ou ceux du DPGF complétés par la bibliothèque (rapprochement automatique, signalé « à confirmer » quand il est douteux) ; ou **quantités seulement**.
- **Le DPGF et le CCTP ne sont jamais modifiés.** Vous obtenez **deux fichiers** : votre DPGF **renseigné** (une copie : mise en page et formules conservées, quantités écrites dans ses colonnes, commentaire de traçabilité sur chaque cellule, onglet « SurveIA - traçabilité ») et l'**estimation SurveIA**, organisée par lots et chapitres du DPGF, options à part. Une quantité déjà présente dans le DPGF n'est jamais écrasée : l'écart avec le métré est signalé.
- **Citations du CCTP vérifiées** : l'IA cite l'article et un extrait ; le code vérifie que l'article existe et que l'extrait figure **mot pour mot** dans le CCTP. Sinon la citation est signalée en rouge et la confiance plafonnée. Le fichier et l'empreinte du CCTP et du DPGF utilisés sont indiqués dans l'Excel.
- Formats : `.xlsx` ; l'ancien `.xls` demande `pip install xlrd` (sinon, enregistrez-le en `.xlsx`).
| Visionneuse 2D (plan DXF) | Vue en plan d'un niveau (IFC) |
|---|---|
| ![Plan 2D](docs/captures/plan_2d_portes.png) | ![Vue de dessus](docs/captures/vue_de_dessus_locaux.png) |

## Ajuster le chiffrage à la main

L'IA propose, l'économiste décide. Une fois les prix associés, tout se corrige en direct, depuis le tableau ou depuis la maquette :

- **Sélectionner** : un clic sur une ligne du métré (un type entier) ou sur un objet dans la vue 3D / 2D. <kbd>Maj</kbd>+clic (ou <kbd>⌘</kbd>+clic) ajoute ou retire des éléments. La fiche propose d'étendre la sélection à **tout le type**, à **toute la catégorie** ou de ne garder qu'**un niveau**.
- **Chiffrer avec…** : une recherche dans la bibliothèque de prix, avec les lignes proposées par l'IA en tête. Chaque prix indique si sa quantité est **mesurable** sur la sélection (m², m³, ml, u) ou restera à saisir. Deux modes : *remplacer* les prix actuels, ou *ajouter* une ligne (une finition en plus, par exemple).
- **Retirer** : la croix d'un élément source retire ce type ou ces objets de la ligne. On peut aussi exclure une sélection du chiffrage, puis la réintégrer.
- **Ouvrir une ligne** (clic) : ses éléments apparaissent en bleu dans la vue. On peut y ajouter la sélection, y déplacer des éléments, **fixer la quantité** (la quantité calculée reste affichée), **valider** la ligne ou la supprimer.
- **Saisie manuelle** : une quantité pour un ouvrage non modélisé. Les ouvrages décrits au CCTP mais absents de la maquette ont un bouton « Chiffrer… ».
- **Plans 2D** : la nature de chaque calque (mur, sol, linéaire…) se change dans le métré, ce qui change la règle de calcul de la quantité.
- **Contrôle** : dans la visionneuse, la case « Contrôle » colore chaque objet selon son état : chiffré par l'IA, chiffré à la main, non chiffré, hors chiffrage. Les oublis sautent aux yeux.
- **Annuler / Rétablir** (<kbd>⌘Z</kbd> / <kbd>⇧⌘Z</kbd>, ou <kbd>Ctrl+Z</kbd> / <kbd>Ctrl+Y</kbd>) et **historique** de toutes les modifications, repris dans l'onglet « Modifications » de l'Excel.
- **Sauvegarde automatique** : vos modifications sont retrouvées quand vous rechargez le même fichier.
- **Apprentissage** : un choix fait sur un type entier (« ce type de mur se chiffre avec telle ligne ») est retenu, puis appliqué automatiquement dans vos projets suivants si le même type y apparaît, avec la même ligne de bibliothèque.

| Choisir un prix pour une sélection | Ligne ouverte (bleu) et mode « Contrôle » |
|---|---|
| ![Choix d'une ligne de prix](docs/captures/ajustement_choix_prix.png) | ![Ligne ouverte](docs/captures/ajustement_ligne.png) |

## Installation

Il faut **Python 3.9 ou plus récent**. Aucun autre logiciel n'est nécessaire, et la visionneuse 3D fonctionne hors ligne.

```bash
git clone https://github.com/halhee/SurveIA.git
cd SurveIA
python3 -m pip install -r requirements.txt
python3 chiffrage_ia.py
```

Le navigateur s'ouvre sur `http://127.0.0.1:8765`. Gardez la fenêtre du Terminal ouverte, et arrêtez avec `Ctrl+C`.

- **Mac** : double-cliquez sur `lancer_mac.command`. La première fois, faites clic droit > Ouvrir pour passer l'avertissement de macOS.
- **Windows** : double-cliquez sur `lancer_windows.bat`.
- **Vérifier l'installation sans clé API** : `python3 chiffrage_ia.py --diagnostic` (ou `diagnostic_mac.command`).

Une clé API DeepSeek se crée sur [platform.deepseek.com](https://platform.deepseek.com). Tout le reste (métré, visionneuse, démos) fonctionne sans clé.

## Démonstration

Les fichiers de démo décrivent un même petit immeuble de bureaux R+1, **entièrement fictif** :

| Fichier | Contenu |
|---|---|
| `maquette_demo.ifc` | Maquette IFC4 : 100 éléments et 18 locaux |
| `plan_RDC_demo.dxf` | Le rez-de-chaussée en DXF, avec calques, blocs et locaux |
| `CCTP_demo.pdf` | Un CCTP de 3 pages. Il contient **volontairement** des contradictions avec la maquette et des ouvrages non modélisés |
| `DPGF_demo.xlsx` | Un DPGF « maître d'œuvre » sans prix (26 articles, 9 lots, un article pour mémoire, une option), cohérent avec le CCTP |
| `DPGF_demo_entreprise.xlsx` | Un DPGF d'une **autre structure** (page de garde, une feuille par lot, sans numéros, prix et quelques quantités déjà renseignés) |
| `bibliotheque_prix.csv` | 33 prix **fictifs** |

Scénarios conseillés :

1. **Maquette + CCTP** : les articles cités et les écarts apparaissent en rouge. Dans l'Excel, saisissez 28 m³ de fondations : le total se met à jour.
2. **Plan DXF + CCTP** : le même bâtiment en 2D.
3. **DPGF + CCTP** : chargez les deux démos, puis changez de mode de prix. Dans l'Excel du DPGF renseigné, les formules d'origine sont intactes. Essayez aussi le DPGF d'entreprise : même fonctionnement avec une structure très différente.
4. **Effet mémoire** : relancez le calcul ou changez la hauteur des murs. Le résultat est instantané, sans nouvel appel à l'IA.

## Utiliser vos propres données

- **Bibliothèque de prix** : un CSV séparé par `;` ou `,`, avec les colonnes `code ; lot ; designation ; unite ; prix_unitaire_ht`. Unités reconnues : `m2`, `m3`, `ml`, `u`, `ens`, `ft`.
- **Revit** : dans l'export IFC, cochez **« Exporter les quantités de base »**, et exportez les pièces pour chiffrer sols et plafonds.
- **Plans** : nommez clairement vos calques (`A-MUR-BA20`, `A-CLOISON-72`…), utilisez des blocs pour les portes et fenêtres, et des polylignes fermées pour les locaux.
- **DWG** : enregistrez le plan en DXF, ou installez le convertisseur gratuit [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter). La conversion devient alors automatique.
- **CCTP** : un PDF avec du texte (pas un scan), ou un `.docx`.

## Confidentialité

L'API DeepSeek est hébergée en Chine. Voici ce qui lui est envoyé :

- les **noms** de types, calques, blocs et matériaux, avec les quantités mesurées ;
- les désignations de la bibliothèque de prix ;
- les **articles du DPGF** (numéro, lot, désignation, unité, description) s'il est fourni, **sans aucun prix ni quantité** ;
- **le texte complet du CCTP**, s'il est fourni.

Les fichiers IFC et DXF eux-mêmes ne sont jamais envoyés. **Vérifiez que c'est compatible avec vos engagements de confidentialité**, en particulier pour le CCTP. Voir aussi [SECURITY.md](SECURITY.md).

## Architecture

| Fichier | Rôle |
|---|---|
| `chiffrage_ia.py` | Serveur local, lecture IFC et métré, appels à l'IA, estimation, export Excel |
| `lecture_dxf.py` | Lecture DXF (et DWG par conversion) : métré 2D et dessin pour la visionneuse |
| `lecture_cctp.py` | Lecture des CCTP (PDF, `.docx`, `.txt`) |
| `lecture_dpgf.py` | Lecture d'un DPGF Excel de structure libre, et écriture du DPGF renseigné (copie) |
| `liaison_dpgf.py` | Articles du DPGF → lignes de chiffrage, modes de prix, vérification des citations du CCTP |
| `geometrie_ifc.py` | Formes 3D des éléments IFC : extrusions, polyèdres, maillages IFC4, types partagés, découpes |
| `interface.html`, `visionneuse.js` | Interface web et visionneuse (WebGL 2 pour la 3D, SVG pour les plans), sans bibliothèque externe |
| `donnees_locales/` | Créé à l'usage : vos modifications par fichier et vos choix retenus (jamais publié) |
| `outils/` | Générateurs des fichiers de démo (reproductibles à l'octet près) |
| `tests/` | Tests de bout en bout contre un faux DeepSeek local |

Dépendances : `openpyxl` (Excel) et `pypdf` (CCTP en PDF). Les lecteurs IFC et DXF et la 3D sont écrits sans dépendance.

## Tests

```bash
python3 tests/test_bout_en_bout.py          # 124 vérifications, environ une minute
python3 tests/test_interface.py             # interface dans un vrai navigateur (facultatif : pip install playwright)
```

Les tests n'ont besoin **ni de clé API ni de réseau**. Un faux DeepSeek local (`tests/faux_deepseek.py`) imite le modèle, y compris ses défauts : réponses tronquées, groupes oubliés, paramètre refusé. Sont vérifiés :

- le métré et l'estimation ;
- les ajustements manuels (objets, types, quantités, annuler / rétablir, sauvegarde, apprentissage) ;
- les DPGF (plusieurs structures, trois modes de prix, DPGF renseigné sans toucher au fichier d'origine) et la vérification des citations du CCTP ;
- les montants de l'Excel ;
- la mémoire des correspondances ;
- la sécurité (requêtes d'autres sites refusées) ;
- la visionneuse ;
- une maquette d'environ 80 Mo.

## Limites connues

C'est un POC, pas un outil de production :

- une seule source de métré à la fois (maquette **ou** plan) ;
- plans 2D : ouvertures non déduites des murs ; les fenêtres ne donnent qu'un nombre ;
- visionneuse : réservations non percées ; formes NURBS et révolutions non dessinées (elles restent dans le métré) ;
- DXF binaire et PDF scannés non lus ;
- les correspondances proposées par l'IA doivent être relues par un économiste ;
- DPGF : seul Excel est lu (pas de PDF) ; les prix rapprochés d'une bibliothèque sont indicatifs ; les quantités du DPGF ne sont jamais écrasées, même quand elles diffèrent du métré ;
- plans 2D : un calque se chiffre en entier (seuls les blocs et les locaux se choisissent un par un).

Voir [CHANGELOG.md](CHANGELOG.md) pour l'historique des versions.

## Licence

Distribué sous licence Apache 2.0 : voir [LICENSE](LICENSE).
