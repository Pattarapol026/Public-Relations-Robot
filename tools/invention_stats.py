"""Questions and answers from the report "รายงานจำนวนสิ่งประดิษฐ์ ปี 2569 การแข่งขันระดับ อศจ." (sent 10 Nov 2026).

Updated 10 Nov 2026 (second report): the total is 316 and the North 19 (the first report had 314 and 17). The
province table of the North was not sent again, so its entries are switched off (PENDING) until it is.
Six tables: the total by region, and the provinces of each of the five regions. Every number below is read from
those tables; the sums (314 in all, and each region's total) are checked when this module is imported.
Answers are written to be spoken: short, Arabic digits, "ชิ้น" for the unit.
"""
from __future__ import annotations

CATEGORY = "สถิติสิ่งประดิษฐ์ ปี 2569"
SCOPE = "ระดับ อศจ. ปี 2569"
SCOPE_EN = "at the provincial (อศจ.) level in 2026 (B.E. 2569)"

# region: (Thai name, English name, spoken name, [(province, English name, count, extra keywords)])
REGIONS: dict[str, tuple[str, str, list[tuple[str, str, int, str]]]] = {
    "ภาคเหนือ": ("the North", "ภาคเหนือ", [
        ("เชียงใหม่", "Chiang Mai", 4, ""), ("ตาก", "Tak", 2, ""), ("พะเยา", "Phayao", 5, ""),
        ("ลำปาง", "Lampang", 4, ""), ("ลำพูน", "Lamphun", 2, ""),
    ]),
    "ภาคตะวันออกเฉียงเหนือ": ("the Northeast", "ภาคตะวันออกเฉียงเหนือ", [
        ("ขอนแก่น", "Khon Kaen", 1, ""), ("ชัยภูมิ", "Chaiyaphum", 1, ""), ("บุรีรัมย์", "Buriram", 2, ""),
        ("มุกดาหาร", "Mukdahan", 2, ""), ("ร้อยเอ็ด", "Roi Et", 3, ""), ("ศรีสะเกษ", "Sisaket", 3, ""),
        ("สกลนคร", "Sakon Nakhon", 1, ""), ("สุรินทร์", "Surin", 1, ""), ("อุดรธานี", "Udon Thani", 5, ""),
        ("อุบลราชธานี", "Ubon Ratchathani", 1, "อุบล"),
    ]),
    "ภาคกลาง": ("the Central region", "ภาคกลาง", [
        ("พระนครศรีอยุธยา", "Phra Nakhon Si Ayutthaya", 4, "อยุธยา,Ayutthaya"),
        ("ราชบุรี", "Ratchaburi", 3, ""), ("ลพบุรี", "Lopburi", 13, ""),
        ("สมุทรสงคราม", "Samut Songkhram", 1, ""), ("สระบุรี", "Saraburi", 2, ""),
        ("สิงห์บุรี", "Singburi", 7, ""), ("สุพรรณบุรี", "Suphan Buri", 5, ""), ("อ่างทอง", "Ang Thong", 9, ""),
    ]),
    "ภาคตะวันออกและกรุงเทพมหานคร": ("the East and Bangkok", "ภาคตะวันออกและกรุงเทพมหานคร", [
        ("กรุงเทพมหานคร", "Bangkok", 25, "กรุงเทพ,กทม,กรุงเทพฯ"),
        ("ฉะเชิงเทรา", "Chachoengsao", 1, ""), ("ชลบุรี", "Chonburi", 63, "ชลบุรี,พัทยา"),
        ("นครนายก", "Nakhon Nayok", 23, ""), ("นนทบุรี", "Nonthaburi", 5, ""),
        ("ปทุมธานี", "Pathum Thani", 51, ""), ("ปราจีนบุรี", "Prachinburi", 20, ""),
        ("สมุทรปราการ", "Samut Prakan", 30, ""), ("สระแก้ว", "Sa Kaeo", 1, ""),
    ]),
    "ภาคใต้": ("the South", "ภาคใต้", [
        ("กระบี่", "Krabi", 3, ""), ("ชุมพร", "Chumphon", 2, ""), ("ตรัง", "Trang", 4, ""),
        ("พัทลุง", "Phatthalung", 1, ""), ("สงขลา", "Songkhla", 3, "หาดใหญ่"), ("สตูล", "Satun", 1, ""),
    ]),
}
# Short forms people say, and what the report calls the region (the East and Bangkok one is long).
REGION_ALIASES = {
    "ภาคเหนือ": "ภาคเหนือ,เหนือ,north",
    "ภาคตะวันออกเฉียงเหนือ": "ภาคตะวันออกเฉียงเหนือ,ภาคอีสาน,อีสาน,ตะวันออกเฉียงเหนือ,northeast,isan",
    "ภาคกลาง": "ภาคกลาง,central",
    "ภาคตะวันออกและกรุงเทพมหานคร": "ภาคตะวันออก,ภาคตะวันออกและ กทม,ภาคตะวันออกและกรุงเทพ,ตะวันออกและกรุงเทพ,กรุงเทพและตะวันออก,east,bangkok",
    "ภาคใต้": "ภาคใต้,ใต้,south",
}
REGION_TOTALS = {"ภาคเหนือ": 19, "ภาคตะวันออกเฉียงเหนือ": 20, "ภาคกลาง": 44,
                 "ภาคตะวันออกและกรุงเทพมหานคร": 219, "ภาคใต้": 14}
