// =========================================================
// Lab notebook report — the look of every PDF lives here.
// Python (report.py) writes the numbers to a JSON file; this file only
// lays them out. Change a colour or a font here and every report
// follows. Built with:  typst compile --root Results ... report.typ
// =========================================================

// ---- brand: research = blue (carltabie gold is NOT used here) ----
#let blue   = rgb("#0071e3")   // headings, accents
#let blue-l = rgb("#e8f2fe")   // table header, boxes
#let ink    = rgb("#1a1a1a")   // main text
#let body-c = rgb("#4b5058")   // secondary text
#let muted  = rgb("#8a8f98")   // small print
#let rule-c = rgb("#e5e7eb")   // thin lines
#let warn   = rgb("#b42318")   // warnings
#let font   = "Inter"

#let data = json(sys.inputs.data)
#let S = data.settings

#set document(title: if data.single { data.entries.at(0).id + " — " + data.entries.at(0).title } else { "Lab notebook" },
              author: S.at("owner", default: ""))
#set text(font: font, size: 9.5pt, fill: ink)
#set par(justify: false, leading: 0.6em)
#set table(stroke: (x, y) => (bottom: 0.5pt + rule-c), inset: (x: 5pt, y: 4pt))
#show table.cell.where(y: 0): set text(weight: "semibold", fill: blue, size: 8.5pt)
#show heading.where(level: 2): it => block(above: 1.3em, below: 0.6em)[
  #text(size: 10.5pt, weight: "bold", fill: blue, upper(it.body))
  #v(-0.5em) #line(length: 100%, stroke: 0.6pt + blue)
]
#show heading.where(level: 3): it => block(above: 1em, below: 0.4em, text(size: 10pt, weight: "semibold", it.body))

#set page(
  paper: "a4",
  margin: (x: 18mm, top: 20mm, bottom: 18mm),
  header: context {
    let cur = here().page()
    let started = query(<entry-start>).filter(m => m.location().page() <= cur)
    let id = if started.len() > 0 { started.last().value } else { "" }
    set text(size: 7.5pt, fill: muted)
    grid(columns: (1fr, auto),
      [#text(fill: blue, weight: "semibold")[LAB NOTEBOOK] #h(4pt) #S.at("project", default: "")],
      id)
    v(-4pt); line(length: 100%, stroke: 0.4pt + rule-c)
  },
  footer: context {
    set text(size: 7.5pt, fill: muted)
    grid(columns: (1fr, auto),
      [#S.at("owner", default: "") #if S.at("affiliation", default: "") != "" [· #S.affiliation] · printed #data.generated],
      [#counter(page).display() / #counter(page).final().first()])
  },
)

// ---- helpers ----
// number -> text with exactly d decimals (0.05 -> "0.0500")
#let fmt(x, d: 4) = if x == none { "" } else if type(x) == str { x } else {
  let s = str(calc.round(x, digits: d))
  if d == 0 { return s }
  if not s.contains(".") { s += "." }
  s + "0" * (d - s.split(".").last().len())
}
// chemical names: digits after a letter become subscripts (Li2CO3 -> Li₂CO₃).
// G is left alone so diglyme stays "G2".
#let chem(s) = {
  show regex("[A-FH-Za-z\)\]]\d+"): it => { it.text.first(); sub(it.text.slice(1)) }
  s
}
// free text is Typst markup; #photo(...) and #chem("Li2CO3") work inside it
#let markup(s, photo) = if s == none or s == "" { none } else { eval(str(s), mode: "markup", scope: (photo: photo, chem: chem)) }
#let chip(t, fill: blue-l, col: blue) = box(fill: fill, inset: (x: 6pt, y: 3pt), radius: 3pt, text(size: 8pt, weight: "semibold", fill: col, t))
#let kv(d) = grid(columns: (auto, 1fr), column-gutter: 12pt, row-gutter: 5pt,
  ..d.pairs().map(((k, v)) => (text(fill: body-c, k.replace("_", " ")), v)).flatten())

