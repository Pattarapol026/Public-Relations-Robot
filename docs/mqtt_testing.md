# ทดสอบ MQTT ของหุ่นยนต์ประชาสัมพันธ์ (ยังไม่มีฮาร์ดแวร์จริง)

เอกสารนี้อธิบายวิธีทดสอบการรับส่งข้อความ MQTT บน Windows ด้วย broker ในเครื่องและ **อุปกรณ์จำลอง**

อุปกรณ์จำลองไม่ใช่หุ่นยนต์ มันไม่สั่งมอเตอร์ ล้อ แขน หรือลำโพงใด ๆ คำสั่ง SPEAK เป็นแค่ตัวจับเวลา ไม่มีเสียงออกจริง ทุกข้อความที่มันส่งมี `"simulated": true`

คำสั่งทั้งหมดรันใน **PowerShell ของ VS Code** (Terminal → New Terminal) โดยอยู่ที่โฟลเดอร์โปรเจกต์:

```powershell
cd C:\receptionRobot
```

## ส่วนประกอบ

| ไฟล์ | หน้าที่ |
|---|---|
| `services/mqtt_service.py` | MQTT client ของโปรแกรมหลัก แยกจากระบบสนทนา UI และหุ่นยนต์ ส่งได้เฉพาะ PING, STATUS, TEST_ECHO, SPEAK, STOP |
| `app/mqtt_controller.py` | ส่งสถานะ MQTT ไปแสดงใน Admin (การ์ด "MQTT Broker" หน้า Dashboard) |
| `tools/mqtt_broker.py` | broker สำหรับทดสอบ (amqtt) ฟังเฉพาะ `127.0.0.1` คอมพิวเตอร์อื่นในเครือข่ายเชื่อมต่อไม่ได้ |
| `tools/mqtt_device_simulator.py` | อุปกรณ์จำลอง: PING→PONG, SPEAK→BUSY→IDLE, STOP, ปฏิเสธข้อความผิด, online/offline |
| `tools/mqtt_system_test.py` | ทดสอบอัตโนมัติแบบครบวงจร เปิด broker และอุปกรณ์จำลองเป็น process แยกจริง |
| `tools/mqtt_check.py` | ทดสอบเร็วด้วยมือ (PING, STATUS, TEST_ECHO, telemetry) |
| `tests/test_mqtt.py` | เทสต์ pytest ของบริการ MQTT |

**Dependencies:** `paho-mqtt` 2.1.0 (client) และ `amqtt` 0.12.1 (broker สำหรับทดสอบ) ติดตั้งไว้ใน `.venv313` แล้ว

ถ้าย้ายเครื่องหรือสร้าง venv ใหม่:

```powershell
.\.venv313\Scripts\python.exe -m pip install paho-mqtt amqtt
```

ไม่ต้องใช้ Docker ไม่ต้องใช้สิทธิ์ admin และไม่ต้องติดตั้ง Mosquitto

## วิธีที่ 1: ทดสอบอัตโนมัติ คำสั่งเดียว (แนะนำ)

```powershell
.\.venv313\Scripts\python.exe tools\mqtt_system_test.py
```

**สิ่งที่สคริปต์ทำ:**
1. เปิด broker บนพอร์ตว่างของ `127.0.0.1` เป็น process แยก
2. เปิดอุปกรณ์จำลองเป็นอีก process
3. ทดสอบ 10 ข้อ แล้วปิดทั้งสอง process เอง (รวมกรณีที่เทสต์ล้มหรือกด Ctrl+C)

ใช้เวลาประมาณ 10 วินาที

**10 ข้อที่ทดสอบ:**
1. เชื่อมต่อ broker
2. PING → PONG
3. SPEAK ไปถึงอุปกรณ์จำลอง: ตรวจจาก log ของอุปกรณ์จำลองเองด้วย
4. สถานะ BUSY → IDLE ของงานนั้น (ตรงกันด้วย `task_id`)
5. STOP:
   - ระหว่าง BUSY ส่ง SPEAK ซ้ำต้องถูกปฏิเสธว่า busy
   - หลัง STOP ต้องได้ `stopped_task: SPEAK`
   - ต้องไม่มีสถานะ "done" ตามมาภายหลัง
