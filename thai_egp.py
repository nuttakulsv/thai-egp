#!/usr/bin/env python3
"""Thai government procurement data (e-GP) from data.go.th: download, clean, load, search."""
import argparse
import csv
import http.client
import io
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

__version__ = "0.1.0"

CKAN = "https://data.go.th/api/3/action/"
UA = {"User-Agent": "thai-egp/" + __version__}

# Package names on data.go.th are not consistent (cgd-, cdg-, egp-contact-).
DATASETS = {
    2558: "cgd-contract-2558", 2559: "cgd-contract-2559", 2560: "cgd-contract-2560",
    2561: "cgd-contract-2561", 2562: "cgd-contract-2562", 2563: "cgd-contract-2563",
    2564: "cdg-contract-2564", 2565: "cdg-contract-2565", 2566: "cdg-contract-2566",
    2567: "cdg-contract-2567", 2568: "egp-contact-2568",
}
TITLE = "ข้อมูลโครงการจัดซื้อจัดจ้างจากระบบการจัดซื้อจัดจ้างภาครัฐ ปีงบประมาณ {}"

FIELDS = [
    "project_id", "project_name", "project_type", "dept", "subdept",
    "method", "method_kind", "category",
    "announce_date", "budget", "reference_price", "agreed_price", "fiscal_year", "transaction_date",
    "province", "district", "subdistrict", "project_status", "lat", "lon",
    "winner_id", "winner_prefix", "winner_juristic", "winner_name", "contract_no", "contract_date", "contract_end",
    "contract_amount", "amount", "contract_status",
]

# Column positions of the two layouts found in the files.
# Wide (2558-2567): 31 columns with English place names, sometimes followed by empty columns.
# Narrow (2568): 28 columns without English names. The header line of these files is wrong
# (26 or 31 names), so rows are read by position, never by header.
WIDE = dict(project_id=1, project_name=2, project_type=3, dept=4, subdept=5, m1=6, m2=7,
            announce_date=8, budget=9, reference_price=10, agreed_price=11, fiscal_year=12,
            transaction_date=13, province=14, district=16, subdistrict=18, project_status=20,
            point=21, lat=22, lon=23, winner_id=24, winner_name=25, contract_no=26,
            contract_date=27, contract_end=28, contract_amount=29, contract_status=30)
NARROW = dict(project_id=1, project_name=2, project_type=3, dept=4, subdept=5, m1=6, m2=7,
              announce_date=8, budget=9, reference_price=10, agreed_price=11, fiscal_year=12,
              transaction_date=13, province=14, district=15, subdistrict=16, project_status=17,
              point=18, lat=19, lon=20, winner_id=21, winner_name=22, contract_no=23,
              contract_date=24, contract_end=25, contract_amount=26, contract_status=27)

MONTHS = {m: i for i, m in enumerate(
    "ม.ค. ก.พ. มี.ค. เม.ย. พ.ค. มิ.ย. ก.ค. ส.ค. ก.ย. ต.ค. พ.ย. ธ.ค.".split(), 1)}
THAI_DATE = re.compile(r"(\d{1,2})[\s-]*([^\s\d-]+?\.?)[\s-]*(\d{2}|\d{4})$")
SPACES = re.compile(r"\s+")
ACT_2560 = date(2017, 8, 23)  # the 2560 Procurement Act took effect; เฉพาะเจาะจง replaced ตกลงราคา
CAP = {"specific": 500_000, "price_agreement": 100_000}


def thai_date(s):
    """'29-ก.ค.-58' or '29 พ.ย. 67' (Buddhist era) -> 'YYYY-MM-DD', else None."""
    m = THAI_DATE.match(s.strip())
    if not m or m.group(2) not in MONTHS:
        return None
    y = int(m.group(3))
    y = (2500 + y if y < 100 else y) - 543
    try:
        return date(y, MONTHS[m.group(2)], int(m.group(1))).isoformat()
    except ValueError:
        return None


def money(s):
    s = s.replace(",", "").strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def text(s):
    return SPACES.sub(" ", s).strip()


def winner_id(s):
    """13-digit IDs are kept. Masked IDs of individuals ('352050022xxxx') are kept as they are.
    IDs that went through Excel ('9.94E+11') lost their digits and become None."""
    s = s.strip()
    if not s or "E+" in s.upper() or s == "-":
        return None
    return s


