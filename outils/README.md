# Outils : générateurs des fichiers de démonstration

Ces scripts recréent les fichiers de démo du dépôt. Ils produisent **exactement les mêmes octets** à chaque exécution, ce qui permet de vérifier que les démos n'ont pas été modifiées.

```bash
python3 outils/generer_maquette.py maquette_demo.ifc        # maquette IFC4 (ajoutez --mm pour une version en millimètres)
python3 outils/generer_plan_dxf.py plan_RDC_demo.dxf        # plan DXF du rez-de-chaussée
python3 outils/generer_cctp.py CCTP_demo.pdf                # CCTP fictif (nécessite : pip install reportlab)
```

Le bâtiment est entièrement fictif : un immeuble de bureaux R+1 de 20 × 12 m. Le CCTP contient volontairement des contradictions avec la maquette (doublages, cloisons des sanitaires, portes) et des ouvrages non modélisés (fondations, relevés d'étanchéité), pour démontrer la détection des écarts.