#let photo-fn(map) = (file, caption: none, width: 70%) => {
  if file in map {
    figure(image(map.at(file), width: width), caption: caption, supplement: [Photo])
  } else {
    block(width: 100%, inset: 8pt, stroke: 0.8pt + warn, radius: 3pt,
      text(fill: warn)[Photo not found: *#file* — put it in Figures/Photos/ (or the entry's own folder).])
  }
}

// ---- one recipe ----
// breakable: false keeps a recipe's heading and tables on one page
#let recipe(r, photo) = block(breakable: false, {
  heading(level: 3)[#r.id — #chem(r.name)]
  let meta = ()
  if r.at("total_g", default: none) != none { meta.push([Total #fmt(r.total_g) g]) }
  if r.at("volume_mL", default: none) != none { meta.push([Volume #str(r.volume_mL) mL]) }
  if r.weighed_total_g != none { meta.push([Weighed #fmt(r.weighed_total_g) g]) }
  text(fill: body-c, meta.join(h(10pt)))
  v(2pt)
  table(columns: (auto, 1fr, auto, auto, auto, 1fr), align: (center, left, right, right, right, left),
    table.header[\#][Weigh out][Target][Mass (g)][Weighed (g)][Note],
    ..r.rows.map(x => (
      str(x.step),
      [#chem(x.weigh) #if x.form == "stock solution" { text(fill: muted, size: 7.5pt)[ (stock)] }],
      if x.target_wt_pct == "balance" { text(fill: muted)[balance] } else if x.target_wt_pct.ends-with("M") { x.target_wt_pct } else { x.target_wt_pct + " %" },
      if x.mass_g == none { "—" } else { strong(fmt(x.mass_g)) },
      if x.weighed_g == none { box(width: 3.2em, line(length: 100%, stroke: 0.4pt + muted)) } else { fmt(x.weighed_g) },
      text(size: 8pt, fill: body-c, if x.note == none { "" } else { x.note }),
    )).flatten())
  if r.composition.len() > 0 {
    v(2pt)
    let has-actual = r.composition.any(c => c.actual_wt_pct != none)
    table(columns: if has-actual { (1fr, auto, auto) } else { (1fr, auto) }, align: (left, right, right),
      ..(if has-actual { ([Final composition], [Target wt%], [Actual wt%]) } else { ([Final composition], [wt%]) }),
      ..r.composition.map(c => if has-actual { (chem(c.species), fmt(c.target_wt_pct, d: 3), fmt(c.actual_wt_pct, d: 3)) } else { (chem(c.species), fmt(c.target_wt_pct, d: 3)) }).flatten())
  }
  for w in r.warnings { text(fill: warn, size: 8.5pt)[⚠ #w] ; linebreak() }
  if r.procedure.len() > 0 {
    text(weight: "semibold", size: 9pt)[Procedure]
    enum(..r.procedure.map(s => markup(s, photo)))
  }
  markup(r.notes, photo)
})

// ---- one entry ----
#let entry(e) = {
  let photo = photo-fn(e.photos)
  [#metadata(e.id) <entry-start>]

  // title block
  block(width: 100%, inset: (bottom: 6pt), {
    chip(e.id, fill: blue, col: white)
    h(6pt)
    if e.status != none { chip(upper(e.status)) }
    for t in e.tags { h(3pt); chip(t, fill: rgb("#f3f4f6"), col: body-c) }
    v(4pt)
    text(size: 17pt, weight: "bold", chem(e.title))
    v(-2pt)
    text(fill: body-c)[Started #e.date #if e.operator != none [· #e.operator]]
  })

  if e.aim != none [== Aim
    #markup(e.aim, photo)]

  if e.conditions.len() > 0 [== Conditions
    #kv(e.conditions)]

  if e.materials.len() > 0 {
    heading(level: 2)[Materials]
    let cols = ("name", "formula", "supplier", "lot", "purity", "molar_mass_g_mol", "density_g_mL")
    let labels = ([Material], [Formula], [Supplier], [Lot], [Purity], [M (g/mol)], [ρ (g/mL)])
    table(columns: (1.4fr, auto, 1fr, auto, auto, auto, auto), align: (left, left, left, left, left, right, right),
      table.header(..labels),
      ..e.materials.map(m => cols.enumerate().map(((i, c)) => if i < 2 { chem(m.at(c, default: "")) } else { m.at(c, default: "") })).flatten())
  }

  if e.stocks.len() > 0 {
    heading(level: 2)[Stock solutions]
    table(columns: (1fr, 1.5fr, auto), table.header[Stock][Composition (wt%)][Made in],
      ..e.stocks.map(s => (chem(s.name), chem(s.composition), s.made_in)).flatten())
  }

  if e.recipes.len() > 0 {
    heading(level: 2)[Formulations]
    for r in e.recipes { recipe(r, photo) }
  }

  if e.electrodes.len() > 0 {
    heading(level: 2)[Electrodes]
    table(columns: (1.3fr, auto, auto, auto, auto, auto, auto, auto),
      align: (left, right, right, right, right, right, right, right),
      table.header[Disc][Coating (mg)][AM (mg)][Thick. (µm)][ρ (g/cc)][Loading (mg/cm²)][Areal (mAh/cm²)][1C (mA)],
      ..e.electrodes.map(x => (
        [#x.name #if x.at("sample", default: none) != none { text(size: 7.5pt, fill: muted)[\ #x.sample] }],
        fmt(x.coating_mass_g * 1000, d: 2), fmt(x.am_mass_g * 1000, d: 2),
        fmt(x.coating_thickness_mm * 1000, d: 1), fmt(x.coating_density_gcc, d: 3),
        fmt(x.loading_mg_cm2, d: 3), fmt(x.areal_capacity_mAh_cm2, d: 3), fmt(x.capacity_1C_mA, d: 4),
      )).flatten())
    text(size: 8pt, fill: muted)[Area #fmt(e.electrodes.at(0).area_cm2, d: 4) cm². C-rate currents (mA): ]
    for x in e.electrodes {
      text(size: 8pt, fill: body-c)[*#x.name*: #x.c_rates.pairs().map(((c, i)) => c + " " + fmt(i, d: 3)).join(", "). ]
    }
  }

  if e.np_ratios.len() > 0 {
    heading(level: 2)[N/P ratio]
    table(columns: (1fr, 1fr, auto, auto, auto), align: (left, left, right, right, right),
      table.header[Anode][Cathode][Anode (mAh/cm²)][Cathode (mAh/cm²)][N/P],
      ..e.np_ratios.map(n => (n.anode, n.cathode, fmt(n.anode_areal_capacity_mAh_cm2, d: 3),
        fmt(n.cathode_areal_capacity_mAh_cm2, d: 3),
        if n.warning != none { text(fill: warn, weight: "bold", fmt(n.np_ratio, d: 3)) } else { strong(fmt(n.np_ratio, d: 3)) })).flatten())
  }

  if e.cells.len() > 0 {
    heading(level: 2)[Cells]
    let keys = ()
    for c in e.cells { for k in c.keys() { if k not in keys { keys.push(k) } } }
    table(columns: keys.len(), table.header(..keys.map(k => k.replace("_", " "))),
      ..e.cells.map(c => keys.map(k => c.at(k, default: ""))).flatten())
  }

  if e.log.len() > 0 {
    heading(level: 2)[Log]
    for l in e.log {
      grid(columns: (3.2em, 1fr), column-gutter: 8pt, text(fill: blue, weight: "semibold", l.time), markup(l.text, photo))
      v(3pt)
    }
  }

  if e.notes != none [== Notes
    #markup(e.notes, photo)]
  if e.results != none [== Results
    #markup(e.results, photo)]
  if e.next_steps != none [== Next steps
    #markup(e.next_steps, photo)]

  if e.unreferenced_photos.len() > 0 {
    heading(level: 2)[Other photos]
    grid(columns: (1fr, 1fr), gutter: 8pt,
      ..e.unreferenced_photos.map(p => figure(image(p.path, width: 100%), caption: text(size: 7.5pt, p.name), supplement: [Photo])))
  }
}

// ---- cover + contents for a multi-entry book ----
#if not data.single {
  v(30%)
  text(size: 9pt, weight: "semibold", fill: blue)[LAB NOTEBOOK]
  v(-4pt)
  text(size: 26pt, weight: "bold", chem(S.at("project", default: "Lab notebook")))
  v(-6pt)
  text(fill: body-c)[#S.at("owner", default: "") #linebreak() #S.at("affiliation", default: "")]
  v(1em)
  text(fill: body-c)[#data.entries.len() entries · #data.entries.first().date → #data.entries.last().date]
  pagebreak()
  heading(level: 2, outlined: false)[Contents]
  table(columns: (auto, 1fr, auto), table.header[Entry][Title][Status],
    ..data.entries.map(e => (e.id, chem(e.title), if e.status == none { "" } else { e.status })).flatten())
  pagebreak()
}

#for (i, e) in data.entries.enumerate() {
  if i > 0 { pagebreak() }
  entry(e)
}
