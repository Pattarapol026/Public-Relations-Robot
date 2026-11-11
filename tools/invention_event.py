"""Questions and answers from the event's own pictures (sent 10 Nov 2026): the schedule of 11 and 12 November, the campus
map and the layout plan, building 9 by floor, the documents for registration, the committee's registration, and the
concept-paper count of provincial group 5 (Chonburi and Samut Prakan).

Every fact below is read from those eight pictures. Where two pictures word the same place differently, both
wordings are kept in the answer. Answers are written to be spoken: short, Arabic digits.
"""
from __future__ import annotations

Entry = tuple[str, str, str, str, bool, str, str]

SCHEDULE = "กำหนดการ"
MAP = "แผนผังสถานที่"
BUILDING = "อาคาร 9"
REGISTER = "รายงานตัวและลงทะเบียน"
GROUP5 = "จำนวนผลงานกลุ่มจังหวัดที่ 5"

# Concept-paper summary of provincial group 5 (picture "วาระที่ 1 ข้อ 1.3"): per category, Chonburi and Samut Prakan.
CHONBURI = (16, 7, 12, 11, 7, 13, 2)
SAMUT_PRAKAN = (7, 5, 6, 3, 0, 10, 0)
CATEGORY_NAMES = (
    ("ด้านนวัตกรรมและเทคโนโลยีการเกษตรหรืออุตสาหกรรม", "agriculture or industry"),
    ("ด้านนวัตกรรมและเทคโนโลยีปัญญาประดิษฐ์สมองกลฝังตัวอัจฉริยะ", "AI and smart embedded systems"),
    ("ด้านนวัตกรรมและเทคโนโลยีพลังงานและสิ่งแวดล้อม", "energy and environment"),
    ("ด้านนวัตกรรมและเทคโนโลยีอาหาร", "food"),
    ("ด้านนวัตกรรมและเทคโนโลยีเพื่อสุขภาพ", "health care"),
    ("ด้านนวัตกรรมและเทคโนโลยีผลิตภัณฑ์เชิงพาณิชย์", "commercial products"),
    ("ด้านนวัตกรรมและเทคโนโลยีหุ่นยนต์ประชาสัมพันธ์อัจฉริยะ", "smart public-relations robots"),
)
TOTALS = tuple(a + b for a, b in zip(CHONBURI, SAMUT_PRAKAN))
assert TOTALS == (23, 12, 18, 14, 7, 23, 2) and sum(TOTALS) == 99
assert (sum(CHONBURI), sum(SAMUT_PRAKAN)) == (68, 31)


