"""
Géométrie 3D des éléments d'une maquette IFC, pour la visionneuse (aucune dépendance).

Formes prises en charge (ce que produisent Revit, ArchiCAD, Tekla… dans la grande majorité des cas) :
  - extrusions (IfcExtrudedAreaSolid) de profils rectangulaires, circulaires, polygonaux, en I, L, T, U, C…
  - polyèdres (IfcFacetedBrep, IfcShellBasedSurfaceModel, IfcFaceBasedSurfaceModel)
  - maillages IFC4 (IfcTriangulatedFaceSet, IfcPolygonalFaceSet)
  - géométries partagées par type (IfcMappedItem : portes, fenêtres, équipements)
  - découpes (IfcBooleanClippingResult) : on garde la forme principale, sans la découpe
  - boîtes (IfcBoundingBox, IfcBlock) et cylindres (IfcRightCircularCylinder)
Les réservations (trémies, baies) ne sont pas soustraites : la visionneuse sert à localiser,
pas à métrer. Le métré reste calculé à partir des quantités IFC.
"""

import json
import math
import struct
from array import array

IDENTITE = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))
SEGMENTS_CERCLE = 20


def est_ref(x):
    return type(x).__name__ == "Ref"


def est_type(x):
    return hasattr(x, "nom") and hasattr(x, "valeurs")


# ----------------------------------------------------------------------------- algèbre

