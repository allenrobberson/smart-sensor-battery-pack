#!/usr/bin/env python3
"""
Generate a battery-size variant of the Smart Sensor Battery Pack.

The 18350 board in the repository root is the master design. This script
copies it into variants/<name>/ and swaps the Keystone 1096 (18350) holder
on the back for a different holder footprint, then:

  * keeps the BAT- solder pad at the bottom of the board, where the sensor
    tab is, so every front-side part and trace stays exactly where it is;
  * lengthens the board at the top to fit the longer holder;
  * widens the board to the right if the holder is wider than 23 mm;
  * extends the BAT+ trace (B.Cu) from the old pad position to the new one;
  * stretches the two full-board GND pours to the new outline;
  * clears all zone fills, so KiCad re-pours them (press B) when opened;
  * updates BT1's value/footprint in the schematic copy so
    "Update PCB from Schematic" keeps the right holder.

It also reports any locating hole of the new holder that lands on existing
copper, so you can choose a different --neg-y if needed.

Usage (from the repo root):
  python3 tools/make_variant.py --name 18650 \
      --footprint Library.pretty/BatteryHolder_Keystone_1042_1x18650.kicad_mod \
      --value 1042 --mpn "Keystone 1042" --neg-y 108.08

Requires only the Python standard library. Re-run it after changing the
master design to regenerate the variants.
"""
import argparse, math, os, re, shutil, sys, uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PCB = "smart_sensor_battery.kicad_pcb"

# Geometry of the master (18350) board, in board coordinates (mm).
HOLDER_X = 135.0            # centre line of the holder / battery traces
OLD_POS_PAD = (135.0, 60.12)  # where the BAT+ B.Cu trace ends today
OLD_NEG_PAD = (135.0, 106.88) # where the BAT- B.Cu trace ends today
TOP_Y, BOTTOM_Y = 55.5, 111.5
LEFT_X, RIGHT_X = 124.0, 147.0
EDGE_MARGIN = 0.5           # copper pad to board edge at the new top end

# ----------------------------------------------------------------- s-expr
TOK = re.compile(r'\s*(\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+)')

def parse(s):
    stack = [[]]
    for m in TOK.finditer(s):
        t = m.group(1)
        if t == '(':
            stack.append([])
        elif t == ')':
            x = stack.pop(); stack[-1].append(x)
        else:
            stack[-1].append(t)
    return stack[0][0]

def find(n, k): return [c for c in n if isinstance(c, list) and c and c[0] == k]
def first(n, k):
    r = find(n, k); return r[0] if r else None
def unq(s): return s[1:-1] if s.startswith('"') else s
def f(v): return ("%.4f" % v).rstrip('0').rstrip('.').replace('-0', '0') if abs(v) < 1e-9 else ("%.4f" % v).rstrip('0').rstrip('.')
def uid(): return '"%s"' % uuid.uuid4()

def block_span(text, start):
    """Return end index (exclusive) of the balanced block starting at text[start]=='('."""
    depth, i, n, ins = 0, start, len(text), False
    while i < n:
        c = text[i]
        if ins:
            if c == '\\': i += 1
            elif c == '"': ins = False
        elif c == '"': ins = True
        elif c == '(': depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0: return i + 1
        i += 1
    raise ValueError("unbalanced")

def top_blocks(text, key):
    """Yield (start, end) of top-level-in-board blocks '\n\t(key'."""
    pat = re.compile(r'\n\t\(%s[\s"]' % re.escape(key))
    for m in pat.finditer(text):
        s = m.start() + 2
        yield s, block_span(text, s)

