"""Run: python3 test_thai_egp.py  (or pytest)"""
import csv
import io
import os
import sqlite3
import tempfile
from types import SimpleNamespace

import thai_egp as t

CAT = "วิธีการจัดหา ประกาศเชิญชวนทั่วไป คัดเลือก เฉพาะเจาะจง"


def row(layout, **v):
    width = 28 if layout is t.NARROW else 31
    r = [""] * width
    base = dict(project_id="67119524001", project_name="ซื้อวัสดุสำนักงาน", project_type="ซื้อ",
                dept="เทศบาลตำบลทดสอบ", subdept="เทศบาลตำบลทดสอบ", m1="เฉพาะเจาะจง", m2=CAT,
                announce_date="-", budget="100000", reference_price="100000", agreed_price="99000",
                fiscal_year="2567", transaction_date="1-ต.ค.-66", province="เชียงใหม่",
                district="แม่ริม", subdistrict="ริมใต้", project_status="สิ้นสุดสัญญา",
                winner_id="1.05555E+11", winner_name="บริษัท ทดสอบ ก่อสร้าง จำกัด", contract_no="1/2567",
                contract_date="2-ต.ค.-66", contract_end="30-ต.ค.-66", contract_amount="99000",
                contract_status="สิ้นสุดสัญญา")
    base.update(v)
    for k, val in base.items():
        r[layout[k]] = val
    return r


def test_dates():
    assert t.thai_date("29-ก.ค.-58") == "2015-07-29"
    assert t.thai_date("8 เม.ย. 68") == "2025-04-08"
    assert t.thai_date("17 มี.ค. 68") == "2025-03-17"
    assert t.thai_date("1 ม.ค. 2568") == "2025-01-01"
    assert t.thai_date("30-ก.พ.-60") is None
    assert t.thai_date("-") is None and t.thai_date("") is None
    assert t.thai_date("มิ.ย.-58") is None  # a contract number Excel turned into a date


def test_layouts():
    wide = t.clean(row(t.WIDE))
    assert wide["district"] == "แม่ริม" and wide["method"] == "เฉพาะเจาะจง" and wide["category"] == CAT
    assert wide["method_kind"] == "specific" and wide["fiscal_year"] == 2567
    assert t.clean(row(t.WIDE) + ["", "", ""])["province"] == "เชียงใหม่"  # 2559/2560 trailing columns
    assert t.clean(row(t.WIDE) + ["shifted"]) is None
    assert t.clean(row(t.WIDE)[:29]) is None
    # 2568 swaps the two method columns and drops the English place names
    narrow = t.clean(row(t.NARROW, m1=CAT, m2="ประกวดราคาอิเล็กทรอนิกส์ (e-bidding)", fiscal_year="2568",
                         winner_id="0105555012345", contract_date="12 ธ.ค. 67"))
    assert narrow["method_kind"] == "ebidding" and narrow["category"] == CAT
    assert narrow["subdistrict"] == "ริมใต้" and narrow["contract_date"] == "2024-12-12"
    assert t.clean(row(t.WIDE, fiscal_year="ริมใต้")) is None  # misaligned row


def test_placeholder_district():
    r = t.clean(row(t.NARROW, m1=CAT, m2="เฉพาะเจาะจง", fiscal_year="2568", province="กรุงเทพมหานคร",
                    district="ตรอน", subdistrict="เพ"))
    assert r["province"] == "กรุงเทพมหานคร" and r["district"] is None and r["subdistrict"] is None
    r = t.clean(row(t.NARROW, m1=CAT, m2="เฉพาะเจาะจง", fiscal_year="2568", province="อุตรดิตถ์",
                    district="ตรอน", subdistrict="วังแดง"))
    assert r["district"] == "ตรอน"


def test_winner_ids():
    r = t.clean(row(t.WIDE))
    assert r["winner_id"] is None and r["winner_prefix"] == "010555" and r["winner_juristic"] == 1
    r = t.clean(row(t.WIDE, winner_id="3.1009E+12", winner_name="ร้านทดสอบ"))
    assert r["winner_juristic"] == 0  # 13 digits not starting with 0: a person
    r = t.clean(row(t.WIDE, winner_id="352050022xxxx", winner_name="นายทดสอบ ระบบ"))
    assert r["winner_juristic"] == 0 and r["winner_id"] == "352050022xxxx"
    for name in ("นายฟรานซิส ทดสอบ", "นางสาวทดสอบ ระบบ", "Mr. Test Person", "MISS.TEST PERSON", "\u0e4bMRS.TEST PERSON"):
        r = t.clean(row(t.NARROW, m1=CAT, m2="เฉพาะเจาะจง", fiscal_year="2568", winner_id="0994000012345", winner_name=name))
        assert r["winner_juristic"] == 0, name  # a 0-number does not make a person a company
    r = t.clean(row(t.NARROW, m1=CAT, m2="เฉพาะเจาะจง", fiscal_year="2568", winner_id="0105555012345",
                    winner_name="บริษัท นายช่างการโยธา จำกัด"))
    assert r["winner_juristic"] == 1
    assert t.juristic_digits("0105555012345", "MR.TEST (THAILAND) CO., LTD.") == "105555012345"
    r = t.clean(row(t.WIDE, winner_id="1.06E+11"))  # narrow Excel column: 105555... rounded to 3 digits
    assert r["winner_juristic"] == 1 and r["winner_prefix"] is None
    r = t.clean(row(t.WIDE, winner_id="", winner_name="หจก.ทดสอบการค้า"))
    assert r["winner_juristic"] == 1 and r["winner_prefix"] is None
    assert t.name_key("บริษัท ทดสอบ ก่อสร้าง จำกัด") == t.name_key("บจก.ทดสอบก่อสร้าง")
    assert t.name_key("บริษัท ทดสอบ จำกัด (มหาชน)") == t.name_key("บริษัท ทดสอบ จำกัด")


