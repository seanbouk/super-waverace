import csv, sys
d = sys.argv[1]
for tag in sys.argv[2:]:
    rows = [list(map(int, r)) for r in csv.reader(open(d + '/' + tag + '_vtrace.csv'))]
    rows = [r for r in rows if 60 <= r[1] <= 900]
    seen = {}
    for r in rows: seen[r[1]] = r
    rows = list(seen.values())
    grav = rows[-1][5]
    h = [r[2] - r[3] for r in rows]
    air = [x for x in h if x > 0]
    vvup = [r[4] for r in rows if r[4] > 0]
    runs, n = [], 0
    for x in h:
        if x > 0: n += 1
        elif n: runs.append(n); n = 0
    print("%-8s grav=%2d ticks=%4d airborne=%4.1f%% maxHeight=%5.2f texels meanAirH=%4.2f maxVvUp=%4.2f tex/loop longestAir=%2d ticks (~%.1fs) jumps=%d" % (
        tag, grav, len(rows), 100 * len(air) / len(rows), max(h) / 256, (sum(air) / len(air) / 256 if air else 0),
        max(vvup) / 256 if vvup else 0, max(runs) if runs else 0, (max(runs) if runs else 0) * 3.2 / 60, len(runs)))