def is_category(s):
    return s.startswith("วิธีการจัดหา") or s.startswith("งาน")


def method_kind(m):
    for key, words in (("ebidding", ("e-bidding", "ประกวดราคา")), ("emarket", ("e-market", "ตลาดอิเล็กทรอนิกส์")),
                       ("specific", ("เฉพาะเจาะจง",)), ("selection", ("คัดเลือก",)),
                       ("price_agreement", ("ตกลง",)), ("price_quotation", ("สอบราคา",)),
                       ("special", ("พิเศษ",)), ("open", ("ประกาศเชิญชวน",))):
        if any(w in m for w in words):
            return key
    return "other" if m else None


def coord(v, lo, hi):
    try:
        f = float(v)
    except ValueError:
        return None
    return f if lo <= f <= hi else None


def clean(row):
    """One raw CSV row (list of strings) -> dict with FIELDS, or None if the row doesn't fit a layout."""
    n = len(row)
    if n == 28:
        c = NARROW
    elif n >= 31 and not any(v.strip() for v in row[31:]):
        c = WIDE
    else:
        return None
    if not re.fullmatch(r"25\d\d", row[c["fiscal_year"]].strip()):
        return None
    g = lambda k: row[c[k]]
    m1, m2 = text(g("m1")), text(g("m2"))
    method, category = (m2, m1) if is_category(m1) and not is_category(m2) else (m1, m2)
    lat, lon = coord(g("lat"), 5, 21), coord(g("lon"), 97, 106)
    if lat is None or lon is None:
        p = re.match(r"POINT\(([\d.]+) ([\d.]+)\)", g("point").strip())
        lat, lon = (coord(p.group(2), 5, 21), coord(p.group(1), 97, 106)) if p else (None, None)
    r = dict(
        project_id=g("project_id").strip(), project_name=text(g("project_name")),
        project_type=text(g("project_type")), dept=text(g("dept")), subdept=text(g("subdept")),
        method=method, method_kind=method_kind(method), category=category,
        announce_date=thai_date(g("announce_date")), budget=money(g("budget")),
        reference_price=money(g("reference_price")), agreed_price=money(g("agreed_price")),
        fiscal_year=int(g("fiscal_year")), transaction_date=thai_date(g("transaction_date")),
        province=text(g("province")), district=text(g("district")), subdistrict=text(g("subdistrict")),
        project_status=text(g("project_status")), lat=lat, lon=lon,
        winner_id=winner_id(g("winner_id")), winner_name=text(g("winner_name")),
        contract_no=text(g("contract_no")), contract_date=thai_date(g("contract_date")),
        contract_end=thai_date(g("contract_end")), contract_amount=money(g("contract_amount")),
        contract_status=text(g("contract_status")),
    )
    r["amount"] = r["contract_amount"] if r["contract_amount"] is not None else r["agreed_price"]
    digits = juristic_digits(g("winner_id").strip(), r["winner_name"])
    r["winner_juristic"] = int(digits is not None)
    r["winner_prefix"] = "0" + digits[:5] if digits and "?" not in digits[:5] else None
    return r


# ---------- data.go.th ----------

def ckan(action, **params):
    url = CKAN + action + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        d = json.load(r)
    if not d.get("success"):
        raise RuntimeError(f"data.go.th: {action} failed")
    return d["result"]


def dataset(year):
    """CKAN package for a fiscal year (Buddhist era)."""
    if year in DATASETS:
        return ckan("package_show", id=DATASETS[year])
    want = TITLE.format(year)
    for p in ckan("package_search", q=f'"{want}"', rows=20)["results"]:
        if p["title"].strip() == want:
            return p
    raise SystemExit(f"egp: no e-GP contract dataset for fiscal year {year} on data.go.th yet")


def parts(year):
    return [(r["name"].removesuffix(".csv") + ".csv", r["url"], r.get("size"))
            for r in dataset(year)["resources"] if (r.get("format") or "").upper() == "CSV"]


def open_text(src):
    """Local path or URL -> text stream (BOM removed, newlines left to the csv module)."""
    if re.match(r"https?://", src):
        raw = urllib.request.urlopen(urllib.request.Request(src, headers=UA), timeout=120)
    else:
        raw = open(src, "rb")
    return io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")


