#!/usr/bin/env python3
"""
Write JLCPCB-style Gerber and Excellon files straight from a .kicad_pcb, without KiCad.

Why this exists: the copper pours in these boards are stored unfilled (they are refilled
by KiCad with the B key). This script builds the fills itself using Gerber polarity:

  1. dark:  each pour (GND across the board, small BAT+/BAT- areas) as a region
  2. clear: board edge, keep-out areas, and every copper item of another net,
            grown by the zone clearance (0.5 mm)
  3. dark:  all real copper (pads, tracks, vias) on top

Same-net pads and vias end up solidly joined to their pour (no thermal spokes).
Silkscreen carries outlines only; text (reference designators, logo) is not drawn.

Usage:  python3 tools/make_gerbers.py <board.kicad_pcb> <output_dir> [--png preview.png]
Writes: <name>-F_Cu.gtl, -B_Cu.gbl, -F_Mask.gts, -B_Mask.gbs, -F_Paste.gtp, -B_Paste.gbp,
        -F_Silkscreen.gto, -B_Silkscreen.gbo, -Edge_Cuts.gm1, -PTH.drl, -NPTH.drl
"""
import math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_variant import parse, find, first, unq  # same s-expression helpers

POUR_CLEARANCE = 0.5     # zone clearance used by every pour in this design
EDGE_CLEARANCE = 0.5     # copper pour pull-back from the board edge
MASK_MARGIN = 0.05       # default solder-mask expansion when a pad gives none


def xf(fx, fy, fa, x, y):
    a = math.radians(fa)
    return fx + x * math.cos(a) + y * math.sin(a), fy - x * math.sin(a) + y * math.cos(a)


