/*
 * Visionneuse du POC Chiffrage IA — aucune dépendance.
 *  - Maquette IFC : rendu 3D WebGL2 (géométrie calculée par le serveur).
 *  - Plan DXF/DWG : dessin 2D en SVG.
 * API : Visionneuse.init(conteneur, { surChoix }) ; .chargerIFC(arrayBuffer) ; .chargerPlan(json)
 *       .afficherChoix({ selection, focus }, cadrer)  — parties = { groupes: [...], unites: [...], sauf: [...] }
 *       .selectionnerGroupes(["G01", ...]) ; .effacerSelection() ; .colorier({ groupes, unites } | null)
 *       .unitesOu({ groupes, niveau }) : objets d'un ou plusieurs groupes (sur un niveau donné)
 * surChoix(info) est appelé quand l'utilisateur clique un objet (info = null : clic dans le vide).
 * info.unite : identifiant de l'objet cliqué (chiffrable seul) ; info.ajout : touche Maj, ⌘ ou Ctrl enfoncée.
 * La sélection (orange) et la ligne consultée (bleu) sont décidées par la page, pas par la visionneuse.
 */
(function () {
  "use strict";

  const ORANGE = [0.93, 0.47, 0.12];
  const BLEU = [0.16, 0.5, 0.92];
  // Contrôle du chiffrage : couleur de chaque objet selon son état
  const ETATS = { ia: [0.36, 0.7, 0.45], manuel: [0.58, 0.42, 0.86], non: [0.9, 0.3, 0.24], exclu: [0.66, 0.66, 0.66] };
  const LIBELLES_ETATS = { ia: "chiffré (IA)", manuel: "chiffré à la main", non: "non chiffré", exclu: "hors chiffrage" };
  const COULEURS = {
    IfcWall: [0.86, 0.85, 0.81], IfcCurtainWall: [0.62, 0.78, 0.9], IfcSlab: [0.72, 0.72, 0.7], IfcRoof: [0.66, 0.6, 0.56],
    IfcColumn: [0.6, 0.62, 0.66], IfcBeam: [0.6, 0.62, 0.66], IfcFooting: [0.55, 0.55, 0.53], IfcPile: [0.55, 0.55, 0.53],
    IfcWindow: [0.6, 0.78, 0.92], IfcDoor: [0.74, 0.56, 0.38], IfcCovering: [0.82, 0.79, 0.7], IfcRailing: [0.52, 0.6, 0.64],
    IfcStair: [0.7, 0.68, 0.64], IfcStairFlight: [0.7, 0.68, 0.64], IfcRamp: [0.7, 0.68, 0.64], IfcPlate: [0.62, 0.78, 0.9],
    IfcMember: [0.55, 0.58, 0.6], IfcSanitaryTerminal: [0.97, 0.97, 0.99], IfcFlowTerminal: [0.93, 0.93, 0.96],
    IfcBuildingElementProxy: [0.76, 0.72, 0.82], IfcSpace: [0.33, 0.55, 0.88],
  };
  const NOMS_CLASSES = {
    IfcWall: "Mur", IfcCurtainWall: "Mur-rideau", IfcSlab: "Dalle", IfcRoof: "Toiture", IfcColumn: "Poteau", IfcBeam: "Poutre",
    IfcFooting: "Fondation", IfcPile: "Pieu", IfcWindow: "Fenêtre", IfcDoor: "Porte", IfcCovering: "Revêtement",
    IfcRailing: "Garde-corps", IfcStair: "Escalier", IfcStairFlight: "Volée d'escalier", IfcRamp: "Rampe", IfcPlate: "Plaque",
    IfcMember: "Montant", IfcSanitaryTerminal: "Appareil sanitaire", IfcFlowTerminal: "Équipement terminal",
    IfcBuildingElementProxy: "Élément générique", IfcSpace: "Local",
  };

  const V = {};
  let racine, barre, scene, surChoix = () => {};
  let mode = null; // "ifc" | "dxf"

  function el(tag, attrs = {}, texte) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (texte !== undefined) e.textContent = texte;
    return e;
  }

  V.init = function (conteneur, options = {}) {
    racine = conteneur;
    surChoix = options.surChoix || surChoix;
    racine.innerHTML = "";
    barre = el("div", { class: "v-barre" });
    scene = el("div", { class: "v-scene" });
    racine.append(barre, scene);
    V.message("Chargez une maquette ou un plan pour l'afficher ici.");
  };

  V.message = function (texte) {
    V.detruire();
    barre.innerHTML = "";
    scene.innerHTML = "";
    scene.append(el("div", { class: "v-message" }, texte));
  };

  V.detruire = function () {
    if (ifc.observateur) ifc.observateur.disconnect();
    if (ifc.gl) { const ext = ifc.gl.getExtension("WEBGL_lose_context"); if (ext) ext.loseContext(); }
    Object.keys(ifc).forEach((k) => delete ifc[k]);
    Object.keys(plan).forEach((k) => delete plan[k]);
    mode = null;
  };

  // =========================================================================
  // 3D — maquette IFC
  // =========================================================================
  const ifc = {};

  const VS = `#version 300 es
precision highp float; precision highp int;
layout(location=0) in vec3 a_pos;
layout(location=1) in float a_elem;
uniform mat4 u_vp;
uniform highp sampler2D u_etat;
out vec4 v_col; out vec3 v_pos; flat out int v_elem;
void main() {
  int e = int(floor(a_elem + 0.01));
  bool devant = fract(a_elem + 0.01) > 0.1;  // baies et garde-corps : légèrement devant les murs
  vec4 s = texelFetch(u_etat, ivec2(e % 2048, e / 2048), 0);
  v_col = s; v_pos = a_pos; v_elem = e;
  gl_Position = s.a < 0.004 ? vec4(2.0, 2.0, 2.0, 1.0) : u_vp * vec4(a_pos, 1.0);
  if (devant) gl_Position.z -= 0.0006 * gl_Position.w;
}`;
  const FS = `#version 300 es
precision highp float; precision highp int;
in vec4 v_col; in vec3 v_pos; flat in int v_elem;
uniform int u_mode; uniform vec3 u_lum;
out vec4 sortie;
void main() {
  if (u_mode == 2) {
    if (v_col.a < 0.1) discard;  // les objets estompés restent cliquables (sélection multiple)
    int id = v_elem + 1;
    sortie = vec4(float(id & 255) / 255.0, float((id >> 8) & 255) / 255.0, float((id >> 16) & 255) / 255.0, 1.0);
    return;
  }
  bool opaque = v_col.a > 0.99;
  if ((u_mode == 0) != opaque) discard;
  vec3 n = normalize(cross(dFdx(v_pos), dFdy(v_pos)));
  float l = 0.5 + 0.5 * abs(dot(n, u_lum));
  sortie = vec4(v_col.rgb * l, v_col.a);
}`;

  function compiler(gl, type, source) {
    const s = gl.createShader(type);
    gl.shaderSource(s, source);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }

  V.chargerIFC = function (tampon) {
    V.detruire();
    mode = "ifc";
    const dv = new DataView(tampon);
    const lg = dv.getUint32(0, true);
    const entete = JSON.parse(new TextDecoder().decode(new Uint8Array(tampon, 4, lg)));
    let o = 4 + lg;
    const pos = new Float32Array(tampon, o, entete.nb_sommets * 3); o += pos.byteLength;
    const num = new Float32Array(tampon, o, entete.nb_sommets); o += num.byteLength;
    const idx = new Uint32Array(tampon, o, entete.nb_indices);
    Object.assign(ifc, { entete, n: entete.elements.length, selection: new Set(), focus: new Set(), niveau: -1, locaux: false,
      controle: false, etats: null });

    // index groupe -> éléments, identifiant d'objet -> élément
    ifc.parGroupe = {};
    ifc.parUnite = new Map();
    entete.elements.forEach((e, i) => { (ifc.parGroupe[e[1]] ||= []).push(i); ifc.parUnite.set(String(e[0]), i); });

    scene.innerHTML = "";
    const canvas = el("canvas", { class: "v-canvas" });
    scene.append(canvas);
    const gl = canvas.getContext("webgl2", { antialias: true, alpha: false });  // opaque : la page ne « délave » pas les transparences
    if (!gl) { V.message("Ce navigateur ne permet pas l'affichage 3D (WebGL 2 indisponible)."); return; }
    ifc.gl = gl; ifc.canvas = canvas;
    const prog = gl.createProgram();
    gl.attachShader(prog, compiler(gl, gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compiler(gl, gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    ifc.prog = prog;
    ifc.u = { vp: gl.getUniformLocation(prog, "u_vp"), etat: gl.getUniformLocation(prog, "u_etat"),
      mode: gl.getUniformLocation(prog, "u_mode"), lum: gl.getUniformLocation(prog, "u_lum") };

    ifc.vao = gl.createVertexArray();
    gl.bindVertexArray(ifc.vao);
    const b1 = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b1); gl.bufferData(gl.ARRAY_BUFFER, pos, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
    const b2 = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b2); gl.bufferData(gl.ARRAY_BUFFER, num, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 1, gl.FLOAT, false, 0, 0);
    const b3 = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, b3); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);
    ifc.nbIndices = idx.length;

    ifc.hTex = Math.max(1, Math.ceil(ifc.n / 2048));
    ifc.etat = new Uint8Array(2048 * ifc.hTex * 4);
    ifc.tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, ifc.tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 2048, ifc.hTex, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);

    // Caméra en orbite (Z vers le haut)
    ifc.cam = { cible: [0, 0, 0], dist: 30, azim: -2.2, elev: 0.55 };
    construireBarreIFC();
    appliquerControle();  // colore selon le chiffrage si le contrôle était actif (appelle majEtat)
    brancherSouris(canvas);
    ifc.observateur = new ResizeObserver(() => { dimensionner(); dessiner(); });
    ifc.observateur.observe(scene);
    dimensionner();
    cadrer(null);
  };

  function construireBarreIFC() {
    barre.innerHTML = "";
    const e = ifc.entete;
    const sel = el("select", { title: "Afficher un seul niveau" });
    sel.append(el("option", { value: "-1" }, "Tous les niveaux"));
    for (const k of e.ordre_niveaux) sel.append(el("option", { value: String(k) }, e.niveaux[k]));
    sel.onchange = () => { ifc.niveau = +sel.value; majEtat(); cadrer(ifc.selection.size ? [...ifc.selection] : null); };
    ifc.selNiveau = sel;
    const locaux = el("label", { class: "v-case" });
    const cb = el("input", { type: "checkbox" });
    cb.onchange = () => { ifc.locaux = cb.checked; majEtat(); };
    ifc.caseLocaux = cb;
    locaux.append(cb, document.createTextNode(" Locaux"));
    const dalles = el("label", { class: "v-case", title: "Afficher les dalles et toitures (masquées en vue de dessus pour voir le plan)" });
    const cbD = el("input", { type: "checkbox" });
    cbD.checked = true;
    cbD.onchange = () => { ifc.sansDalles = !cbD.checked; majEtat(); };
    ifc.caseDalles = cbD;
    dalles.append(cbD, document.createTextNode(" Dalles"));
    const b3d = el("button", { type: "button", title: "Vue en perspective" }, "3D");
    b3d.onclick = () => {
      ifc.cam.elev = 0.55; ifc.cam.azim = -2.2;
      ifc.sansDalles = false; cbD.checked = true; majEtat();
      cadrer(ifc.selection.size ? [...ifc.selection] : null);
    };
    const bDessus = el("button", { type: "button", title: "Vue de dessus (plan)" }, "Dessus");
    bDessus.onclick = () => {
      // vue en plan : on retire les dalles et toitures qui masqueraient le niveau
      ifc.cam.elev = 1.5697; ifc.cam.azim = -Math.PI / 2;
      ifc.sansDalles = true; cbD.checked = false; majEtat();
      cadrer(ifc.selection.size ? [...ifc.selection] : null);
    };
    const bTout = el("button", { type: "button", title: "Tout afficher" }, "Tout voir");
    bTout.onclick = () => { V.effacerSelection(); surChoix(null); cadrer(null); };
    barre.append(sel, locaux, dalles, b3d, bDessus, bTout, caseControle());
    if (e.sans_geometrie) barre.append(el("span", { class: "v-note", title: "Éléments dont la forme n'a pas pu être lue" }, e.sans_geometrie + " sans forme"));
    barre.append(el("span", { class: "v-aide" }, "Glisser : tourner · Maj+glisser : déplacer · Molette : zoom · Maj+clic : ajouter à la sélection"));
  }

  // Case « Contrôle » : colore chaque objet selon son état de chiffrage (vert, violet, rouge, gris)
  function caseControle() {
    const lab = el("label", { class: "v-case v-controle", title: "Colorer les objets selon leur chiffrage : repérez d'un coup d'œil ce qui n'est pas chiffré" });
    const cb = el("input", { type: "checkbox" });
    cb.disabled = !etatsCourants;
    cb.checked = controleActif && !!etatsCourants;
    cb.onchange = () => { controleActif = cb.checked; appliquerControle(); };
    lab.append(cb, document.createTextNode(" Contrôle"));
    const legende = el("span", { class: "v-legende" });
    for (const [k, c] of Object.entries(ETATS)) {
      const p = el("span", { class: "v-pastille" });
      p.style.background = `rgb(${c.map((x) => Math.round(x * 255)).join(",")})`;
      legende.append(p, document.createTextNode(LIBELLES_ETATS[k] + " "));
    }
    const boite = el("span", { class: "v-bloc-controle" });
    boite.append(lab, legende);
    return boite;
  }
  let etatsCourants = null, controleActif = false;

  function appliquerControle() {
    const actif = controleActif && !!etatsCourants;
    if (racine) racine.classList.toggle("v-en-controle", actif);
    if (mode === "ifc") {
      ifc.controle = actif;
      if (actif) {
        ifc.etats = ifc.entete.elements.map((inf) => etatsCourants.unites[String(inf[0])] || etatsCourants.groupes[inf[1]] || null);
      }
      majEtat();
    } else if (mode === "dxf") {
      plan.chemins.forEach((c, i) => {
        c.classList.remove("e-ia", "e-manuel", "e-non", "e-exclu");
        if (!actif) return;
        const e = (plan.uDe[i] && etatsCourants.unites[plan.uDe[i]]) || (plan.gDe[i] && etatsCourants.groupes[plan.gDe[i]]);
        if (e) c.classList.add("e-" + e);
      });
    }
  }

  V.colorier = function (etats) {
    etatsCourants = etats;
    const cb = barre && barre.querySelector(".v-controle input");
    if (cb) { cb.disabled = !etats; cb.checked = controleActif && !!etats; }
    if (mode) appliquerControle();
  };

  function majEtat() {
    const e = ifc.entete, et = ifc.etat, sel = ifc.selection, foc = ifc.focus, avecSel = sel.size > 0 || foc.size > 0;
    const controle = ifc.controle && ifc.etats;
    const idxLocal = e.classes.indexOf("IfcSpace");
    const dalles = new Set(["IfcSlab", "IfcRoof", "IfcCovering"].map((c) => e.classes.indexOf(c)).filter((k) => k >= 0));
    for (let i = 0; i < ifc.n; i++) {
      const inf = e.elements[i];
      const estLocal = inf[2] === idxLocal;
      const coul = COULEURS[e.classes[inf[2]]] || [0.8, 0.8, 0.8];
      let r = coul[0], g = coul[1], b = coul[2], a = 1;
      const bonNiveau = ifc.niveau < 0 || inf[3] === ifc.niveau;
      if (!bonNiveau) a = 0;
      else if (ifc.sansDalles && dalles.has(inf[2]) && !sel.has(i)) a = 0;
      else if (sel.has(i)) { [r, g, b] = ORANGE; a = estLocal ? 0.62 : 1; }
      else if (foc.has(i)) { [r, g, b] = BLEU; a = estLocal ? 0.55 : 1; }
      else if (controle && ifc.etats[i]) {
        [r, g, b] = ETATS[ifc.etats[i]];
        a = estLocal ? (ifc.locaux && !avecSel ? 0.4 : 0) : (ifc.etats[i] === "exclu" ? (avecSel ? 0.12 : 0.3) : (avecSel ? 0.16 : 1));
      }
      else if (estLocal) a = ifc.locaux && !avecSel ? 0.35 : 0;
      else if (avecSel) { r = g = b = 0.62; a = 0.13; }
      const o = i * 4;
      et[o] = r * 255; et[o + 1] = g * 255; et[o + 2] = b * 255; et[o + 3] = Math.round(a * 255);
    }
    const gl = ifc.gl;
    gl.bindTexture(gl.TEXTURE_2D, ifc.tex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 2048, ifc.hTex, gl.RGBA, gl.UNSIGNED_BYTE, et);
    dessiner();
  }

  function dimensionner() {
    const c = ifc.canvas, dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(1, Math.round(scene.clientWidth * dpr)), h = Math.max(1, Math.round(scene.clientHeight * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; ifc.fbo = null; }
  }

  // --- matrices (colonnes)
  function perspective(fov, asp, pres, loin) {
    const f = 1 / Math.tan(fov / 2), nf = 1 / (pres - loin);
    return [f / asp, 0, 0, 0, 0, f, 0, 0, 0, 0, (loin + pres) * nf, -1, 0, 0, 2 * loin * pres * nf, 0];
  }
  function regard(oeil, cible, haut) {
    let z = [oeil[0] - cible[0], oeil[1] - cible[1], oeil[2] - cible[2]]; z = norm(z);
    let x = norm(cross(haut, z)); const y = cross(z, x);
    return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, oeil), -dot(y, oeil), -dot(z, oeil), 1];
  }
  function mul(a, b) {
    const r = new Array(16).fill(0);
    for (let c = 0; c < 4; c++) for (let l = 0; l < 4; l++) for (let k = 0; k < 4; k++) r[c * 4 + l] += a[k * 4 + l] * b[c * 4 + k];
    return r;
  }
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const norm = (v) => { const n = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / n, v[1] / n, v[2] / n]; };

  function oeil() {
    const c = ifc.cam;
    return [c.cible[0] + c.dist * Math.cos(c.elev) * Math.cos(c.azim), c.cible[1] + c.dist * Math.cos(c.elev) * Math.sin(c.azim),
      c.cible[2] + c.dist * Math.sin(c.elev)];
  }

  function matriceVue() {
    const c = ifc.cam, o = oeil();
    const haut = c.elev > 1.5 ? [-Math.cos(c.azim), -Math.sin(c.azim), 0] : [0, 0, 1];
    return mul(perspective(0.8, ifc.canvas.width / ifc.canvas.height, c.dist / 200, c.dist * 20 + 100), regard(o, c.cible, haut));
  }

  let demande = 0;
  function dessiner() {
    if (!ifc.gl || demande) return;
    demande = requestAnimationFrame(() => { demande = 0; rendu(null); });
  }

  function rendu(cibleFbo) {
    const gl = ifc.gl;
    gl.bindFramebuffer(gl.FRAMEBUFFER, cibleFbo);
    gl.viewport(0, 0, ifc.canvas.width, ifc.canvas.height);
    const fond = cibleFbo ? [0, 0, 0, 0] : (matchMedia("(prefers-color-scheme: dark)").matches ? [0.1, 0.12, 0.15, 1] : [0.96, 0.95, 0.93, 1]);
    gl.clearColor(...fond);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.useProgram(ifc.prog);
    gl.bindVertexArray(ifc.vao);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, ifc.tex); gl.uniform1i(ifc.u.etat, 0);
    gl.uniformMatrix4fv(ifc.u.vp, false, new Float32Array(matriceVue()));
    const o = oeil(), c = ifc.cam.cible;
    gl.uniform3fv(ifc.u.lum, norm([o[0] - c[0] + 0.3 * ifc.cam.dist, o[1] - c[1] + 0.2 * ifc.cam.dist, o[2] - c[2] + 0.6 * ifc.cam.dist]));
    if (cibleFbo) {
      gl.disable(gl.BLEND); gl.depthMask(true); gl.uniform1i(ifc.u.mode, 2);
      gl.drawElements(gl.TRIANGLES, ifc.nbIndices, gl.UNSIGNED_INT, 0);
    } else {
      gl.disable(gl.BLEND); gl.depthMask(true); gl.uniform1i(ifc.u.mode, 0);
      gl.drawElements(gl.TRIANGLES, ifc.nbIndices, gl.UNSIGNED_INT, 0);
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false); gl.uniform1i(ifc.u.mode, 1);
      gl.drawElements(gl.TRIANGLES, ifc.nbIndices, gl.UNSIGNED_INT, 0);
      gl.depthMask(true);
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  function prelever(x, y) {
    const gl = ifc.gl, w = ifc.canvas.width, h = ifc.canvas.height;
    if (!ifc.fbo) {
      ifc.fbo = gl.createFramebuffer();
      const t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      const p = gl.createRenderbuffer();
      gl.bindRenderbuffer(gl.RENDERBUFFER, p);
      gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, w, h);
      gl.bindFramebuffer(gl.FRAMEBUFFER, ifc.fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
      gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, p);
    }
    rendu(ifc.fbo);
    const px = new Uint8Array(4);
    gl.bindFramebuffer(gl.FRAMEBUFFER, ifc.fbo);
    gl.readPixels(Math.round(x), Math.round(h - y), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    const id = px[0] + (px[1] << 8) + (px[2] << 16);
    return id > 0 ? id - 1 : -1;
  }

  function brancherSouris(canvas) {
    let depart = null;
    canvas.addEventListener("contextmenu", (e) => e.preventDefault());
    canvas.addEventListener("pointerdown", (e) => {
      depart = { x: e.clientX, y: e.clientY, bouge: false, pan: e.button !== 0 || e.shiftKey };
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!depart) return;
      const dx = e.clientX - (depart.dx ?? depart.x), dy = e.clientY - (depart.dy ?? depart.y);
      depart.dx = e.clientX; depart.dy = e.clientY;
      if (Math.abs(e.clientX - depart.x) + Math.abs(e.clientY - depart.y) > 4) depart.bouge = true;
      const c = ifc.cam;
      if (depart.pan) {
        const o = oeil(), av = norm([c.cible[0] - o[0], c.cible[1] - o[1], c.cible[2] - o[2]]);
        const droite = norm(cross(av, [0, 0, 1])), haut = cross(droite, av);
        const k = c.dist * 0.0016;
        for (let i = 0; i < 3; i++) c.cible[i] += (-dx * droite[i] + dy * haut[i]) * k;
      } else {
        c.azim -= dx * 0.008;
        c.elev = Math.max(-1.45, Math.min(1.5697, c.elev + dy * 0.008));
      }
      dessiner();
    });
    canvas.addEventListener("pointerup", (e) => {
      if (depart && !depart.bouge) {
        const r = canvas.getBoundingClientRect(), dpr = canvas.width / r.width;
        const i = prelever((e.clientX - r.left) * dpr, (e.clientY - r.top) * dpr);
        choisirElement(i, e.shiftKey || e.metaKey || e.ctrlKey);
      }
      depart = null;
    });
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      ifc.cam.dist = Math.max(0.3, Math.min(1e5, ifc.cam.dist * Math.exp(e.deltaY * (e.ctrlKey ? 0.01 : 0.0015))));
      dessiner();
    }, { passive: false });
    // Safari (Mac) : le pincement du pavé tactile arrive sous forme de « gesture »
    let distDepart = null;
    canvas.addEventListener("gesturestart", (e) => { e.preventDefault(); distDepart = ifc.cam.dist; });
    canvas.addEventListener("gesturechange", (e) => {
      e.preventDefault();
      if (distDepart) { ifc.cam.dist = Math.max(0.3, Math.min(1e5, distDepart / e.scale)); dessiner(); }
    });
    canvas.addEventListener("gestureend", (e) => { e.preventDefault(); distDepart = null; });
  }

  function choisirElement(i, ajout) {
    if (i < 0) { surChoix(ajout ? { vide: true, ajout } : null); return; }
    const e = ifc.entete, inf = e.elements[i];
    const classe = e.classes[inf[2]];
    surChoix({
      source: "ifc", groupe: inf[1], unite: String(inf[0]), ajout, niveau: e.niveaux[inf[3]],
      titre: inf[4] || NOMS_CLASSES[classe] || classe,
      details: [["Catégorie", (NOMS_CLASSES[classe] || classe) + " (" + classe + ")"], ["Niveau", e.niveaux[inf[3]]], ["Identifiant IFC", "#" + inf[0]]],
      nbGroupe: (ifc.parGroupe[inf[1]] || []).length,
    });
  }

  function cadrer(indices) {
    const e = ifc.entete, bx = e.boites;
    let mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
    const liste = indices || [...Array(ifc.n).keys()].filter((i) => ifc.etat[i * 4 + 3] > 40);
    for (const i of liste) for (let k = 0; k < 3; k++) { mn[k] = Math.min(mn[k], bx[6 * i + k]); mx[k] = Math.max(mx[k], bx[6 * i + 3 + k]); }
    if (!isFinite(mn[0])) return dessiner();
    const c = ifc.cam;
    c.cible = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
    const rayon = Math.max(0.5, Math.hypot(mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]) / 2);
    const asp = ifc.canvas.width / ifc.canvas.height;
    c.dist = rayon / Math.sin(0.4) * (asp < 1 ? 1.25 / asp : 1.1);
    dessiner();
  }

  // =========================================================================
  // 2D — plan DXF / DWG
  // =========================================================================
  const plan = {};
  const SVG = "http://www.w3.org/2000/svg";

  V.chargerPlan = function (d) {
    V.detruire();
    mode = "dxf";
    Object.assign(plan, { d, selection: new Set() });
    barre.innerHTML = "";
    const bTout = el("button", { type: "button" }, "Tout voir");
    bTout.onclick = () => { V.effacerSelection(); surChoix(null); cadrerPlan(null); };
    barre.append(bTout, caseControle(), el("span", { class: "v-aide" }, "Glisser : déplacer · Molette : zoom · Clic : objet, local ou calque · Maj+clic : ajouter"));
    if (d.tronque) barre.append(el("span", { class: "v-note" }, "plan très lourd : affichage partiel"));

    scene.innerHTML = "";
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "v-svg");
    const g = document.createElementNS(SVG, "g");
    g.setAttribute("transform", "scale(1,-1)");
    const gt = document.createElementNS(SVG, "g");
    const locaux = d.locaux || {};
    plan.chemins = d.prims.map((p, i) => {
      const c = p[3];
      let s = "M" + c[0] + " " + c[1];
      for (let k = 2; k < c.length; k += 2) s += "L" + c[k] + " " + c[k + 1];
      if (p[4]) s += "Z";
      const path = document.createElementNS(SVG, "path");
      path.setAttribute("d", s);
      path.setAttribute("class", "prim" + (locaux[i] ? " local" : "") + (p[1] >= 0 ? " bloc" : ""));
      g.append(path);
      return path;
    });
    for (const [x, y, h, rot, t] of d.textes) {
      const tx = document.createElementNS(SVG, "text");
      tx.setAttribute("x", x); tx.setAttribute("y", -y); tx.setAttribute("font-size", h);
      if (rot) tx.setAttribute("transform", `rotate(${-rot} ${x} ${-y})`);
      tx.textContent = t;
      gt.append(tx);
    }
    svg.append(g, gt);
    scene.append(svg);
    plan.svg = svg; plan.g = g;
    plan.focus = new Set();
    // pour chaque trait : l'objet chiffrable (bloc inséré, local) et le groupe du métré auxquels il appartient
    const gCalque = {}, gBloc = {};
    for (const [gid, c] of Object.entries(d.groupes)) {
      if (c.calque !== undefined) gCalque[c.calque] = gid;
      if (c.bloc !== undefined) gBloc[c.bloc] = gid;
    }
    plan.uDe = d.prims.map((p, i) => (p[1] >= 0 ? "i" + p[1] : locaux[i] ? "l" + i : null));
    plan.gDe = d.prims.map((p, i) => locaux[i] ? locaux[i][2] || null
      : p[1] >= 0 ? (p[2].map((b) => gBloc[b]).find(Boolean) || null) : (gCalque[p[0]] || null));
    plan.parUnite = new Map();
    plan.uDe.forEach((u, i) => { if (u) { if (!plan.parUnite.has(u)) plan.parUnite.set(u, []); plan.parUnite.get(u).push(i); } });
    brancherPlan(svg);
    cadrerPlan(null);
    appliquerControle();
  };

  function cadrerPlan(indices) {
    let x0, y0, x1, y1;
    if (indices && indices.length) {
      x0 = y0 = Infinity; x1 = y1 = -Infinity;
      for (const i of indices) {
        const c = plan.d.prims[i][3];
        for (let k = 0; k < c.length; k += 2) { x0 = Math.min(x0, c[k]); x1 = Math.max(x1, c[k]); y0 = Math.min(y0, c[k + 1]); y1 = Math.max(y1, c[k + 1]); }
      }
      const m = Math.max(x1 - x0, y1 - y0) * 0.35 + 1e-6;
      x0 -= m; y0 -= m; x1 += m; y1 += m;
    } else [x0, y0, x1, y1] = plan.d.bornes;
    plan.vue = [x0, -y1, x1 - x0, y1 - y0];
    appliquerVue();
  }

  function appliquerVue() { plan.svg.setAttribute("viewBox", plan.vue.join(" ")); }

  function versMonde(e) {
    const pt = plan.svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const m = plan.g.getScreenCTM().inverse();
    const r = pt.matrixTransform(m);
    return [r.x, r.y];
  }

  function brancherPlan(svg) {
    let depart = null;
    svg.addEventListener("pointerdown", (e) => { depart = { x: e.clientX, y: e.clientY, vue: [...plan.vue], bouge: false }; svg.setPointerCapture(e.pointerId); });
    svg.addEventListener("pointermove", (e) => {
      if (!depart) return;
      const dx = e.clientX - depart.x, dy = e.clientY - depart.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) depart.bouge = true;
      const k = plan.vue[2] / svg.clientWidth;
      plan.vue[0] = depart.vue[0] - dx * k; plan.vue[1] = depart.vue[1] - dy * k;
      appliquerVue();
    });
    svg.addEventListener("pointerup", (e) => { if (depart && !depart.bouge) cliquerPlan(e, e.shiftKey || e.metaKey || e.ctrlKey); depart = null; });
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = svg.getBoundingClientRect();
      const f = Math.exp(e.deltaY * (e.ctrlKey ? 0.01 : 0.0015));
      const vx = plan.vue[0] + (e.clientX - r.left) / r.width * plan.vue[2];
      const vy = plan.vue[1] + (e.clientY - r.top) / r.height * plan.vue[3];
      plan.vue = [vx - (vx - plan.vue[0]) * f, vy - (vy - plan.vue[1]) * f, plan.vue[2] * f, plan.vue[3] * f];
      appliquerVue();
    }, { passive: false });
    let vueDepart = null;
    svg.addEventListener("gesturestart", (e) => { e.preventDefault(); vueDepart = [...plan.vue]; });
    svg.addEventListener("gesturechange", (e) => {
      e.preventDefault();
      if (!vueDepart) return;
      const f = 1 / e.scale, cx = vueDepart[0] + vueDepart[2] / 2, cy = vueDepart[1] + vueDepart[3] / 2;
      plan.vue = [cx - vueDepart[2] * f / 2, cy - vueDepart[3] * f / 2, vueDepart[2] * f, vueDepart[3] * f];
      appliquerVue();
    });
    svg.addEventListener("gestureend", (e) => { e.preventDefault(); vueDepart = null; });
  }

  function dansPolygone(x, y, c) {
    let dedans = false;
    for (let i = 0, j = c.length - 2; i < c.length; j = i, i += 2) {
      const xi = c[i], yi = c[i + 1], xj = c[j], yj = c[j + 1];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / ((yj - yi) || 1e-12) + xi) dedans = !dedans;
    }
    return dedans;
  }

  function distanceSegment(x, y, c, ferme) {
    let d = Infinity;
    const n = c.length;
    for (let k = 0; k + 2 < n + (ferme ? 2 : 0); k += 2) {
      const x1 = c[k], y1 = c[k + 1], x2 = c[(k + 2) % n], y2 = c[(k + 3) % n];
      const dx = x2 - x1, dy = y2 - y1, l2 = dx * dx + dy * dy;
      const t = l2 ? Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / l2)) : 0;
      d = Math.min(d, Math.hypot(x - x1 - t * dx, y - y1 - t * dy));
    }
    return d;
  }

  function groupeDe(test) {
    for (const [gid, c] of Object.entries(plan.d.groupes)) if (test(c)) return gid;
    return null;
  }

  function cliquerPlan(e, ajout) {
    const [x, y] = versMonde(e);
    const d = plan.d, tol = 7 * plan.vue[2] / plan.svg.clientWidth;
    // 1. un bloc (porte, fenêtre, équipement) sous le curseur ?
    let meilleur = null;
    for (const [num, ib, x0, y0, x1, y1] of d.emprises) {
      if (x >= x0 - tol && x <= x1 + tol && y >= y0 - tol && y <= y1 + tol) {
        const aire = (x1 - x0) * (y1 - y0);
        if (!meilleur || aire < meilleur.aire) meilleur = { num, ib, aire };
      }
    }
    if (meilleur && meilleur.aire < (d.bornes[2] - d.bornes[0]) * (d.bornes[3] - d.bornes[1]) * 0.05) {
      const prims = plan.parUnite.get("i" + meilleur.num) || [];
      const gid = groupeDe((c) => c.bloc === meilleur.ib);
      surChoix({ source: "dxf", groupe: gid, unite: "i" + meilleur.num, ajout, titre: "Bloc « " + d.blocs[meilleur.ib] + " »",
        details: [["Nature", "Bloc (objet inséré)"], ["Calque", prims.length ? d.calques[d.prims[prims[0]][0]] || "?" : "?"]],
        nbGroupe: null });
      return;
    }
    // 2. un trait de l'espace objet (mur, cloison…) ?
    let proche = null;
    d.prims.forEach((p, i) => {
      if (p[1] >= 0) return;
      const dist = distanceSegment(x, y, p[3], p[4]);
      if (dist < tol && (!proche || dist < proche.dist)) proche = { i, dist };
    });
    const locaux = d.locaux || {};
    if (proche && !locaux[proche.i]) {
      const k = d.prims[proche.i][0];
      const gid = groupeDe((c) => c.calque === k);
      surChoix({ source: "dxf", groupe: gid, unite: null, ajout, titre: "Calque « " + d.calques[k] + " »",
        details: [["Nature", "Traits du calque (le calque se chiffre en entier)"]], nbGroupe: null });
      return;
    }
    // 3. à l'intérieur d'un local ?
    let local = null;
    for (const [i, info] of Object.entries(locaux)) {
      const c = d.prims[+i][3];
      if (dansPolygone(x, y, c) && (!local || info[1] < local.info[1])) local = { i: +i, info };
    }
    if (local) {
      surChoix({ source: "dxf", groupe: local.info[2] || null, unite: local.info[2] ? "l" + local.i : null, ajout, titre: local.info[0],
        details: [["Nature", "Local"], ["Surface", local.info[1].toLocaleString("fr-FR", { minimumFractionDigits: 2 }) + " m²"]], nbGroupe: null });
      return;
    }
    surChoix(ajout ? { vide: true, ajout } : null);
  }

  function marquer(indices, focus = []) {
    for (const i of plan.selection) plan.chemins[i].classList.remove("sel");
    for (const i of plan.focus) plan.chemins[i].classList.remove("foc");
    plan.selection = new Set(indices);
    plan.focus = new Set(focus);
    for (const i of focus) plan.chemins[i].classList.add("foc");
    for (const i of indices) plan.chemins[i].classList.add("sel");
    plan.svg.classList.toggle("avec-selection", indices.length + focus.length > 0);
  }

  function primsDuGroupe(gid) {
    const c = plan.d.groupes[gid];
    if (!c) return [];
    if (c.prims) return c.prims;
    if (c.calque !== undefined) return plan.d.prims.map((p, i) => (p[0] === c.calque && p[1] < 0 ? i : -1)).filter((i) => i >= 0);
    if (c.bloc !== undefined) return plan.d.prims.map((p, i) => (p[2].includes(c.bloc) ? i : -1)).filter((i) => i >= 0);
    return [];
  }

  // =========================================================================
  // Commandes communes
  // =========================================================================
  // parties = { groupes: [gid…], unites: [uid…], sauf: [uid…] } : groupes entiers (moins « sauf ») + objets isolés
  function indicesDe(parties) {
    if (!parties) return [];
    const sauf = new Set(parties.sauf || []), res = new Set();
    if (mode === "ifc") {
      for (const g of parties.groupes || []) for (const i of ifc.parGroupe[g] || []) if (!sauf.has(String(ifc.entete.elements[i][0]))) res.add(i);
      for (const u of parties.unites || []) { const i = ifc.parUnite.get(String(u)); if (i !== undefined) res.add(i); }
    } else if (mode === "dxf") {
      for (const g of parties.groupes || []) for (const i of primsDuGroupe(g)) if (!sauf.has(plan.uDe[i])) res.add(i);
      for (const u of parties.unites || []) for (const i of plan.parUnite.get(u) || []) res.add(i);
    }
    return [...res];
  }

  function compter(indices) {
    if (mode !== "dxf") return indices.length;
    // un bloc inséré = 1 objet, quel que soit son nombre de traits
    const inserts = new Set();
    return indices.filter((i) => { const n = plan.d.prims[i][1]; if (n >= 0) { inserts.add(n); return false; } return true; }).length + inserts.size;
  }

  V.afficherChoix = function ({ selection = null, focus = null } = {}, cadrerSur = null) {
    const s = indicesDe(selection), f = indicesDe(focus);
    if (mode === "ifc") {
      ifc.selection = new Set(s); ifc.focus = new Set(f);
      const cible = cadrerSur === "focus" ? f : cadrerSur === "selection" ? s : [];
      if (cible.length && ifc.niveau >= 0 && !cible.some((i) => ifc.entete.elements[i][3] === ifc.niveau)) {
        ifc.niveau = -1; ifc.selNiveau.value = "-1";
      }
      majEtat();
      if (cible.length) cadrer(cible.filter((i) => ifc.niveau < 0 || ifc.entete.elements[i][3] === ifc.niveau));
    } else if (mode === "dxf") {
      marquer(s, f);
      const cible = cadrerSur === "focus" ? f : cadrerSur === "selection" ? s : [];
      if (cible.length) cadrerPlan(cible);
    }
    return { nbSelection: compter(s), nbFocus: compter(f) };
  };

  V.unitesOu = function ({ groupes = [], niveau = null } = {}) {
    const res = [];
    if (mode === "ifc") {
      const k = niveau === null ? -1 : ifc.entete.niveaux.indexOf(niveau);
      for (const g of groupes) for (const i of ifc.parGroupe[g] || []) {
        const inf = ifc.entete.elements[i];
        if (k < 0 || inf[3] === k) res.push(String(inf[0]));
      }
    } else if (mode === "dxf") {
      const vus = new Set();
      for (const g of groupes) for (const i of primsDuGroupe(g)) if (plan.uDe[i] && !vus.has(plan.uDe[i])) { vus.add(plan.uDe[i]); res.push(plan.uDe[i]); }
    }
    return res;
  };

  V.selectionnerGroupes = function (groupes) {
    if (mode === "ifc") {
      const liste = [];
      for (const g of groupes) liste.push(...(ifc.parGroupe[g] || []));
      ifc.selection = new Set(liste);
      const estLocal = liste.length && liste.every((i) => ifc.entete.classes[ifc.entete.elements[i][2]] === "IfcSpace");
      if (liste.length && ifc.niveau >= 0 && !liste.some((i) => ifc.entete.elements[i][3] === ifc.niveau)) {
        ifc.niveau = -1; ifc.selNiveau.value = "-1";
      }
      majEtat();
      if (liste.length) cadrer(liste.filter((i) => ifc.niveau < 0 || ifc.entete.elements[i][3] === ifc.niveau));
      return { nb: liste.length, locaux: estLocal };
    }
    if (mode === "dxf") {
      const liste = [];
      for (const g of groupes) liste.push(...primsDuGroupe(g));
      marquer(liste);
      if (liste.length) cadrerPlan(liste);
      // compte des objets : un bloc inséré = 1 objet, quel que soit son nombre de traits
      const inserts = new Set(), seuls = liste.filter((i) => { const n = plan.d.prims[i][1]; if (n >= 0) inserts.add(n); return n < 0; });
      return { nb: inserts.size + seuls.length };
    }
    return { nb: 0 };
  };

  V.effacerSelection = function () {
    if (mode === "ifc") { ifc.selection = new Set(); ifc.focus = new Set(); majEtat(); }
    if (mode === "dxf") marquer([]);
  };

  V.mode = () => mode;
  window.Visionneuse = V;
})();