def event_entries() -> list[Entry]:
    entries: list[Entry] = []

    def add(category: str, question: str, answer: str, keywords: str, question_en: str, answer_en: str,
            shortcut: bool = False) -> None:
        entries.append((category, question, answer, keywords, shortcut, question_en, answer_en))

    # ------------------------------------------------------------------ the two days
    add(SCHEDULE, "งานเริ่มกี่โมง",
        "วันที่ 11 พฤศจิกายน เริ่มลงทะเบียนผลงานและรายงานตัว 13.00 น. ที่อาคาร 9 ชั้น 1 ครับ "
        "ส่วนวันที่ 12 พฤศจิกายน แขกผู้มีเกียรติและนักเรียนนักศึกษาเข้าร่วมพิธีเปิดตั้งแต่ 08.00 น. ที่อาคาร 9 ชั้น 4 "
        "และพิธีเปิดเริ่ม 09.00 น.",
        "งานเริ่ม,เริ่มกี่โมง,เริ่มกี่โมงครับ,เปิดงานกี่โมง,เริ่มเมื่อไหร่,เวลาเริ่มงาน,when does it start,start time",
        "What time does the event start?",
        "On 11 November registration and check-in start at 13:00 in building 9, floor 1. On 12 November guests, students "
        "and visitors join the opening ceremony from 08:00 on floor 4 of building 9, and the ceremony starts at 09:00.", True)
    add(SCHEDULE, "งานเลิกกี่โมง",
        "วันที่ 11 พฤศจิกายน กิจกรรมถึง 17.30 น. ครับ ส่วนวันที่ 12 พฤศจิกายน ประกาศผลและมอบเกียรติบัตร "
        "แล้วพิธีปิดเวลา 16.30 ถึง 17.30 น.",
        "งานเลิก,เลิกกี่โมง,งานจบกี่โมง,จบกี่โมง,ปิดงานกี่โมง,เวลาเลิก,when does it end,end time,finish",
        "What time does the event end?",
        "On 11 November the activities run until 17:30. On 12 November the results are announced and certificates given, "
        "and the closing ceremony runs from 16:30 to 17:30.")
    add(SCHEDULE, "กำหนดการวันที่ 11 พฤศจิกายนมีอะไรบ้าง",
        "วันพุธที่ 11 พฤศจิกายน 13.00 ถึง 14.00 น. ลงทะเบียนผลงานและรายงานตัวที่อาคาร 9 ชั้น 1 "
        "พร้อมลงทะเบียนและประชุมคณะกรรมการตัดสินที่อาคาร 9 ชั้น 3 "
        "จากนั้น 14.00 ถึง 17.30 น. ผู้เข้าประกวดติดตั้งบูธนำเสนอผลงาน และคณะกรรมการเริ่มตรวจให้คะแนนเอกสารครับ",
        "กำหนดการวันที่ 11,วันที่ 11,วันพุธ,วันแรก,11 พฤศจิกายน,วันแรกมีอะไร,schedule day 1,11 november",
        "What is the schedule on 11 November?",
        "Wednesday 11 November: 13:00 to 14:00 registration and check-in on floor 1 of building 9, and the judges' "
        "registration and meeting on floor 3. Then 14:00 to 17:30 entrants set up their booths and the judges start "
        "scoring the documents.", True)
    add(SCHEDULE, "กำหนดการวันที่ 12 พฤศจิกายนมีอะไรบ้าง",
        "วันพฤหัสบดีที่ 12 พฤศจิกายน 08.00 น. แขกและนักเรียนนักศึกษาเข้าพิธีเปิด 09.00 น. พิธีเปิด "
        "09.30 ถึง 12.00 น. ผู้เข้าประกวดนำเสนอผลงานและกรรมการให้คะแนนบูธ 12.00 น. พักกลางวัน "
        "13.00 ถึง 15.00 น. นำเสนอผลงานต่อ 15.00 ถึง 16.30 น. กรรมการประมวลผลคะแนน "
        "และ 16.30 ถึง 17.30 น. ประกาศผล มอบเกียรติบัตร และพิธีปิดครับ",
        "กำหนดการวันที่ 12,วันที่ 12,วันพฤหัส,วันสุดท้าย,12 พฤศจิกายน,วันสุดท้ายมีอะไร,schedule day 2,12 november",
        "What is the schedule on 12 November?",
        "Thursday 12 November: 08:00 guests and students arrive for the opening; 09:00 opening ceremony; 09:30 to 12:00 "
        "entrants present and judges score the booths; 12:00 lunch break; 13:00 to 15:00 presentations continue; 15:00 to "
        "16:30 judges tally the scores; 16:30 to 17:30 results, certificates and the closing ceremony.", True)
    add(SCHEDULE, "วันนี้มีกิจกรรมอะไรบ้าง",
        "งานจัด 2 วันครับ วันที่ 11 พฤศจิกายน ลงทะเบียน รายงานตัว และติดตั้งบูธ "
        "ส่วนวันที่ 12 พฤศจิกายน พิธีเปิด นำเสนอและตรวจให้คะแนนผลงาน ประกาศผล และพิธีปิด "
        "ถามกำหนดการของวันไหนก็ได้ครับ",
        "วันนี้มีอะไร,วันนี้ทำอะไร,กิจกรรมวันนี้,มีกิจกรรมอะไร,กำหนดการ,ตารางงาน,ตารางกิจกรรม,schedule,program,what is on today",
        "What is on today?",
        "The event runs two days. On 11 November: registration, check-in and booth set-up. On 12 November: the opening "
        "ceremony, presentations and scoring, results and the closing ceremony. Ask about either day.")
    add(SCHEDULE, "พิธีเปิดเริ่มกี่โมง และจัดที่ไหน",
        "พิธีเปิดวันที่ 12 พฤศจิกายน 09.00 ถึง 09.30 น. ที่อาคาร 9 ชั้น 4 ครับ "
        "แขกผู้มีเกียรติและนักเรียนนักศึกษาเข้าร่วมตั้งแต่ 08.00 น.",
        "พิธีเปิด,เปิดงาน,พิธีเปิดกี่โมง,พิธีเปิดที่ไหน,opening ceremony",
        "When and where is the opening ceremony?",
        "The opening ceremony is on 12 November from 09:00 to 09:30 on floor 4 of building 9. Guests and students arrive "
        "from 08:00.")
    add(SCHEDULE, "แขกผู้มีเกียรติและนักเรียนนักศึกษาต้องมาถึงกี่โมง",
        "วันที่ 12 พฤศจิกายน เข้าร่วมพิธีเปิดตั้งแต่ 08.00 ถึง 09.00 น. ที่อาคาร 9 ชั้น 4 ครับ",
        "แขกผู้มีเกียรติ,แขก,นักเรียนนักศึกษา,มาถึงกี่โมง,เข้าร่วมพิธีเปิด,guests arrive",
        "What time do guests and students need to arrive?",
        "On 12 November, from 08:00 to 09:00 on floor 4 of building 9, to take part in the opening ceremony.")
    add(SCHEDULE, "พิธีปิดและประกาศผลเมื่อไหร่ ใครเป็นผู้ปิดงาน",
        "วันที่ 12 พฤศจิกายน 16.30 ถึง 17.30 น. ประกาศผลการประกวดและมอบเกียรติบัตร แล้วพิธีปิดครับ "
        "ปิดงานโดย ดร.พีระพงษ์ พันธ์โสดา ผู้อำนวยการวิทยาลัยเทคนิคชลบุรี",
        "พิธีปิด,ปิดงาน,ประกาศผล,ประกาศผลกี่โมง,ใครปิดงาน,ผู้ปิดงาน,มอบเกียรติบัตร,เกียรติบัตร,พีระพงษ์,closing ceremony,results,certificates",
        "When are the results announced, and who closes the event?",
        "On 12 November from 16:30 to 17:30 the results are announced and certificates given, then the closing ceremony. "
        "It is closed by Dr. Peerapong Phansoda, director of Chonburi Technical College.", True)
    add(SCHEDULE, "ผู้เข้าประกวดนำเสนอผลงานและกรรมการให้คะแนนบูธกี่โมง",
        "วันที่ 12 พฤศจิกายน ช่วงเช้า 09.30 ถึง 12.00 น. และช่วงบ่าย 13.00 ถึง 15.00 น. ครับ "
        "ผู้เข้าประกวดนำเสนอผลงานและคณะกรรมการตรวจให้คะแนนบูธผลงาน",
        "นำเสนอผลงาน,ให้คะแนนบูธ,กรรมการตรวจบูธ,กรรมการให้คะแนน,เวลานำเสนอ,เวลาตัดสิน,presentation time,judging time",
        "When do entrants present and the judges score the booths?",
        "On 12 November: 09:30 to 12:00 in the morning and 13:00 to 15:00 in the afternoon. Entrants present and the "
        "judges score the booths.")
    add(SCHEDULE, "พักกลางวันกี่โมง",
        "วันที่ 12 พฤศจิกายน พักรับประทานอาหารกลางวัน 12.00 ถึง 13.00 น. ครับ",
        "พักกลางวัน,พักเที่ยง,อาหารกลางวัน,กินข้าวกี่โมง,เที่ยง,lunch,lunch break",
        "When is the lunch break?",
        "On 12 November, from 12:00 to 13:00.")
    add(SCHEDULE, "กรรมการประมวลผลคะแนนเมื่อไหร่ ที่ไหน",
        "วันที่ 12 พฤศจิกายน 15.00 ถึง 16.30 น. ที่อาคาร 9 ชั้น 1 ครับ ในกำหนดการเรียกว่าห้องงานส่งเสริมการวิจัย "
        "ส่วนในแผนผังอาคารเรียกว่าห้องประกัน",
        "ประมวลผลคะแนน,รวมคะแนน,นับคะแนน,ห้องประมวลผล,ห้องประกัน,tally,scoring room",
        "When and where do the judges tally the scores?",
        "On 12 November from 15:00 to 16:30 on floor 1 of building 9. The schedule calls it the research promotion room "
        "and the building plan calls it the assurance room.")
    add(SCHEDULE, "วันที่ 11 ผู้เข้าประกวดต้องทำอะไรบ้าง",
        "13.00 ถึง 14.00 น. ลงทะเบียนผลงานและรายงานตัวที่อาคาร 9 ชั้น 1 พร้อมส่งแบบคุณลักษณะและรูปเล่ม "
        "จากนั้น 14.00 ถึง 17.30 น. นำผลงานเข้าติดตั้งบูธ ประเภทที่ 1 2 3 6 และ 7 ที่ลานอเนกประสงค์หลังอาคาร 9 "
        "ประเภทที่ 4 และ 5 ที่หอประชุม T-VET ครับ",
        "ผู้เข้าประกวดต้องทำอะไร,วันที่ 11 ทำอะไร,ขั้นตอนวันแรก,รายงานตัวแล้วต้องทำอะไร,what do entrants do on day one",
        "What do entrants have to do on 11 November?",
        "From 13:00 to 14:00 register and check in on floor 1 of building 9 and hand in the feature form and the "
        "report books. From 14:00 to 17:30 set up the booth: categories 1, 2, 3, 6 and 7 on the open lawn behind "
        "building 9, categories 4 and 5 in the T-VET hall.")

    # ------------------------------------------------------------------ registration and check-in, 11 Nov
    add(REGISTER, "ลงทะเบียนผลงานและรายงานตัวที่ไหน เวลาไหน",
        "วันพุธที่ 11 พฤศจิกายน 13.00 ถึง 14.00 น. ที่อาคาร 9 ชั้น 1 ครับ",
        "รายงานตัว,ลงทะเบียนที่ไหน,จุดลงทะเบียน,ลงทะเบียนผลงาน,ลงทะเบียนกี่โมง,เวลารายงานตัว,check-in,registration desk",
        "Where and when do I register and check in?",
        "On Wednesday 11 November from 13:00 to 14:00 on floor 1 of building 9.", True)
    add(REGISTER, "วันประกวดต้องนำอะไรมาที่จุดลงทะเบียน",
        "มี 3 อย่างครับ 1 รูปเล่ม 2 เล่ม ประกอบด้วย ว-สอศ-2 ว-สอศ-3 และภาคผนวก "
        "2 แบบคุณลักษณะ 1 แผ่น พิมพ์จากระบบ thaiinvention.net ให้อยู่ใน 1 หน้ากระดาษ A4 "
        "และ 3 สไลด์ Presentation สำหรับนำเสนอ เฉพาะประเภทที่ 2",
        "ต้องนำอะไรมา,เอกสารที่ต้องนำมา,เอกสารลงทะเบียน,เอกสารรายงานตัว,นำอะไรมาลงทะเบียน,เอกสารที่ต้องนำมาลงทะเบียน,ต้องเตรียมอะไร,documents to bring",
        "What do I bring to the registration desk?",
        "Three things: 1 two report books, with form W-SOS-2, form W-SOS-3 and the appendix; 2 one feature form, printed from "
        "thaiinvention.net on a single A4 page; 3 presentation slides, for category 2 only.", True)
    add(REGISTER, "รูปเล่มที่ต้องนำมาลงทะเบียนมีกี่เล่ม",
        "รูปเล่ม 2 เล่มครับ แต่ละเล่มประกอบด้วย ว-สอศ-2 ว-สอศ-3 และภาคผนวก",
        "รูปเล่ม,รูปเล่มกี่เล่ม,กี่เล่ม,2 เล่ม,report book,book",
        "How many report books do I bring?",
        "Two report books, each with form W-SOS-2, form W-SOS-3 and the appendix.")
    add(REGISTER, "แบบคุณลักษณะต้องนำมากี่แผ่น พิมพ์จากไหน",
        "แบบคุณลักษณะ 1 แผ่นครับ ต้องพิมพ์จากระบบ thaiinvention.net และต้องอยู่ใน 1 หน้ากระดาษ A4",
        "แบบคุณลักษณะ,แบบแสดงคุณลักษณะ,คุณลักษณะผลงาน,feature form,A4,1 แผ่น,พิมพ์จากระบบ",
        "How many feature forms do I bring, and where do I print it?",
        "One feature form, printed from the thaiinvention.net system, and it must fit on a single A4 page.")
    add(REGISTER, "สไลด์นำเสนอ Presentation ต้องมีทุกประเภทไหม",
        "ไม่ต้องครับ สไลด์ Presentation สำหรับนำเสนอ ใช้เฉพาะประเภทที่ 2",
        "สไลด์,สไลด์นำเสนอ,presentation,พรีเซนต์,พรีเซนเทชัน,ต้องมีสไลด์ไหม,slides",
        "Do all categories need presentation slides?",
        "No. Presentation slides are for category 2 only.")
    add(REGISTER, "ลงทะเบียนและประชุมคณะกรรมการตัดสินเมื่อไหร่ ที่ไหน",
        "วันพุธที่ 11 พฤศจิกายน 13.00 ถึง 14.00 น. ที่อาคาร 9 ชั้น 3 ครับ",
        "กรรมการประชุมกี่โมง,ประชุมกรรมการกี่โมง,กรรมการประชุมที่ไหน,ลงทะเบียนกรรมการ,ประชุมกรรมการ,ประชุมคณะกรรมการตัดสิน,ลงทะเบียนคณะกรรมการ,กรรมการตัดสินลงทะเบียน,judges registration,judges meeting",
        "When and where do the judges register and meet?",
        "On Wednesday 11 November from 13:00 to 14:00 on floor 3 of building 9.", True)
    add(REGISTER, "คณะกรรมการเริ่มตรวจให้คะแนนเอกสารเมื่อไหร่ ที่ไหน",
        "วันที่ 11 พฤศจิกายน 14.00 ถึง 17.30 น. ที่ห้องประชุม อาคาร 9 ชั้น 1 ครับ",
        "ตรวจเอกสาร,ให้คะแนนเอกสาร,ตรวจให้คะแนนเอกสาร,กรรมการตรวจเอกสาร,ตรวจเอกสารที่ไหน,scoring documents",
        "When and where do the judges start scoring the documents?",
        "On 11 November from 14:00 to 17:30 in the meeting room on floor 1 of building 9.")
    add(REGISTER, "ติดตั้งบูธนำเสนอผลงานได้เมื่อไหร่",
        "วันที่ 11 พฤศจิกายน 14.00 ถึง 17.30 น. ครับ ผู้เข้าประกวดเข้าติดตั้งบูธนำเสนอผลงานได้ในช่วงนั้น",
        "ติดตั้งบูธ,ติดตั้งผลงาน,ตั้งบูธ,เข้าติดตั้ง,ติดตั้งกี่โมง,เวลาติดตั้ง,booth set-up,set up",
        "When can I set up my booth?",
        "On 11 November from 14:00 to 17:30.")
    add(REGISTER, "ติดตั้งบูธที่ไหน",
        "แบ่งตามประเภทครับ ประเภทที่ 1 2 3 6 และ 7 ที่ลานอเนกประสงค์หลังอาคาร 9 "
        "ส่วนประเภทที่ 4 และ 5 ที่หอประชุม T-VET",
        "บูธอยู่ที่ไหน,ติดตั้งผลงานที่ไหน,ตั้งบูธที่ไหน,พื้นที่บูธ,จุดติดตั้ง,where to set up,booth location",
        "Where do I set up my booth?",
        "By category: categories 1, 2, 3, 6 and 7 on the open lawn behind building 9; categories 4 and 5 in the T-VET hall.", True)

    # ------------------------------------------------------------------ where each category is shown
    for number in range(1, 8):
        name, name_en = CATEGORY_NAMES[number - 1]
        if number in (4, 5):
            place, place_en = "หอประชุม T-VET", "the T-VET hall"
            answer = f"ประเภทที่ {number} {name} แสดงผลงานที่หอประชุม T-VET ครับ ติดตั้งบูธวันที่ 11 พฤศจิกายน 14.00 ถึง 17.30 น."
        else:
            place, place_en = "ลานอเนกประสงค์หลังอาคาร 9", "the open lawn behind building 9"
            answer = (
                f"ประเภทที่ {number} {name} แสดงผลงานที่ลานอเนกประสงค์หลังอาคาร 9 ครับ "
                "ติดตั้งบูธวันที่ 11 พฤศจิกายน 14.00 ถึง 17.30 น."
            )
            if number == 2:
                answer += " และมีห้อง 933 ที่อาคาร 9 ชั้น 3 เป็นห้องนำเสนอผลงานประเภทที่ 2"
        add(SCHEDULE, f"ผลงานประเภทที่ {number} แสดงที่ไหน",
            answer,
            f"ประเภทที่ {number} แสดงที่ไหน,ประเภทที่ {number} บูธ,ประเภทที่ {number} อยู่ตรงไหน,จุดแสดงประเภทที่ {number},category {number} where,{place}",
            f"Where is category {number} shown?",
            f"Category {number} ({name_en}) is shown at {place_en}; booths go up on 11 November from 14:00 to 17:30."
            + (" Room 933 on floor 3 of building 9 is the presentation room for category 2." if number == 2 else ""))

    # ------------------------------------------------------------------ the map and the layout plan
    add(MAP, "แผนผังงานมีกี่จุด อะไรบ้าง",
        "แผนผังจัดกิจกรรมมี 5 จุดครับ จุดที่ 1 ตึกอำนวยการ จุดที่ 2 อาคาร 9 จุดที่ 3 ลานอเนกประสงค์ "
        "จุดที่ 4 หอประชุม T-VET และจุดที่ 5 ลานจอดรถสนามฟุตบอล",
        "แผนผัง,แผนที่,แผนผังงาน,แผนผังบริเวณ,ผังงาน,มีกี่จุด,จุดที่ 1,จุดที่ 2,จุดที่ 3,จุดที่ 4,จุดที่ 5,map,layout",
        "How many points are on the event layout plan?",
        "Five: 1 the administration building, 2 building 9, 3 the open lawn, 4 the T-VET hall and 5 the football field car park.", True)
    add(MAP, "การประกวดครั้งนี้เป็นกลุ่มจังหวัดไหน",
        "เป็นกลุ่มจังหวัดที่ 5 ครับ คือจังหวัดชลบุรีและสมุทรปราการ จัดที่วิทยาลัยเทคนิคสมุทรปราการ "
        "ระหว่างวันที่ 11 ถึง 12 พฤศจิกายน 2569",
        "กลุ่มจังหวัด,กลุ่มที่ 5,กลุ่มจังหวัดที่ 5,ชลบุรี-สมุทรปราการ,ชลบุรีสมุทรปราการ,กลุ่มจังหวัดไหน,which group",
        "Which provincial group is this contest for?",
        "Provincial group 5: Chonburi and Samut Prakan, held at Samut Prakan Technical College on 11 and 12 November 2026.")
    add(MAP, "ตึกอำนวยการอยู่ตรงไหน มีอะไรบ้าง",
        "ตึกอำนวยการคือจุดที่ 1 ในแผนผังครับ มีห้องรับรองผู้อำนวยการ ใช้รับรองประธานเปิดงาน "
        "และห้องประชุมทองขาว ใช้รับรองคณะผู้บริหารและผู้ติดตาม",
        "ตึกอำนวยการ,อาคารอำนวยการ,อำนวยการ,ห้องรับรอง,ห้องรับรองผู้อำนวยการ,ประธานเปิดงาน,จุดที่ 1,administration building",
        "Where is the administration building and what is in it?",
        "It is point 1 on the plan. It has the director's reception room, for the chairman of the opening, and the "
        "Thong Khao meeting room, for the executives and their companions.")
    add(MAP, "ห้องประชุมทองขาวคือห้องอะไร",
        "ห้องประชุมทองขาวอยู่ที่ตึกอำนวยการ ใช้รับรองคณะผู้บริหารและผู้ติดตามครับ",
        "ทองขาว,ห้องทองขาว,ห้องประชุมทองขาว,รับรองผู้บริหาร,Thong Khao",
        "What is the Thong Khao meeting room?",
        "It is in the administration building and is used to receive the executives and their companions.")
    add(MAP, "ห้องรับรองประธานในพิธีอยู่ที่ไหน",
        "ห้องรับรองผู้อำนวยการ ที่ตึกอำนวยการ ใช้รับรองประธานเปิดงานครับ",
        "ห้องรับรองประธาน,รับรองประธาน,ประธานในพิธี,ประธานเปิดงาน,ห้องผู้อำนวยการ,chairman reception",
        "Where is the chairman's reception room?",
        "The director's reception room in the administration building is used to receive the chairman of the opening.")
    add(MAP, "อาคาร 9 อยู่ตรงไหน",
        "อาคาร 9 คือจุดที่ 2 ในแผนผังครับ สูง 4 ชั้น มีป้ายเลข 9 ติดอยู่ที่ระเบียง "
        "ด้านหลังเป็นลานอเนกประสงค์ที่แสดงผลงาน",
        "อาคาร 9,อาคารเก้า,อาคาร9,อาคาร 9 อยู่ไหน,หาอาคาร 9,จุดที่ 2,building 9",
        "Where is building 9?",
        "Building 9 is point 2 on the plan. It has four floors and a number 9 sign on its balcony. The open lawn "
        "where the works are shown is behind it.", True)
    add(MAP, "ลานอเนกประสงค์คืออะไร อยู่ตรงไหน",
        "ลานอเนกประสงค์หลังอาคาร 9 คือจุดที่ 3 ในแผนผังครับ เป็นพื้นที่จัดแสดงผลงานสิ่งประดิษฐ์ของคนรุ่นใหม่ "
        "ประเภทที่ 1 2 3 6 และ 7",
        "ลานอเนกประสงค์,ลานหลังอาคาร 9,ลานแสดงผลงาน,พื้นที่จัดแสดง,นิทรรศการ,จุดที่ 3,open lawn",
        "What is the open lawn and where is it?",
        "The open lawn behind building 9 is point 3 on the plan. It is where categories 1, 2, 3, 6 and 7 are shown.")
    add(MAP, "หอประชุม T-VET ใช้ทำอะไร",
        "หอประชุม T-VET เป็นจุดที่ 4 ครับ เป็นที่แสดงผลงานประเภทที่ 4 และ 5 "
        "และในแผนผังวิทยาลัยทำเครื่องหมายเป็นจุดลงทะเบียนผลงานด้วย แต่การลงทะเบียนวันที่ 11 ตามกำหนดการอยู่ที่อาคาร 9 ชั้น 1",
        "T-VET,ทีเวท,หอประชุม,หอประชุมทีเวท,หอประชุม T-VET,จุดที่ 4,T-VET hall",
        "What is the T-VET hall used for?",
        "It is point 4. Categories 4 and 5 are shown there, and the campus map also marks it as a registration point, but "
        "the schedule puts the 11 November registration on floor 1 of building 9.")
    add(MAP, "จุดแสดงผลงานอยู่ตรงไหน",
        "ผลงานประเภทที่ 1 2 3 6 และ 7 แสดงที่ลานอเนกประสงค์หลังอาคาร 9 "
        "ส่วนประเภทที่ 4 และ 5 แสดงที่หอประชุม T-VET ครับ",
        "จุดแสดงผลงาน,ดูผลงาน,ดูบูธ,นิทรรศการอยู่ไหน,แสดงผลงานที่ไหน,ที่แสดงผลงาน,exhibition,where are the exhibits",
        "Where are the inventions exhibited?",
        "Categories 1, 2, 3, 6 and 7 on the open lawn behind building 9; categories 4 and 5 in the T-VET hall.", True)
    add(MAP, "ที่จอดรถอยู่ที่ไหน",
        "จอดรถได้ที่ลานจอดรถสนามฟุตบอล ซึ่งเป็นจุดที่ 5 ในผังครับ และในแผนผังวิทยาลัยมีพื้นที่จอดรถทำเครื่องหมายไว้หลายจุด",
        "ที่จอดรถ,จอดรถ,ลานจอดรถ,จอดรถที่ไหน,สนามฟุตบอล,จุดที่ 5,parking,car park,where to park",
        "Where can I park?",
        "At the football field car park, point 5 on the plan. The campus map marks several other car parks too.", True)
    add(MAP, "ห้องน้ำอยู่ที่ไหน",
        "ในแผนผังวิทยาลัยมีห้องน้ำหลายจุดครับ ใช้สัญลักษณ์หมายเลข 6 ดูตำแหน่งจากแผนผังหรือสอบถามเจ้าหน้าที่ได้เลย",
        "ห้องน้ำ,ส้วม,toilet,restroom,bathroom,ห้องสุขา,where is the toilet,where is the restroom",
        "Where is the toilet?",
        "The campus map shows several restrooms, marked with the number 6. Check the map or ask a member of staff.", True)
    add(MAP, "โรงอาหารอยู่ที่ไหน",
        "ในแผนผังวิทยาลัยมีโรงอาหารทำเครื่องหมายหมายเลข 7 อยู่สองจุดครับ ดูตำแหน่งจากแผนผังหรือสอบถามเจ้าหน้าที่ได้เลย",
        "โรงอาหาร,โรงอาหารอยู่ไหน,กินข้าวที่ไหน,ร้านอาหาร,canteen,cafeteria,where to eat",
        "Where is the canteen?",
        "The campus map marks the canteen, number 7, in two places. Check the map or ask a member of staff.", True)
    add(MAP, "ทางเข้างานอยู่ตรงไหน",
        "ในผังจัดกิจกรรม ทางเข้าอยู่ด้านตึกอำนวยการ ซึ่งเป็นจุดที่ 1 ครับ",
        "ทางเข้า,ทางเข้างาน,เข้างานทางไหน,ทางเข้าวิทยาลัย,entrance,entry,where is the entrance",
        "Where is the entrance?",
        "On the layout plan the entrance is by the administration building, point 1.")

    # ------------------------------------------------------------------ building 9
    add(BUILDING, "อาคาร 9 แต่ละชั้นมีอะไรบ้าง",
        "ชั้น 1 ลงทะเบียนผลงานและรายงานตัว ห้องคณะกรรมการแบ่งเป็นกลุ่มประเภท ห้องรับประทานอาหาร และห้องประกันซึ่งเป็นห้องประมวลผลคะแนน "
        "ชั้น 3 ลงทะเบียนและห้องประชุมคณะกรรมการตัดสินทั้ง 7 ประเภท และห้อง 933 ห้องนำเสนอผลงานประเภทที่ 2 "
        "ชั้น 4 พิธีเปิดและพิธีปิดงานครับ",
        "อาคาร 9 มีอะไร,อาคาร 9 ชั้น,แต่ละชั้น,สถานที่จัดกิจกรรมอาคาร 9,อาคาร 9 ทุกชั้น,building 9 floors",
        "What is on each floor of building 9?",
        "Floor 1: registration and check-in, the judges' room split by category and lunch room, and the assurance room "
        "where scores are tallied. Floor 3: registration and the meeting room for the judges of all 7 categories, and room "
        "933 for category 2 presentations. Floor 4: the opening and closing ceremonies.", True)
    add(BUILDING, "อาคาร 9 ชั้น 1 มีอะไร",
        "ชั้น 1 มี 4 อย่างครับ ลงทะเบียนผลงานและรายงานตัว ห้องคณะกรรมการแบ่งเป็นกลุ่มประเภทหรือห้องรับประทานอาหาร "
        "ห้องประมวลผลคะแนนหรือห้องประกัน และห้องประชุมที่กรรมการตรวจเอกสาร",
        "ชั้น 1,อาคาร 9 ชั้น 1,ชั้นหนึ่ง,ห้องประชุมชั้น 1,floor 1",
        "What is on floor 1 of building 9?",
        "Registration and check-in; the judges' room split by category, also the lunch room; the score-tally room, "
        "the assurance room; and the meeting room where the judges check the documents.")
    add(BUILDING, "อาคาร 9 ชั้น 3 มีอะไร",
        "ชั้น 3 มีลงทะเบียนและห้องประชุมคณะกรรมการตัดสินทั้ง 7 ประเภท "
        "กับห้อง 933 ซึ่งเป็นห้องนำเสนอผลงานสิ่งประดิษฐ์ประเภทที่ 2 ครับ",
        "ชั้น 3,อาคาร 9 ชั้น 3,ชั้นสาม,floor 3",
        "What is on floor 3 of building 9?",
        "Registration and the meeting room for the judges of all 7 categories, plus room 933, the presentation room for category 2.")
    add(BUILDING, "อาคาร 9 ชั้น 4 มีอะไร",
        "ชั้น 4 ใช้จัดพิธีเปิดและพิธีปิดงาน การแข่งขันสิ่งประดิษฐ์ครับ",
        "ชั้น 4,อาคาร 9 ชั้น 4,ชั้นสี่,ชั้นบนสุด,พิธีเปิด-ปิด,floor 4",
        "What is on floor 4 of building 9?",
        "The opening and closing ceremonies of the invention contest.")
    add(BUILDING, "ห้อง 933 คือห้องอะไร",
        "ห้อง 933 อยู่ที่อาคาร 9 ชั้น 3 เป็นห้องนำเสนอผลงานสิ่งประดิษฐ์ประเภทที่ 2 ครับ",
        "ห้อง 933,933,ห้องเก้าสามสาม,ห้องนำเสนอ,ห้องนำเสนอประเภทที่ 2,room 933",
        "What is room 933?",
        "It is on floor 3 of building 9 and is the presentation room for category 2 inventions.")
    add(BUILDING, "ห้องรับประทานอาหารอยู่ที่ไหน",
        "ที่อาคาร 9 ชั้น 1 ครับ เป็นห้องเดียวกับห้องคณะกรรมการแบ่งเป็นกลุ่มประเภท "
        "ส่วนโรงอาหารของวิทยาลัยดูหมายเลข 7 ในแผนผัง",
        "ห้องรับประทานอาหาร,ห้องอาหาร,อาหารกลางวันที่ไหน,กรรมการกินข้าวที่ไหน,lunch room",
        "Where is the lunch room?",
        "On floor 1 of building 9, the same room as the judges' room split by category. The college canteen is number 7 on the map.")
    add(BUILDING, "ห้องคณะกรรมการตามประเภทอยู่ที่ไหน",
        "อยู่ที่อาคาร 9 ชั้น 1 ครับ เป็นห้องคณะกรรมการแบ่งเป็นกลุ่มประเภท",
        "ห้องคณะกรรมการ,ห้องกรรมการ,ห้องกรรมการตามประเภท,กรรมการแบ่งกลุ่ม,judges room",
        "Where is the judges' room for each category?",
        "On floor 1 of building 9, split into groups by category.")

    # ------------------------------------------------------------------ the concept papers of provincial group 5
    source = "ตามสรุปข้อเสนอเชิงแนวคิด (Concept Paper) ของกลุ่มจังหวัดที่ 5"
    source_en = "according to the concept paper summary of provincial group 5"
    group_keys = "กลุ่มจังหวัดที่ 5,กลุ่มที่ 5,concept paper,คอนเซ็ปต์เปเปอร์,สรุปข้อเสนอเชิงแนวคิด,ข้อเสนอเชิงแนวคิด"
    add(GROUP5, "กลุ่มจังหวัดที่ 5 มีผลงานทั้งหมดกี่ชิ้น",
        f"{source} มีผลงานทั้งหมด 99 ชิ้นครับ สอจ.ชลบุรี 68 ชิ้น และ สอจ.สมุทรปราการ 31 ชิ้น",
        f"{group_keys},กลุ่มจังหวัดนี้กี่ชิ้น,ผลงานกลุ่มจังหวัด,กี่ชิ้นในกลุ่ม,กลุ่มจังหวัดที่ 5 มีผลงานกี่ชิ้น,กลุ่มที่ 5 กี่ชิ้น,ทั้งกลุ่มกี่ชิ้น,ผลงานทั้งหมดของกลุ่ม,99,group 5 total",
        "How many works are in provincial group 5?",
        f"{source_en.capitalize()}, 99 works: 68 from the Chonburi vocational office and 31 from Samut Prakan.", True)
    add(GROUP5, "สอจ.ชลบุรีมีผลงานในกลุ่มจังหวัดที่ 5 กี่ชิ้น",
        f"{source} สอจ.ชลบุรี มี 68 ชิ้นครับ ประเภทที่ 1 มี 16 ประเภทที่ 2 มี 7 ประเภทที่ 3 มี 12 ประเภทที่ 4 มี 11 "
        "ประเภทที่ 5 มี 7 ประเภทที่ 6 มี 13 และประเภทที่ 7 มี 2 ชิ้น",
        f"{group_keys},สอจ.ชลบุรีมีผลงานกี่ชิ้น,สอจ.ชลบุรี,สอจชลบุรี,สำนักงานอาชีวศึกษาจังหวัดชลบุรี,68,ชลบุรีในกลุ่ม,Chonburi group 5",
        "How many works does the Chonburi office have in provincial group 5?",
        f"{source_en.capitalize()}, 68: category 1 has 16, category 2 has 7, category 3 has 12, category 4 has 11, "
        "category 5 has 7, category 6 has 13 and category 7 has 2.")
    add(GROUP5, "สอจ.สมุทรปราการมีผลงานในกลุ่มจังหวัดที่ 5 กี่ชิ้น",
        f"{source} สอจ.สมุทรปราการ มี 31 ชิ้นครับ ประเภทที่ 1 มี 7 ประเภทที่ 2 มี 5 ประเภทที่ 3 มี 6 ประเภทที่ 4 มี 3 "
        "ประเภทที่ 5 ไม่มี ประเภทที่ 6 มี 10 และประเภทที่ 7 ไม่มี",
        f"{group_keys},สอจ.สมุทรปราการมีผลงานกี่ชิ้น,สอจ.สมุทรปราการ,สอจสมุทรปราการ,สำนักงานอาชีวศึกษาจังหวัดสมุทรปราการ,31,สมุทรปราการในกลุ่ม,Samut Prakan group 5",
        "How many works does the Samut Prakan office have in provincial group 5?",
        f"{source_en.capitalize()}, 31: category 1 has 7, category 2 has 5, category 3 has 6, category 4 has 3, category 5 "
        "has none, category 6 has 10 and category 7 has none.")
    for number in range(1, 8):
        index = number - 1
        name, name_en = CATEGORY_NAMES[index]
        ch, sp, both = CHONBURI[index], SAMUT_PRAKAN[index], TOTALS[index]
        sp_text = "สมุทรปราการไม่มี" if sp == 0 else f"สมุทรปราการ {sp} ชิ้น"
        extra = ""
        if number == 7:
            extra = " ซึ่งเป็นประเภทหุ่นยนต์ประชาสัมพันธ์อัจฉริยะเหมือนตัวผมเองครับ"
        add(GROUP5, f"ประเภทที่ {number} ในกลุ่มจังหวัดที่ 5 มีผลงานกี่ชิ้น",
            f"{source} ประเภทที่ {number} {name} มี {both} ชิ้นครับ ชลบุรี {ch} ชิ้น {sp_text}{extra}",
            f"{group_keys},ประเภทที่ {number} กี่ชิ้น,ประเภทที่ {number} มีกี่ชิ้น,ประเภทที่ {number} มีผลงานกี่ชิ้น,ประเภทที่ {number} มีกี่ผลงาน,จำนวนผลงานประเภทที่ {number},category {number} count",
            f"How many category {number} works are in provincial group 5?",
            f"{source_en.capitalize()}, category {number} ({name_en}) has {both}: Chonburi {ch}, Samut Prakan {sp}.")
    ranked = sorted(range(7), key=lambda i: (-TOTALS[i], i))
    add(GROUP5, "ประเภทไหนมีผลงานมากที่สุดในกลุ่มจังหวัดที่ 5",
        f"{source} ประเภทที่ 1 และประเภทที่ 6 มากที่สุดเท่ากันครับ ประเภทละ 23 ชิ้น รองลงมาคือประเภทที่ 3 มี 18 ชิ้น",
        f"{group_keys},ประเภทไหนมีผลงานมากที่สุด,ประเภทไหนมีผลงานเยอะที่สุด,ประเภทไหนมากที่สุด,ประเภทที่มีผลงานเยอะ,ประเภทไหนเยอะสุด,most popular category",
        "Which category has the most works in provincial group 5?",
        f"{source_en.capitalize()}, categories 1 and 6 tie with 23 each, then category 3 with 18.")
    add(GROUP5, "ประเภทไหนมีผลงานน้อยที่สุดในกลุ่มจังหวัดที่ 5",
        f"{source} ประเภทที่ 7 หุ่นยนต์ประชาสัมพันธ์อัจฉริยะ น้อยที่สุดครับ มี 2 ชิ้น ทั้งสองชิ้นเป็นของสอจ.ชลบุรี "
        "รองลงมาคือประเภทที่ 5 มี 7 ชิ้น",
        f"{group_keys},ประเภทไหนมีผลงานน้อยที่สุด,ประเภทไหนน้อยที่สุด,ประเภทที่มีผลงานน้อย,ประเภทไหนน้อยสุด,fewest category",
        "Which category has the fewest works in provincial group 5?",
        f"{source_en.capitalize()}, category 7, the smart public-relations robots, has the fewest: 2, both from Chonburi, "
        "then category 5 with 7.")
    add(GROUP5, "เรียงลำดับจำนวนผลงานแต่ละประเภทในกลุ่มจังหวัดที่ 5",
        f"{source} เรียงจากมากไปน้อยครับ ประเภทที่ 1 และ 6 ประเภทละ 23 ชิ้น ประเภทที่ 3 มี 18 ประเภทที่ 4 มี 14 "
        "ประเภทที่ 2 มี 12 ประเภทที่ 5 มี 7 และประเภทที่ 7 มี 2 ชิ้น",
        f"{group_keys},เรียงลำดับประเภท,จัดอันดับประเภท,ผลงานแต่ละประเภท,แต่ละประเภทกี่ชิ้น,by category",
        "Rank the categories in provincial group 5 by number of works.",
        f"{source_en.capitalize()}: categories 1 and 6 have 23 each, category 3 has 18, category 4 has 14, category 2 has 12, "
        "category 5 has 7 and category 7 has 2.", True)
    add(GROUP5, "ประเภทไหนที่สมุทรปราการไม่มีผลงาน",
        f"{source} สอจ.สมุทรปราการไม่มีผลงานประเภทที่ 5 เพื่อสุขภาพ และประเภทที่ 7 หุ่นยนต์ประชาสัมพันธ์อัจฉริยะครับ",
        f"{group_keys},สมุทรปราการไม่มี,ไม่มีผลงาน,ประเภทที่ว่าง,no works",
        "Which categories has Samut Prakan no works in?",
        f"{source_en.capitalize()}, Samut Prakan has no works in category 5, health care, or category 7, smart public-relations robots.")
    add(GROUP5, "ตัวเลขชลบุรี 63 กับ 68 ต่างกันทำไม",
        "ตัวเลขมาจากรายงานคนละฉบับครับ รายงานจำนวนสิ่งประดิษฐ์ ปี 2569 ระดับ อศจ. ของชลบุรีคือ 63 ชิ้น "
        "ส่วนสรุปข้อเสนอเชิงแนวคิด Concept Paper ของกลุ่มจังหวัดที่ 5 คือ 68 ชิ้น สมุทรปราการก็เช่นกัน คือ 30 กับ 31 ชิ้น "
        "ผมไม่มีข้อมูลว่าทำไมตัวเลขจึงต่างกัน สอบถามเจ้าหน้าที่ได้ครับ",
        "63 กับ 68,ต่างกันทำไม,ตัวเลขไม่ตรงกัน,ทำไมไม่เท่ากัน,30 กับ 31,ตัวเลขต่างกัน,why different numbers",
        "Why do the Chonburi figures 63 and 68 differ?",
        "They come from different reports. The 2026 provincial report gives Chonburi 63; the concept paper summary of group 5 "
        "gives 68. Samut Prakan likewise: 30 and 31. I do not know why they differ; please ask the staff.")
    return entries