# ------------------------------------------------------------------ model
class Board:
    def __init__(self, path):
        self.t = parse(open(path).read())
        self.netname = {n[1]: unq(n[2]) for n in find(self.t, 'net')}
        self.pads, self.tracks, self.vias, self.gfx = [], [], [], []
        self.edges, self.zones = [], []
        self._load()

    def _load(self):
        t = self.t
        for fp in find(t, 'footprint'):
            at = first(fp, 'at'); fx, fy = float(at[1]), float(at[2])
            fa = float(at[3]) if len(at) > 3 else 0.0
            for pd in find(fp, 'pad'):
                pa = first(pd, 'at'); sz = first(pd, 'size')
                x, y = xf(fx, fy, fa, float(pa[1]), float(pa[2]))
                ang = float(pa[3]) if len(pa) > 3 else fa
                w, h = float(sz[1]), float(sz[2])
                if round(ang) % 180 == 90:
                    w, h = h, w
                lay = [unq(l) for l in first(pd, 'layers')[1:]]
                cu = set()
                if '*.Cu' in lay: cu = {'F', 'B'}
                else: cu = {l[0] for l in lay if l.endswith('.Cu')}
                mask = set()
                if '*.Mask' in lay: mask = {'F', 'B'}
                else: mask = {l[0] for l in lay if l.endswith('.Mask')}
                paste = {l[0] for l in lay if l.endswith('.Paste')}
                dr = first(pd, 'drill'); n = first(pd, 'net')
                mm = first(pd, 'solder_mask_margin')
                # (drill d) or (drill oval w h) for a slot
                slot = None
                if dr and dr[1] == 'oval':
                    sw, sh = float(dr[2]), float(dr[3])
                    if round(ang) % 180 == 90: sw, sh = sh, sw
                    slot = (sw, sh)
                    drill = min(sw, sh)
                else:
                    drill = float(dr[1]) if dr else 0.0
                self.pads.append(dict(kind=pd[2], shape=pd[3], x=x, y=y, w=w, h=h, cu=cu, mask=mask,
                                      paste=paste, drill=drill, slot=slot,
                                      net=unq(n[2]) if n else None,
                                      mask_margin=float(mm[1]) if mm else MASK_MARGIN))
            for k in ('fp_line', 'fp_circle', 'fp_poly', 'fp_rect', 'fp_arc'):
                for g in find(fp, k):
                    L = unq(first(g, 'layer')[1])
                    if L not in ('F.SilkS', 'B.SilkS'): continue
                    self.gfx.append(self._graphic(k, g, L, (fx, fy, fa)))
        for s in find(t, 'segment'):
            a, e = first(s, 'start'), first(s, 'end')
            self.tracks.append(dict(a=(float(a[1]), float(a[2])), b=(float(e[1]), float(e[2])),
                                    w=float(first(s, 'width')[1]), layer=unq(first(s, 'layer')[1])[0],
                                    net=self.netname.get(first(s, 'net')[1])))
        for v in find(t, 'via'):
            a = first(v, 'at')
            self.vias.append(dict(x=float(a[1]), y=float(a[2]), d=float(first(v, 'size')[1]),
                                  drill=float(first(v, 'drill')[1]), net=self.netname.get(first(v, 'net')[1])))
        for k in ('gr_line', 'gr_circle', 'gr_poly', 'gr_rect', 'gr_arc'):
            for g in find(t, k):
                L = unq(first(g, 'layer')[1])
                if L == 'Edge.Cuts':
                    a, e = first(g, 'start'), first(g, 'end')
                    self.edges.append(((float(a[1]), float(a[2])), (float(e[1]), float(e[2]))))
                elif L in ('F.SilkS', 'B.SilkS'):
                    self.gfx.append(self._graphic(k, g, L, (0, 0, 0)))
        for z in find(t, 'zone'):
            lay = first(z, 'layer') or first(z, 'layers')
            pts = first(first(z, 'polygon'), 'pts')
            poly = [(float(p[1]), float(p[2])) for p in find(pts, 'xy')]
            self.zones.append(dict(layer=unq(lay[1])[0], net=unq(first(z, 'net_name')[1]) if first(z, 'net_name') else '',
                                   keepout=bool(first(z, 'keepout')), poly=poly))

    def _graphic(self, k, g, L, T):
        fx, fy, fa = T
        w = first(g, 'width') or first(first(g, 'stroke') or [], 'width')
        w = float(w[1]) if w else 0.12
        P = lambda key: xf(fx, fy, fa, float(first(g, key)[1]), float(first(g, key)[2]))
        side = L[0]
        if k.endswith('line'):
            return ('line', side, P('start'), P('end'), w)
        if k.endswith('circle'):
            c = P('center'); e = P('end'); return ('circle', side, c, math.dist(c, e), w)
        if k.endswith('rect'):
            a, b = P('start'), P('end')
            return ('poly', side, [a, (b[0], a[1]), b, (a[0], b[1])], w)
        if k.endswith('arc'):
            return ('arc', side, P('start'), P('mid'), P('end'), w)
        pts = [xf(fx, fy, fa, float(p[1]), float(p[2])) for p in find(first(g, 'pts'), 'xy')]
        return ('poly', side, pts, w)

    def outline(self):
        segs = self.edges[:]
        poly = [segs[0][0], segs[0][1]]; segs.pop(0)
        while segs:
            for i, (p, q) in enumerate(segs):
                if math.dist(p, poly[-1]) < 1e-4: poly.append(q); segs.pop(i); break
                if math.dist(q, poly[-1]) < 1e-4: poly.append(p); segs.pop(i); break
            else:
                raise ValueError('board outline is not a closed chain')
        return poly[:-1] if math.dist(poly[0], poly[-1]) < 1e-4 else poly


# ------------------------------------------------------------------ layer ops
# op = (dark: bool, kind, params)
#   ('region', [pts]), ('flash_c', x, y, d), ('flash_r', x, y, w, h), ('line', a, b, w)

def pad_ops(p, grow, dark=True):
    if p['shape'] == 'circle':
        return [(dark, 'flash_c', p['x'], p['y'], max(p['w'], p['h']) + 2 * grow)]
    if p['shape'] == 'oval':
        w, h = p['w'] + 2 * grow, p['h'] + 2 * grow
        d = min(w, h)
        if w >= h: a, b = (p['x'] - (w - d) / 2, p['y']), (p['x'] + (w - d) / 2, p['y'])
        else: a, b = (p['x'], p['y'] - (h - d) / 2), (p['x'], p['y'] + (h - d) / 2)
        return [(dark, 'line', a, b, d)]
    return [(dark, 'flash_r', p['x'], p['y'], p['w'] + 2 * grow, p['h'] + 2 * grow)]


