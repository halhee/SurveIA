# Sécurité

## Signaler une vulnérabilité

Merci de **ne pas ouvrir d'issue publique** pour une faille de sécurité. Utilisez plutôt le signalement privé de GitHub : onglet *Security* > *Report a vulnerability*. Décrivez le problème et, si possible, un fichier ou une manipulation qui le reproduit.

## Ce que fait l'application pour vous protéger

### Clé API
- La clé DeepSeek est demandée à chaque lancement et reste **en mémoire uniquement**. Elle n'est jamais écrite sur le disque, ni dans un journal, ni dans l'Excel, ni dans la mémoire des correspondances.
- Elle n'est envoyée qu'à l'API DeepSeek, en HTTPS.

### Serveur local
- L'application n'écoute que sur `127.0.0.1` : elle n'est **pas joignable depuis le réseau**.
- Les requêtes venant d'un autre site ouvert dans le navigateur sont refusées : contrôle des en-têtes `Host` et `Origin`. Cela protège aussi contre le « DNS rebinding ». Sans ce contrôle, un site malveillant pourrait utiliser l'application, et donc votre crédit DeepSeek.
- En-têtes de sécurité : `Content-Security-Policy` (la page ne peut contacter que l'application elle-même), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
- Aucun fichier du disque n'est servi en dehors des fichiers de l'application (pas de parcours de chemin).
- La taille des envois est limitée.

### Fichiers importés (IFC, DXF, CCTP, CSV)
- **Injection de formules Excel** : un texte venant d'un fichier ou de l'IA qui commence par `=` (nom d'objet, calque, désignation…) est écrit comme du **texte**, jamais comme une formule. Seules les formules produites par l'application elle-même sont conservées.
- Toutes les données affichées dans la page sont échappées (pas d'injection HTML ou JavaScript).
- CCTP :
  - `pypdf` ≥ 6.19.0 est exigé : les versions antérieures ont des failles de déni de service sur des PDF piégés. Le lanceur et le diagnostic la mettent à jour ou le signalent ;
  - le nombre de pages est plafonné ;
  - la taille décompressée des `.docx` est contrôlée.
- La conversion DWG appelle ODA File Converter avec un nom de fichier nettoyé, sans passer par un shell.

### Modifications manuelles
- Elles ne peuvent désigner que des codes de la bibliothèque, des groupes et des objets du métré chargé : tout le reste est refusé. Les quantités saisies sont bornées, les libellés limités en longueur, et tout texte est écrit dans l'Excel comme du texte.

### Réponses de l'IA
- L'IA ne peut choisir que des codes **existants** dans la bibliothèque de prix : tout code inventé est rejeté. Les quantités ne viennent jamais de l'IA.
- Ses réponses sont traitées comme des données, jamais exécutées.

## Confidentialité des données de projet

- L'API DeepSeek est hébergée en Chine. Sont envoyés : noms de types, calques, blocs et matériaux, quantités mesurées, désignations de prix, et **texte complet du CCTP** s'il est fourni. Les fichiers IFC et DXF eux-mêmes ne sont jamais envoyés.
- **Vérifiez que c'est compatible avec vos engagements de confidentialité** avant d'utiliser des données de projets clients.
- `memoire_correspondances.json`, créé à côté de l'application, contient les réponses de l'IA sur vos projets. Il est exclu du dépôt par `.gitignore` : **ne le publiez pas**. Vous pouvez le supprimer sans risque, ou utiliser « Vider la mémoire » dans l'interface.
- `donnees_locales/`, créé à côté de l'application, contient vos modifications par fichier (repérés par une empreinte, pas par leur nom) et les choix que vous avez faits sur des types d'ouvrages. Il est aussi exclu du dépôt : **ne le publiez pas**. Emplacement modifiable avec la variable `CHIFFRAGE_DONNEES`.
- Les fichiers de démonstration du dépôt sont entièrement fictifs.
