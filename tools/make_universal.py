#!/usr/bin/env python3
"""
Generate the universal Smart Sensor Battery Pack board: one PCB that takes a
Keystone 1096 (18350), 1042 (18650) or 1121 (21700) holder.

It starts from the 21700 variant (variants/21700, the widest board) and:

  * replaces BT1 with a combined footprint on the back:
      - BAT- end: one SMD pad covering the 1096 and 1042 contacts, plus the
        plated hole for the 1121's solder pin;
      - BAT+ end: the 1042 pad with the 1121 pin hole at the top of the board,
        and the 1096 pad 30.7 mm further down, joined by the existing B.Cu trace;
      - locating holes for all three holders; holes that overlap are merged
        into slots;
  * lengthens the board to fit the 1042 (the longest holder): 86.25 x 24 mm;
  * draws large + / - marks and the cell size next to each + pad on the back
    silkscreen;
  * sets R1 (charge current) to 1 kohm (about 540 mA, a basic JLCPCB part),
    a safe compromise for every cell size.

Usage (from the repo root, after tools/make_variant.py has built variants/21700):
  python3 tools/make_universal.py
"""
import os, re, shutil, sys, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_variant import block_span, top_blocks, f  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'variants', '21700')
DST = os.path.join(ROOT, 'variants', 'universal')
PCB = 'smart_sensor_battery.kicad_pcb'
LIBNAME = 'BatteryHolder_Universal_18350_18650_21700'

OLD_TOP, NEW_TOP = 26.95, 25.25     # 21700 board top -> 1042 needs 25.25
ORIGIN = (136.0, 68.5)              # BT1 footprint origin (board coordinates), rotation 0

# Pads in board coordinates (x, y, w, h); w across the board, h along it.
SMD = [
    ('P', 135.0, 29.42, 6.35, 7.34),    # 1042 + contact (top)
    ('P', 135.0, 60.12, 6.35, 7.34),    # 1096 + contact
    ('N', 135.0, 107.205, 6.35, 7.99),  # 1096 (y 106.88) + 1042 (y 108.08) - contacts, kept 0.3 mm off the edge
]
PINS = [('P', 136.0, 30.22), ('N', 136.0, 106.88)]   # 1121 solder pins: pad 2.8, drill 1.7
# Locating holes: (x, y, w, h) -> round if w == h, otherwise a slot.
HOLES = [
    (127.0, 31.52, 2.4, 4.99),    # small pegs: 1042 (127, 32.82) + 1121 (127, 30.22)
    (127.0, 63.62, 2.39, 2.39),   # small peg: 1096
    (127.0, 97.545, 3.55, 5.94),  # large pegs: 1042 (127, 96.35), 1121 (127, 96.60), 1096 (127, 98.74)
    (143.0, 68.26, 3.45, 3.45),   # large peg: 1096
    (144.0, 40.95, 6.0, 4.0),     # large pegs: 1042 (143, 41.15) + 1121 (145, 40.75)
]

# Tiny stroke font for the silkscreen labels (KiCad text isn't plotted by make_gerbers).
GLYPH = {
    '0': [(0, 0, 1, 0), (1, 0, 1, 2), (1, 2, 0, 2), (0, 2, 0, 0)],
    '1': [(0.5, 0, 0.5, 2), (0.2, 0.3, 0.5, 0)],
    '2': [(0, 0, 1, 0), (1, 0, 1, 1), (1, 1, 0, 1), (0, 1, 0, 2), (0, 2, 1, 2)],
    '3': [(0, 0, 1, 0), (1, 0, 1, 2), (1, 2, 0, 2), (0.2, 1, 1, 1)],
    '5': [(1, 0, 0, 0), (0, 0, 0, 1), (0, 1, 1, 1), (1, 1, 1, 2), (1, 2, 0, 2)],
    '6': [(1, 0, 0, 0), (0, 0, 0, 2), (0, 2, 1, 2), (1, 2, 1, 1), (1, 1, 0, 1)],
    '7': [(0, 0, 1, 0), (1, 0, 0.4, 2)],
    '8': [(0, 0, 1, 0), (1, 0, 1, 2), (1, 2, 0, 2), (0, 2, 0, 0), (0, 1, 1, 1)],
}


def uid(): return '"%s"' % uuid.uuid4()


def silk_line(x1, y1, x2, y2, w=0.2):
    return ('\t(gr_line (start %s %s) (end %s %s) (stroke (width %s) (type solid)) (layer "B.SilkS") (uuid %s))\n'
            % (f(x1), f(y1), f(x2), f(y2), f(w), uid()))