6. ไม่มีใครตอบ → ต้องหมดเวลาและ FAIL ไม่ใช่ PASS
7. ข้อความผิดรูปแบบ:
   - JSON เสีย หรือ schema version ผิด → อุปกรณ์จำลอง log แล้วทิ้ง
   - SPEAK ที่ค่า `duration_s` ผิด → ปฏิเสธพร้อมเหตุผล
   - หลังจากนั้นยังตอบ PING ได้
8. ปิด broker แล้วเปิดใหม่ → ทั้ง client และอุปกรณ์จำลองเชื่อมต่อใหม่เอง แล้ว PING ได้อีกครั้ง
9. โปรแกรมหลัก (`Application` แบบไม่เปิดหน้าต่าง และใช้ฐานข้อมูลชั่วคราว ไม่แตะข้อมูลจริง):
   - เปิด MQTT แล้วเชื่อมต่อ broker เดียวกัน
   - **กดปุ่มบนแผง "ควบคุม MQTT" ของ Dashboard:** PING ได้คำตอบจาก SIMULATOR, SPEAK เปลี่ยนสถานะ BUSY → IDLE, STOP หยุดงาน SPEAK
   - ก่อนได้คำตอบ แผงต้องแสดง "รอคำตอบ..." ไม่ใช่ "สำเร็จ"
10. ปิดอุปกรณ์จำลองแล้ว ต้องได้สถานะ offline

**กติกา PASS/FAIL:**
- PASS ได้เฉพาะเมื่อได้รับข้อความตอบกลับจริงที่อ้างถึงคำสั่งที่ส่ง (`reply_to` หรือ `task_id`)
- ทุกข้อมี timeout
- ท้ายผลจะสรุป `SUMMARY: x/10 PASS` พร้อมเหตุผลของแต่ละข้อ
- exit code เป็น 0 เมื่อผ่านทุกข้อ