def hole_ops(p, grow, dark=False):
    """Clearance/opening around a drilled hole: a circle, or a stadium for a slot."""
    if p.get('slot'):
        return pad_ops(dict(shape='oval', x=p['x'], y=p['y'], w=p['slot'][0], h=p['slot'][1]), grow, dark)
    return [(dark, 'flash_c', p['x'], p['y'], p['drill'] + 2 * grow)]


def grow_rect_poly(poly, g):
    xs = [x for x, _ in poly]; ys = [y for _, y in poly]
    x0, x1, y0, y1 = min(xs) - g, max(xs) + g, min(ys) - g, max(ys) + g
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def is_axis_rect(poly):
    return len(poly) == 4 and len({round(x, 4) for x, _ in poly}) == 2 and len({round(y, 4) for _, y in poly}) == 2


def copper_ops(B, side):
    ops = []
    outline = B.outline()
    pours = [z for z in B.zones if z['layer'] == side and not z['keepout'] and z['net']]
    span = lambda z: max(x for x, _ in z['poly']) - min(x for x, _ in z['poly'])
    big = [z for z in pours if z['net'] == 'GND' and span(z) > 15]   # the board-wide ground pours
    small = [z for z in pours if z not in big]
    keepouts = [z for z in B.zones if z['layer'] == side and z['keepout']]

    def items(net_filter):
        for p in B.pads:
            if side in p['cu'] and net_filter(p['net']): yield ('pad', p)
        for t in B.tracks:
            if t['layer'] == side and net_filter(t['net']): yield ('trk', t)
        for v in B.vias:
            if net_filter(v['net']): yield ('via', v)

    def item_ops(kind, o, grow, dark):
        if kind == 'pad': return pad_ops(o, grow, dark)
        if kind == 'trk': return [(dark, 'line', o['a'], o['b'], o['w'] + 2 * grow)]
        return [(dark, 'flash_c', o['x'], o['y'], o['d'] + 2 * grow)]

    # 1. big GND pour = board outline
    if big:
        ops.append((True, 'region', outline))
        for a, b in B.edges:
            ops.append((False, 'line', a, b, 2 * EDGE_CLEARANCE))
        for z in keepouts:
            ops.append((False, 'region', z['poly']))
        # holes without copper
        for p in B.pads:
            if p['kind'] == 'np_thru_hole':
                ops += hole_ops(p, POUR_CLEARANCE)
        for kind, o in items(lambda n: n != 'GND'):
            ops += item_ops(kind, o, POUR_CLEARANCE, False)
        for z in small:
            if z['net'] != 'GND':
                ops.append((False, 'region', grow_rect_poly(z['poly'], POUR_CLEARANCE) if is_axis_rect(z['poly']) else z['poly']))
    # 2. small pours
    for z in small:
        ops.append((True, 'region', z['poly']))
        xs = [x for x, _ in z['poly']]; ys = [y for _, y in z['poly']]
        bb = (min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2)
        def near(kind, o):
            if kind == 'trk':
                return not (max(o['a'][0], o['b'][0]) < bb[0] or min(o['a'][0], o['b'][0]) > bb[2] or
                            max(o['a'][1], o['b'][1]) < bb[1] or min(o['a'][1], o['b'][1]) > bb[3])
            return bb[0] <= o['x'] <= bb[2] and bb[1] <= o['y'] <= bb[3]
        for kind, o in items(lambda n, zn=z['net']: n != zn):
            if near(kind, o):
                ops += item_ops(kind, o, POUR_CLEARANCE, False)
        for p in B.pads:
            if p['kind'] == 'np_thru_hole' and near('pad', p):
                ops += hole_ops(p, POUR_CLEARANCE)
    # 3. real copper on top
    for kind, o in items(lambda n: True):
        ops += item_ops(kind, o, 0.0, True)
    return ops