def read(src, bad=None):
    """Yield cleaned rows from a CSV path or URL. Rows that don't fit are counted in bad[0]."""
    with open_text(src) as f:
        reader = csv.reader(f)
        next(reader, None)  # the header is not trusted, see NARROW
        for row in reader:
            r = clean(row)
            if r:
                yield r
            elif bad is not None and any(v.strip() for v in row):
                bad[0] += 1


def rows(source):
    """Python API: rows(2568) streams a whole fiscal year from data.go.th; rows('file.csv') reads a file."""
    if isinstance(source, int):
        for _, url, _ in parts(source):
            yield from read(url)
    else:
        yield from read(source)


def expand(sources):
    """CLI arguments -> [(name, path-or-url)]. A 4-digit Buddhist year means every part of that year."""
    out = []
    for s in sources:
        if re.fullmatch(r"25\d\d", s):
            out += [(name, url) for name, url, _ in parts(int(s))]
        else:
            out.append((os.path.basename(s), s))
    return out


def download(year, folder):
    os.makedirs(folder, exist_ok=True)
    for name, url, size in parts(year):
        path = os.path.join(folder, name)
        have = os.path.getsize(path) if os.path.exists(path) else 0
        if size and have == size:
            print(f"{name}: done", file=sys.stderr)
            continue
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={have}-"} if have else UA)
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "ab" if r.status == 206 else "wb") as f:
            got = have if r.status == 206 else 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if size:
                    print(f"\r{name}: {got * 100 // size}%", end="", file=sys.stderr)
        print(f"\r{name}: {got // 1_000_000} MB", file=sys.stderr)


# ---------- SQLite ----------

def connect(path):
    db = sqlite3.connect(path)
    db.execute(f"CREATE TABLE IF NOT EXISTS contracts ({', '.join(FIELDS)}, source)")
    db.execute("CREATE TABLE IF NOT EXISTS loaded (source PRIMARY KEY, rows, bad_rows, loaded_at)")
    return db


def load(db, sources, force=False):
    db.execute("PRAGMA synchronous = OFF")
    done = {s for s, in db.execute("SELECT source FROM loaded")}
    sql = f"INSERT INTO contracts VALUES ({', '.join('?' * (len(FIELDS) + 1))})"
    for name, src in sources:
        if name in done and not force:
            print(f"{name}: already loaded (use --force to reload)", file=sys.stderr)
            continue
        bad, n, batch = [0], 0, []
        with db:
            db.execute("DELETE FROM contracts WHERE source = ?", (name,))
            for r in read(src, bad):
                batch.append([r[k] for k in FIELDS] + [name])
                if len(batch) == 20_000:
                    db.executemany(sql, batch)
                    n += len(batch)
                    batch = []
                    print(f"\r{name}: {n:,} rows", end="", file=sys.stderr)
            db.executemany(sql, batch)
            n += len(batch)
            db.execute("INSERT OR REPLACE INTO loaded VALUES (?, ?, ?, datetime('now'))", (name, n, bad[0]))
        print(f"\r{name}: {n:,} rows" + (f", {bad[0]:,} rows skipped (wrong shape)" if bad[0] else ""),
              file=sys.stderr)
    for col in ("fiscal_year", "dept", "subdept", "winner_name", "province", "project_id"):
        db.execute(f"CREATE INDEX IF NOT EXISTS i_{col} ON contracts ({col})")
    db.commit()


def where(a):
    sql, args = ["1"], []
    if a.year:
        sql.append(f"fiscal_year IN ({', '.join('?' * len(a.year))})")
        args += a.year
    for col, val in (("winner_name", a.winner), ("province", a.province)):
        if val:
            sql.append(f"{col} LIKE ?")
            args.append(f"%{val}%")
    if a.dept:
        sql.append("(dept LIKE ? OR subdept LIKE ?)")
        args += [f"%{a.dept}%"] * 2
    if getattr(a, "method", None):
        sql.append("method_kind = ?")
        args.append(a.method)
    return " AND ".join(sql), args


def baht(x):
    return f"{x:,.0f}" if x is not None else "-"


def emit(cur, as_csv, show):
    names = [d[0] for d in cur.description]
    if as_csv:
        w = csv.writer(sys.stdout)
        w.writerow(names)
        w.writerows(cur)
        return
    n = 0
    for row in cur:
        print(show(dict(zip(names, row))))
        n += 1
    if not n:
        print("ไม่พบข้อมูล / nothing found", file=sys.stderr)