**log:** อยู่ในโฟลเดอร์ `logs\mqtt_test\` แต่ละรอบเขียนต่อท้าย และขึ้นต้นด้วย `===== start`
- `broker.log`: broker
- `simulator.log`: อุปกรณ์จำลอง
- `client.log`: ตัวทดสอบเอง รวม packet ของ MQTT

```powershell
Get-Content logs\mqtt_test\simulator.log -Tail 30 -Encoding UTF8
```

**ถ้าไม่ต้องการทดสอบโปรแกรมหลัก (ข้อ 9):**

```powershell
.\.venv313\Scripts\python.exe tools\mqtt_system_test.py --skip-app
```

## วิธีที่ 2: เปิดแยกทีละส่วน (ดู log สด)

เปิด Terminal 3 หน้าต่างใน VS Code (กด `+` ในแผง Terminal) แต่ละหน้าต่างรันแบบ foreground และหยุดด้วย Ctrl+C

**Terminal 1: broker**

```powershell
.\.venv313\Scripts\python.exe tools\mqtt_broker.py --port 1883
```

ขึ้น `MQTT test broker listening on 127.0.0.1:1883 (this computer only)` แปลว่าพร้อม

**Terminal 2: อุปกรณ์จำลอง**

```powershell
.\.venv313\Scripts\python.exe tools\mqtt_device_simulator.py --port 1883
```

log จะแสดงเวลา คำสั่งที่ได้รับ (`RECV`) คำตอบ (`SEND response`) และสถานะ (`SEND status`)

**Terminal 3: ทดสอบ**

ทดสอบเร็ว:

```powershell
.\.venv313\Scripts\python.exe tools\mqtt_check.py
```

หรือเปิดโปรแกรมหลักโดยเปิด MQTT เฉพาะรอบนี้ ไม่ต้องแก้ไฟล์ `.env`:

```powershell
$env:MQTT_ENABLED = "true"; $env:MQTT_PORT = "1883"
.\.venv313\Scripts\python.exe main.py
```

จากนั้นเข้า Admin (ปุ่มเข้าสู่ระบบผู้ดูแลบนหน้าจอผู้มาติดต่อ) → หน้า Dashboard แล้วเลื่อนลงถึงส่วน **"ควบคุม MQTT (ทดสอบกับ SIMULATOR)"** (อยู่เหนือส่วน "ระบบ AI")

ค่า `$env:` มีผลเฉพาะ Terminal นั้น ปิด Terminal แล้วก็หายไป หรือล้างด้วย:

```powershell
Remove-Item Env:MQTT_ENABLED, Env:MQTT_PORT
```

ถ้าต้องการเปิด MQTT ถาวร ให้ใส่ `MQTT_ENABLED=true` ในไฟล์ `.env` ดูตัวอย่างค่าทั้งหมดใน `.env.example`

## ทดสอบผ่าน Dashboard (กดเอง)

ต้องเปิด Terminal 1 (broker) และ Terminal 2 (อุปกรณ์จำลอง) ไว้ก่อน แล้วเปิดโปรแกรมหลักตามวิธีที่ 2

**ป้ายสถานะ 3 อัน (แยกกัน):**

| ป้าย | ความหมาย |
|---|---|
| `Broker: Connected / Connecting / Disconnected / Error` | การเชื่อมต่อกับ broker เท่านั้น ไม่ได้บอกว่ามีอุปกรณ์ |
| `อุปกรณ์: Online / Offline / Unknown (SIMULATOR)` | จากข้อความสถานะของอุปกรณ์เอง ขึ้น Unknown ถ้ายังไม่เคยได้ยินจากอุปกรณ์ หรือ broker หลุด และมีคำว่า (SIMULATOR) ถ้าเป็นอุปกรณ์จำลอง |
| `งาน: IDLE / BUSY / STOPPED` | สถานะงานล่าสุดที่อุปกรณ์รายงาน |

**ขั้นตอนทดสอบ:**
1. **PING:** แผงขึ้น `รอคำตอบ...` ทันที แล้วเปลี่ยนเป็น `สำเร็จ · ได้รับ <เวลา> (<ms>) จาก SIMULATOR prrobot-sim-device: {'ok': True, 'pong': True}` ใน Terminal 2 จะเห็น `RECV PING`
2. **SPEAK:**
   - พิมพ์ข้อความไทยหรืออังกฤษในช่อง (ไม่เกิน 500 ตัวอักษร มีตัวนับใต้ช่อง) แล้วกด SPEAK
   - ป้ายงานเป็น `BUSY` แล้วกลับเป็น `IDLE` เมื่อการพูดจำลองจบ
   - ไม่มีเสียงออกจากที่ใด อุปกรณ์จำลองเพียงจับเวลา
   - ถ้าช่องว่างจะขึ้น "ใส่ข้อความ..." และไม่ส่ง
3. **STOP:**
   - ส่ง SPEAK ข้อความยาวก่อน แล้วกด STOP ระหว่าง `BUSY`
   - จะมีหน้าต่างยืนยัน ถ้ากด Yes จะได้ `'stopped_task': 'SPEAK'` และงานกลับเป็น `IDLE`
   - ถ้ากด No จะไม่ส่งอะไร
4. **Timeout:** ปิดอุปกรณ์จำลอง (Ctrl+C ที่ Terminal 2)
   - ป้ายอุปกรณ์เป็น `Offline (SIMULATOR)`
   - กด PING จะขึ้น `รอคำตอบ...` แล้วเป็น `Timeout — ไม่มีอุปกรณ์ตอบ` หลัง 3 วินาที (`MQTT_RESPONSE_TIMEOUT_S`)
5. **broker หลุด:** ปิด broker (Ctrl+C ที่ Terminal 1)
   - ป้าย Broker เปลี่ยนเป็น `Connecting` หรือ `Error` ป้ายอุปกรณ์เป็น `Unknown`
   - ปุ่มกดไม่ได้ ชี้เมาส์ที่ปุ่มจะเห็นเหตุผล
   - เปิด broker ใหม่แล้วโปรแกรมจะเชื่อมต่อเอง

**ข้อจำกัดเพื่อความปลอดภัย:**
- ส่งได้ทีละคำสั่ง ระหว่างรอคำตอบปุ่มจะกดไม่ได้
- ปุ่มใช้ได้เฉพาะเมื่อ `MQTT_HOST` เป็น `127.0.0.1` หรือ `localhost`
- ไม่มีปุ่มใดสั่งมอเตอร์หรือการเคลื่อนที่

## แยกปัญหา Broker / อุปกรณ์จำลอง / Dashboard

| อาการ | น่าจะเป็นที่ | ตรวจด้วย |
|---|---|---|
| `Broker: Connecting` หรือ `Error` ค้าง | broker ไม่ได้เปิด หรือพอร์ตไม่ตรง | Terminal 1 ต้องขึ้น `listening on 127.0.0.1:<พอร์ต>` และ `$env:MQTT_PORT` ต้องตรงกัน ตรวจด้วย `Get-NetTCPConnection -LocalPort 1883 -State Listen` |
| Broker Connected แต่ `อุปกรณ์: Unknown` | อุปกรณ์จำลองไม่ได้เปิด หรือ prefix ไม่ตรง | Terminal 2 ต้องขึ้น `ready; listening on prrobot/test/command` และ `--prefix` ต้องตรงกับ `MQTT_TOPIC_PREFIX` |
| อุปกรณ์ Online แต่กดแล้ว Timeout | อุปกรณ์จำลองค้าง หรือข้อความไม่ถึง | ดู Terminal 2 ว่ามี `RECV` หรือไม่ ถ้าไม่มี ให้ลอง `tools\mqtt_check.py` ถ้า mqtt_check ผ่านแต่ Dashboard ไม่ผ่าน แปลว่าปัญหาอยู่ที่แอป ให้ดู `logs\app.log` |
| ทุกอย่าง Connected แต่ปุ่มกดไม่ได้ | กำลังรอคำตอบอยู่ หรือ `MQTT_HOST` ไม่ใช่เครื่องนี้ | ชี้เมาส์ที่ปุ่มเพื่อดูเหตุผล |
| `mqtt_system_test.py` ล้ม | ดูข้อที่ FAIL และเหตุผล | ดู `logs\mqtt_test\client.log`, `simulator.log`, `broker.log` ในรอบนั้น (หลังบรรทัด `===== start` ล่าสุด) |

## วิธีหยุดทุก process

- **Terminal ที่รันอยู่:** กด `Ctrl+C`
  - **อุปกรณ์จำลอง:** แจ้ง offline ก่อนปิด
  - **broker:** ปิดพอร์ต
  - **โปรแกรมหลัก:** ปิดหน้าต่างตามปกติ
- **สคริปต์ทดสอบอัตโนมัติ:** ปิด process ลูกเองทุกครั้ง
- **ตรวจว่ามี process MQTT ค้างหรือไม่:**

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object CommandLine -match 'mqtt_broker|mqtt_device_simulator|mqtt_system_test' |
  Select-Object ProcessId, CommandLine
```