def mask_ops(B, side):
    ops = []
    for p in B.pads:
        if side in p['mask']:
            ops += pad_ops(p, p['mask_margin'] if p['kind'] != 'np_thru_hole' else 0.0)
            if p['kind'] == 'np_thru_hole':
                ops[-1:] = hole_ops(p, 0.05, True)
    return ops


def paste_ops(B, side):
    return [o for p in B.pads if side in p['paste'] and p['kind'] == 'smd' for o in pad_ops(p, 0.0)]


def silk_ops(B, side):
    ops = []
    for g in B.gfx:
        if g[1] != side: continue
        if g[0] == 'line':
            ops.append((True, 'line', g[2], g[3], g[4]))
        elif g[0] == 'circle':
            c, r, w = g[2], g[3], g[4]
            n = 48
            pts = [(c[0] + r * math.cos(2 * math.pi * i / n), c[1] + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
            ops += [(True, 'line', pts[i], pts[i + 1], w) for i in range(n)]
        elif g[0] == 'poly':
            pts = g[2] + [g[2][0]]
            ops += [(True, 'line', pts[i], pts[i + 1], g[3]) for i in range(len(pts) - 1)]
        elif g[0] == 'arc':
            ops += [(True, 'line', g[2], g[3], g[5]), (True, 'line', g[3], g[4], g[5])]
    return ops


def edge_ops(B):
    return [(True, 'line', a, b, 0.1) for a, b in B.edges]


# ------------------------------------------------------------------ writers
def fmt(v):
    return str(int(round(v * 1e6)))


def write_gerber(path, ops, func):
    aps = {}
    def ap(key):
        if key not in aps: aps[key] = 10 + len(aps)
        return aps[key]
    body, pol = [], None
    for op in ops:
        dark, kind = op[0], op[1]
        if dark != pol:
            body.append('%LPD*%' if dark else '%LPC*%'); pol = dark
        if kind == 'region':
            pts = op[2]
            body.append('G36*')
            body.append('X%sY%sD02*' % (fmt(pts[0][0]), fmt(-pts[0][1])))
            for x, y in pts[1:] + [pts[0]]:
                body.append('X%sY%sD01*' % (fmt(x), fmt(-y)))
            body.append('G37*')
        elif kind == 'flash_c':
            body.append('D%d*' % ap(('C', round(op[4], 4))))
            body.append('X%sY%sD03*' % (fmt(op[2]), fmt(-op[3])))
        elif kind == 'flash_r':
            body.append('D%d*' % ap(('R', round(op[4], 4), round(op[5], 4))))
            body.append('X%sY%sD03*' % (fmt(op[2]), fmt(-op[3])))
        elif kind == 'line':
            a, b, w = op[2], op[3], op[4]
            body.append('D%d*' % ap(('C', round(w, 4))))
            body.append('X%sY%sD02*' % (fmt(a[0]), fmt(-a[1])))
            body.append('X%sY%sD01*' % (fmt(b[0]), fmt(-b[1])))
    head = ['G04 Generated by tools/make_gerbers.py*', '%%TF.FileFunction,%s*%%' % func,
            '%FSLAX46Y46*%', '%MOMM*%', '%LPD*%', 'G01*']
    for key, n in sorted(aps.items(), key=lambda kv: kv[1]):
        if key[0] == 'C': head.append('%%ADD%dC,%.4f*%%' % (n, key[1]))
        else: head.append('%%ADD%dR,%.4fX%.4f*%%' % (n, key[1], key[2]))
    open(path, 'w').write('\n'.join(head + body + ['M02*']) + '\n')


def write_drill(path, holes, plated):
    """holes: (d, x, y) for round holes, or (d, x, y, (x1, y1), (x2, y2)) for a slot (G85)."""
    tools = {}
    for h in holes:
        tools.setdefault(round(h[0], 3), []).append(h[1:])
    out = ['M48', '; DRILL file generated by tools/make_gerbers.py',
           '; FORMAT={-:-/ absolute / metric / decimal}',
           '; #@! TF.FileFunction,%s,1,2,%s' % ('Plated' if plated else 'NonPlated', 'PTH' if plated else 'NPTH'),
           'FMAT,2', 'METRIC']
    for i, d in enumerate(sorted(tools), 1):
        out.append('T%dC%.3f' % (i, d))
    out += ['%', 'G90', 'G05']
    for i, d in enumerate(sorted(tools), 1):
        out.append('T%d' % i)
        for h in tools[d]:
            if len(h) == 2:
                out.append('X%.3fY%.3f' % (h[0], -h[1]))
            else:
                (x1, y1), (x2, y2) = h[2], h[3]
                out.append('X%.3fY%.3fG85X%.3fY%.3f' % (x1, -y1, x2, -y2))
    out.append('M30')
    open(path, 'w').write('\n'.join(out) + '\n')


# ------------------------------------------------------------------ raster preview / check
def rasterize(ops, bbox, res=0.02):
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = bbox
    W, H = int((x1 - x0) / res) + 1, int((y1 - y0) / res) + 1
    im = Image.new('1', (W, H), 0); dr = ImageDraw.Draw(im)
    P = lambda p: ((p[0] - x0) / res, (p[1] - y0) / res)
    for op in ops:
        c = 1 if op[0] else 0; kind = op[1]
        if kind == 'region':
            dr.polygon([P(p) for p in op[2]], fill=c)
        elif kind == 'flash_c':
            (cx, cy), r = P((op[2], op[3])), op[4] / 2 / res
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
        elif kind == 'flash_r':
            (cx, cy) = P((op[2], op[3])); w, h = op[4] / 2 / res, op[5] / 2 / res
            dr.rectangle([cx - w, cy - h, cx + w, cy + h], fill=c)
        elif kind == 'line':
            a, b, r = P(op[2]), P(op[3]), op[4] / 2 / res
            dr.line([a, b], fill=c, width=max(1, int(round(2 * r))))
            for q in (a, b): dr.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=c)
    return im


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, outdir = sys.argv[1], sys.argv[2]
    png = sys.argv[sys.argv.index('--png') + 1] if '--png' in sys.argv else None
    name = sys.argv[sys.argv.index('--name') + 1] if '--name' in sys.argv else os.path.splitext(os.path.basename(src))[0]
    B = Board(src)
    os.makedirs(outdir, exist_ok=True)
    o = lambda suf: os.path.join(outdir, '%s-%s' % (name, suf))
    layers = {
        'F_Cu.gtl': (copper_ops(B, 'F'), 'Copper,L1,Top'),
        'B_Cu.gbl': (copper_ops(B, 'B'), 'Copper,L2,Bot'),
        'F_Mask.gts': (mask_ops(B, 'F'), 'Soldermask,Top'),
        'B_Mask.gbs': (mask_ops(B, 'B'), 'Soldermask,Bot'),
        'F_Paste.gtp': (paste_ops(B, 'F'), 'Paste,Top'),
        'B_Paste.gbp': (paste_ops(B, 'B'), 'Paste,Bot'),
        'F_Silkscreen.gto': (silk_ops(B, 'F'), 'Legend,Top'),
        'B_Silkscreen.gbo': (silk_ops(B, 'B'), 'Legend,Bot'),
        'Edge_Cuts.gm1': (edge_ops(B), 'Profile,NP'),
    }
    for suf, (ops, func) in layers.items():
        write_gerber(o(suf), ops, func)
    pth = [(v['drill'], v['x'], v['y']) for v in B.vias] + \
          [(p['drill'], p['x'], p['y']) for p in B.pads if p['kind'] == 'thru_hole']
    def hole(p):
        if not p.get('slot'):
            return (p['drill'], p['x'], p['y'])
        sw, sh = p['slot']; d = min(sw, sh); L = (max(sw, sh) - d) / 2
        a, b = ((p['x'] - L, p['y']), (p['x'] + L, p['y'])) if sw >= sh else ((p['x'], p['y'] - L), (p['x'], p['y'] + L))
        return (d, p['x'], p['y'], a, b)
    npth = [hole(p) for p in B.pads if p['kind'] == 'np_thru_hole']
    write_drill(o('PTH.drl'), pth, True)
    write_drill(o('NPTH.drl'), npth, False)
    print('wrote', len(layers) + 2, 'files to', outdir)
    return B, layers


if __name__ == '__main__':
    main()