def cmd_search(db, a):
    w, args = where(a)
    for word in a.words:
        w += " AND project_name LIKE ?"
        args.append(f"%{word}%")
    if a.min is not None:
        w += " AND amount >= ?"
        args.append(a.min)
    if a.max is not None:
        w += " AND amount <= ?"
        args.append(a.max)
    cur = db.execute(f"SELECT project_id, project_name, dept, subdept, province, method, winner_name, "
                     f"reference_price, amount, contract_date, fiscal_year FROM contracts WHERE {w} "
                     f"ORDER BY amount DESC LIMIT ?", args + [a.limit])
    emit(cur, a.csv, lambda r: (
        f"{r['project_id']}  {baht(r['amount'])} บาท  {r['method']}  {r['contract_date'] or r['fiscal_year']}\n"
        f"  {r['project_name']}\n  {r['subdept'] or r['dept']}, {r['province']} -> {r['winner_name'] or '-'}\n"))


def cmd_top(db, a):
    col = {"dept": "dept", "subdept": "subdept", "winner": "winner_name", "province": "province",
           "method": "method_kind", "type": "project_type", "year": "fiscal_year"}[a.by]
    w, args = where(a)
    cur = db.execute(f"SELECT {col} AS name, COUNT(DISTINCT project_id) AS projects, SUM(amount) AS baht "
                     f"FROM contracts WHERE {w} GROUP BY 1 ORDER BY 3 DESC LIMIT ?", args + [a.limit])
    emit(cur, a.csv, lambda r: f"{baht(r['baht']):>18}  {r['projects']:>9,}  {r['name'] or '-'}")


# ---------- red flags ----------

FLAG_NOTE = ("สัญญาณให้ไปดูต่อ ไม่ใช่หลักฐานว่าทุจริต หลายรายการมีเหตุผลที่ถูกต้องตามระเบียบ\n"
             "Signals worth a closer look, not evidence of wrongdoing.\n")


def flag_where(a):
    """Flags only look at companies and other juristic persons, never at people by name."""
    w, args = where(a)
    return w + " AND winner_juristic = 1", args


def flag_split(db, a):
    """Three or more direct-purchase projects from the same unit to the same winner, of the same type,
    within 30 days, each under the direct-purchase cap but together over it."""
    w, args = flag_where(a)
    cur = db.execute(f"""
        SELECT fiscal_year, dept, subdept, winner_name, project_type, method_kind, project_id,
               MIN(COALESCE(contract_date, transaction_date)) AS d, SUM(amount) AS amt
        FROM contracts WHERE {w} AND method_kind IN ('specific', 'price_agreement') AND winner_name <> ''
        GROUP BY project_id, winner_name HAVING d IS NOT NULL
        ORDER BY subdept, winner_name, project_type, method_kind, d""", args)
    out, group, key = [], [], None

    def scan(g):
        best, i = None, 0
        for j in range(len(g)):
            while date.fromisoformat(g[j][7]) - date.fromisoformat(g[i][7]) > timedelta(days=30):
                i += 1
            win = g[i:j + 1]
            total = sum(x[8] or 0 for x in win)
            if len(win) >= 3 and total > CAP[g[j][5]] and (not best or total > best[1]):
                best = (win, total)
        if best:
            win, total = best
            r = win[0]
            out.append(dict(flag="split", fiscal_year=r[0], dept=r[1], subdept=r[2], winner=r[3],
                            projects=len(win), baht=total,
                            detail=f"{r[4]}, {win[0][7]} .. {win[-1][7]}, cap {CAP[r[5]]:,}: "
                                   + " ".join(x[6] for x in win)))

    for r in cur:
        k = r[2], r[3], r[4], r[5]
        if k != key:
            scan(group)
            group, key = [], k
        if r[8] is not None and r[8] < CAP[r[5]]:
            group.append(r)
    scan(group)
    return sorted(out, key=lambda x: -x["baht"])[:a.limit]