TOTAL = 316
# Regions whose province table no longer matches the report (their entries are written but switched off), and the
# questions switched off for that reason; the build tool writes "ไม่" in the enabled column for these.
PENDING_REGIONS = {"ภาคเหนือ"}
DISABLED_QUESTIONS: set[str] = set()

for _region, (_en, _alias, _provinces) in REGIONS.items():  # the report's own totals must come out
    if _region not in PENDING_REGIONS:
        assert sum(count for _p, _e, count, _k in _provinces) == REGION_TOTALS[_region], _region
assert sum(REGION_TOTALS.values()) == TOTAL

Entry = tuple[str, str, str, str, bool, str, str]


def _names(items: list[str]) -> str:
    return " ".join(items[:-1]) + f" และ{items[-1]}" if len(items) > 1 else items[0]


def _names_en(items: list[str]) -> str:
    return ", ".join(items[:-1]) + f" and {items[-1]}" if len(items) > 1 else items[0]


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _piece(count: int) -> str:
    return f"{count} ชิ้น"


def stats_entries() -> list[Entry]:
    DISABLED_QUESTIONS.clear()
    everyone = [(province, english, count, keys, region) for region, (_en, _a, provinces) in REGIONS.items()
                for province, english, count, keys in provinces]
    by_count = sorted(everyone, key=lambda row: (-row[2], row[0]))
    region_rows = sorted(REGION_TOTALS.items(), key=lambda row: -row[1])
    english_region = {region: values[0] for region, values in REGIONS.items()}
    entries: list[Entry] = []

    def add(question: str, answer: str, keywords: str, question_en: str, answer_en: str, shortcut: bool = False,
            off: bool = False) -> None:
        if off:
            DISABLED_QUESTIONS.add(question)
        entries.append((CATEGORY, question, answer, keywords, shortcut, question_en, answer_en))

    # --- the whole country ---
    add("ปีนี้มีสิ่งประดิษฐ์กี่ชิ้น",
        f"{SCOPE} มีสิ่งประดิษฐ์ทั้งหมด {_piece(TOTAL)} ครับ จาก 5 ภาค",
        "ปีนี้มีสิ่งประดิษฐ์กี่ชิ้น,มีสิ่งประดิษฐ์กี่ชิ้น,ทั้งหมดกี่ชิ้น,ทั้งหมดกี่ผลงาน,316,รวมทุกภาค,รวมทั้งหมด,รวมเท่าไหร่,รวมเท่าไร,ผลรวม,ยอดรวม,รวมกี่ชิ้น,รวมกี่ผลงาน,ปีนี้กี่ชิ้น,ปีนี้มีกี่ผลงาน,ผลงานทั้งหมด,รวมกี่ชิ้น,314,จำนวนผลงานทั้งหมด,สิ่งประดิษฐ์ทั้งหมด,มีผลงานกี่ชิ้น,ส่งเข้าประกวดกี่ชิ้น,how many inventions,total",
        "How many inventions are entered this year?",
        f"There are {TOTAL} inventions in total {SCOPE_EN}, from 5 regions.", True)
    add("รายงานจำนวนสิ่งประดิษฐ์ปี 2569 เป็นของระดับไหน",
        f"เป็นรายงานจำนวนสิ่งประดิษฐ์ ปี 2569 ของการแข่งขัน{SCOPE.split(' ปี')[0]} แบ่งตาม 5 ภาค และแยกเป็นรายจังหวัดครับ",
        "รายงานจำนวน,ระดับ อศจ,อศจ,ข้อมูลสถิติ,สถิติสิ่งประดิษฐ์,report",
        "What level is the 2026 inventions report for?",
        "It is the 2026 (B.E. 2569) report of inventions in the provincial (อศจ.) level competition, by region and by province.")
    add("สิ่งประดิษฐ์ในแต่ละภาคมีกี่ชิ้น",
        "แบ่งตามภาคครับ " + " ".join(f"{region} {_piece(count)}" for region, count in REGION_TOTALS.items())
        + f" รวม {_piece(TOTAL)}",
        "แต่ละภาค,ทุกภาค,แยกตามภาค,ภาคละกี่ชิ้น,จำนวนแต่ละภาค,by region,each region",
        "How many inventions are there in each region?",
        "; ".join(f"{english_region[region]} {count}" for region, count in REGION_TOTALS.items()) + f"; total {TOTAL}.")
    top_region, top_count = region_rows[0]
    rest = TOTAL - top_count
    add("ภาคไหนมีสิ่งประดิษฐ์มากที่สุด",
        f"{top_region} มากที่สุดครับ {_piece(top_count)} คิดเป็นประมาณ {round(100 * top_count / TOTAL)} เปอร์เซ็นต์ของทั้งหมด",
        "ภาคไหนมากที่สุด,ภาคไหนเยอะที่สุด,ภาคไหนมากสุด,ภาคไหนเยอะสุด,ภาคที่มากสุด,มากที่สุด,เยอะที่สุด,เยอะสุด,อันดับหนึ่ง,สูงสุด,ผลงานเยอะ,most inventions,which region has the most",
        "Which region has the most inventions?",
        f"{_cap(english_region[top_region])} has the most, {top_count} of {TOTAL}, about {round(100 * top_count / TOTAL)} percent.")
    low_region, low_count = region_rows[-1]
    add("ภาคไหนมีสิ่งประดิษฐ์น้อยที่สุด",
        f"{low_region} น้อยที่สุดครับ {_piece(low_count)}",
        "ภาคไหนน้อยที่สุด,ภาคไหนน้อยสุด,ภาคที่น้อยสุด,น้อยที่สุด,น้อยสุด,ต่ำสุด,fewest,least",
        "Which region has the fewest inventions?",
        f"{_cap(english_region[low_region])} has the fewest, {low_count}.")
    add("เรียงลำดับภาคจากมากไปน้อย",
        "เรียงจากมากไปน้อยครับ " + " ".join(f"{region} {_piece(count)}" for region, count in region_rows),
        "เรียงลำดับ,อันดับภาค,จัดอันดับ,ranking,rank,เรียงจากมากไปน้อย",
        "Rank the regions from most to fewest inventions.",
        "; ".join(f"{english_region[region]} {count}" for region, count in region_rows) + ".")
    add("ภาคตะวันออกและกรุงเทพมหานครมีมากกว่าภาคอื่นรวมกันไหม",
        f"มากกว่าครับ ภาคตะวันออกและกรุงเทพมหานครมี {_piece(top_count)} ส่วนอีก 4 ภาครวมกันได้ {_piece(rest)}",
        "มากกว่าภาคอื่นรวมกัน,เทียบกับภาคอื่น,เปรียบเทียบภาค,compare",
        "Does the East and Bangkok region have more than all the others together?",
        f"Yes. It has {top_count}, while the other four regions together have {rest}.")
    add("จังหวัดไหนส่งสิ่งประดิษฐ์มากที่สุด",
        f"{by_count[0][0]} มากที่สุดครับ {_piece(by_count[0][2])} รองลงมาคือ{by_count[1][0]} {_piece(by_count[1][2])} "
        f"และ{by_count[2][0]} {_piece(by_count[2][2])}",
        "จังหวัดไหนมากที่สุด,จังหวัดไหนเยอะที่สุด,จังหวัดไหนมากสุด,จังหวัดไหนเยอะสุด,จังหวัดที่ส่งมากสุด,ส่งมากที่สุด,ส่งเยอะที่สุด,อันดับหนึ่ง,most inventions province,top province",
        "Which province sent the most inventions?",
        f"{by_count[0][1]} with {by_count[0][2]}, then {by_count[1][1]} with {by_count[1][2]} and {by_count[2][1]} with {by_count[2][2]}.")
    top_six = by_count[:6]
    add("จังหวัดที่ส่งสิ่งประดิษฐ์มากที่สุด 6 อันดับแรกคือจังหวัดอะไร",
        "6 อันดับแรกครับ " + " ".join(f"{province} {_piece(count)}" for province, _e, count, _k, _r in top_six),
        "6 อันดับแรก,อันดับต้น,สิบอันดับ,ท็อป,top,อันดับแรก,จังหวัดที่ส่งเยอะ",
        "Which are the top six provinces?",
        "; ".join(f"{english} {count}" for _p, english, count, _k, _r in top_six) + ".")
    ones = [row for row in everyone if row[2] == 1]
    add("จังหวัดไหนส่งสิ่งประดิษฐ์น้อยที่สุด",
        f"มี {len(ones)} จังหวัดที่ส่งเพียง 1 ชิ้นครับ คือ {_names([row[0] for row in ones])}",
        "จังหวัดไหนน้อยที่สุด,จังหวัดไหนน้อยสุด,ส่งน้อยที่สุด,ส่งน้อยสุด,ชิ้นเดียว,1 ชิ้น,fewest province,least",
        "Which provinces sent the fewest inventions?",
        f"{len(ones)} provinces sent just one each: {_names_en([row[1] for row in ones])}.")
    add("มีกี่จังหวัดที่ส่งสิ่งประดิษฐ์เข้าประกวด",
        f"รายงานล่าสุดรวม {_piece(TOTAL)} จาก 5 ภาคครับ ส่วนจำนวนจังหวัดที่ส่งยังไม่ได้อัปเดตตามรายงานล่าสุด รบกวนสอบถามเจ้าหน้าที่ประชาสัมพันธ์ครับ",
        "กี่จังหวัด,จำนวนจังหวัด,จังหวัดที่เข้าร่วม,provinces took part,how many provinces",
        "How many provinces took part?",
        f"The latest report totals {TOTAL} from 5 regions. The number of provinces has not been updated to it; please ask the staff.")
    twenty = [row for row in by_count if row[2] >= 20]
    add("จังหวัดไหนส่งสิ่งประดิษฐ์ตั้งแต่ 20 ชิ้นขึ้นไป",
        f"มี {len(twenty)} จังหวัดครับ " + " ".join(f"{province} {_piece(count)}" for province, _e, count, _k, _r in twenty),
        "20 ชิ้นขึ้นไป,เกิน 20,มากกว่า 20,ตั้งแต่ 20,twenty or more",
        "Which provinces sent 20 or more inventions?",
        "; ".join(f"{english} {count}" for _p, english, count, _k, _r in twenty) + ".")
    samut = next(row for row in by_count if row[0] == "สมุทรปราการ")
    rank = by_count.index(samut) + 1
    add("จังหวัดสมุทรปราการอยู่ในอันดับที่เท่าไรของจังหวัดที่ส่งสิ่งประดิษฐ์มากที่สุด",
        f"จังหวัดสมุทรปราการซึ่งเป็นเจ้าภาพที่นี่ ส่ง {_piece(samut[2])} ครับ มากเป็นอันดับที่ {rank} "
        f"ของ {len(everyone)} จังหวัด รองจาก{by_count[0][0]}และ{by_count[1][0]}",
        "อันดับสมุทรปราการ,สมุทรปราการอันดับ,สมุทรปราการอยู่อันดับ,เจ้าภาพอันดับที่,จังหวัดเจ้าภาพอันดับ,host province rank",
        "How many inventions did Samut Prakan send, and what is its rank?",
        f"Samut Prakan, the host province, sent {samut[2]}, ranked number {rank} of {len(everyone)} provinces, after "
        f"{by_count[0][1]} and {by_count[1][1]}.")

    # --- each region ---
    for region, (english, _alias, provinces) in REGIONS.items():
        total = REGION_TOTALS[region]
        aliases = REGION_ALIASES[region]
        ordered = sorted(provinces, key=lambda row: (-row[2], row[0]))
        leaders = [row for row in ordered if row[2] == ordered[0][2]]
        share = 100 * total / TOTAL
        share_text = f"ประมาณ {round(share)} เปอร์เซ็นต์"
        pending = region in PENDING_REGIONS
        add(f"{region}มีสิ่งประดิษฐ์กี่ชิ้น",
            f"{region}มี {_piece(total)} ครับ" + ("" if pending else f" จาก {len(provinces)} จังหวัด") + f" คิดเป็น{share_text}ของทั้งหมด {TOTAL} ชิ้น",
            f"{aliases},กี่ชิ้น,จำนวนสิ่งประดิษฐ์,กี่ผลงาน,จำนวนผลงาน,inventions in {english}",
            f"How many inventions are there in {english}?",
            f"{_cap(english)} has {total} inventions" + ("" if pending else f" from {len(provinces)} provinces") + f", about {round(share)} percent of {TOTAL}.")
        stale_th = f"{region}มี {_piece(total)} รวมกันครับ ตัวเลขรายจังหวัดของภาคนี้ยังไม่ได้อัปเดตตามรายงานล่าสุด รบกวนสอบถามเจ้าหน้าที่ประชาสัมพันธ์ครับ"
        stale_en = f"{_cap(english)} has {total} in total. The province figures for this region have not been updated to the latest report; please ask the staff."
        add(f"{region}มีจังหวัดอะไรบ้าง และแต่ละจังหวัดส่งกี่ชิ้น",
            stale_th if pending else f"{region}มี {len(provinces)} จังหวัดครับ " + " ".join(f"{p} {_piece(c)}" for p, _e, c, _k in provinces)
            + f" รวม {_piece(total)}",
            f"{aliases},มีจังหวัดอะไรบ้าง,จังหวัดอะไร,แต่ละจังหวัด,รายชื่อจังหวัด,provinces in {english}",
            f"Which provinces are in {english}, and how many inventions does each have?",
            stale_en if pending else "; ".join(f"{e} {c}" for _p, e, c, _k in provinces) + f"; total {total}.")
        add(f"{region}จังหวัดไหนส่งสิ่งประดิษฐ์มากที่สุด",
            stale_th if pending else f"{_names([row[0] for row in leaders])} มากที่สุดในภาคครับ {_piece(leaders[0][2])}"
            + (f" รองลงมาคือ{ordered[len(leaders)][0]} {_piece(ordered[len(leaders)][2])}" if len(ordered) > len(leaders) else ""),
            f"{aliases},จังหวัดไหนมากที่สุด,มากที่สุดในภาค,top province in {english}",
            f"Which province in {english} sent the most inventions?",
            stale_en if pending else f"{_names_en([row[1] for row in leaders])} with {leaders[0][2]} in {english}.")

    # --- each province ---
    for province, english, count, keys, region in everyone:
        extra = f",{keys}" if keys else ""
        rank_no = 1 + sum(1 for row in everyone if row[2] > count)
        top_rank = by_count.index(next(row for row in by_count if row[0] == province)) < 6
        rank_text = f" เป็นอันดับที่ {rank_no} ของ {len(everyone)} จังหวัด" if top_rank else ""
        rank_en = f", number {rank_no} of {len(everyone)} provinces" if top_rank else ""
        note = ""
        if province == by_count[0][0]:
            note = " ซึ่งมากที่สุดในประเทศ"
        elif count == 1:
            note = " ซึ่งเป็นจำนวนน้อยที่สุด"
        note_en = ", the most of any province" if province == by_count[0][0] else (", the fewest of any province" if count == 1 else "")
        if region in PENDING_REGIONS:
            add(f"จังหวัด{province}ส่งสิ่งประดิษฐ์เข้าประกวดกี่ชิ้น",
                f"จังหวัด{province}อยู่ใน{region} ซึ่งมี {_piece(REGION_TOTALS[region])} รวมกันครับ "
                "ตัวเลขรายจังหวัดของภาคนี้ยังไม่ได้อัปเดตตามรายงานล่าสุด รบกวนสอบถามเจ้าหน้าที่ประชาสัมพันธ์ครับ",
                f"{province}{extra},{english},จังหวัด{province},{province}มี,{province}ส่ง,{province}กี่,{province}มีสิ่งประดิษฐ์,{province}มีผลงาน,{province}มีกี่,{province}ส่งกี่,กี่ชิ้น,กี่ผลงาน,จำนวนสิ่งประดิษฐ์,จำนวนผลงาน",
                f"How many inventions did {english} send?",
                f"{english} is in {english_region[region]}, which has {REGION_TOTALS[region]} in total. The province figures for "
                "this region have not been updated to the latest report; please ask the staff.")
            continue
        add(f"จังหวัด{province}ส่งสิ่งประดิษฐ์เข้าประกวดกี่ชิ้น",
            f"จังหวัด{province}ส่งสิ่งประดิษฐ์ {_piece(count)} ครับ{note}{rank_text} อยู่ใน{region}",
            f"{province}{extra},{english},จังหวัด{province},{province}มี,{province}ส่ง,{province}กี่,{province}มีสิ่งประดิษฐ์,{province}มีผลงาน,{province}มีกี่,{province}ส่งกี่,กี่ชิ้น,กี่ผลงาน,จำนวนสิ่งประดิษฐ์,จำนวนผลงาน" + (",อันดับ,อันดับที่เท่าไร,อยู่อันดับ" if top_rank else ""),
            f"How many inventions did {english} send?",
            f"{english} sent {count}{note_en}{rank_en}, in {english_region[region]}.")
    return entries