# --------------------------------------------------------------- footprint
def load_holder(path, pos_name, neg_name):
    fp = parse(open(path).read())
    gfx, pads = [], []
    for k in ('fp_line', 'fp_rect', 'fp_circle', 'fp_arc', 'fp_poly'):
        for g in find(fp, k):
            layer = unq(first(g, 'layer')[1])
            if layer not in ('F.SilkS', 'F.Fab', 'F.CrtYd'):
                continue
            w = first(g, 'width') or first(first(g, 'stroke') or [], 'width')
            pts = {}
            for key in ('start', 'end', 'center', 'mid'):
                p = first(g, key)
                if p: pts[key] = (float(p[1]), float(p[2]))
            poly = first(g, 'pts')
            if poly: pts['pts'] = [(float(p[1]), float(p[2])) for p in find(poly, 'xy')]
            gfx.append((k, layer, float(w[1]) if w else 0.12, pts))
    for p in find(fp, 'pad'):
        name, kind, shape = unq(p[1]), p[2], p[3]
        at = first(p, 'at'); sz = first(p, 'size'); dr = first(p, 'drill')
        pads.append(dict(name=name, kind=kind, shape=shape,
                         x=float(at[1]), y=float(at[2]), a=float(at[3]) if len(at) > 3 else 0.0,
                         w=float(sz[1]), h=float(sz[2]),
                         drill=float(dr[1]) if dr else None))
    names = [p['name'] for p in pads]
    for n in (pos_name, neg_name):
        if n not in names:
            sys.exit("pad %r not found in %s (pads: %s)" % (n, path, names))
    for p in pads:
        if p['name'] == pos_name: p['name'] = 'P'
        elif p['name'] == neg_name: p['name'] = 'N'
    model = first(fp, 'model')
    mpath = unq(model[1]).replace('${KISYS3DMOD}', '${KICAD9_3DMODEL_DIR}') if model else None
    return gfx, pads, mpath

def gfx_sexp(k, layer, w, pts, flip):
    sy = -1 if flip else 1
    lay = layer.replace('F.', 'B.') if flip else layer
    def P(key):
        x, y = pts[key]; return '(%s %s %s)' % (key, f(x), f(sy * y))
    if k == 'fp_poly':
        body = '(pts %s)' % ' '.join('(xy %s %s)' % (f(x), f(sy * y)) for x, y in pts['pts'])
    else:
        body = ' '.join(P(key) for key in ('start', 'mid', 'center', 'end') if key in pts)
    fill = ' (fill no)' if k in ('fp_rect', 'fp_circle', 'fp_poly') else ''
    return '\t\t(%s %s (stroke (width %s) (type solid))%s (layer "%s") (uuid %s))\n' % (
        k, body, f(w), fill, lay, uid())

def pad_sexp(p, flip, fp_angle, nets):
    sy = -1 if flip else 1
    ang = (p['a'] + fp_angle) % 360
    if p['kind'] == 'np_thru_hole':
        return ('\t\t(pad "" np_thru_hole circle (at %s %s %s) (size %s %s) (drill %s) '
                '(layers "*.Cu" "*.Mask") (uuid %s))\n') % (
            f(p['x']), f(sy * p['y']), f(ang), f(p['w']), f(p['h']), f(p['drill']), uid())
    side = 'B' if flip else 'F'
    net = ''
    if p['kind'] == 'thru_hole':
        if nets:
            n = nets[p['name']]; net = ' (net %s "%s") (pintype "passive")' % n
        return ('\t\t(pad "%s" thru_hole %s (at %s %s %s) (size %s %s) (drill %s) (layers "*.Cu" "*.Mask")%s '
                '(uuid %s))\n') % (p['name'], p['shape'], f(p['x']), f(sy * p['y']), f(ang),
                                   f(p['w']), f(p['h']), f(p['drill']), net, uid())
    if nets:
        n = nets[p['name']]; net = ' (net %s "%s") (pintype "passive")' % n
    return ('\t\t(pad "%s" smd %s (at %s %s %s) (size %s %s) (layers "%s.Cu" "%s.Mask" "%s.Paste")%s '
            '(uuid %s))\n') % (p['name'], p['shape'], f(p['x']), f(sy * p['y']), f(ang),
                               f(p['w']), f(p['h']), side, side, side, net, uid())

def write_library_fp(path, libname, gfx, pads, model, descr):
    out = ['(footprint "%s"\n\t(version 20241229)\n\t(generator "pcbnew")\n\t(generator_version "9.0")\n\t(layer "F.Cu")\n' % libname,
           '\t(descr "%s")\n' % descr,
           '\t(property "Reference" "REF**" (at 0 -12 0) (layer "F.SilkS") (uuid %s) (effects (font (size 1 1) (thickness 0.15))))\n' % uid(),
           '\t(property "Value" "%s" (at 0 12 0) (layer "F.Fab") (uuid %s) (effects (font (size 1 1) (thickness 0.15))))\n' % (libname, uid()),
           '\t(attr smd)\n']
    for g in gfx: out.append(gfx_sexp(*g, flip=False).replace('\t\t', '\t', 1))
    for p in pads: out.append(pad_sexp(p, False, 0, None).replace('\t\t', '\t', 1))
    if model:
        out.append('\t(model "%s" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))\n' % model)
    out.append(')\n')
    open(path, 'w').write(''.join(out))