def flag_near_reference(db, a):
    """Competitive procedures where the winning price is within 1% of the reference price (ราคากลาง)."""
    w, args = flag_where(a)
    cur = db.execute(f"""
        SELECT fiscal_year, dept, subdept, winner_name, project_id, MAX(reference_price) AS ref,
               MAX(agreed_price) AS price, project_name
        FROM contracts WHERE {w} AND method_kind IN ('ebidding', 'selection', 'emarket')
        GROUP BY project_id HAVING ref >= ? AND price > 0 AND price <= ref AND price >= ref * ?
        ORDER BY ref DESC LIMIT ?""", args + [a.min_baht, 1 - a.near, a.limit])
    return [dict(flag="near_reference", fiscal_year=r[0], dept=r[1], subdept=r[2], winner=r[3], projects=1,
                 baht=r[6], detail=f"{r[4]} ราคากลาง {baht(r[5])}, ชนะ {baht(r[6])} "
                                   f"({(1 - r[6] / r[5]) * 100:.2f}% ต่ำกว่า): {r[7][:80]}") for r in cur]


def flag_concentration(db, a):
    """One winner takes at least half of a unit's contract value in a fiscal year."""
    w, args = flag_where(a)
    cur = db.execute(f"""
        WITH t AS (SELECT fiscal_year, dept, subdept, winner_name, SUM(amount) AS amt,
                          COUNT(DISTINCT project_id) AS n
                   FROM contracts WHERE {w} AND winner_name <> '' GROUP BY 1, 2, 3, 4),
             s AS (SELECT fiscal_year, dept, subdept, SUM(amt) AS total, COUNT(*) AS winners FROM t GROUP BY 1, 2, 3)
        SELECT t.fiscal_year, t.dept, t.subdept, t.winner_name, t.n, t.amt, s.total, s.winners
        FROM t JOIN s USING (fiscal_year, dept, subdept)
        WHERE s.total >= ? AND s.winners >= 3 AND t.n >= 3 AND t.amt >= 0.5 * s.total
        ORDER BY t.amt DESC LIMIT ?""", args + [a.min_baht, a.limit])
    return [dict(flag="concentration", fiscal_year=r[0], dept=r[1], subdept=r[2], winner=r[3], projects=r[4],
                 baht=r[5], detail=f"{r[5] / r[6] * 100:.0f}% ของ {baht(r[6])} บาท ({r[7]} ผู้ชนะ)") for r in cur]


def flag_direct_over_cap(db, a):
    """เฉพาะเจาะจง above 500,000 baht. Allowed only for listed reasons (urgency, sole supplier, ...)."""
    w, args = flag_where(a)
    cur = db.execute(f"""
        SELECT fiscal_year, dept, subdept, winner_name, project_id, MAX(COALESCE(reference_price, budget)) AS ref,
               SUM(amount) AS amt, project_name, MIN(COALESCE(contract_date, transaction_date)) AS d
        FROM contracts WHERE {w} AND method_kind = 'specific'
        GROUP BY project_id HAVING ref > ? AND ref >= ? AND (d IS NULL OR d >= ?)
        ORDER BY ref DESC LIMIT ?""", args + [CAP["specific"], a.min_baht, ACT_2560.isoformat(), a.limit])
    return [dict(flag="direct_over_cap", fiscal_year=r[0], dept=r[1], subdept=r[2], winner=r[3], projects=1,
                 baht=r[6], detail=f"{r[4]} ราคากลาง {baht(r[5])}: {r[7][:80]}") for r in cur]


FLAGS = {"split": flag_split, "near_reference": flag_near_reference,
         "concentration": flag_concentration, "direct_over_cap": flag_direct_over_cap}


def cmd_flags(db, a):
    found = [f for k in (a.only or FLAGS) for f in FLAGS[k](db, a)]
    if a.csv:
        w = csv.DictWriter(sys.stdout, ["flag", "fiscal_year", "dept", "subdept", "winner", "projects", "baht", "detail"])
        w.writeheader()
        w.writerows(found)
        return
    print(FLAG_NOTE, file=sys.stderr)
    for f in found:
        print(f"[{f['flag']}] {f['fiscal_year']}  {baht(f['baht'])} บาท  {f['projects']} โครงการ\n"
              f"  {f['subdept'] or f['dept']} -> {f['winner'] or '-'}\n  {f['detail']}\n")
    if not found:
        print("ไม่พบสัญญาณตามเงื่อนไขนี้ / no flags for these filters", file=sys.stderr)


# ---------- winners across the whole country, streamed (no database needed) ----------