def silk_text(text, x, y, h=1.4, w=0.18):
    """Draw digits starting at (x, y) top-left, mirrored so they read correctly from the back."""
    out, s, adv = [], h / 2, h / 2 * 1.6
    for i, ch in enumerate(text):
        ox = x + i * adv
        for a, b, c, d in GLYPH[ch]:
            # mirror horizontally inside the glyph cell: viewed from the back the board is flipped
            out.append(silk_line(ox + (1 - a) * s, y + b * s, ox + (1 - c) * s, y + d * s, w))
    return out


def silk_text_mirrored_run(text, x_right, y, h=1.4, w=0.18):
    """Text that reads left-to-right when the board is turned over: lay glyphs right-to-left."""
    adv = h / 2 * 1.6
    out = []
    for i, ch in enumerate(text):
        out += silk_text(ch, x_right - (i + 1) * adv + (adv - h / 2), y, h, w)
    return out


def plus(x, y, size=3.0, w=0.4):
    return [silk_line(x - size / 2, y, x + size / 2, y, w), silk_line(x, y - size / 2, x, y + size / 2, w)]


def minus(x, y, size=3.0, w=0.4):
    return [silk_line(x - size / 2, y, x + size / 2, y, w)]


def footprint_body(nets):
    ox, oy = ORIGIN
    out = ['\n\t\t(attr smd)\n']
    for name, x, y, w, h in SMD:
        code, net = nets[name]
        out.append('\t\t(pad "%s" smd rect (at %s %s) (size %s %s) (layers "B.Cu" "B.Mask" "B.Paste") '
                   '(net %s "%s") (pintype "passive") (uuid %s))\n'
                   % (name, f(x - ox), f(y - oy), f(w), f(h), code, net, uid()))
    for name, x, y in PINS:
        code, net = nets[name]
        out.append('\t\t(pad "%s" thru_hole circle (at %s %s) (size 2.8 2.8) (drill 1.7) (layers "*.Cu" "*.Mask") '
                   '(net %s "%s") (pintype "passive") (uuid %s))\n'
                   % (name, f(x - ox), f(y - oy), code, net, uid()))
    for x, y, w, h in HOLES:
        drill = '(drill %s)' % f(w) if w == h else '(drill oval %s %s)' % (f(w), f(h))
        shape = 'circle' if w == h else 'oval'
        out.append('\t\t(pad "" np_thru_hole %s (at %s %s) (size %s %s) %s (layers "*.Cu" "*.Mask") (uuid %s))\n'
                   % (shape, f(x - ox), f(y - oy), f(w), f(h), drill, uid()))
    # courtyard: the largest holder footprint
    out.append('\t\t(fp_rect (start %s %s) (end %s %s) (stroke (width 0.05) (type solid)) (fill no) (layer "B.CrtYd") (uuid %s))\n'
               % (f(124.25 - ox), f(NEW_TOP + 0.25 - oy), f(147.75 - ox), f(111.25 - oy), uid()))
    out.append('\t)')
    return ''.join(out)


