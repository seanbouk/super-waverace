"""validate_course - sanity-check a course.json before baking.

Encodes every structural rule the bake ASSERTS on (so a pass here means
the bake won't die on your map) plus the softer lints that only print
WARNINGs (buoy label vs racing-line side, out-of-range coords, sky
horizon brightness). It does NOT compute the 256-tile budget or the
palette shade-pair collapse - those need the full compose/quantise, so
the bake's own report is the authority there; this is the fast
"is my JSON sane" gate for authoring lots of maps.

Usage:
  python tools/validate_course.py <path/to/course.json>
  python tools/validate_course.py            # all assets/courses/*/course.json

Exit code is non-zero if any ERROR (bake-fatal) was found.
"""
import glob
import json
import math
import os
import sys

MAX_BUOYS = 16          # bake_tables.MAX_BUOYS
MAX_PATH = 24           # bake_tables.MAX_PATH
GRID = 128
COORD_MAX = 1023        # painter texel space (0..1023)
MENU_NAME_MAX = 20

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
WAVES = os.path.join(ROOT, "assets", "waves")

# valid palette roles (bake_tables PALETTE_ROLES + FADE_ROLES + SKY_ROLES)
PALETTE_ROLES = {
    "sand", "sand_shade", "foam", "wet_sand", "float", "calm", "teal",
    "teal_sand", "check_dark", "check_white",
}
FADE_ROLES = {"sand_far", "sand_deep"}
SKY_ROLES = {"sky", "sky_horizon"}
ALL_ROLES = PALETTE_ROLES | FADE_ROLES | SKY_ROLES


class Report:
    def __init__(self, name):
        self.name = name
        self.errors = []
        self.warns = []
        self.info = []

    def err(self, m):
        self.errors.append(m)

    def warn(self, m):
        self.warns.append(m)

    def note(self, m):
        self.info.append(m)

    def dump(self):
        print("=" * 68)
        print(self.name)
        print("=" * 68)
        for m in self.info:
            print("  info  " + m)
        for m in self.warns:
            print("  WARN  " + m)
        for m in self.errors:
            print("  ERROR " + m)
        if not self.errors and not self.warns:
            print("  OK - no problems found")
        elif not self.errors:
            print("  -> valid (warnings only; the bake will still run)")
        else:
            print("  -> INVALID: fix the ERRORs or the bake will fail")
        print()


def parse_rgb(v):
    """mirror of bake_tables.parse_rgb; returns (r,g,b) or raises ValueError."""
    if isinstance(v, str):
        s = v.lstrip("#")
        if len(s) != 6:
            raise ValueError("must be #rrggbb")
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return tuple(int(x) for x in v)
    raise ValueError("must be #rrggbb or [r,g,b]")


def in_range(x, y):
    return 0 <= x <= COORD_MAX and 0 <= y <= COORD_MAX


def check_zones(c, r):
    z = c.get("zones")
    if not isinstance(z, list):
        r.err("'zones' missing or not a list")
        return None
    if len(z) != GRID or not all(isinstance(row, str) and len(row) == GRID
                                 for row in z):
        r.err("'zones' must be %d rows of exactly %d chars" % (GRID, GRID))
        return None
    bad = set(ch for row in z for ch in row) - {"w", "s"}
    if bad:
        r.warn("zones contains chars other than w/s: %r - anything not "
               "'s' is treated as open water, nothing special" % sorted(bad))
    land = sum(row.count("s") for row in z)
    r.note("zones: %d land cells / %d (%.0f%% land)"
           % (land, GRID * GRID, 100.0 * land / (GRID * GRID)))
    if land == 0:
        r.warn("no 's' (land) cells at all - an open-water course with no "
               "shoreline. Legal, but check it's intended.")
    return z


def check_path(c, r):
    p = c.get("path")
    if not isinstance(p, list):
        r.err("'path' (racing line) missing or not a list")
        return None
    if len(p) < 2:
        r.err("racing line needs >= 2 waypoints (bake asserts this)")
    if len(p) > MAX_PATH:
        r.err("racing line has %d waypoints; max is %d (WAVE_MAX_PATH)"
              % (len(p), MAX_PATH))
    oob = []
    for i, pt in enumerate(p):
        if not (isinstance(pt, list) and len(pt) == 2
                and all(isinstance(v, int) for v in pt)):
            r.err("path[%d] is not an [int x, int y] pair: %r" % (i, pt))
            continue
        if not in_range(*pt):
            oob.append(i)
    if oob:
        r.warn("path waypoints outside 0..%d (wrap-masked in game, but "
               "usually a mistake): indices %s" % (COORD_MAX, oob))
    if len(p) >= 2 and p[0] == p[1]:
        r.warn("path[0] == path[1]: the start/finish line direction is "
               "degenerate - give the opening segment a direction.")
    r.note("racing line: %d waypoints (point 0 = start/finish)" % len(p))
    return p


def check_buoys(c, r):
    b = c.get("buoys", [])
    if not isinstance(b, list):
        r.err("'buoys' is not a list")
        return []
    if len(b) > MAX_BUOYS:
        r.err("%d buoys; max is %d (WAVE_MAX_BUOYS)" % (len(b), MAX_BUOYS))
    clean = []
    for i, entry in enumerate(b):
        if not (isinstance(entry, list) and len(entry) == 3):
            r.err("buoys[%d] must be [x, y, 'L'|'R']: %r" % (i, entry))
            continue
        x, y, side = entry
        if not (isinstance(x, int) and isinstance(y, int)):
            r.err("buoys[%d] x/y must be ints: %r" % (i, entry))
            continue
        if side not in ("L", "R"):
            r.warn("buoys[%d] side %r is not 'L'/'R' - the bake treats "
                   "anything != 'R' as Left." % (i, side))
        if not in_range(x, y):
            r.warn("buoys[%d] at (%d,%d) is outside 0..%d" % (i, x, y, COORD_MAX))
        clean.append((x, y, side))
    r.note("buoys: %d" % len(b))
    return clean