COMPANY_WORDS = re.compile(r"บริษัท|บจก|บมจ|ห้างหุ้นส่วน|หจก|หกจ|กิจการร่วมค้า|กิจการค้าร่วม|สหกรณ์|องค์การ|จำกัด")
ENTITY = {"5": "บริษัทจำกัด", "7": "บริษัทมหาชน", "3": "ห้างหุ้นส่วนจำกัด", "2": "ห้างหุ้นส่วนสามัญนิติบุคคล"}


def juristic_digits(wid, name):
    """The 12 digits after the leading 0 of a juristic person's ID ('?' where lost), or None for people.
    Excel turned most IDs before 2568 into '1.05522E+11'; the first five digits (registrar office,
    entity type, registration decade) survive that and tell same-named companies apart."""
    if wid and re.fullmatch(r"0\d{12}", wid):
        return wid[1:]
    if wid and "E+" in wid.upper():
        try:
            n = int(float(wid))
        except ValueError:
            return None
        return f"{n:012d}"[:5] + "?" * 7 if n < 10 ** 12 else None
    if wid and ("x" in wid.lower() or re.fullmatch(r"[1-9]\d{12}", wid)):
        return None  # a person
    return "?" * 12 if COMPANY_WORDS.search(name) else None


def name_key(name):
    s = re.sub(r"\s|\.|,", "", name)
    s = re.sub(r"\(?สำนักงานใหญ่\)?|\(มหาชน\)|มหาชน", "", s)
    s = re.sub(r"^(บริษัท|บจก|บมจ|ห้างหุ้นส่วนจำกัด|หจก|หกจ)", "", s)
    return re.sub(r"จำกัด$", "", s)


def tally(src):
    """Aggregate one CSV (path or URL) by juristic winner. Returns a JSON-able dict."""
    w, years, bad, odd = {}, {}, [0], []
    for r in read(src, bad):
        amt = r["amount"] or 0
        y = years.setdefault(str(r["fiscal_year"]), {})
        k = r["method_kind"] or "none"
        y[k] = [y.get(k, [0, 0])[0] + 1, y.get(k, [0, 0])[1] + amt]
        if not r["winner_juristic"] or not r["winner_name"]:
            continue
        ceiling = max(r["budget"] or 0, r["reference_price"] or 0)
        if amt > 1e7 and ceiling and amt > 2 * ceiling:
            odd.append([r["project_id"], r["winner_name"], amt, ceiling])  # likely a typo, left out
            continue
        key = name_key(r["winner_name"]) + "|" + (r["winner_prefix"] or "0?????")[1:]
        wid = r["winner_id"] if r["winner_id"] and re.fullmatch(r"0\d{12}", r["winner_id"]) else None
        e = w.get(key)
        if e is None:
            e = w[key] = dict(n=0, baht=0, spec_n=0, spec_baht=0, agree_n=0, agree_baht=0,
                              y0=9999, y1=0, ids={}, names={}, depts={})
        e["n"] += 1
        e["baht"] += amt
        if k == "specific":
            e["spec_n"] += 1
            e["spec_baht"] += amt
        elif k == "price_agreement":
            e["agree_n"] += 1
            e["agree_baht"] += amt
        e["y0"], e["y1"] = min(e["y0"], r["fiscal_year"]), max(e["y1"], r["fiscal_year"])
        if wid:
            e["ids"][wid] = e["ids"].get(wid, 0) + 1
        e["names"][r["winner_name"]] = e["names"].get(r["winner_name"], 0) + 1
        d = r["subdept"] or r["dept"]
        e["depts"][d] = e["depts"].get(d, 0) + amt
    for e in w.values():  # keep the top 10 units per file; enough for a "main buyers" column
        e["depts"] = dict(sorted(e["depts"].items(), key=lambda x: -x[1])[:10])
    return dict(winners=w, years=years, bad=bad[0], odd=odd)


def _tally_cached(job):
    name, src, cache = job
    path = os.path.join(cache, name + ".json") if cache else None
    if path and os.path.exists(path):
        return name, path, None
    for attempt in range(3):
        try:
            t = tally(src)
            break
        except (OSError, http.client.HTTPException, csv.Error) as e:  # dropped connection: start the file again
            err = e
    else:
        return name, None, f"{type(err).__name__}: {err}"
    if not path:
        return name, t, None
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)
    return name, path, None


