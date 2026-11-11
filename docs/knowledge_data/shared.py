"""Used at every kind of place: the robot itself, and courtesy."""
VENUE = ""

ENTRIES = [
    ("เกี่ยวกับหุ่นยนต์", "คุณทำอะไรได้บ้าง",
     "ผมเป็นหุ่นยนต์ประชาสัมพันธ์ครับ ตอบคำถามเกี่ยวกับสถานที่นี้ได้ทั้งภาษาไทยและภาษาอังกฤษ "
     "จะพูดกับผม พิมพ์ถาม หรือแตะปุ่มคำถามยอดนิยมบนจอก็ได้ครับ",
     "What can you do?",
     "I'm a reception robot. I can answer questions about this place in Thai and English. "
     "You can talk to me, type your question, or tap one of the popular questions on the screen.",
     "ทำอะไรได้,ช่วยอะไรได้,ความสามารถ,what can you do,help", False),
    ("เกี่ยวกับหุ่นยนต์", "คุยภาษาอังกฤษได้ไหม",
     "ได้ครับ ถามเป็นภาษาอังกฤษได้เลย ถ้าจะพูดเป็นภาษาอังกฤษ กดปุ่ม English บนจอก่อนพูดนะครับ",
     "Can you speak English?",
     "Yes, of course. You can ask me in English. If you want to speak in English, "
     "please tap the English button on the screen first.",
     "ภาษาอังกฤษ,english,speak english,language", False),
    ("เกี่ยวกับหุ่นยนต์", "หุ่นยนต์บันทึกภาพหรือเสียงไหม",
     "ไม่บันทึกครับ กล้องใช้วัดระยะเพื่อทักทาย ไม่จดจำใบหน้า และไม่บันทึกภาพหรือเสียงของคุณ",
     "Do you record pictures or voices?",
     "No. My camera only measures distance so I can greet people. I do not recognise faces "
     "and I do not record your picture or your voice.",
     "บันทึก,ถ่ายรูป,กล้อง,ความเป็นส่วนตัว,privacy,record,camera", False),
    ("เกี่ยวกับหุ่นยนต์", "ใช้งานหุ่นยนต์อย่างไร",
     "เดินเข้ามาใกล้ ๆ แล้วพูดคำถามได้เลยครับ หรือกดปุ่มเริ่มสนทนา พิมพ์คำถาม "
     "หรือแตะคำถามยอดนิยมบนจอ ถ้าอยากให้ผมหยุดพูด แบมือให้กล้องดูได้ครับ",
     "How do I use this robot?",
     "Just come closer and ask your question. You can also press Start, type a question, "
     "or tap a popular question on the screen. Show me an open hand if you want me to stop talking.",
     "วิธีใช้,ใช้งาน,how to use,instructions", False),
    ("เกี่ยวกับหุ่นยนต์", "คุณเป็นใคร",
     "ผมชื่อไอร่าครับ เป็นหุ่นยนต์ประชาสัมพันธ์ของที่นี่ คอยต้อนรับและตอบคำถามให้ผู้มาติดต่อ",
     "Who are you?",
     "I'm Aira, the reception robot here. I welcome visitors and answer their questions.",
     "เป็นใคร,คือใคร,แนะนำตัว,who are you,introduce yourself", False),
    ("เกี่ยวกับหุ่นยนต์", "คุณชื่ออะไร",
     "ผมชื่อไอร่าครับ เขียนว่า Aira เป็นหุ่นยนต์ประชาสัมพันธ์ที่ช่วยตอบคำถามที่นี่",
     "What is your name?",
     "My name is Aira. I'm the reception robot here, and I'm happy to answer your questions.",
     "ชื่ออะไร,ชื่อว่าอะไร,ชื่อไร,เรียกว่าอะไร,your name,what is your name,what's your name,aira,ไอร่า", False),
    ("เกี่ยวกับหุ่นยนต์", "คุณเป็น AI ไหม",
     "ใช่ครับ ผมใช้ AI ช่วยเข้าใจคำถามและเรียบเรียงคำตอบ แต่ข้อมูลของสถานที่ผมตอบจากข้อมูลที่เจ้าหน้าที่เตรียมไว้เท่านั้น",
     "Are you an AI?",
     "Yes. I use AI to understand questions and put answers into words, but facts about this place "
     "come only from information prepared by the staff.",
     "ai,ปัญญาประดิษฐ์,artificial intelligence,robot", False),
    ("เกี่ยวกับหุ่นยนต์", "ถ้าคุณตอบไม่ได้ต้องทำอย่างไร",
     "ถ้าผมยังไม่มีข้อมูลเรื่องไหน สอบถามเจ้าหน้าที่ประชาสัมพันธ์ได้เลยครับ เจ้าหน้าที่ยินดีช่วยครับ",
     "What if you cannot answer?",
     "If I don't have the information yet, please ask the staff at the information desk. "
     "They will be happy to help.",
     "ตอบไม่ได้,ไม่รู้,ถามเจ้าหน้าที่,can't answer,ask staff", False),
    ("มารยาท", "ขอบคุณ",
     "ยินดีครับ ถ้ามีอะไรสงสัยเพิ่มเติม ถามผมได้ตลอดเลยนะครับ",
     "Thank you",
     "You're welcome. Ask me any time if you have more questions.",
     "ขอบคุณ,thank you,thanks", False),
    ("มารยาท", "สวัสดี",
     "สวัสดีครับ ยินดีต้อนรับ มีอะไรให้ผมช่วยไหมครับ",
     "Hello",
     "Hello and welcome. How can I help you?",
     "สวัสดี,hello,hi,good morning,good afternoon", False),
    ("มารยาท", "ลาก่อน",
     "ขอบคุณที่มาใช้บริการครับ เดินทางปลอดภัยนะครับ",
     "Goodbye",
     "Thank you for visiting. Have a safe trip.",
     "ลาก่อน,บ๊ายบาย,goodbye,bye,see you", False),
    ("เหตุฉุกเฉิน", "เบอร์โทรฉุกเฉินมีอะไรบ้าง",
     "เบอร์ฉุกเฉินในประเทศไทย เจ็บป่วยฉุกเฉินโทร 1669 เหตุด่วนเหตุร้ายแจ้งตำรวจ 191 "
     "ไฟไหม้โทร 199 และตำรวจท่องเที่ยว 1155 ครับ ถ้าเกิดเหตุในอาคาร แจ้งเจ้าหน้าที่ที่อยู่ใกล้ที่สุดด้วยครับ",
     "What are the emergency numbers?",
     "In Thailand, call 1669 for a medical emergency, 191 for the police, 199 for fire, "
     "and 1155 for the tourist police. If something happens in this building, "
     "please also tell the nearest staff member.",
     "ฉุกเฉิน,เบอร์ฉุกเฉิน,1669,191,199,emergency,police,ambulance", False),
    ("เหตุฉุกเฉิน", "มีคนเป็นลมหรือบาดเจ็บต้องทำอย่างไร",
     "แจ้งเจ้าหน้าที่ที่อยู่ใกล้ที่สุดทันทีครับ ถ้าอาการหนักโทร 1669 "
     "อย่าเคลื่อนย้ายผู้บาดเจ็บถ้าไม่จำเป็น และให้มีคนอยู่ด้วยตลอดครับ",
     "Someone fainted or is hurt. What should I do?",
     "Tell the nearest staff member right away. If it is serious, call 1669. "
     "Don't move the injured person unless necessary, and stay with them.",
     "เป็นลม,บาดเจ็บ,ไม่สบาย,ปฐมพยาบาล,fainted,injured,first aid,sick", False),
    ("เหตุฉุกเฉิน", "ถ้าเกิดไฟไหม้ต้องทำอย่างไร",
     "ตั้งสติ ออกจากอาคารทางบันไดหนีไฟตามป้ายทางออกฉุกเฉิน ห้ามใช้ลิฟต์ "
     "แล้วไปรวมกันที่จุดรวมพล และโทรแจ้ง 199 ครับ",
     "What should I do if there is a fire?",
     "Stay calm and leave the building by the fire stairs, following the exit signs. "
     "Do not use the lift. Go to the assembly point and call 199.",
     "ไฟไหม้,หนีไฟ,ทางออกฉุกเฉิน,fire,fire exit,evacuation", False),
    ("ทั่วไป", "ทำของหายต้องทำอย่างไร",
     "แจ้งเจ้าหน้าที่ประชาสัมพันธ์ได้เลยครับ บอกลักษณะของที่หายและบริเวณที่น่าจะทำหาย "
     "ถ้ามีคนเก็บได้ เจ้าหน้าที่จะติดต่อกลับครับ",
     "I lost something. What should I do?",
     "Please tell the staff at the information desk. Describe the item and where you think "
     "you lost it. If it is found, they will contact you.",
     "ของหาย,ทำของหาย,lost,lost and found,lost item", False),
    ("ทั่วไป", "เก็บของได้ต้องทำอย่างไร",
     "นำไปส่งที่เจ้าหน้าที่ประชาสัมพันธ์ได้เลยครับ ขอบคุณมากที่ช่วยคืนของให้เจ้าของครับ",
     "I found something. What should I do?",
     "Please take it to the information desk staff. Thank you for helping return it to its owner.",
     "เก็บของได้,เจอของ,found,found item", False),
    ("ทั่วไป", "อยากคุยกับเจ้าหน้าที่",
     "ติดต่อเจ้าหน้าที่ได้ที่จุดประชาสัมพันธ์ครับ ถ้าไม่เห็นเจ้าหน้าที่ บอกผมได้ว่าต้องการติดต่อเรื่องอะไร "
     "ผมจะช่วยบอกว่าควรไปที่ไหนครับ",
     "I'd like to talk to a staff member.",
     "You can find staff at the information desk. If you tell me what it is about, "
     "I can tell you where to go.",
     "เจ้าหน้าที่,พนักงาน,คุยกับคน,staff,talk to someone,person", False),
]