- **ถ้ายังค้าง:** ปิดเฉพาะ PID ที่แสดงจากคำสั่งด้านบน อย่าปิด python ตัวอื่น

```powershell
Stop-Process -Id <PID> -Force
```

- **ตรวจว่าพอร์ต 1883 ว่างแล้ว:**

```powershell
Get-NetTCPConnection -LocalPort 1883 -State Listen -ErrorAction SilentlyContinue
```

ไม่มีผลลัพธ์แปลว่าว่างแล้ว

## Topic และรูปแบบข้อความ

**Topic** (prefix ค่าเริ่มต้น `prrobot/test` ตั้งได้ด้วย `MQTT_TOPIC_PREFIX`)

| Topic | ทิศทาง | ใช้ทำอะไร |
|---|---|---|
| `prrobot/test/command` | โปรแกรม → อุปกรณ์ | คำสั่ง |
| `prrobot/test/response` | อุปกรณ์ → โปรแกรม | คำตอบของคำสั่ง (`reply_to` = id ของคำสั่ง) |
| `prrobot/test/status/<client_id>` | ทุก client (retained) | online/offline และสถานะงาน IDLE/BUSY/STOPPED ใช้ topic ของใครของมัน เพราะ topic หนึ่งเก็บข้อความ retained ได้ข้อความเดียว ฝั่งโปรแกรมใส่ `"role": "app"` จึงไม่ถูกนับเป็นอุปกรณ์ |
| `prrobot/test/telemetry` | อุปกรณ์ → โปรแกรม | ค่าที่อุปกรณ์ส่งเอง |