def merge(parts_):
    W, Y, bad, odd = {}, {}, 0, []
    for t in parts_:
        bad += t["bad"]
        odd += t["odd"]
        for y, kinds in t["years"].items():
            for k, (n, b) in kinds.items():
                v = Y.setdefault(y, {}).setdefault(k, [0, 0])
                v[0] += n
                v[1] += b
        for key, e in t["winners"].items():
            E = W.get(key)
            if E is None:
                W[key] = e
                continue
            for f in ("n", "baht", "spec_n", "spec_baht", "agree_n", "agree_baht"):
                E[f] += e[f]
            E["y0"], E["y1"] = min(E["y0"], e["y0"]), max(E["y1"], e["y1"])
            for f in ("ids", "names", "depts"):
                for k, v in e[f].items():
                    E[f][k] = E[f].get(k, 0) + v
    # Rows without any ID join the one company of that name that has one, if there is exactly one.
    by_name = {}
    for key in W:
        nm, p = key.rsplit("|", 1)
        if "?" not in p:
            by_name.setdefault(nm, []).append(key)
    for key in [k for k in W if k.endswith("|?????")]:
        target = by_name.get(key.rsplit("|", 1)[0], [])
        if len(target) == 1:
            E, e = W[target[0]], W.pop(key)
            for f in ("n", "baht", "spec_n", "spec_baht", "agree_n", "agree_baht"):
                E[f] += e[f]
            E["y0"], E["y1"] = min(E["y0"], e["y0"]), max(E["y1"], e["y1"])
            for f in ("ids", "names", "depts"):
                for k, v in e[f].items():
                    E[f][k] = E[f].get(k, 0) + v
    return W, Y, bad, odd


def winner_rows(W):
    for key, e in W.items():
        wid = max(e["ids"], key=e["ids"].get) if e["ids"] else ""
        prefix = key.rsplit("|", 1)[1]
        etype = ENTITY.get(wid[3] if wid else prefix[2:3], "")
        if (wid[1:3] if wid else prefix[:2]) == "99":
            etype = "เลขจากกรมสรรพากร (หน่วยงานรัฐ สหกรณ์ กิจการร่วมค้า ฯลฯ)"
        top = sorted(e["depts"].items(), key=lambda x: -x[1])[:3]
        yield dict(
            winner_id=wid, id_prefix="0" + prefix if "?" not in prefix else "", entity=etype,
            name=max(e["names"], key=e["names"].get), contracts=e["n"], baht=round(e["baht"], 2),
            specific_contracts=e["spec_n"], specific_baht=round(e["spec_baht"], 2),
            direct_contracts=e["spec_n"] + e["agree_n"], direct_baht=round(e["spec_baht"] + e["agree_baht"], 2),
            first_year=e["y0"], last_year=e["y1"], top_units=" | ".join(d for d, _ in top),
            dbd=f"https://datawarehouse.dbd.go.th/company/profile/{wid[3]}/{wid}" if wid and wid[3] in ENTITY else "")


def cmd_winners(a):
    from multiprocessing import Pool
    years = a.years or [str(y) for y in sorted(DATASETS)]
    if a.cache:
        os.makedirs(a.cache, exist_ok=True)
    jobs = [(name, src, a.cache) for name, src in expand(years)]
    results, failed = [], []
    with Pool(a.jobs) as pool:
        for i, (name, res, err) in enumerate(pool.imap_unordered(_tally_cached, jobs), 1):
            print(f"[{i}/{len(jobs)}] {name}" + (f" FAILED {err}" if err else ""), file=sys.stderr)
            if err:
                failed.append(name)
            else:
                results.append(res)
    loaded = (json.load(open(r, encoding="utf-8")) if isinstance(r, str) else r for r in results)
    W, Y, bad, odd = merge(loaded)
    out = sorted(winner_rows(W), key=lambda r: -r["baht"])
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(out[0]) if out else ["name"])
        w.writeheader()
        w.writerows(out)
    with open(os.path.splitext(a.out)[0] + "-summary.json", "w", encoding="utf-8") as f:
        json.dump(dict(years=Y, rows_skipped=bad, failed=failed,
                       left_out_typos=sorted(odd, key=lambda x: -x[2])), f, ensure_ascii=False, indent=1)
    print(f"{len(out):,} juristic winners -> {a.out}" + (f"; {len(failed)} files failed, run again" if failed else ""),
          file=sys.stderr)


