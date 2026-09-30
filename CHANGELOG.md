# Historique des versions

## Version 6 — ajustements en direct
- **Corriger l'IA en quelques clics**, depuis le métré ou la visionneuse :
  - sélection par type, par catégorie, par niveau ou objet par objet (<kbd>Maj</kbd>+clic dans la vue 3D / 2D) ;
  - « Chiffrer avec… » : recherche dans la bibliothèque, lignes proposées par l'IA en tête, unité mesurable ou non ;
  - retrait d'un type ou d'objets d'une ligne, exclusion puis réintégration, quantité forcée, saisie manuelle, validation ;
  - nature des calques des plans 2D modifiable.
- Chaque objet IFC garde ses propres quantités : une partie d'un type se chiffre exactement.
- Recalcul instantané, sans appel à l'IA ; annuler / rétablir (⌘Z) et historique des modifications.
- **Contrôle** dans la visionneuse : objets colorés selon leur état (IA, manuel, non chiffré, hors chiffrage).
- **Sauvegarde automatique** des modifications par fichier, et **apprentissage** des choix faits sur des types entiers, repris dans les projets suivants.
- Excel : statut « manuelle » ou « validée », quantités saisies commentées, onglet « Modifications ».
- Les réponses aux questions tiennent compte des modifications de l'économiste.

## Version 5 — publication sur GitHub
- **Visionneuse 3D (IFC) et 2D (DXF/DWG)**, liée dans les deux sens au métré et à l'estimation.
  - Un clic sur une ligne allume et cadre les objets concernés.
  - Un clic sur un objet ouvre sa fiche avec ses lignes de prix.
- **3D** : vue « rayons X », filtre par niveau, vue en plan (dalles masquées automatiquement), affichage des locaux. Moteur écrit sans dépendance : il fonctionne hors ligne.
- **Sécurité** :
  - neutralisation de l'injection de formules dans l'Excel ;
  - en-têtes de sécurité HTTP et taille maximale des envois ;
  - `pypdf` ≥ 6.19.0 (failles corrigées) ;
  - limites de lecture des CCTP ;
  - emplacement de la mémoire configurable (`CHIFFRAGE_MEMOIRE`), pour que les tests ne touchent jamais la mémoire réelle.
- Maquette de démo alignée sur le plan DXF. Générateurs reproductibles à l'octet près (`outils/`).
- Tests de bout en bout portables (`tests/`), sans clé ni réseau.

## Version 4
- **Maquettes IFC** :
  - les locaux (IfcSpace) donnent les surfaces de sols et de plafonds ;
  - les appareils sanitaires sont comptés ;
  - lecture plus rapide des gros fichiers, en arrière-plan.
- **Plans DXF** : blocs dynamiques AutoCAD comptés, noms accentués des anciens DXF décodés.
- **Fiabilité de l'IA** :
  - réponses tronquées redécoupées automatiquement ;
  - groupes oubliés redemandés ;
  - repli si un paramètre est refusé.
- **Coûts de l'IA** :
  - mémoire des correspondances : un recalcul (hauteur de murs, relance) ne rappelle plus l'IA ;
  - préfixe commun des demandes (CCTP puis prix), pour profiter du cache de DeepSeek.
- **Excel** : les ouvrages du CCTP non modélisés deviennent des lignes à compléter, incluses dans le total.
- **Sécurité et outils** : protection contre les requêtes d'autres sites, liste des modèles lue chez DeepSeek, mode `--diagnostic`.

## Version 3
- Calculs longs en arrière-plan avec progression : corrige l'erreur « Broken pipe » quand le navigateur abandonnait l'attente.
- Mode réflexion de DeepSeek désactivé par défaut (réponses bien plus rapides), réactivable.
- Lanceur Mac (`lancer_mac.command`) et aide pour les certificats de sécurité sous macOS.

## Version 2
- Plans **DXF / DWG** : métré par calque, bloc et local.
- **CCTP** : articles cités, écarts avec la maquette, ouvrages non modélisés.

## Version 1
- Métré IFC, correspondance des prix par DeepSeek, estimation, export Excel, questions libres.
