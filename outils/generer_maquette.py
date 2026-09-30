"""Génère une maquette IFC4 de démonstration (petit immeuble de bureaux R+1)
avec géométrie simple (boîtes extrudées) et quantités de base (Qto_*).

Usage : python generer_maquette.py sortie.ifc [--mm]
  --mm : longueurs exprimées en millimètres (comme beaucoup d'exports Revit),
         pour tester la conversion d'unités du lecteur.
"""
import random
import sys
import time

CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
random.seed(42)


def guid():
    return "".join(random.choice(CHARS) for _ in range(22))


def step_str(s):
    """Encode une chaîne au format STEP (accents en \\X2\\...\\X0\\)."""
    out = []
    for ch in s:
        if ch == "'":
            out.append("''")
        elif ch == "\\":
            out.append("\\\\")
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append("\\X2\\%04X\\X0\\" % ord(ch))
    return "'" + "".join(out) + "'"


class Ifc:
    def __init__(self, scale):
        self.lines = []
        self.n = 0
        self.s = scale  # facteur longueur (1 = m, 1000 = mm)

    def add(self, text):
        self.n += 1
        self.lines.append("#%d=%s;" % (self.n, text))
        return "#%d" % self.n

    def f(self, v):
        return ("%.6f" % float(v)).rstrip("0")

    def L(self, v):  # longueur dans l'unité du fichier
        return self.f(v * self.s)