# ---------------------------------------------------------------- geometry
def xf(fx, fy, fa, x, y):
    a = math.radians(fa)
    return fx + x * math.cos(a) + y * math.sin(a), fy - x * math.sin(a) + y * math.cos(a)

def copper_features(board_tree, skip_ref='BT1'):
    feats = []
    for fp in find(board_tree, 'footprint'):
        ref = [unq(p[2]) for p in find(fp, 'property') if unq(p[1]) == 'Reference'][0]
        if ref == skip_ref: continue
        at = first(fp, 'at'); fx, fy = float(at[1]), float(at[2]); fa = float(at[3]) if len(at) > 3 else 0
        for pd in find(fp, 'pad'):
            pa = first(pd, 'at'); sz = first(pd, 'size')
            x, y = xf(fx, fy, fa, float(pa[1]), float(pa[2]))
            n = first(pd, 'net')
            feats.append(('%s pad %s' % (ref, unq(pd[1])), x, y, math.hypot(float(sz[1]), float(sz[2])) / 2, n[1] if n else None))
    for v in find(board_tree, 'via'):
        a = first(v, 'at'); feats.append(('via', float(a[1]), float(a[2]), float(first(v, 'size')[1]) / 2, first(v, 'net')[1]))
    for s in find(board_tree, 'segment'):
        a = first(s, 'start'); b = first(s, 'end'); w = float(first(s, 'width')[1])
        x1, y1, x2, y2 = map(float, (a[1], a[2], b[1], b[2]))
        n = max(2, int(math.hypot(x2 - x1, y2 - y1) / 0.1))
        for i in range(n + 1):
            feats.append(('%s trace (%s,%s)-(%s,%s)' % (unq(first(s, 'layer')[1]), x1, y1, x2, y2),
                          x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n, w / 2, first(s, 'net')[1]))
    return feats

# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', required=True, help='variant folder name, e.g. 18650')
    ap.add_argument('--footprint', required=True, help='holder .kicad_mod file')
    ap.add_argument('--pos-pad', default=None, help='name of the + pad in that footprint (default: auto)')
    ap.add_argument('--neg-pad', default=None, help='name of the - pad in that footprint (default: auto)')
    ap.add_argument('--value', required=True, help='BT1 value, e.g. 1042')
    ap.add_argument('--mpn', required=True, help='description, e.g. "Keystone 1042"')
    ap.add_argument('--cell', default=None, help='cell size for the description (default: --name)')
    ap.add_argument('--neg-y', type=float, default=OLD_NEG_PAD[1],
                    help='board Y of the BAT- pad centre (default %.2f, same as the 18350 board)' % OLD_NEG_PAD[1])
    ap.add_argument('--hole-clearance', type=float, default=0.25)
    a = ap.parse_args()
    cell = a.cell or a.name

    fpath = os.path.abspath(a.footprint)
    libname = os.path.splitext(os.path.basename(fpath))[0]
    raw = parse(open(fpath).read())
    smd = [unq(p[1]) for p in find(raw, 'pad') if p[2] != 'np_thru_hole']
    pos_name = a.pos_pad or ('P' if 'P' in smd else '1')
    neg_name = a.neg_pad or ('N' if 'N' in smd else '2')
    gfx, pads, model = load_holder(fpath, pos_name, neg_name)
    P = next(p for p in pads if p['name'] == 'P'); N = next(p for p in pads if p['name'] == 'N')

    # Put the footprint into the shared library with P/N pad names.
    lib_dir = os.path.join(ROOT, 'Library.pretty')
    write_library_fp(os.path.join(lib_dir, libname + '.kicad_mod'), libname, gfx, pads, model,
                     '%s single-cell %s holder, SMT (pads renamed P/N to match the BT1 symbol)' % (a.mpn, cell))

    # Orientation on the back side: flipped (local y -> -y), rotated so '+' is at the top.
    flip = True
    def board_xy(fa, cx, cy, p): return xf(cx, cy, fa, p['x'], -p['y'])
    fa = 90.0
    if board_xy(fa, 0, 0, P)[1] > board_xy(fa, 0, 0, N)[1]:
        fa = -90.0
    # Courtyard half-width across the board (local y extent).
    ys = [abs(pt[1]) for k, l, w, pts in gfx if l == 'F.CrtYd' for key, pt in pts.items() if key != 'pts'] or [10.6]
    xs = [abs(pt[0]) for k, l, w, pts in gfx if l == 'F.CrtYd' for key, pt in pts.items() if key != 'pts']
    half_w = max(ys)
    cx = HOLDER_X
    right = RIGHT_X
    if cx - half_w < LEFT_X: cx = LEFT_X + half_w
    if cx + half_w > right: right = round(cx + half_w + 0.0, 2)
    if cx + half_w > right or cx - half_w < LEFT_X - 1e-6:
        pass
    # Position: BAT- pad centre at --neg-y.
    nx, ny = board_xy(fa, 0, 0, N)
    cy = a.neg_y - ny
    pos_xy = board_xy(fa, cx, cy, P)
    neg_xy = board_xy(fa, cx, cy, N)
    # pad long axis is along the board (local x -> board y)
    pad_extent_top = pos_xy[1] - P['w'] / 2
    fab_x = [abs(pt[0]) for k, l, w, pts in gfx if l == 'F.Fab' for key, pt in pts.items() if key != 'pts']
    body_half = max(fab_x) if fab_x else 0.0
    body_top = cy - body_half
    new_top = round(min(TOP_Y, pad_extent_top - EDGE_MARGIN, body_top - EDGE_MARGIN), 2)
    neg_bottom = max(neg_xy[1] + N['w'] / 2 if N['kind'] == 'smd' else 0, cy + body_half)

    src = open(os.path.join(ROOT, PCB)).read()
    tree = parse(src)
    nets = {}
    for fp in find(tree, 'footprint'):
        ref = [unq(p[2]) for p in find(fp, 'property') if unq(p[1]) == 'Reference'][0]
        if ref == 'BT1':
            for pd in find(fp, 'pad'):
                n = first(pd, 'net')
                if n: nets[unq(pd[1])] = (n[1], unq(n[2]))
    assert set(nets) == {'P', 'N'}, nets

    out = src
    # 1. Replace the BT1 footprint block.
    bt = [(s, e) for s, e in top_blocks(out, 'footprint') if '(property "Reference" "BT1"' in out[s:e]]
    assert len(bt) == 1
    s, e = bt[0]
    old = out[s:e]
    hdr_end = old.index('\n\t\t(attr ')
    hdr = old[:hdr_end]
    hdr = hdr.replace('(footprint "Library:BAT_1096"', '(footprint "Library:%s"' % libname, 1)
    hdr = re.sub(r'\(at [-\d.]+ [-\d.]+ [-\d.]+\)', '(at %s %s %s)' % (f(cx), f(cy), f(fa)), hdr, count=1)
    hdr = hdr.replace('(property "Value" "1096"', '(property "Value" "%s"' % a.value, 1)
    hdr = hdr.replace('(property "MP" "1096"', '(property "MP" "%s"' % a.value, 1)
    hdr = hdr.replace('(property "SNAPEDA_PN" "1096"', '(property "SNAPEDA_PN" "%s"' % a.value, 1)
    hdr = re.sub(r'\(property "Description_1" "[^"]*"', '(property "Description_1" "SMT Battery Holder for %s Battery | %s"' % (cell, a.mpn), hdr, count=1)
    hdr = re.sub(r'(\(property "(?:Check_prices|SnapEDA_Link|Purchase-URL)" )"[^"]*"', r'\1""', hdr)
    hdr = re.sub(r'\(property "MAXIMUM_PACKAGE_HEIGHT" "[^"]*"', '(property "MAXIMUM_PACKAGE_HEIGHT" ""', hdr, count=1)
    body = ['\n\t\t(attr smd)\n']
    for g in gfx: body.append(gfx_sexp(*g, flip=flip))
    for p in pads: body.append(pad_sexp(p, flip, fa, nets))
    if model:
        body.append('\t\t(model "%s" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))\n' % model)
    body.append('\t)')
    out = out[:s] + hdr + ''.join(body) + out[e:]

    # 2. Board outline + full-board zones: move the top edge, maybe the right edge.
    def move_xy(txt, keys):
        def rep(m):
            x, y = float(m.group(2)), float(m.group(3))
            if abs(y - TOP_Y) < 1e-6: y = new_top
            if abs(x - RIGHT_X) < 1e-6: x = right
            return '(%s %s %s)' % (m.group(1), f(x), f(y))
        return re.sub(r'\((%s) ([-\d.]+) ([-\d.]+)\)' % keys, rep, txt)
    pieces, last = [], 0
    for s, e in list(top_blocks(out, 'gr_line')) + list(top_blocks(out, 'gr_rect')):
        blk = out[s:e]
        if '"Edge.Cuts"' in blk:
            pieces.append(out[last:s]); pieces.append(move_xy(blk, 'start|end')); last = e
    pieces.append(out[last:]); out = ''.join(pieces)

    pieces, last = [], 0
    for s, e in top_blocks(out, 'zone'):
        blk = out[s:e]
        # drop stale fills; KiCad re-pours on open (B)
        while True:
            i = blk.find('\n\t\t(filled_polygon')
            if i < 0: break
            j = block_span(blk, i + 3); blk = blk[:i] + blk[j:]
        pts_i = blk.find('(polygon')
        pts_e = block_span(blk, pts_i)
        poly = blk[pts_i:pts_e]
        xy = [(float(x), float(y)) for x, y in re.findall(r'\(xy ([-\d.]+) ([-\d.]+)\)', poly)]
        full = xy and min(y for _, y in xy) <= TOP_Y + 1e-6 and max(x for x, _ in xy) >= RIGHT_X - 1e-6
        if full:
            blk = blk[:pts_i] + move_xy(poly, 'xy') + blk[pts_e:]
        pieces.append(out[last:s]); pieces.append(blk); last = e
    pieces.append(out[last:]); out = ''.join(pieces)

    # 3. Extend the battery traces on B.Cu.
    def seg(x1, y1, x2, y2, net):
        return ('\t(segment (start %s %s) (end %s %s) (width 0.8) (layer "B.Cu") (net %s) (uuid %s))\n'
                % (f(x1), f(y1), f(x2), f(y2), net, uid()))
    new_segs = []
    px, py = pos_xy
    if abs(px - OLD_POS_PAD[0]) < 1e-6:
        new_segs.append(seg(px, py, OLD_POS_PAD[0], OLD_POS_PAD[1], nets['P'][0]))
    else:
        jog = abs(px - OLD_POS_PAD[0])
        new_segs.append(seg(px, py, px, OLD_POS_PAD[1] - jog, nets['P'][0]))
        new_segs.append(seg(px, OLD_POS_PAD[1] - jog, OLD_POS_PAD[0], OLD_POS_PAD[1], nets['P'][0]))
    qx, qy = neg_xy
    if abs(qx - OLD_NEG_PAD[0]) > N['h'] / 2 - 0.5 or abs(qy - OLD_NEG_PAD[1]) > N['w'] / 2 - 0.5:
        new_segs.append(seg(OLD_NEG_PAD[0], OLD_NEG_PAD[1], qx, qy, nets['N'][0]))
    i = out.rindex('\n\t(embedded_fonts') if '\n\t(embedded_fonts' in out else out.rindex('\n)')
    out = out[:i + 1] + ''.join(new_segs) + out[i + 1:]

    # 4. Write the variant project.
    vdir = os.path.join(ROOT, 'variants', a.name)
    os.makedirs(vdir, exist_ok=True)
    for fn in os.listdir(ROOT):
        if fn.startswith('smart_sensor_battery.kicad_') or fn == 'BMS.kicad_sch':
            if fn.endswith('.kicad_sym'): continue
            shutil.copy(os.path.join(ROOT, fn), os.path.join(vdir, fn))
    open(os.path.join(vdir, PCB), 'w', newline='\r\n').write(out)  # keep the repo's CRLF endings
    open(os.path.join(vdir, 'fp-lib-table'), 'w').write(
        '(fp_lib_table\n  (version 7)\n  (lib (name "Library")(type "KiCad")(uri "${KIPRJMOD}/../../Library.pretty")(options "")(descr ""))\n)\n')
    open(os.path.join(vdir, 'sym-lib-table'), 'w').write(
        '(sym_lib_table\n  (version 7)\n  (lib (name "smart_sensor_battery")(type "KiCad")(uri "${KIPRJMOD}/../../smart_sensor_battery.kicad_sym")(options "")(descr ""))\n)\n')
    # Schematic: BT1 instance only (the cached library symbol is left alone).
    sch_p = os.path.join(vdir, 'BMS.kicad_sch'); sch = open(sch_p).read()
    k = sch.index('(lib_id "smart_sensor_battery:1096")')
    s0 = sch.rindex('\n\t(symbol', 0, k) + 1; e0 = block_span(sch, s0)
    sym = sch[s0:e0]
    sym = sym.replace('(property "Value" "1096"', '(property "Value" "%s"' % a.value, 1)
    sym = sym.replace('(property "Footprint" "CustomBattery:BAT_1096"', '(property "Footprint" "Library:%s"' % libname, 1)
    sym = sym.replace('(property "MP" "1096"', '(property "MP" "%s"' % a.value, 1)
    sym = sym.replace('(property "SNAPEDA_PN" "1096"', '(property "SNAPEDA_PN" "%s"' % a.value, 1)
    sym = re.sub(r'\(property "Description_1" "[^"]*"', '(property "Description_1" "SMT Battery Holder for %s Battery | %s"' % (cell, a.mpn), sym, count=1)
    sym = re.sub(r'(\(property "(?:Check_prices|SnapEDA_Link|Purchase-URL)" )"[^"]*"', r'\1""', sym)
    sym = re.sub(r'\(property "MAXIMUM_PACKAGE_HEIGHT" "[^"]*"', '(property "MAXIMUM_PACKAGE_HEIGHT" ""', sym, count=1)
    sch = sch[:s0] + sym + sch[e0:]
    open(sch_p, 'w', newline='\r\n').write(sch)

    # 5. Report.
    feats = copper_features(tree)
    print('Variant %s -> %s' % (a.name, os.path.relpath(vdir, ROOT)))
    print('  holder at (%.2f, %.2f) rot %g on B.Cu' % (cx, cy, fa))
    print('  BAT+ pad (%.2f, %.2f)   BAT- pad (%.2f, %.2f)' % (pos_xy + neg_xy))
    print('  board outline: x %.2f..%.2f  y %.2f..%.2f  (%.1f x %.1f mm)' % (
        LEFT_X, right, new_top, BOTTOM_Y, right - LEFT_X, BOTTOM_Y - new_top))
    if neg_bottom > BOTTOM_Y:
        print('  note: holder pad/body overhangs the bottom edge by %.2f mm (the 18350 board overhangs its BAT+ pad by 0.95 mm)' % (neg_bottom - BOTTOM_Y))
    ok = True
    for p in pads:
        if p['kind'] not in ('np_thru_hole', 'thru_hole'): continue
        x, y = board_xy(fa, cx, cy, p)
        r = p['drill'] / 2 if p['kind'] == 'np_thru_hole' else max(p['w'], p['h']) / 2
        if p['kind'] == 'thru_hole':
            # same-net copper is fine for the battery lugs; only report other nets' features nearby
            pass
        own = nets[p['name']][0] if p['kind'] == 'thru_hole' else None  # same-net copper is fine for a lug
        hits = sorted({d for d, fx, fy, fr, fn in feats if fn != own and math.hypot(fx - x, fy - y) < r + fr + a.hole_clearance})
        edge = min(x - LEFT_X, right - x, y - new_top, BOTTOM_Y - y) - r
        status = 'ok' if not hits and edge > 0.3 else 'CLASH'
        ok &= status == 'ok'
        what = 'locating hole d%.2f' % p['drill'] if p['kind'] == 'np_thru_hole' else 'through-hole lug %s (pad d%.2f)' % (p['name'], max(p['w'], p['h']))
        print('  %s at (%.2f, %.2f): %s %s' % (what, x, y, status, '; '.join(hits)))
    if not ok:
        print('  -> move the holder with --neg-y or reroute the listed traces.')
    print('Open the project in KiCad, press B to refill zones, then run DRC.')

if __name__ == '__main__':
    main()