def cmd_clean(a):
    w = csv.DictWriter(sys.stdout, FIELDS + ["source"])
    w.writeheader()
    for name, src in expand(a.sources):
        bad = [0]
        for r in read(src, bad):
            r["source"] = name
            w.writerow(r)
        if bad[0]:
            print(f"{name}: {bad[0]:,} rows skipped (wrong shape)", file=sys.stderr)


def cmd_years(a):
    for y in sorted(DATASETS):
        ps = parts(y)
        print(f"{y}  {len(ps):>2} files  {sum(s or 0 for *_, s in ps) / 1e9:5.1f} GB")


def main(argv=None):
    p = argparse.ArgumentParser(prog="egp", description="Thai government procurement (e-GP) data from data.go.th")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def filters(sp):
        sp.add_argument("--db", default="egp.sqlite")
        sp.add_argument("--year", type=int, action="append", help="fiscal year (Buddhist era), repeatable")
        sp.add_argument("--dept", help="unit name contains (dept or sub-unit)")
        sp.add_argument("--winner", help="winner name contains")
        sp.add_argument("--province", help="province name contains")
        sp.add_argument("--limit", type=int, default=30)
        sp.add_argument("--csv", action="store_true", help="CSV output")
        return sp

    sub.add_parser("years", help="list fiscal years available on data.go.th")
    sp = sub.add_parser("download", help="download the raw CSV files of a fiscal year")
    sp.add_argument("year", type=int)
    sp.add_argument("--dir", default="egp-data")
    sp = sub.add_parser("load", help="load fiscal years or CSV files into SQLite")
    sp.add_argument("sources", nargs="+", help="2568, or path/URL of CSV files")
    sp.add_argument("--db", default="egp.sqlite")
    sp.add_argument("--force", action="store_true", help="reload sources that are already loaded")
    sp = sub.add_parser("clean", help="write cleaned CSV (English column names) to stdout")
    sp.add_argument("sources", nargs="+", help="2568, or path/URL of CSV files")
    sp = filters(sub.add_parser("search", help="search projects by name"))
    sp.add_argument("words", nargs="*", help="words in the project name (all must match)")
    sp.add_argument("--method", choices=["ebidding", "emarket", "specific", "selection", "price_agreement",
                                         "price_quotation", "special", "open", "other"])
    sp.add_argument("--min", type=float, help="minimum baht")
    sp.add_argument("--max", type=float, help="maximum baht")
    sp = filters(sub.add_parser("top", help="largest totals by unit, winner, province, method, type or year"))
    sp.add_argument("by", choices=["dept", "subdept", "winner", "province", "method", "type", "year"])
    sp.add_argument("--method")
    sp = filters(sub.add_parser("flags", help="red flags worth a closer look"))
    sp.add_argument("--only", action="append", choices=list(FLAGS))
    sp.add_argument("--near", type=float, default=0.01, help="near_reference: within this share (default 0.01)")
    sp.add_argument("--min-baht", type=float, default=5_000_000, help="ignore smaller units/projects")
    sp = sub.add_parser("winners", help="every juristic winner in the country, streamed from data.go.th")
    sp.add_argument("years", nargs="*", help="fiscal years or CSV paths (default: all years)")
    sp.add_argument("--out", default="winners.csv")
    sp.add_argument("--jobs", type=int, default=3, help="files downloaded at the same time")
    sp.add_argument("--cache", default="egp-cache", help="per-file results, so a rerun skips finished files")
    a = p.parse_args(argv)

    if a.cmd == "years":
        return cmd_years(a)
    if a.cmd == "download":
        return download(a.year, os.path.join(a.dir, str(a.year)))
    if a.cmd == "clean":
        return cmd_clean(a)
    if a.cmd == "winners":
        return cmd_winners(a)
    if a.cmd == "load":
        return load(connect(a.db), expand(a.sources), a.force)
    if not os.path.exists(a.db):
        raise SystemExit(f"egp: {a.db} not found. Load data first, e.g. `egp load 2568`")
    {"search": cmd_search, "top": cmd_top, "flags": cmd_flags}[a.cmd](connect(a.db), a)


if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        sys.exit(1)