def normaliser(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-12 else None


def vectoriel(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def scalaire(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def matrice(x, y, z, t):
    """Matrice 3x4 à partir des axes (colonnes) et de la translation."""
    return ((x[0], y[0], z[0], t[0]), (x[1], y[1], z[1], t[1]), (x[2], y[2], z[2], t[2]))


def composer(a, b):
    """a · b (matrices 3x4 affines)."""
    r = []
    for i in range(3):
        ai = a[i]
        r.append((ai[0] * b[0][0] + ai[1] * b[1][0] + ai[2] * b[2][0],
                  ai[0] * b[0][1] + ai[1] * b[1][1] + ai[2] * b[2][1],
                  ai[0] * b[0][2] + ai[1] * b[1][2] + ai[2] * b[2][2],
                  ai[0] * b[0][3] + ai[1] * b[1][3] + ai[2] * b[2][3] + ai[3]))
    return tuple(r)


def appliquer(m, p):
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
            m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
            m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3])


def appliquer_vecteur(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


# ----------------------------------------------------------------------------- triangulation

def aire2(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2


def trianguler2d(poly):
    """Découpage en oreilles d'un polygone simple. Renvoie des triplets d'indices."""
    n = len(poly)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]
    idx = list(range(n))
    if aire2(poly) < 0:
        idx.reverse()
    tris = []

    def dans_triangle(p, a, b, c):
        d1 = (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
        d2 = (p[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (p[1] - c[1])
        d3 = (p[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (p[1] - a[1])
        neg = d1 < 0 or d2 < 0 or d3 < 0
        pos = d1 > 0 or d2 > 0 or d3 > 0
        return not (neg and pos)

    garde = 0
    while len(idx) > 3 and garde < 10 * n:
        garde += 1
        trouve = False
        m = len(idx)
        for k in range(m):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = poly[i0], poly[i1], poly[i2]
            croix = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if croix <= 1e-14:
                continue
            if any(dans_triangle(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            del idx[k]
            trouve = True
            break
        if not trouve:
            break
    if len(idx) >= 3:  # polygone dégénéré : éventail pour ne rien perdre
        for k in range(1, len(idx) - 1):
            tris.append((idx[0], idx[k], idx[k + 1]))
    return tris


def trianguler3d(points):
    """Triangule une face plane en 3D (projection sur le plan dominant)."""
    n = len(points)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]
    nx = ny = nz = 0.0
    for i in range(n):  # normale de Newell
        x1, y1, z1 = points[i]
        x2, y2, z2 = points[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    ax, ay, az = abs(nx), abs(ny), abs(nz)
    if az >= ax and az >= ay:
        proj = [(p[0], p[1]) for p in points] if nz >= 0 else [(p[1], p[0]) for p in points]
    elif ax >= ay:
        proj = [(p[1], p[2]) for p in points] if nx >= 0 else [(p[2], p[1]) for p in points]
    else:
        proj = [(p[2], p[0]) for p in points] if ny >= 0 else [(p[0], p[2]) for p in points]
    return trianguler2d(proj)


# ----------------------------------------------------------------------------- lecteur de géométrie

class Geometrie:
    def __init__(self, ifc, facteur_longueur):
        self.ifc = ifc
        self.f = facteur_longueur
        self.cache_placement = {}
        self.cache_carte = {}  # IfcRepresentationMap -> maillage local
        self.facteur_angle = self._facteur_angle()

    def _facteur_angle(self):
        ifc = self.ifc
        for ua in ifc.tous("IFCUNITASSIGNMENT"):
            for ref in ifc.args(ua)[0] or []:
                if not est_ref(ref):
                    continue
                a = ifc.args(ref)
                if ifc.type_de(ref) == "IFCCONVERSIONBASEDUNIT" and a[1] == "PLANEANGLEUNIT":
                    try:
                        m = ifc.args(a[3])
                        v = m[0].valeur if est_type(m[0]) else m[0]
                        return float(v)
                    except Exception:
                        return math.pi / 180
        return 1.0

    # --- éléments de base
    def point(self, ref):
        a = self.ifc.args(ref)
        c = list(a[0]) if a and isinstance(a[0], list) else [0.0, 0.0, 0.0]
        while len(c) < 3:
            c.append(0.0)
        return (float(c[0]), float(c[1]), float(c[2]))

    def direction(self, ref, defaut):
        if not est_ref(ref):
            return defaut
        a = self.ifc.args(ref)
        c = list(a[0]) if a and isinstance(a[0], list) else list(defaut)
        while len(c) < 3:
            c.append(0.0)
        return normaliser((float(c[0]), float(c[1]), float(c[2]))) or defaut

    def placement_axes(self, ref):
        """IfcAxis2Placement3D / 2D -> matrice."""
        if not est_ref(ref):
            return IDENTITE
        t, a = self.ifc.type_de(ref), self.ifc.args(ref)
        origine = self.point(a[0]) if est_ref(a[0]) else (0.0, 0.0, 0.0)
        if t == "IFCAXIS2PLACEMENT2D":
            x = self.direction(a[1], (1.0, 0.0, 0.0))
            x = normaliser((x[0], x[1], 0.0)) or (1.0, 0.0, 0.0)
            return matrice(x, (-x[1], x[0], 0.0), (0.0, 0.0, 1.0), origine)
        z = self.direction(a[1] if len(a) > 1 else None, (0.0, 0.0, 1.0))
        x = self.direction(a[2] if len(a) > 2 else None, (1.0, 0.0, 0.0) if abs(z[0]) < 0.9 else (0.0, 1.0, 0.0))
        x = normaliser(tuple(x[i] - scalaire(x, z) * z[i] for i in range(3))) or (
            normaliser(vectoriel((0.0, 1.0, 0.0), z)) or (1.0, 0.0, 0.0))
        y = vectoriel(z, x)
        return matrice(x, y, z, origine)

    def placement_objet(self, ref, profondeur=0):
        """IfcLocalPlacement (chaîné) -> matrice absolue."""
        if not est_ref(ref) or profondeur > 50:
            return IDENTITE
        if ref in self.cache_placement:
            return self.cache_placement[ref]
        t, a = self.ifc.type_de(ref), self.ifc.args(ref)
        if t == "IFCLOCALPLACEMENT":
            parent = self.placement_objet(a[0], profondeur + 1) if est_ref(a[0]) else IDENTITE
            m = composer(parent, self.placement_axes(a[1]))
        else:
            m = IDENTITE  # IfcGridPlacement : rare, ignoré
        self.cache_placement[ref] = m
        return m

    def operateur(self, ref):
        """IfcCartesianTransformationOperator3D (et non uniforme) -> matrice."""
        if not est_ref(ref):
            return IDENTITE
        t, a = self.ifc.type_de(ref), self.ifc.args(ref)
        origine = self.point(a[2]) if len(a) > 2 and est_ref(a[2]) else (0.0, 0.0, 0.0)
        s = float(a[3]) if len(a) > 3 and isinstance(a[3], (int, float)) else 1.0
        if "2D" in t:
            x = self.direction(a[0], (1.0, 0.0, 0.0))
            x = normaliser((x[0], x[1], 0.0)) or (1.0, 0.0, 0.0)
            s2 = float(a[4]) if len(a) > 4 and isinstance(a[4], (int, float)) else s
            y = (-x[1], x[0], 0.0)
            return matrice(tuple(c * s for c in x), tuple(c * s2 for c in y), (0.0, 0.0, 1.0), origine)
        z = self.direction(a[4] if len(a) > 4 else None, (0.0, 0.0, 1.0))
        x = self.direction(a[0], (1.0, 0.0, 0.0))
        x = normaliser(tuple(x[i] - scalaire(x, z) * z[i] for i in range(3))) or (1.0, 0.0, 0.0)
        y = vectoriel(z, x)
        s2 = float(a[5]) if len(a) > 5 and isinstance(a[5], (int, float)) else s
        s3 = float(a[6]) if len(a) > 6 and isinstance(a[6], (int, float)) else s
        return matrice(tuple(c * s for c in x), tuple(c * s2 for c in y), tuple(c * s3 for c in z), origine)

    # --- courbes 2D (profils)
    def courbe(self, ref, profondeur=0):
        """Renvoie une liste de points 2D (x, y) approximant la courbe."""
        if not est_ref(ref) or profondeur > 10:
            return []
        t, a = self.ifc.type_de(ref), self.ifc.args(ref)
        if t == "IFCPOLYLINE":
            return [self.point(p)[:2] for p in a[0] if est_ref(p)]
        if t == "IFCINDEXEDPOLYCURVE":
            liste = self.ifc.args(a[0])[0] if est_ref(a[0]) else []
            pts = [(float(c[0]), float(c[1])) for c in liste]
            if not a[1]:
                return pts
            res = []
            for seg in a[1]:
                indices = seg.valeurs[0] if est_type(seg) and seg.valeurs and isinstance(seg.valeurs[0], list) else []
                if est_type(seg) and seg.nom == "IFCARCINDEX" and len(indices) == 3:
                    res.extend(self._arc3(*(pts[i - 1] for i in indices)))
                else:
                    res.extend(pts[i - 1] for i in indices if 0 < i <= len(pts))
            return res
        if t == "IFCCOMPOSITECURVE":
            res = []
            for seg in a[0] or []:
                sa = self.ifc.args(seg)
                pts = self.courbe(sa[2], profondeur + 1)
                if sa[1] is False or sa[1] == "F":
                    pts = pts[::-1]
                res.extend(pts)
            return res
        if t == "IFCTRIMMEDCURVE":
            return self._courbe_limitee(a, profondeur)
        if t in ("IFCCIRCLE", "IFCELLIPSE"):
            m = self.placement_axes(a[0])
            r1 = float(a[1])
            r2 = float(a[2]) if t == "IFCELLIPSE" else r1
            return [appliquer(m, (r1 * math.cos(2 * math.pi * k / SEGMENTS_CERCLE),
                                  r2 * math.sin(2 * math.pi * k / SEGMENTS_CERCLE), 0.0))[:2]
                    for k in range(SEGMENTS_CERCLE)]
        return []

    def _arc3(self, p1, p2, p3):
        """Arc passant par 3 points (approximé)."""
        ax, ay = p1
        bx, by = p2
        cx, cy = p3
        d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(d) < 1e-12:
            return [p1, p2, p3]
        ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
        uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
        r = math.hypot(ax - ux, ay - uy)
        a1, a2, a3 = (math.atan2(p[1] - uy, p[0] - ux) for p in (p1, p2, p3))

        def entre(a, b, c):  # c est-il sur l'arc de a vers b dans le sens positif ?
            return (c - a) % (2 * math.pi) < (b - a) % (2 * math.pi)

        balayage = (a3 - a1) % (2 * math.pi)
        if not entre(a1, a3, a2):
            balayage -= 2 * math.pi
        n = max(3, int(abs(balayage) / (2 * math.pi) * SEGMENTS_CERCLE) + 1)
        return [(ux + r * math.cos(a1 + balayage * k / n), uy + r * math.sin(a1 + balayage * k / n)) for k in range(n + 1)]

    def _courbe_limitee(self, a, profondeur):
        base, trim1, trim2, sens = a[0], a[1] or [], a[2] or [], a[3]
        tb, ab = self.ifc.type_de(base), self.ifc.args(base)

        def point_trim(trims):
            for x in trims:
                if est_ref(x) and self.ifc.type_de(x) == "IFCCARTESIANPOINT":
                    return ("p", self.point(x)[:2])
            for x in trims:
                if est_type(x) and x.nom == "IFCPARAMETERVALUE":
                    return ("t", float(x.valeur))
            return None

        d1, d2 = point_trim(trim1), point_trim(trim2)
        if tb == "IFCLINE":
            origine = self.point(ab[0])[:2]
            va = self.ifc.args(ab[1])
            dirv = self.direction(va[0], (1.0, 0.0, 0.0))
            mag = float(va[1]) if len(va) > 1 and isinstance(va[1], (int, float)) else 1.0

            def pt(d):
                if d is None:
                    return origine
                return d[1] if d[0] == "p" else (origine[0] + dirv[0] * mag * d[1], origine[1] + dirv[1] * mag * d[1])
            return [pt(d1), pt(d2)]
        if tb in ("IFCCIRCLE", "IFCELLIPSE"):
            m = self.placement_axes(ab[0])
            r1 = float(ab[1])
            r2 = float(ab[2]) if tb == "IFCELLIPSE" else r1

            def angle(d):
                if d is None:
                    return 0.0
                if d[0] == "t":
                    return d[1] * self.facteur_angle
                # point -> angle dans le repère du cercle
                inv_x = (m[0][0], m[1][0])
                inv_y = (m[0][1], m[1][1])
                dx, dy = d[1][0] - m[0][3], d[1][1] - m[1][3]
                return math.atan2((dx * inv_y[0] + dy * inv_y[1]) / r2, (dx * inv_x[0] + dy * inv_x[1]) / r1)
            t1, t2 = angle(d1), angle(d2)
            if sens is False or sens == "F":
                balayage = -((t1 - t2) % (2 * math.pi))
            else:
                balayage = (t2 - t1) % (2 * math.pi)
            n = max(2, int(abs(balayage) / (2 * math.pi) * SEGMENTS_CERCLE) + 1)
            return [appliquer(m, (r1 * math.cos(t1 + balayage * k / n), r2 * math.sin(t1 + balayage * k / n), 0.0))[:2]
                    for k in range(n + 1)]
        return self.courbe(base, profondeur + 1)

    # --- profils
    def profil(self, ref, profondeur=0):
        """Renvoie une liste de polygones 2D (contours extérieurs)."""
        if not est_ref(ref) or profondeur > 5:
            return []
        t, a = self.ifc.type_de(ref), self.ifc.args(ref)
        if t in ("IFCARBITRARYCLOSEDPROFILEDEF", "IFCARBITRARYPROFILEDEFWITHVOIDS"):
            pts = self.courbe(a[2])
            if len(pts) > 1 and math.dist(pts[0], pts[-1]) < 1e-9:
                pts = pts[:-1]
            # supprime les doublons consécutifs
            nettoye = [p for i, p in enumerate(pts) if i == 0 or math.dist(p, pts[i - 1]) > 1e-9]
            return [nettoye] if len(nettoye) >= 3 else []
        if t == "IFCDERIVEDPROFILEDEF":
            m = self.operateur(a[3])
            return [[appliquer(m, (p[0], p[1], 0.0))[:2] for p in poly] for poly in self.profil(a[2], profondeur + 1)]
        if t == "IFCCOMPOSITEPROFILEDEF":
            res = []
            for p in a[2] or []:
                res.extend(self.profil(p, profondeur + 1))
            return res
        # profils paramétrés : Position en 3e attribut
        m = self.placement_axes(a[2]) if len(a) > 2 and est_ref(a[2]) else IDENTITE
        nums = [x for x in a[3:] if isinstance(x, (int, float))]

        def rect(lx, ly):
            return [[appliquer(m, (sx * lx / 2, sy * ly / 2, 0.0))[:2]
                     for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]]

        def cercle(r1, r2):
            return [[appliquer(m, (r1 * math.cos(2 * math.pi * k / SEGMENTS_CERCLE),
                                   r2 * math.sin(2 * math.pi * k / SEGMENTS_CERCLE), 0.0))[:2]
                     for k in range(SEGMENTS_CERCLE)]]

        if t in ("IFCCIRCLEPROFILEDEF", "IFCCIRCLEHOLLOWPROFILEDEF") and nums:
            return cercle(nums[0], nums[0])
        if t == "IFCELLIPSEPROFILEDEF" and len(nums) >= 2:
            return cercle(nums[0], nums[1])
        if t in ("IFCRECTANGLEPROFILEDEF", "IFCRECTANGLEHOLLOWPROFILEDEF", "IFCROUNDEDRECTANGLEPROFILEDEF",
                 "IFCISHAPEPROFILEDEF", "IFCASYMMETRICISHAPEPROFILEDEF", "IFCTRAPEZIUMPROFILEDEF") and len(nums) >= 2:
            return rect(nums[0], nums[1])
        if t in ("IFCLSHAPEPROFILEDEF", "IFCTSHAPEPROFILEDEF", "IFCUSHAPEPROFILEDEF", "IFCCSHAPEPROFILEDEF",
                 "IFCZSHAPEPROFILEDEF") and len(nums) >= 2:
            return rect(nums[1], nums[0])  # Depth puis largeur
        if len(nums) >= 2:
            return rect(nums[0], nums[1])
        return []

    # --- solides et surfaces -> triangles (liste de points + liste de triplets)
    def item(self, ref, m, sortie, profondeur=0):
        if not est_ref(ref) or profondeur > 12:
            return
        t, a = self.ifc.type_de(ref), self.ifc.args(ref)
        if t in ("IFCEXTRUDEDAREASOLID", "IFCEXTRUDEDAREASOLIDTAPERED"):
            mp = composer(m, self.placement_axes(a[1])) if est_ref(a[1]) else m
            d = self.direction(a[2], (0.0, 0.0, 1.0))
            h = float(a[3])
            for poly in self.profil(a[0]):
                self._prisme(poly, d, h, mp, sortie)
        elif t in ("IFCFACETEDBREP", "IFCFACETEDBREPWITHVOIDS", "IFCADVANCEDBREP"):
            self._coque(a[0], m, sortie)
        elif t in ("IFCCLOSEDSHELL", "IFCOPENSHELL", "IFCCONNECTEDFACESET"):
            self._coque(ref, m, sortie)
        elif t == "IFCSHELLBASEDSURFACEMODEL":
            for coque in a[0] or []:
                self._coque(coque, m, sortie)
        elif t == "IFCFACEBASEDSURFACEMODEL":
            for coque in a[0] or []:
                self._coque(coque, m, sortie)
        elif t == "IFCTRIANGULATEDFACESET":
            coords = self.ifc.args(a[0])[0]
            pn = a[4] if len(a) > 4 and isinstance(a[4], list) and a[4] and not isinstance(a[4][0], list) else None
            base = len(sortie[0])
            sortie[0].extend(appliquer(m, tuple(float(c) for c in (list(p) + [0.0, 0.0])[:3])) for p in coords)
            n = len(coords)
            for tri in a[3] or []:
                idx = [(pn[i - 1] if pn else i) - 1 for i in tri]
                if all(0 <= i < n for i in idx):
                    sortie[1].append((base + idx[0], base + idx[1], base + idx[2]))
        elif t == "IFCPOLYGONALFACESET":
            coords = self.ifc.args(a[0])[0]
            pn = a[3] if len(a) > 3 and isinstance(a[3], list) and a[3] and not isinstance(a[3][0], list) else None
            pts = [appliquer(m, tuple(float(c) for c in (list(p) + [0.0, 0.0])[:3])) for p in coords]
            for face in a[2] or []:
                fa = self.ifc.args(face)
                idx = [(pn[i - 1] if pn else i) - 1 for i in (fa[0] or [])]
                idx = [i for i in idx if 0 <= i < len(pts)]
                self._face(idx and [pts[i] for i in idx], sortie)
        elif t == "IFCMAPPEDITEM":
            source, cible = a[0], a[1]
            sa = self.ifc.args(source)
            if source not in self.cache_carte:
                local = ([], [])
                origine = self.placement_axes(sa[0]) if est_ref(sa[0]) else IDENTITE
                for it in self.ifc.args(sa[1])[3] or []:
                    self.item(it, origine, local, profondeur + 1)
                self.cache_carte[source] = local
            pts, tris = self.cache_carte[source]
            mc = composer(m, self.operateur(cible))
            base = len(sortie[0])
            sortie[0].extend(appliquer(mc, p) for p in pts)
            sortie[1].extend((base + i, base + j, base + k) for i, j, k in tris)
        elif t in ("IFCBOOLEANCLIPPINGRESULT", "IFCBOOLEANRESULT"):
            self.item(a[1], m, sortie, profondeur + 1)
            if a[0] == "UNION":
                self.item(a[2], m, sortie, profondeur + 1)
        elif t == "IFCCSGSOLID":
            self.item(a[0], m, sortie, profondeur + 1)
        elif t == "IFCBOUNDINGBOX":
            c = self.point(a[0])
            self._boite(c, float(a[1]), float(a[2]), float(a[3]), m, sortie)
        elif t == "IFCBLOCK":
            mp = composer(m, self.placement_axes(a[0]))
            self._boite((0.0, 0.0, 0.0), float(a[1]), float(a[2]), float(a[3]), mp, sortie)
        elif t == "IFCRIGHTCIRCULARCYLINDER":
            mp = composer(m, self.placement_axes(a[0]))
            r = float(a[2])
            poly = [(r * math.cos(2 * math.pi * k / SEGMENTS_CERCLE), r * math.sin(2 * math.pi * k / SEGMENTS_CERCLE))
                    for k in range(SEGMENTS_CERCLE)]
            self._prisme(poly, (0.0, 0.0, 1.0), float(a[1]), mp, sortie)
        elif t == "IFCSWEPTDISKSOLID":
            self._tube(a, m, sortie)
        # autres formes (révolutions, NURBS…) : ignorées

    def _prisme(self, poly, d, h, m, sortie):
        n = len(poly)
        if n < 3:
            return
        base = len(sortie[0])
        bas = [appliquer(m, (p[0], p[1], 0.0)) for p in poly]
        haut = [appliquer(m, (p[0] + d[0] * h, p[1] + d[1] * h, d[2] * h)) for p in poly]
        sortie[0].extend(bas)
        sortie[0].extend(haut)
        tris = trianguler2d(poly)
        for i, j, k in tris:
            sortie[1].append((base + i, base + k, base + j))
            sortie[1].append((base + n + i, base + n + j, base + n + k))
        for i in range(n):
            j = (i + 1) % n
            sortie[1].append((base + i, base + j, base + n + j))
            sortie[1].append((base + i, base + n + j, base + n + i))

    def _boite(self, c, lx, ly, lz, m, sortie):
        poly = [(c[0], c[1]), (c[0] + lx, c[1]), (c[0] + lx, c[1] + ly), (c[0], c[1] + ly)]
        mp = composer(m, matrice((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, c[2])))
        self._prisme(poly, (0.0, 0.0, 1.0), lz, mp, sortie)

    def _tube(self, a, m, sortie):
        """Tube (canalisation, armature) : approximé par des boîtes le long de la directrice."""
        pts = []
        try:
            t = self.ifc.type_de(a[0])
            if t == "IFCPOLYLINE":
                pts = [self.point(p) for p in self.ifc.args(a[0])[0]]
            elif t == "IFCINDEXEDPOLYCURVE":
                liste = self.ifc.args(self.ifc.args(a[0])[0])[0]
                pts = [tuple(float(c) for c in (list(p) + [0.0])[:3]) for p in liste]
        except Exception:
            pts = []
        r = float(a[1]) if isinstance(a[1], (int, float)) else 0.01
        for p, q in zip(pts, pts[1:]):
            d = normaliser((q[0] - p[0], q[1] - p[1], q[2] - p[2]))
            if not d:
                continue
            x = normaliser(vectoriel(d, (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)))
            y = vectoriel(d, x)
            mp = composer(m, matrice(x, y, d, p))
            poly = [(-r, -r), (r, -r), (r, r), (-r, r)]
            self._prisme(poly, (0.0, 0.0, 1.0), math.dist(p, q), mp, sortie)

    def _coque(self, ref, m, sortie):
        if not est_ref(ref):
            return
        for face in self.ifc.args(ref)[0] or []:
            limites = self.ifc.args(face)[0] or []
            exterieure = None
            for lim in limites:
                if self.ifc.type_de(lim) == "IFCFACEOUTERBOUND":
                    exterieure = lim
                    break
            if exterieure is None and limites:
                exterieure = limites[0]
            if exterieure is None:
                continue
            la = self.ifc.args(exterieure)
            if self.ifc.type_de(la[0]) != "IFCPOLYLOOP":
                continue
            pts = [appliquer(m, self.point(p)) for p in self.ifc.args(la[0])[0] if est_ref(p)]
            if la[1] is False or la[1] == "F":
                pts.reverse()
            self._face(pts, sortie)

    def _face(self, pts, sortie):
        if not pts or len(pts) < 3:
            return
        base = len(sortie[0])
        sortie[0].extend(pts)
        sortie[1].extend((base + i, base + j, base + k) for i, j, k in trianguler3d(pts))

    # --- représentation d'un produit
    def produit(self, ident):
        a = self.ifc.args(ident)
        if len(a) < 7 or not est_ref(a[6]):
            return None
        m = self.placement_objet(a[5]) if est_ref(a[5]) else IDENTITE
        reps = [r for r in (self.ifc.args(a[6])[2] or []) if est_ref(r)]
        if not reps:
            return None

        def rang(r):
            ident_rep = self.ifc.args(r)[1] or ""
            return {"Body": 0, "Facetation": 1, "Reference": 3, "Box": 4}.get(ident_rep, 2 if ident_rep not in (
                "Axis", "FootPrint", "Annotation", "Profile", "Plan", "Clearance", "CoG", "Lighting", "Survey") else 9)
        reps.sort(key=rang)
        for r in reps:
            if rang(r) == 9:
                break
            sortie = ([], [])
            for it in self.ifc.args(r)[3] or []:
                try:
                    self.item(it, m, sortie)
                except (TypeError, ValueError, IndexError, ZeroDivisionError, AttributeError):
                    continue  # forme mal écrite : on passe à la suivante
            if sortie[1]:
                return sortie
        return None


# ----------------------------------------------------------------------------- export binaire

def construire(ifc, facteur_longueur, elements, progres=None):
    """
    elements : liste de dicts {id, g, classe, niveau, nom}.
    Renvoie les octets à envoyer au navigateur :
      [longueur de l'en-tête (uint32)] [en-tête JSON] [bourrage] [positions float32 xyz] [n° d'élément float32] [indices uint32]
    """
    geo = Geometrie(ifc, facteur_longueur)
    positions, numeros, indices = array("f"), array("f"), array("I")
    infos, sans_geometrie = [], 0
    classes, niveaux = [], []
    total = len(elements)
    # locaux (transparents) à la fin, pour être dessinés après les éléments opaques
    elements = sorted(elements, key=lambda e: e["classe"] == "IfcSpace")
    boites = []
    for k, e in enumerate(elements):
        if progres and k % 500 == 0:
            progres("Géométrie 3D : %d / %d éléments…" % (k, total))
        try:
            maillage = geo.produit(e["id"])
        except Exception:
            maillage = None
        if not maillage:
            sans_geometrie += 1
            continue
        pts, tris = maillage
        n = len(infos)
        base = len(positions) // 3
        f = facteur_longueur
        mn = [float("inf")] * 3
        mx = [float("-inf")] * 3
        for p in pts:
            x, y, z = p[0] * f, p[1] * f, p[2] * f
            positions.append(x); positions.append(y); positions.append(z)
            if x < mn[0]: mn[0] = x
            if y < mn[1]: mn[1] = y
            if z < mn[2]: mn[2] = z
            if x > mx[0]: mx[0] = x
            if y > mx[1]: mx[1] = y
            if z > mx[2]: mx[2] = z
        # +0,25 : objets posés dans l'épaisseur d'un mur (baies, garde-corps…) — la visionneuse les fait
        # passer légèrement devant pour éviter le scintillement des faces confondues
        decalage = 0.25 if e["classe"] in ("IfcWindow", "IfcDoor", "IfcPlate", "IfcRailing", "IfcMember") else 0.0
        numeros.extend([float(n) + decalage] * len(pts))
        for i, j, l in tris:
            indices.append(base + i); indices.append(base + j); indices.append(base + l)
        if e["classe"] not in classes:
            classes.append(e["classe"])
        if e["niveau"] not in niveaux:
            niveaux.append(e["niveau"])
        infos.append([e["id"], e["g"], classes.index(e["classe"]), niveaux.index(e["niveau"]), e["nom"]])
        boites.extend(mn + mx)

    # recentrage (coordonnées géoréférencées : évite les tremblements en simple précision)
    if boites:
        cx = sorted((boites[6 * i] + boites[6 * i + 3]) / 2 for i in range(len(infos)))[len(infos) // 2]
        cy = sorted((boites[6 * i + 1] + boites[6 * i + 4]) / 2 for i in range(len(infos)))[len(infos) // 2]
        cz = min(boites[6 * i + 2] for i in range(len(infos)))
        for i in range(0, len(positions), 3):
            positions[i] -= cx
            positions[i + 1] -= cy
            positions[i + 2] -= cz
        for i in range(0, len(boites), 3):
            boites[i] -= cx
            boites[i + 1] -= cy
            boites[i + 2] -= cz
    niveaux_tries = sorted(niveaux, key=lambda nv: min((boites[6 * i + 2] for i, inf in enumerate(infos)
                                                        if niveaux[inf[3]] == nv), default=0))
    entete = {"elements": infos, "boites": [round(b, 3) for b in boites], "classes": classes, "niveaux": niveaux,
              "ordre_niveaux": [niveaux.index(nv) for nv in niveaux_tries],
              "nb_sommets": len(positions) // 3, "nb_indices": len(indices), "sans_geometrie": sans_geometrie,
              "premier_local": next((i for i, inf in enumerate(infos) if classes[inf[2]] == "IfcSpace"), len(infos))}
    brut = json.dumps(entete, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    brut += b" " * ((4 - len(brut) % 4) % 4)
    if progres:
        progres("Géométrie 3D prête : %d éléments, %d triangles." % (len(infos), len(indices) // 3))
    return struct.pack("<I", len(brut)) + brut + positions.tobytes() + numeros.tobytes() + indices.tobytes()