**รูปแบบข้อความ (JSON, schema v1)**

```json
{"v": 1, "id": "<uuid hex>", "ts": "2026-10-27T06:24:10.738+00:00", "type": "command",
 "source": "prrobot-app", "command": "SPEAK", "payload": {"text": "สวัสดีครับ", "duration_s": 1.5}}
```

**คำสั่งที่อนุญาต** (คำสั่งอื่นถูกปฏิเสธในโปรแกรมก่อนส่งออก)

| คำสั่ง | payload | คำตอบของอุปกรณ์จำลอง |
|---|---|---|
| PING | – | `{"ok": true, "pong": true}` |
| STATUS | – | `{"ok": true, "state": "IDLE", "task": null, ...}` |
| TEST_ECHO | อะไรก็ได้ | `{"ok": true, "echo": <payload>}` |
| SPEAK | `text` 1–500 ตัวอักษร, `duration_s` (0–30, ไม่ใส่ก็ได้) | `{"ok": true, "accepted": true}` แล้วสถานะ BUSY → IDLE (`result: "done"`) ถ้า BUSY อยู่จะได้ `{"ok": false, "error": "busy"}` |
| STOP | – | `{"ok": true, "stopped_task": "SPEAK" หรือ null}` แล้วสถานะ STOPPED → IDLE (`result: "stopped"`) |

**สถานะของอุปกรณ์ใน `status`:**

```json
{"online": true, "state": "BUSY", "task": "SPEAK", "task_id": "<id ของคำสั่ง>", "result": null}
```

ถ้าอุปกรณ์หลุดโดยไม่ได้แจ้ง broker จะส่ง `{"online": false}` แทนผ่าน MQTT last will

## ควบคุมหุ่นยนต์จริง (Admin → ควบคุมหุ่นยนต์ (MQTT))

แยกจากทุกอย่างด้านบน: เชื่อม broker ของฮาร์ดแวร์จริง ส่งข้อความธรรมดา (ไม่ใช่ JSON) เช่น `left 50` ไป `robot/direction` **หุ่นยนต์จริงอาจขยับทันทีที่กด**

**เปิดใช้:** ใส่ใน `.env` แล้วเปิดโปรแกรมใหม่ (ดูคำอธิบายทุกค่าใน `.env.example`)

```
MQTT_HW_HOST=192.168.10.1
MQTT_HW_DIRECTION_TOPIC=robot/direction
MQTT_HW_STOP_PAYLOAD=stop
MQTT_HW_REPLY_TOPIC=
```

**กติกา:**
- **การ์ด "หันตัว":** `left` / `right` ตามมุม (องศา) เลือกเร็ว 15 30 45 90 หรือพิมพ์ 1–`MQTT_HW_TURN_MAX_DEG` (ค่าเริ่มต้น 180) เช่น `left 30`
- **การ์ด "เดินหน้า / ถอยหลัง":** `forward` / `backward` ตามระยะ (ซม.) เลือกเร็ว 10 25 50 100 หรือพิมพ์ 1–`MQTT_HW_MOVE_MAX_CM` (ค่าเริ่มต้น 100) เช่น `forward 25`
- แต่ละการ์ดมี topic ของตัวเอง (เริ่มจาก `MQTT_HW_TURN_TOPIC` / `MQTT_HW_MOVE_TOPIC` ถ้าว่างใช้ `MQTT_HW_DIRECTION_TOPIC`) ข้อความไม่ retained
- ถ้ายังไม่ได้ตั้ง `MQTT_HW_STOP_PAYLOAD` ปุ่มหันและเดินกดไม่ได้
- เดินหน้า/ถอยหลังมีเฉพาะหน้านี้ หน้าจอผู้ใช้และ AI ยังถือว่าหุ่นยนต์ไม่เดิน
- แถบบนสุดแสดงข้อความล่าสุดจากหุ่นยนต์บน `MQTT_HW_STATUS_TOPIC` (ค่าเริ่มต้น `robot/r1/status`) ถ้าเป็นข้อความที่ broker เก็บไว้ (retained) จะบอกว่าเป็นข้อความเก่า
- ต้องติ๊กยืนยันว่าไม่มีคนรอบหุ่นยนต์และมีคนพร้อมกดหยุด ทุกครั้งที่เข้า Admin
- ส่งทิศทางได้ไม่เกิน 1 ครั้งต่อวินาที ปุ่มหยุดกดได้ตลอด
- `MQTT_HW_REQUIRE_CAMERA=true` ให้กล้องตรวจระยะคนก่อนส่ง (ต้องเสียบ OAK-D)
- ทุกคำสั่งลงประวัติการทำงาน (`hardware.direction`, `hardware.stop`)
- **เลือก topic ได้ (แยกต่อการ์ด):** ช่อง Topic มีค่าจาก `MQTT_HW_TOPICS` (ค่าเริ่มต้น `robot/direction`, `robot/status`, `robot/mode`) พิมพ์ topic ใหม่แล้วกด Enter หรือ "ใช้ topic นี้" จะเพิ่มและบันทึกไว้ในฐานข้อมูล (`app_settings`: `hardware.topics`, `hardware.topic.turn`, `hardware.topic.move`) ลบได้เฉพาะ topic ที่พิมพ์เพิ่ม ส่งไป topic ที่หุ่นยนต์ใช้รายงาน (`MQTT_HW_STATUS_TOPIC`, `MQTT_HW_REPLY_TOPIC`) ไม่ได้ และห้ามมี `#` `+` **คำสั่งหยุดส่งไป `MQTT_HW_DIRECTION_TOPIC` เสมอ** ไม่ขึ้นกับ topic ที่เลือก