def main():
    if not os.path.exists(os.path.join(SRC, PCB)):
        sys.exit('variants/21700 not found: run tools/make_variant.py for 21700 first')
    src = open(os.path.join(SRC, PCB)).read()

    # BT1 nets from the 21700 board
    bt = [(s, e) for s, e in top_blocks(src, 'footprint') if '(property "Reference" "BT1"' in src[s:e]]
    assert len(bt) == 1
    s, e = bt[0]
    blk = src[s:e]
    nets = {}
    for m in re.finditer(r'\(pad "([PN])".*?\(net (\d+) "([^"]*)"\)', blk, re.S):
        nets[m.group(1)] = (m.group(2), m.group(3))
    assert set(nets) == {'P', 'N'}, nets

    hdr = blk[:blk.index('\n\t\t(attr ')]
    hdr = re.sub(r'\(footprint "Library:[^"]+"', '(footprint "Library:%s"' % LIBNAME, hdr, count=1)
    hdr = re.sub(r'\(at [-\d.]+ [-\d.]+(?: [-\d.]+)?\)', '(at %s %s)' % (f(ORIGIN[0]), f(ORIGIN[1])), hdr, count=1)
    hdr = re.sub(r'\(property "Value" "[^"]*"', '(property "Value" "1096/1042/1121"', hdr, count=1)
    hdr = re.sub(r'\(property "MP" "[^"]*"', '(property "MP" "1096 / 1042 / 1121"', hdr, count=1)
    hdr = re.sub(r'\(property "Description_1" "[^"]*"',
                 '(property "Description_1" "Universal pads for Keystone 1096 (18350), 1042 (18650) or 1121 (21700)"', hdr, count=1)
    out = src[:s] + hdr + footprint_body(nets) + src[e:]

    # Board outline + full-board zones: move the top edge up.
    def move(txt):
        return re.sub(r'\((start|end|xy) ([-\d.]+) %s\)' % re.escape(f(OLD_TOP)),
                      lambda m: '(%s %s %s)' % (m.group(1), m.group(2), f(NEW_TOP)), txt)
    pieces, last = [], 0
    for s, e in list(top_blocks(out, 'gr_line')) + list(top_blocks(out, 'gr_rect')) + list(top_blocks(out, 'zone')):
        b = out[s:e]
        if '"Edge.Cuts"' in b or b.startswith('(zone'):
            pieces.append(out[last:s]); pieces.append(move(b)); last = e
    pieces.append(out[last:]); out = ''.join(pieces)

    # Back silkscreen: + / - marks and which + pad is for which cell.
    silk = []
    silk += plus(141.8, 27.6) + silk_text_mirrored_run('18650', 146.6, 29.6) + silk_text_mirrored_run('21700', 146.6, 31.6)
    silk += plus(141.8, 57.6) + silk_text_mirrored_run('18350', 146.6, 59.6)
    silk += minus(141.8, 105.5) + minus(128.2, 105.5)
    i = out.rindex('\n\t(embedded_fonts') if '\n\t(embedded_fonts' in out else out.rindex('\n)')
    out = out[:i + 1] + ''.join(silk) + out[i + 1:]

    # R1: 1 kohm, basic part C11702.
    k = out.index('(property "Reference" "R1"')
    fs = out.rindex('\n\t(footprint', 0, k) + 1; fe = block_span(out, fs)
    out = out[:fs] + re.sub(r'\(property "Value" "[^"]*"', '(property "Value" "1k"', out[fs:fe], count=1) + out[fe:]

    # Write the project.
    os.makedirs(DST, exist_ok=True)
    for fn in os.listdir(SRC):
        if fn != PCB:
            shutil.copy(os.path.join(SRC, fn), os.path.join(DST, fn))
    open(os.path.join(DST, PCB), 'w', newline='\r\n').write(out)

    for fn in ('BMS.kicad_sch', 'smart_sensor_battery.kicad_sch'):
        p = os.path.join(DST, fn); txt = open(p).read()
        for ref, fnc in (('BT1', lambda b: re.sub(r'\(property "Footprint" "[^"]*"', '(property "Footprint" "Library:%s"' % LIBNAME,
                                                   re.sub(r'\(property "Value" "[^"]*"', '(property "Value" "1096/1042/1121"', b, count=1), count=1)),
                         ('R1', lambda b: re.sub(r'\(property "LCSC Part no\." "[^"]*"', '(property "LCSC Part no." "C11702"',
                                                  re.sub(r'\(property "Value" "[^"]*"', '(property "Value" "1k"', b, count=1), count=1))):
            key = '\n\t\t(property "Reference" "%s"' % ref
            if key not in txt: continue
            k = txt.index(key); ss = txt.rindex('\n\t(symbol', 0, k) + 1; se = block_span(txt, ss)
            txt = txt[:ss] + fnc(txt[ss:se]) + txt[se:]
        open(p, 'w', newline='\r\n').write(txt)

    # Library copy of the footprint (origin = ORIGIN, as placed on the back of the board).
    lib = ['(footprint "%s"\n\t(version 20241229)\n\t(generator "pcbnew")\n\t(generator_version "9.0")\n\t(layer "B.Cu")\n' % LIBNAME,
           '\t(descr "Universal pads for a Keystone 1096 (18350), 1042 (18650) or 1121 (21700) holder, back side")\n',
           '\t(property "Reference" "BT1" (at 0 0 0) (layer "B.SilkS") (uuid %s) (effects (font (size 1 1) (thickness 0.15)) (justify mirror)))\n' % uid(),
           '\t(property "Value" "%s" (at 0 2 0) (layer "B.Fab") (uuid %s) (effects (font (size 1 1) (thickness 0.15)) (justify mirror)))\n' % (LIBNAME, uid())]
    lib.append(footprint_body(nets).replace('\n\t\t', '\n\t').replace(' (net %s "%s")' % nets['P'], '')
               .replace(' (net %s "%s")' % nets['N'], '').rstrip('\t)') + '\n)\n')
    open(os.path.join(ROOT, 'Library.pretty', LIBNAME + '.kicad_mod'), 'w').write(''.join(lib))
    print('wrote', os.path.relpath(DST, ROOT), '- board 124..148 x %.2f..111.5 (%.2f x 24 mm)' % (NEW_TOP, 111.5 - NEW_TOP))


if __name__ == '__main__':
    main()