def check_ropes(c, r):
    ropes = c.get("ropes", [])
    if not isinstance(ropes, list):
        r.err("'ropes' is not a list")
        return
    pts = 0
    for i, rope in enumerate(ropes):
        if not (isinstance(rope, list) and len(rope) >= 2):
            r.err("ropes[%d] must be a polyline of >= 2 points: %r" % (i, rope))
            continue
        for pt in rope:
            if not (isinstance(pt, list) and len(pt) == 2
                    and all(isinstance(v, int) for v in pt)):
                r.err("ropes[%d] has a bad point: %r" % (i, pt))
                break
            pts += 1
    r.note("ropes: %d polyline(s), %d points" % (len(ropes), pts))


def check_style(c, r):
    pal = c.get("palette")
    if pal is not None:
        if not isinstance(pal, dict):
            r.err("'palette' must be an object of role -> colour")
        else:
            for role, v in pal.items():
                if role not in ALL_ROLES:
                    r.err("unknown palette role %r (bake asserts). Valid: %s"
                          % (role, ", ".join(sorted(ALL_ROLES))))
                    continue
                try:
                    rgb = parse_rgb(v)
                except ValueError as e:
                    r.err("palette.%s: %s" % (role, e))
                    continue
                if role == "sky_horizon" and min(rgb) < 112:
                    r.warn("palette.sky_horizon channel < 112 - the mode-7 "
                           "strip can't reach it; the bake will warn and the "
                           "horizon seam may band. Raise every channel >= 112.")
    amb = c.get("ambient")
    if amb is not None:
        try:
            parse_rgb(amb)
        except ValueError as e:
            r.err("ambient: %s" % e)
    wp = c.get("wave_profile")
    if wp is not None:
        f = os.path.join(WAVES, str(wp) + ".json")
        if not os.path.exists(f):
            avail = sorted(os.path.splitext(os.path.basename(p))[0]
                           for p in glob.glob(os.path.join(WAVES, "*.json")))
            r.err("wave_profile %r has no assets/waves/%s.json (available: %s)"
                  % (wp, wp, ", ".join(avail) or "none"))
        else:
            r.note("wave_profile: %s" % wp)


def lint_buoy_sides(buoys, path, r):
    """Replicate bake_tables.order_gates' label lint. Both buoys and path
    are X-mirrored (1023-x) at load, so mirror here too before the
    cross-product test or the handedness is wrong."""
    if not buoys or len(path) < 2:
        return
    bm = [(COORD_MAX - x, y, side) for x, y, side in buoys]
    pm = [(COORD_MAX - x, y) for x, y in path]
    n = len(pm)
    for i, (bx, by, side) in enumerate(bm):
        best = None
        for k in range(n):
            ax, ay = pm[k]
            dx, dy = pm[(k + 1) % n][0] - ax, pm[(k + 1) % n][1] - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else min(1.0, max(0.0, (
                (bx - ax) * dx + (by - ay) * dy) / L2))
            px, py = ax + t * dx, ay + t * dy
            d2 = (bx - px) ** 2 + (by - py) ** 2
            if best is None or d2 < best[0]:
                best = (d2, dx, dy, px, py)
        _, dx, dy, px, py = best
        line_side = dx * (py - by) - dy * (px - bx)
        want_left = side != "R"
        if (line_side > 0) != want_left:
            r.warn("buoy %d labelled %r but the racing line passes it on the "
                   "OTHER side - the bake will warn; re-check the label or "
                   "the line." % (i, side))


def validate(path):
    r = Report(os.path.relpath(path, ROOT) if path.startswith(ROOT) else path)
    try:
        with open(path) as f:
            c = json.load(f)
    except FileNotFoundError:
        r.err("file not found")
        return r
    except json.JSONDecodeError as e:
        r.err("invalid JSON: %s" % e)
        return r
    if not isinstance(c, dict):
        r.err("top level must be a JSON object")
        return r

    check_zones(c, r)
    path_pts = check_path(c, r)
    buoys = check_buoys(c, r)
    check_ropes(c, r)
    check_style(c, r)
    if path_pts and buoys:
        lint_buoy_sides(buoys, path_pts, r)

    # folder-name menu-name check only applies in an assets/courses/ layout
    d = os.path.basename(os.path.dirname(path))
    if "_" in d:
        name = d.split("_", 1)[1].replace("_", " ").upper()
        if len(name) > MENU_NAME_MAX:
            r.err("menu name %r (from folder) is > %d chars"
                  % (name, MENU_NAME_MAX))
    return r


def main(argv):
    if argv:
        targets = argv
    else:
        targets = sorted(glob.glob(os.path.join(
            ROOT, "assets", "courses", "*", "course.json")))
        if not targets:
            print("no course.json given and none under assets/courses/")
            return 2
    bad = 0
    for t in targets:
        rep = validate(t)
        rep.dump()
        if rep.errors:
            bad += 1
    print("%d file(s) checked, %d with errors." % (len(targets), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