**ผลที่แสดง:** "Broker: รับแล้ว" แปลแค่ว่าข้อความถึง broker ส่วน "ฮาร์ดแวร์ยืนยัน" ขึ้นเฉพาะเมื่อมีข้อความบน `MQTT_HW_REPLY_TOPIC` ภายใน 3 วินาทีหลังส่ง ถ้าไม่ได้ตั้ง topic ตอบกลับ หน้าจอจะบอกว่าไม่รู้ว่าฮาร์ดแวร์ได้รับหรือไม่

**ไม่เกี่ยวกับ:** ปุ่มหันซ้าย/หันขวาบนหน้าจอผู้ใช้ (ยังสั่งหุ่นยนต์จำลอง)

**หน้าภาพรวม:** เมื่อตั้ง `MQTT_HW_HOST` แล้ว หน้าภาพรวมไม่มีแผงควบคุม MQTT (แผงทดสอบกับ SIMULATOR แสดงเฉพาะเมื่อตั้ง `MQTT_ENABLED=true`) เหลือเพียงการ์ด "ฮาร์ดแวร์ (MQTT)" แสดงสถานะ

## ข้อจำกัดที่รู้แล้ว

- **ฮาร์ดแวร์จริงยังไม่ได้ทดสอบ:** อุปกรณ์จริงต้องรับและตอบตาม topic และรูปแบบข้างบน แล้วทดสอบด้วย `tools\mqtt_check.py` กับ broker ที่อุปกรณ์ใช้
- **ปุ่มบน Dashboard:** มีแค่ PING, SPEAK, STOP สำหรับทดสอบกับ broker ในเครื่องเท่านั้น
- **SPEAK ผ่าน MQTT ยังไม่เชื่อมกับเสียงพูดของหุ่นยนต์:** หุ่นยนต์ยังพูดผ่านลำโพงของคอมพิวเตอร์ตามเดิม
- **ข้อจำกัดของ amqtt 0.12:** ตัวรับรายใหม่อาจได้ข้อความ retained ของ topic อื่นด้วย ซึ่งผิดมาตรฐาน โปรแกรมและอุปกรณ์จำลองตรวจทุกข้อความ จึงไม่กระทบผล
- **ใช้กับอุปกรณ์จริงในเครือข่าย:** ควรใช้ broker จริง เช่น Mosquitto (`winget install EclipseFoundation.Mosquitto` ต้องกดอนุญาตสิทธิ์ admin) และตั้ง username/password หรือ TLS ใน `.env` (`MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TLS`, `MQTT_CA_FILE`) broker ทดสอบนี้ไม่เหมาะกับงานนั้น
