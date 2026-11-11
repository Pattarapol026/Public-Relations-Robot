"""College / place of education."""
VENUE = "education"

ENTRIES = [
    # --- the institution ---
    ("ข้อมูลหน่วยงาน", "แนะนำวิทยาลัยหน่อย",
     "วิทยาลัยของเราเปิดสอนหลักสูตรวิชาชีพ เพื่อให้ผู้เรียนมีความรู้และทักษะไปทำงานหรือเรียนต่อได้ "
     "ถามผมเรื่องแผนกวิชา การสมัครเรียน หรือบริการนักศึกษาได้เลยครับ",
     "Tell me about the college.",
     "Our college offers vocational programmes that give students the knowledge and skills "
     "to work or continue their studies. Ask me about departments, admission or student services.",
     "แนะนำ,ประวัติ,ข้อมูลทั่วไป,about,introduction", False),
    ("ข้อมูลหน่วยงาน", "วิทยาลัยตั้งอยู่ที่ไหน",
     "ดูที่อยู่และแผนที่ของวิทยาลัยได้ที่เว็บไซต์หรือเพจของวิทยาลัย หรือสอบถามเจ้าหน้าที่ประชาสัมพันธ์ครับ",
     "Where is the college?",
     "You can find the college's address and map on its website or Facebook page, "
     "or ask the information desk.",
     "ที่ตั้ง,ที่อยู่,แผนที่,address,location,map", False),
    # --- programmes ---
    ("หลักสูตร", "แต่ละแผนกเรียนเกี่ยวกับอะไร",
     "แต่ละแผนกเรียนทั้งทฤษฎีและฝึกปฏิบัติในสาขาวิชาชีพนั้น ๆ ครับ บอกชื่อแผนกที่สนใจได้เลย "
     "หรือขอรายละเอียดหลักสูตรได้ที่แผนกวิชาหรืองานแนะแนวครับ",
     "What does each department teach?",
     "Each department teaches theory and hands-on practice in its own field. Tell me which "
     "department interests you, or ask the department or the guidance office for the curriculum.",
     "เรียนอะไร,วิชา,course,curriculum,subjects", False),
    ("หลักสูตร", "เรียนกี่ปีจบ และได้วุฒิอะไร",
     "ระดับ ปวช. เรียน 3 ปี รับผู้จบ ม.3 ระดับ ปวส. เรียน 2 ปี รับผู้จบ ปวช. หรือ ม.6 "
     "เมื่อเรียนจบจะได้วุฒิประกาศนียบัตรตามระดับนั้นครับ",
     "How long is the course and what qualification do I get?",
     "The vocational certificate (Por Wor Chor) takes 3 years after Grade 9. The higher "
     "vocational diploma (Por Wor Sor) takes 2 years after the vocational certificate or Grade 12. "
     "You receive the certificate or diploma for that level when you finish.",
     "กี่ปี,วุฒิ,จบ,ปวช,ปวส,degree,certificate,diploma,how long", False),
    ("หลักสูตร", "มีเรียนภาคค่ำหรือวันหยุดไหม",
     "บางสาขาอาจเปิดสอนนอกเวลาหรือภาคสมทบครับ สอบถามรอบที่เปิดได้ที่งานทะเบียนหรืองานแนะแนว",
     "Are there evening or weekend classes?",
     "Some programmes may have evening or part-time classes. Please ask the registration office "
     "or the guidance office which ones are open.",
     "ภาคค่ำ,วันหยุด,ภาคพิเศษ,นอกเวลา,สมทบ,evening,weekend,part-time", False),
    ("หลักสูตร", "ระบบทวิภาคีคืออะไร",
     "ระบบทวิภาคีคือการเรียนที่วิทยาลัยสลับกับฝึกงานจริงในสถานประกอบการ ได้ทั้งความรู้และประสบการณ์ทำงานครับ "
     "สอบถามสาขาที่เปิดได้ที่งานทวิภาคีครับ",
     "What is dual vocational training?",
     "Dual vocational training means studying at college and working in a real company in turns, "
     "so you gain both knowledge and work experience. Ask the dual-training office which "
     "programmes offer it.",
     "ทวิภาคี,dual,dual training,dve", False),
    ("หลักสูตร", "จบแล้วเรียนต่อได้ไหม",
     "ได้ครับ จบ ปวช. เรียนต่อ ปวส. ได้ และจบ ปวส. เรียนต่อปริญญาตรีได้ ขอคำแนะนำเรื่องการเรียนต่อได้ที่งานแนะแนวครับ",
     "Can I continue my studies after graduating?",
     "Yes. After the vocational certificate you can go on to the higher vocational diploma, "
     "and after that to a bachelor's degree. The guidance office can advise you.",
     "เรียนต่อ,ปริญญาตรี,continue,further study,university", False),
    ("หลักสูตร", "จบแล้วทำงานอะไรได้บ้าง",
     "ขึ้นกับสาขาที่เรียนครับ ผู้จบสายอาชีพทำงานในสถานประกอบการ ประกอบอาชีพอิสระ หรือเรียนต่อได้ "
     "สอบถามข้อมูลอาชีพของแต่ละสาขาได้ที่แผนกวิชาหรืองานแนะแนวครับ",
     "What jobs can I get after graduating?",
     "It depends on the programme. Vocational graduates work in companies, run their own "
     "business or continue studying. The department or the guidance office can tell you more.",
     "งาน,อาชีพ,ทำงาน,career,job,after graduating", False),
    # --- admission ---
    ("การสมัคร", "รับสมัครเรียนวันไหนถึงวันไหน",
     "วิทยาลัยจะประกาศกำหนดการรับสมัครในแต่ละปีครับ ติดตามได้ที่เว็บไซต์ เพจของวิทยาลัย หรือสอบถามงานทะเบียน",
     "When is the application period?",
     "The college announces the application dates each year on its website and Facebook page. "
     "You can also ask the registration office.",
     "รับสมัคร,วันรับสมัคร,กำหนดการ,admission date,deadline,application period", False),
    ("การสมัคร", "ขั้นตอนการสมัครเรียน",
     "โดยทั่วไปคือ กรอกใบสมัครและยื่นเอกสาร ชำระค่าสมัคร สอบคัดเลือกหรือสัมภาษณ์ตามที่วิทยาลัยกำหนด "
     "ตรวจสอบประกาศรายชื่อ แล้วรายงานตัวและมอบตัวครับ รายละเอียดแต่ละปีสอบถามงานทะเบียนได้ครับ",
     "How do I apply?",
     "Usually you fill in the application and hand in your documents, pay the application fee, "
     "take the selection test or interview if there is one, check the results, then report and "
     "enrol. Ask the registration office for this year's details.",
     "ขั้นตอน,วิธีสมัคร,how to apply,process,steps,application", False),
    ("การสมัคร", "สมัครเรียนออนไลน์ได้ไหม",
     "ถ้าวิทยาลัยเปิดรับสมัครออนไลน์ จะมีลิงก์ประกาศที่เว็บไซต์หรือเพจของวิทยาลัยครับ สอบถามงานทะเบียนได้ครับ",
     "Can I apply online?",
     "If online applications are open, the link is posted on the college website or Facebook page. "
     "Please check with the registration office.",
     "ออนไลน์,เว็บไซต์,online,website,apply online", False),
    ("คุณสมบัติ", "คุณสมบัติผู้สมัครแต่ละระดับ",
     "ระดับ ปวช. รับผู้จบ ม.3 หรือเทียบเท่า ระดับ ปวส. รับผู้จบ ปวช. หรือ ม.6 ในสาขาที่กำหนดครับ "
     "บางสาขาอาจมีเงื่อนไขเพิ่ม สอบถามงานทะเบียนได้ครับ",
     "Who can apply for each level?",
     "The vocational certificate accepts students who finished Grade 9 or equivalent. The higher "
     "vocational diploma accepts holders of a vocational certificate or Grade 12, depending on "
     "the programme. Some programmes have extra requirements; ask the registration office.",
     "คุณสมบัติ,เกณฑ์,วุฒิ,requirement,qualification,eligibility", False),
    ("เอกสาร", "สมัครเรียนต้องใช้เอกสารอะไรบ้าง",
     "โดยทั่วไปใช้ใบสมัคร สำเนาบัตรประชาชน สำเนาทะเบียนบ้าน หลักฐานการศึกษาเช่นใบ ปพ.1 และรูปถ่ายครับ "
     "รายการที่แน่นอนของปีนี้สอบถามงานทะเบียนได้ครับ",
     "What documents do I need to apply?",
     "Usually the application form, a copy of your ID card, a copy of your house registration, "
     "your school transcript, and photos. Ask the registration office for this year's exact list.",
     "เอกสาร,หลักฐาน,สำเนา,ปพ.1,documents,paperwork,transcript", True),
    ("เอกสาร", "นักศึกษาใหม่ต้องเตรียมเอกสารอะไรตอนรายงานตัว",
     "โดยทั่วไปเตรียมหลักฐานการจบการศึกษาตัวจริง สำเนาบัตรประชาชนและทะเบียนบ้านของนักเรียนและผู้ปกครอง "
     "และรูปถ่ายครับ ตรวจรายการเอกสารที่แน่นอนได้จากประกาศรายงานตัวของวิทยาลัยครับ",
     "What should new students bring when they report?",
     "Usually your original graduation documents, copies of ID cards and house registration "
     "for you and your parent, and photos. Check the college's reporting announcement for the "
     "exact list.",
     "รายงานตัว,มอบตัว,นักศึกษาใหม่,new student,enrolment documents,report", False),
    # --- costs and support ---
    ("ค่าใช้จ่าย", "ค่าเทอมเท่าไหร่",
     "ค่าเทอมแตกต่างกันตามระดับและสาขาวิชาครับ สอบถามอัตราล่าสุดได้ที่งานการเงินหรืองานทะเบียนครับ",
     "How much are the tuition fees?",
     "Tuition fees depend on the level and the programme. Please ask the finance or registration "
     "office for the current rates.",
     "ค่าเทอม,ค่าเล่าเรียน,ค่าธรรมเนียม,tuition,fee,cost", True),
    ("ค่าใช้จ่าย", "ค่าสมัครเรียนเท่าไหร่",
     "ค่าสมัครของแต่ละปีจะแจ้งในประกาศรับสมัครครับ สอบถามได้ที่งานทะเบียนครับ",
     "How much is the application fee?",
     "The application fee is given in each year's admission announcement. Please ask the "
     "registration office.",
     "ค่าสมัคร,application fee", False),
    ("ค่าใช้จ่าย", "ผ่อนชำระค่าเทอมได้ไหม",
     "ถ้ามีความจำเป็น ปรึกษาเรื่องการผ่อนผันหรือแบ่งชำระได้ที่งานการเงินครับ",
     "Can I pay the fees in instalments?",
     "If you need to, please talk to the finance office about paying in instalments.",
     "ผ่อน,แบ่งจ่าย,ผ่อนผัน,installment,instalment,payment plan", False),
    ("ทุน", "มีทุนการศึกษาไหม",
     "มีทุนการศึกษาจากหลายแหล่งครับ ติดตามประกาศรับสมัครทุนและสอบถามได้ที่งานแนะแนวหรืองานทุนครับ",
     "Are there scholarships?",
     "Yes, there are scholarships from several sources. Watch for announcements and ask the "
     "guidance or scholarship office.",
     "ทุน,ทุนการศึกษา,scholarship,grant", True),
    ("ทุน", "กู้ยืม กยศ. ได้ไหม",
     "นักเรียนนักศึกษาที่มีคุณสมบัติตามเกณฑ์ของกองทุนเงินให้กู้ยืมเพื่อการศึกษา (กยศ.) ยื่นกู้ได้ครับ "
     "สอบถามขั้นตอนและกำหนดการได้ที่งาน กยศ. ของวิทยาลัยครับ",
     "Can I get a student loan?",
     "Students who meet the criteria of the Student Loan Fund (Kor Yor Sor) can apply. "
     "Ask the college's student loan office about the steps and dates.",
     "กยศ,กู้ยืม,student loan,loan", False),
    # --- contact ---
    ("ติดต่อ", "เบอร์โทรศัพท์และอีเมลของวิทยาลัย",
     "ดูเบอร์โทรศัพท์และอีเมลของวิทยาลัยได้ที่เว็บไซต์หรือเพจของวิทยาลัย หรือขอได้ที่เจ้าหน้าที่ประชาสัมพันธ์ครับ",
     "What is the college's phone number and email?",
     "The phone number and email are on the college website and Facebook page, "
     "or you can ask the information desk.",
     "เบอร์,โทร,อีเมล,phone,email,contact", False),
    ("ติดต่อ", "เว็บไซต์และเพจ Facebook ของวิทยาลัย",
     "ค้นหาชื่อวิทยาลัยใน Google หรือ Facebook ได้เลยครับ หรือขอที่อยู่เว็บไซต์ได้ที่เจ้าหน้าที่ประชาสัมพันธ์ครับ",
     "What is the college's website and Facebook page?",
     "Search for the college's name on Google or Facebook, or ask the information desk for the address.",
     "เว็บไซต์,เพจ,เฟซบุ๊ก,website,facebook,line", False),
    # --- places on campus ---
    ("สถานที่", "ห้องน้ำอยู่ที่ไหน",
     "มีห้องน้ำในแต่ละอาคารครับ ดูป้ายสัญลักษณ์ หรือถามเจ้าหน้าที่ที่ใกล้ที่สุดได้ครับ",
     "Where is the toilet?",
     "There are toilets in each building. Follow the signs or ask the nearest staff member.",
     "ห้องน้ำ,สุขา,toilet,restroom,bathroom", False),
    ("สถานที่", "โรงอาหารอยู่ที่ไหน",
     "สอบถามทางไปโรงอาหารได้ที่เจ้าหน้าที่ประชาสัมพันธ์ หรือดูแผนผังวิทยาลัยครับ",
     "Where is the canteen?",
     "Please ask the information desk for directions to the canteen, or check the campus map.",
     "โรงอาหาร,อาหาร,canteen,cafeteria,food", False),
    ("สถานที่", "ที่จอดรถอยู่ที่ไหน",
     "ผู้มาติดต่อจอดรถในบริเวณที่วิทยาลัยจัดไว้ครับ สอบถามเจ้าหน้าที่รักษาความปลอดภัยที่ประตูได้ครับ",
     "Where can I park?",
     "Visitors park in the area set aside by the college. Please ask the security guard at the gate.",
     "ที่จอดรถ,จอดรถ,parking,car park", False),
    ("สถานที่", "ห้องสมุดอยู่ที่ไหน เปิดกี่โมง",
     "สอบถามตำแหน่งและเวลาเปิดของห้องสมุดได้ที่เจ้าหน้าที่ประชาสัมพันธ์ หรือดูป้ายหน้าห้องสมุดครับ",
     "Where is the library and when is it open?",
     "Please ask the information desk, or check the sign at the library entrance for opening times.",
     "ห้องสมุด,library", False),
    ("สถานที่", "ห้องพยาบาลอยู่ที่ไหน",
     "ถ้าไม่สบาย แจ้งครูหรือเจ้าหน้าที่ที่อยู่ใกล้ได้เลยครับ เจ้าหน้าที่จะพาไปห้องพยาบาล ถ้าฉุกเฉินโทร 1669 ครับ",
     "Where is the nurse's room?",
     "If you feel unwell, tell a teacher or any staff member and they will take you to the nurse's "
     "room. In an emergency, call 1669.",
     "ห้องพยาบาล,ไม่สบาย,first aid,nurse,sick", False),
    # --- times ---
    ("เวลาให้บริการ", "วันเสาร์อาทิตย์เปิดไหม",
     "สำนักงานส่วนใหญ่เปิดวันจันทร์ถึงศุกร์ครับ บางช่วงอาจเปิดวันหยุดเพื่อรับสมัครหรือจัดกิจกรรม "
     "ติดตามประกาศของวิทยาลัยครับ",
     "Is the college open at weekends?",
     "Most offices are open Monday to Friday. Some may open at weekends for admissions or "
     "events; watch the college's announcements.",
     "เสาร์,อาทิตย์,วันหยุด,weekend,saturday,sunday", False),
    ("เวลาให้บริการ", "งานทะเบียนเปิดกี่โมง",
     "สอบถามเวลาทำการของงานทะเบียนได้ที่เจ้าหน้าที่ประชาสัมพันธ์ หรือดูป้ายหน้าห้องงานทะเบียนครับ",
     "When is the registration office open?",
     "Please ask the information desk, or check the sign at the registration office.",
     "งานทะเบียน,ทะเบียน,registrar,registration office hours", False),
    # --- activities and rules ---
    ("กิจกรรม", "ช่วงนี้มีกิจกรรมอะไรบ้าง",
     "ติดตามกิจกรรมล่าสุดได้จากประกาศบนจอนี้ เพจของวิทยาลัย หรือสอบถามงานกิจกรรมนักเรียนนักศึกษาครับ",
     "What events are coming up?",
     "Watch the announcements on this screen and the college's Facebook page, "
     "or ask the student activities office.",
     "กิจกรรม,งาน,event,activity", False),
    ("กิจกรรม", "มีงานเปิดบ้าน (Open House) เมื่อไหร่",
     "วิทยาลัยจะประกาศวันจัดงานเปิดบ้านทางเว็บไซต์และเพจครับ สอบถามงานประชาสัมพันธ์ได้ครับ",
     "When is the open house?",
     "The college announces the open house date on its website and Facebook page. "
     "The public relations office can tell you more.",
     "เปิดบ้าน,open house", False),
    ("ระเบียบ", "ระเบียบการแต่งกายของนักศึกษา",
     "นักศึกษาแต่งเครื่องแบบตามระเบียบของวิทยาลัยครับ รายละเอียดสอบถามงานปกครองหรือครูที่ปรึกษาได้ครับ",
     "What is the student dress code?",
     "Students wear the uniform set out in the college rules. Ask the student affairs office "
     "or your advisor for details.",
     "แต่งกาย,เครื่องแบบ,ชุดนักศึกษา,uniform,dress code", False),
    ("ระเบียบ", "ผู้มาติดต่อต้องแลกบัตรหรือแต่งกายอย่างไร",
     "ผู้มาติดต่อแต่งกายสุภาพครับ หากมีการแลกบัตรผู้มาติดต่อ เจ้าหน้าที่รักษาความปลอดภัยที่ประตูจะแจ้งครับ",
     "Do visitors need a pass or a dress code?",
     "Visitors should dress politely. If a visitor pass is needed, the security guard at the gate "
     "will tell you.",
     "แลกบัตร,ผู้มาติดต่อ,visitor,visitor pass,dress", False),
    ("ฝึกงาน", "มีฝึกงานหรือระบบทวิภาคีไหม",
     "นักศึกษาสายอาชีพต้องฝึกงานหรือฝึกประสบการณ์ในสถานประกอบการตามหลักสูตรครับ "
     "บางสาขาเรียนแบบทวิภาคี สอบถามได้ที่งานทวิภาคีหรือแผนกวิชาครับ",
     "Is there an internship or dual training?",
     "Vocational students do an internship or work experience in a company as part of the "
     "programme. Some programmes use dual training; ask the dual-training office or your department.",
     "ฝึกงาน,ทวิภาคี,สหกิจ,internship,apprenticeship,internship,ฝึกงานไหม", False),
    # --- student services ---
    ("บริการนักศึกษา", "มีหอพักสำหรับนักศึกษาไหม",
     "สอบถามเรื่องหอพักทั้งในและนอกวิทยาลัยได้ที่งานกิจการนักเรียนนักศึกษาครับ",
     "Is there student accommodation?",
     "Please ask the student affairs office about accommodation on or near campus.",
     "หอพัก,ที่พัก,dormitory,accommodation", False),
    ("บริการนักศึกษา", "มีรถรับส่งนักศึกษาไหม",
     "สอบถามเรื่องรถรับส่งและเส้นทางได้ที่งานกิจการนักเรียนนักศึกษาครับ",
     "Is there a student shuttle?",
     "Please ask the student affairs office about shuttles and routes.",
     "รถรับส่ง,รถ,shuttle,bus,transport", False),
    ("บริการนักศึกษา", "ขอใบรับรองผลการเรียนได้ที่ไหน",
     "ยื่นคำร้องขอใบรับรองผลการเรียนหรือใบ รบ. ได้ที่งานทะเบียนครับ เตรียมบัตรประชาชนหรือบัตรนักศึกษาไปด้วย",
     "Where can I get a transcript?",
     "Apply for a transcript at the registration office. Please bring your ID card or student card.",
     "ใบรับรอง,ผลการเรียน,ทรานสคริปต์,รบ,transcript,certificate", False),
    ("บริการนักศึกษา", "ผู้ปกครองติดต่อครูที่ปรึกษาได้อย่างไร",
     "ผู้ปกครองติดต่อผ่านแผนกวิชาของนักศึกษา หรือขอช่องทางติดต่อครูที่ปรึกษาได้ที่งานครูที่ปรึกษาครับ",
     "How can parents contact the advisor?",
     "Parents can contact the advisor through the student's department, or ask the advisory "
     "office for contact details.",
     "ผู้ปกครอง,ครูที่ปรึกษา,parent,advisor,teacher", False),
    ("บริการนักศึกษา", "ชมรมของนักศึกษามีอะไรบ้าง",
     "สอบถามรายชื่อชมรมและการสมัครเข้าชมรมได้ที่งานกิจกรรมนักเรียนนักศึกษาครับ",
     "What student clubs are there?",
     "Please ask the student activities office for the list of clubs and how to join.",
     "ชมรม,club,activities", False),
    ("บริการนักศึกษา", "นักศึกษาใช้ Wi-Fi ของวิทยาลัยได้ไหม",
     "สอบถามการลงทะเบียนใช้ Wi-Fi ได้ที่งานศูนย์ข้อมูลหรือเจ้าหน้าที่ไอทีของวิทยาลัยครับ",
     "Can students use the college Wi-Fi?",
     "Please ask the college's IT or information centre how to register for Wi-Fi.",
     "wifi,ไวไฟ,อินเทอร์เน็ต,internet", False),
    ("บริการนักศึกษา", "ทำบัตรนักศึกษาหายต้องทำอย่างไร",
     "แจ้งทำบัตรนักศึกษาใหม่ได้ที่งานทะเบียนครับ",
     "I lost my student card. What should I do?",
     "Please ask the registration office for a new student card.",
     "บัตรนักศึกษา,บัตรหาย,student card,student id,lost card", False),
    ("บริการนักศึกษา", "ลาป่วยลากิจต้องทำอย่างไร",
     "แจ้งครูที่ปรึกษาและยื่นใบลาตามแบบฟอร์มของวิทยาลัยครับ ถ้าลาป่วยหลายวันอาจต้องมีใบรับรองแพทย์",
     "How do I ask for sick or personal leave?",
     "Tell your advisor and hand in a leave form. For several days of sick leave you may need "
     "a medical certificate.",
     "ลาป่วย,ลากิจ,ใบลา,ขาดเรียน,sick leave,leave,absence", False),
    ("บริการนักศึกษา", "ย้ายสถานศึกษาต้องทำอย่างไร",
     "ปรึกษาครูที่ปรึกษาและยื่นคำร้องขอย้ายที่งานทะเบียนครับ เจ้าหน้าที่จะแจ้งเอกสารที่ต้องใช้",
     "How do I transfer to another school?",
     "Talk to your advisor and submit a transfer request at the registration office. "
     "They will tell you which documents you need.",
     "ย้าย,ย้ายโรงเรียน,ย้ายสถานศึกษา,transfer", False),
    ("บริการนักศึกษา", "มีงานแนะแนวไหม",
     "มีครับ งานแนะแนวให้คำปรึกษาเรื่องการเรียน การเรียนต่อ ทุนการศึกษา และอาชีพครับ",
     "Is there a guidance office?",
     "Yes. The guidance office advises on studies, further education, scholarships and careers.",
     "แนะแนว,ปรึกษา,guidance,counselling,advice", False),
    ("บริการนักศึกษา", "เรียนรักษาดินแดนได้ไหม",
     "สอบถามการสมัครเรียนรักษาดินแดน (ร.ด.) ได้ที่งานกิจการนักเรียนนักศึกษาครับ",
     "Can I do military reserve training?",
     "Please ask the student affairs office about reserve officer training (Ror Dor).",
     "รด,ร.ด.,รักษาดินแดน,military,reserve training", False),
]