def test_safe_name():
    assert t.safe_name("2568-egp-contract-1") == "2568-egp-contract-1"
    for evil in ("../../etc/passwd", "..\\..\\x", "a/b/c", "/abs", ".."):
        n = t.safe_name(evil)
        assert "/" not in n and "\\" not in n and not n.startswith(".") and n


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["header", "is", "ignored"])
        w.writerows(rows)


def test_winners_merge_across_years():
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "2567.csv"), os.path.join(d, "2568.csv")
        write_csv(a, [row(t.WIDE, project_id=str(i)) for i in range(3)]
                  + [row(t.WIDE, project_id="9", winner_id="")]  # no ID at all
                  + [row(t.WIDE, project_id="8", winner_id="352050022xxxx", winner_name="นายทดสอบ ระบบ")]
                  + [row(t.WIDE, project_id="7", contract_amount="999999999", budget="1000")])  # typo
        write_csv(b, [row(t.NARROW, m1=CAT, m2="เฉพาะเจาะจง", fiscal_year="2568", project_id="10",
                          winner_id="0105555012345", winner_name="บจก. ทดสอบก่อสร้าง")])
        W, Y, bad, odd = t.merge([t.tally(a), t.tally(b)])
        rows = list(t.winner_rows(W))
        assert len(rows) == 1, rows  # the person is left out, the three spellings are one company
        r = rows[0]
        assert r["winner_id"] == "0105555012345" and r["contracts"] == 5 and r["specific_contracts"] == 5
        assert r["first_year"] == 2567 and r["last_year"] == 2568 and r["entity"] == "บริษัทจำกัด"
        assert r["dbd"].endswith("/5/0105555012345")
        assert odd and odd[0][0] == "7"
        assert Y["2567"]["specific"][0] == 6  # year totals include everyone, typos too


def test_winners_merge_rounded_and_mistyped_ids():
    def e(n):
        return dict(n=n, baht=n * 1000, spec_n=n, spec_baht=n * 1000, agree_n=0, agree_baht=0,
                    y0=2560, y1=2568, ids={}, names={"บริษัท ทดสอบ จำกัด": n}, depts={})
    part = dict(bad=0, odd=[], years={}, winners={
        "ทดสอบ|10555": e(100),  # the real company
        "ทดสอบ|10600": e(10),   # '1.06E+11' read before the fix: entity digit 6 does not exist
        "ทดสอบ|?????": e(5),    # no ID
        "ทดสอบ|10222": e(1),    # an agency typed someone else's number
    })
    W = t.merge([part])[0]
    assert W["ทดสอบ|10555"]["n"] == 115 and set(W) == {"ทดสอบ|10555", "ทดสอบ|10222"}, W.keys()


def test_load_and_flags():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "x.csv")
        split = [row(t.WIDE, project_id=f"s{i}", subdept="กองช่าง", contract_amount="200000",
                     contract_date=f"{i + 1}-ม.ค.-67", reference_price="200000", budget="200000") for i in range(3)]
        near = [row(t.WIDE, project_id="n1", m1="ประกวดราคาอิเล็กทรอนิกส์ (e-bidding)", reference_price="10000000",
                    budget="10000000", agreed_price="9995000", contract_amount="9995000", winner_name="บริษัท ใกล้ จำกัด")]
        over = [row(t.WIDE, project_id="o1", reference_price="6000000", budget="6000000", agreed_price="6000000",
                    contract_amount="6000000", winner_name="บริษัท เกิน จำกัด", contract_date="1-มี.ค.-67")]
        others = [row(t.WIDE, project_id=f"x{i}", winner_name=f"บริษัท อื่น {i} จำกัด", contract_amount="10000")
                  for i in range(3)]
        big = [row(t.WIDE, project_id=f"b{i}", subdept="กองคลัง", m1="ประกวดราคาอิเล็กทรอนิกส์ (e-bidding)",
                   reference_price="4000000", budget="4000000", agreed_price="3000000", contract_amount="3000000",
                   winner_name="บริษัท ใหญ่ จำกัด") for i in range(3)]
        small = [row(t.WIDE, project_id=f"k{i}", subdept="กองคลัง", winner_name=f"บริษัท เล็ก {i} จำกัด",
                     contract_amount="100000") for i in range(2)]
        people = [row(t.WIDE, project_id=f"p{i}", subdept="กองช่าง", contract_amount="200000", winner_id="352050022xxxx",
                      winner_name="นายทดสอบ ระบบ", contract_date=f"{i + 1}-ม.ค.-67") for i in range(3)]
        write_csv(src, split + near + over + others + big + small + people)
        db = t.connect(os.path.join(d, "egp.sqlite"))
        t.load(db, [("x.csv", src)])
        t.load(db, [("x.csv", src)])  # second load is skipped
        assert db.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 16
        a = SimpleNamespace(year=None, dept=None, winner=None, province=None, method=None, limit=10,
                            near=0.01, min_baht=1_000_000)
        s = t.flag_split(db, a)
        assert len(s) == 1 and s[0]["projects"] == 3 and s[0]["baht"] == 600000
        assert [f["winner"] for f in t.flag_near_reference(db, a)] == ["บริษัท ใกล้ จำกัด"]
        assert [f["winner"] for f in t.flag_direct_over_cap(db, a)] == ["บริษัท เกิน จำกัด"]
        assert [f["winner"] for f in t.flag_concentration(db, a)] == ["บริษัท ใหญ่ จำกัด"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