def build(path, mm=False):
    s = 1000.0 if mm else 1.0
    m = Ifc(s)
    A = m.add
    F = m.f
    Lf = m.L

    person = A("IFCPERSON($,'Demo',$,$,$,$,$,$)")
    org = A("IFCORGANIZATION($,'POC Chiffrage IA',$,$,$)")
    po = A("IFCPERSONANDORGANIZATION(%s,%s,$)" % (person, org))
    app = A("IFCAPPLICATION(%s,'1.0','Generateur demo','GEN')" % org)
    oh = A("IFCOWNERHISTORY(%s,%s,$,.ADDED.,$,$,$,%d)" % (po, app, 1790726400))  # date fixe : fichier reproductible

    if mm:
        u_len = A("IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)")
    else:
        u_len = A("IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)")
    u_area = A("IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.)")
    u_vol = A("IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.)")
    u_ang = A("IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.)")
    units = A("IFCUNITASSIGNMENT((%s,%s,%s,%s))" % (u_len, u_area, u_vol, u_ang))

    origin = A("IFCCARTESIANPOINT((0.,0.,0.))")
    zdir = A("IFCDIRECTION((0.,0.,1.))")
    xdir = A("IFCDIRECTION((1.,0.,0.))")
    ydir = A("IFCDIRECTION((0.,1.,0.))")
    wcs = A("IFCAXIS2PLACEMENT3D(%s,%s,%s)" % (origin, zdir, xdir))
    ctx = A("IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,%s,$)" % wcs)
    body = A("IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Body','Model',*,*,*,*,%s,$,.MODEL_VIEW.,$)" % ctx)

    project = A("IFCPROJECT('%s',%s,%s,$,$,$,$,(%s),%s)" % (guid(), oh, step_str("Immeuble de bureaux - démo"), ctx, units))

    def placement(x=0, y=0, z=0, rel=None, along_y=False):
        p = A("IFCCARTESIANPOINT((%s,%s,%s))" % (Lf(x), Lf(y), Lf(z)))
        ax = A("IFCAXIS2PLACEMENT3D(%s,%s,%s)" % (p, zdir, ydir if along_y else xdir))
        return A("IFCLOCALPLACEMENT(%s,%s)" % (rel if rel else "$", ax))

    site_pl = placement()
    site = A("IFCSITE('%s',%s,'Terrain',$,$,%s,$,$,.ELEMENT.,$,$,$,$,$)" % (guid(), oh, site_pl))
    bld_pl = placement(rel=site_pl)
    bld = A("IFCBUILDING('%s',%s,%s,$,$,%s,$,$,.ELEMENT.,$,$,$)" % (guid(), oh, step_str("Bâtiment A"), bld_pl))
    A("IFCRELAGGREGATES('%s',%s,$,$,%s,(%s))" % (guid(), oh, project, site))
    A("IFCRELAGGREGATES('%s',%s,$,$,%s,(%s))" % (guid(), oh, site, bld))

    def box(lx, ly, lz):
        p0 = A("IFCCARTESIANPOINT((%s,%s))" % (Lf(lx / 2), Lf(ly / 2)))
        p2 = A("IFCAXIS2PLACEMENT2D(%s,$)" % p0)
        prof = A("IFCRECTANGLEPROFILEDEF(.AREA.,$,%s,%s,%s)" % (p2, Lf(lx), Lf(ly)))
        solid = A("IFCEXTRUDEDAREASOLID(%s,%s,%s,%s)" % (prof, wcs, zdir, Lf(lz)))
        rep = A("IFCSHAPEREPRESENTATION(%s,'Body','SweptSolid',(%s))" % (body, solid))
        return A("IFCPRODUCTDEFINITIONSHAPE($,$,(%s))" % rep)

    types = {}
    materials = {}
    type_members = {}
    mat_members = {}

    def get_type(ifc_type_class, name, predef):
        key = (ifc_type_class, name)
        if key not in types:
            if ifc_type_class in ("IFCDOORTYPE", "IFCWINDOWTYPE"):
                if ifc_type_class == "IFCDOORTYPE":
                    t = A("%s('%s',%s,%s,$,$,$,$,$,$,%s,.NOTDEFINED.,$,$)" % (ifc_type_class, guid(), oh, step_str(name), predef))
                else:
                    t = A("%s('%s',%s,%s,$,$,$,$,$,$,%s,.NOTDEFINED.,$,$)" % (ifc_type_class, guid(), oh, step_str(name), predef))
            elif ifc_type_class == "IFCBUILDINGELEMENTPROXYTYPE":
                t = A("%s('%s',%s,%s,$,$,$,$,$,$,.NOTDEFINED.)" % (ifc_type_class, guid(), oh, step_str(name)))
            else:
                t = A("%s('%s',%s,%s,$,$,$,$,$,$,%s)" % (ifc_type_class, guid(), oh, step_str(name), predef))
            types[key] = t
            type_members[t] = []
        return types[key]

    def get_mat(name):
        if name not in materials:
            materials[name] = A("IFCMATERIAL(%s,$,$)" % step_str(name))
            mat_members[materials[name]] = []
        return materials[name]

    qto_links = []  # (element, qto)

    def qto(qset_name, quantities):
        qs = []
        for qname, kind, val in quantities:
            if kind == "L":
                qs.append(A("IFCQUANTITYLENGTH('%s',$,$,%s,$)" % (qname, Lf(val))))
            elif kind == "A":
                qs.append(A("IFCQUANTITYAREA('%s',$,$,%s,$)" % (qname, F(val))))
            elif kind == "V":
                qs.append(A("IFCQUANTITYVOLUME('%s',$,$,%s,$)" % (qname, F(val))))
            elif kind == "C":
                qs.append(A("IFCQUANTITYCOUNT('%s',$,$,%s,$)" % (qname, F(val))))
        return A("IFCELEMENTQUANTITY('%s',%s,'%s',$,$,(%s))" % (guid(), oh, qset_name, ",".join(qs)))

    storeys = []
    espaces = []  # (niveau, [locaux])
    counter = [100000]

    def revit_name(family, typ):
        counter[0] += 1
        return step_str("%s:%s:%d" % (family, typ, counter[0]))

    LX, LY, H, Z = 20.0, 12.0, 3.0, 3.2

    for lvl, (lvl_name, elev) in enumerate([("RDC", 0.0), ("R+1", Z)]):
        st_pl = placement(0, 0, elev, rel=bld_pl)
        st = A("IFCBUILDINGSTOREY('%s',%s,%s,$,$,%s,$,$,.ELEMENT.,%s)" % (guid(), oh, step_str(lvl_name), st_pl, Lf(elev)))
        contained = []
        storeys.append((st, contained))

        def wall(typ, mat, x, y, length, thick, along_y, openings_area=0.0, h=H):
            t = get_type("IFCWALLTYPE", typ, ".STANDARD.")
            pl = placement(x, y, 0, rel=st_pl, along_y=along_y)
            shape = box(length, thick, h)
            e = A("IFCWALL('%s',%s,%s,$,%s,%s,%s,$,.STANDARD.)" % (guid(), oh, revit_name("Mur de base", typ), step_str("Mur de base:" + typ), pl, shape))
            gross = length * h
            q = qto("Qto_WallBaseQuantities", [
                ("Length", "L", length), ("Width", "L", thick), ("Height", "L", h),
                ("GrossSideArea", "A", gross), ("NetSideArea", "A", gross - openings_area),
                ("GrossVolume", "V", gross * thick), ("NetVolume", "V", (gross - openings_area) * thick),
            ])
            qto_links.append((e, q))
            type_members[t].append(e)
            mat_members[get_mat(mat)].append(e)
            contained.append(e)
            return e

        # Fenêtres : 5 petites + 1 grande par long pan, 1 grande sur un pignon
        win_s = (1.20, 1.35)
        win_l = (2.40, 1.35)
        a_s = win_s[0] * win_s[1]
        a_l = win_l[0] * win_l[1]
        long_open = 5 * a_s + a_l
        door_entree = (1.40, 2.20) if lvl == 0 else None

        # Voiles de façade
        wall("Voile BA 20", "Béton armé C25/30", 0, 0, LX, 0.20, False, long_open + (door_entree[0] * door_entree[1] if door_entree else 0))
        wall("Voile BA 20", "Béton armé C25/30", 0, LY - 0.20, LX, 0.20, False, long_open)
        wall("Voile BA 20", "Béton armé C25/30", 0.20, 0.20, LY - 0.40, 0.20, True, a_l)
        wall("Voile BA 20", "Béton armé C25/30", LX, 0.20, LY - 0.40, 0.20, True, 0.0)
        # Doublages isolants intérieurs
        wall("Doublage PSE 10+100", "Polystyrène expansé + plâtre", 0.2, 0.2, LX - 0.4, 0.11, False, long_open + (door_entree[0] * door_entree[1] if door_entree else 0))
        wall("Doublage PSE 10+100", "Polystyrène expansé + plâtre", 0.2, LY - 0.31, LX - 0.4, 0.11, False, long_open)
        # Cloisons
        for i, (x, y, l, ay) in enumerate([(5.072, 5.572, 6.118, True), (10.072, 5.572, 6.118, True), (15.072, 5.572, 2.928, True), (0.31, 4.0, 19.38, False), (0.31, 5.5, 19.38, False)]):
            wall("Cloison 72/48 BA13", "Plaque de plâtre BA13", x, y, l, 0.072, ay, 0.83 * 2.04 * (2 if i < 3 else 1))
        for x, y, l, ay in [(9.098, 0.31, 3.69, True), (15.098, 0.31, 3.69, True)]:
            wall("Cloison 98/48 acoustique", "Plaque de plâtre BA13 (2 par face)", x, y, l, 0.098, ay, 0.93 * 2.04)
        for x, y, l, ay in [(15.072, 8.572, 3.118, True), (15.072, 8.5, 4.618, False)]:
            wall("Cloison 72/48 BA13 hydro", "Plaque de plâtre BA13 H", x, y, l, 0.072, ay, 0.83 * 2.04)

        # Poteaux
        for x, y in [(4.875, 4.625), (9.875, 4.625), (14.875, 4.625), (7.375, 1.875)]:
            t = get_type("IFCCOLUMNTYPE", "Poteau BA 25x25", ".COLUMN.")
            pl = placement(x, y, 0, rel=st_pl)
            e = A("IFCCOLUMN('%s',%s,%s,$,%s,%s,%s,$,.COLUMN.)" % (guid(), oh, revit_name("Poteau béton rectangulaire", "Poteau BA 25x25"), step_str("Poteau béton rectangulaire:Poteau BA 25x25"), pl, box(0.25, 0.25, H)))
            q = qto("Qto_ColumnBaseQuantities", [("Length", "L", H), ("CrossSectionArea", "A", 0.0625), ("NetVolume", "V", 0.0625 * H), ("GrossVolume", "V", 0.0625 * H)])
            qto_links.append((e, q)); type_members[t].append(e); mat_members[get_mat("Béton armé C25/30")].append(e); contained.append(e)

        # Dalles
        def slab(typ, mat, thick, z, predef):
            t = get_type("IFCSLABTYPE", typ, predef)
            pl = placement(0, 0, z, rel=st_pl)
            e = A("IFCSLAB('%s',%s,%s,$,%s,%s,%s,$,%s)" % (guid(), oh, revit_name("Sol", typ), step_str("Sol:" + typ), pl, box(LX, LY, thick), predef))
            area = LX * LY
            q = qto("Qto_SlabBaseQuantities", [("Width", "L", thick), ("Perimeter", "L", 2 * (LX + LY)), ("GrossArea", "A", area), ("NetArea", "A", area), ("GrossVolume", "V", area * thick), ("NetVolume", "V", area * thick)])
            qto_links.append((e, q)); type_members[t].append(e); mat_members[get_mat(mat)].append(e); contained.append(e)

        if lvl == 0:
            slab("Dallage BA 15 sur terre-plein", "Béton armé C25/30", 0.15, -0.15, ".BASESLAB.")
        slab("Dalle BA 20" if lvl == 0 else "Dalle toiture BA 22 + étanchéité", "Béton armé C25/30", 0.20 if lvl == 0 else 0.22, H, ".FLOOR." if lvl == 0 else ".ROOF.")

        # Portes
        def door(typ, w, h, positions):
            for x, y in positions:
                t = get_type("IFCDOORTYPE", typ, ".DOOR.")
                pl = placement(x, y, 0, rel=st_pl)
                e = A("IFCDOOR('%s',%s,%s,$,%s,%s,%s,$,%s,%s,.DOOR.,.NOTDEFINED.,$)" % (guid(), oh, revit_name("Porte", typ), step_str("Porte:" + typ), pl, box(w, 0.1, h), Lf(h), Lf(w)))
                q = qto("Qto_DoorBaseQuantities", [("Width", "L", w), ("Height", "L", h), ("Area", "A", w * h)])
                qto_links.append((e, q)); type_members[t].append(e); contained.append(e)

        door("Porte bois 83x204", 0.83, 2.04, [(1.5, 5.5), (6.5, 5.5), (11.5, 5.5), (16.0, 5.5), (2.17, 3.95), (11.17, 3.95), (16.17, 3.95), (16.0, 8.5)])
        door("Porte coupe-feu EI30 93x204", 0.93, 2.04, [(18.5, 5.5), (0.6, 3.95)])
        if lvl == 0:
            door("Porte d'entrée alu vitrée 140x220", 1.40, 2.20, [(4.0, -0.05)])

        # Fenêtres
        def window(typ, w, h, x, y, n):
            for i in range(n):
                t = get_type("IFCWINDOWTYPE", typ, ".WINDOW.")
                pl = placement(x + i * 3.2, y, 0.9, rel=st_pl)
                e = A("IFCWINDOW('%s',%s,%s,$,%s,%s,%s,$,%s,%s,.WINDOW.,.NOTDEFINED.,$)" % (guid(), oh, revit_name("Fenêtre", typ), step_str("Fenêtre:" + typ), pl, box(w, 0.1, h), Lf(h), Lf(w)))
                q = qto("Qto_WindowBaseQuantities", [("Width", "L", w), ("Height", "L", h), ("Area", "A", w * h)])
                qto_links.append((e, q)); type_members[t].append(e); mat_members[get_mat("Aluminium laqué")].append(e); contained.append(e)

        window("Fenêtre alu 120x135", win_s[0], win_s[1], 1, 0, 5)
        window("Fenêtre alu 120x135", win_s[0], win_s[1], 1, LY - 0.1, 5)
        window("Fenêtre alu 240x135", win_l[0], win_l[1], 16.5, 0, 1)
        window("Fenêtre alu 240x135", win_l[0], win_l[1], 16.5, LY - 0.1, 1)
        window("Fenêtre alu 240x135", win_l[0], win_l[1], 0, 5, 1)

        # Élément « générique » sans quantités (cas fréquent dans les vraies maquettes)
        if lvl == 1:
            for i in range(2):
                t = get_type("IFCBUILDINGELEMENTPROXYTYPE", "Garde-corps vitré", "")
                pl = placement(2 + 8 * i, 0, 3.0, rel=st_pl)
                e = A("IFCBUILDINGELEMENTPROXY('%s',%s,%s,$,%s,%s,%s,$,.NOTDEFINED.)" % (guid(), oh, revit_name("Garde-corps", "Garde-corps vitré"), step_str("Garde-corps:Garde-corps vitré"), pl, box(3.0, 0.05, 1.0)))
                type_members[t].append(e); mat_members[get_mat("Verre feuilleté")].append(e); contained.append(e)

        # Appareils sanitaires (3 WC suspendus + 2 lavabos par niveau)
        for nom_t, predef, n, x0 in (("WC suspendu", ".TOILETPAN.", 3, 16.2), ("Lavabo sur colonne", ".WASHHANDBASIN.", 2, 16.4)):
            for i in range(n):
                t = get_type("IFCSANITARYTERMINALTYPE", nom_t, predef)
                pl = placement(x0 + i * 1.1, 9.0, 0, rel=st_pl)
                e = A("IFCSANITARYTERMINAL('%s',%s,%s,$,%s,%s,%s,$,%s)" % (guid(), oh, revit_name("Sanitaire", nom_t), step_str("Sanitaire:" + nom_t), pl, box(0.4, 0.6, 0.8), predef))
                type_members[t].append(e); mat_members[get_mat("Porcelaine vitrifiée")].append(e); contained.append(e)

        # Locaux (IfcSpace), mêmes contours que le plan DXF du RDC, avec leurs quantités
        locaux = [("Bureau 1", 0.31, 5.572, 5.0, 11.69), ("Bureau 2", 5.072, 5.572, 10.0, 11.69),
                  ("Bureau 3", 10.072, 5.572, 15.0, 11.69), ("Local technique", 15.072, 5.572, 19.69, 8.5),
                  ("Sanitaires", 15.072, 8.572, 19.69, 11.69), ("Circulation", 0.31, 4.072, 19.69, 5.5),
                  ("Accueil" if lvl == 0 else "Bureau 5", 0.31, 0.31, 9.0, 4.0), ("Salle de réunion", 9.098, 0.31, 15.0, 4.0),
                  ("Bureau 4", 15.098, 0.31, 19.69, 4.0)]
        ids_locaux = []
        for k, (nom, x1, y1, x2, y2) in enumerate(locaux):
            pl = placement(x1, y1, 0, rel=st_pl)
            surf = round((x2 - x1) * (y2 - y1), 4)
            e = A("IFCSPACE('%s',%s,%s,$,$,%s,%s,%s,.ELEMENT.,.INTERNAL.,$)" % (guid(), oh, step_str("%d%02d" % (lvl + 1, k + 1)), pl, box(x2 - x1, y2 - y1, H), step_str(nom)))
            q = qto("Qto_SpaceBaseQuantities", [("NetFloorArea", "A", surf), ("GrossFloorArea", "A", surf), ("Height", "L", H)])
            qto_links.append((e, q)); ids_locaux.append(e)
        espaces.append((st, ids_locaux))

    storey_ids = [s for s, _ in storeys]
    for st, ids_locaux in espaces:
        A("IFCRELAGGREGATES('%s',%s,$,$,%s,(%s))" % (guid(), oh, st, ",".join(ids_locaux)))
    A("IFCRELAGGREGATES('%s',%s,$,$,%s,(%s))" % (guid(), oh, bld, ",".join(storey_ids)))
    for st, contained in storeys:
        A("IFCRELCONTAINEDINSPATIALSTRUCTURE('%s',%s,$,$,(%s),%s)" % (guid(), oh, ",".join(contained), st))
    for e, q in qto_links:
        A("IFCRELDEFINESBYPROPERTIES('%s',%s,$,$,(%s),%s)" % (guid(), oh, e, q))
    for t, members in type_members.items():
        if members:
            A("IFCRELDEFINESBYTYPE('%s',%s,$,$,(%s),%s)" % (guid(), oh, ",".join(members), t))
    for mt, members in mat_members.items():
        if members:
            A("IFCRELASSOCIATESMATERIAL('%s',%s,$,$,(%s),%s)" % (guid(), oh, ",".join(members), mt))

    header = (
        "ISO-10303-21;\nHEADER;\n"
        "FILE_DESCRIPTION(('ViewDefinition [ReferenceView]'),'2;1');\n"
        "FILE_NAME('maquette_demo.ifc','2026-09-30T10:00:00',('Demo'),('POC Chiffrage IA'),'Generateur demo','Generateur demo','');\n"
        "FILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n"
    )
    with open(path, "w", encoding="ascii") as fh:
        fh.write(header + "\n".join(m.lines) + "\nENDSEC;\nEND-ISO-10303-21;\n")
    print("Écrit :", path, "(%d entités)" % m.n)


if __name__ == "__main__":
    build(sys.argv[1], mm="--mm" in sys.argv)
