"""Mock clinic data (in-memory) and the three business operations the agent can call."""
import datetime as dt
import itertools
import random
import re

DOCTORS = [
    {"id": "d1", "name": "Dr. Anika Rao", "speciality": "Cardiology"},
    {"id": "d2", "name": "Dr. Vikram Shah", "speciality": "General Medicine"},
    {"id": "d3", "name": "Dr. Meera Iyer", "speciality": "Dermatology"},
    {"id": "d4", "name": "Dr. Arjun Menon", "speciality": "Orthopedics"},
]
TIMES = ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00"]

SLOTS: dict[str, dict] = {}
APPOINTMENTS: list[dict] = []
_ids = itertools.count(1)


def _seed():
    rnd = random.Random(7)
    day, made = dt.date.today(), 0
    while made < 5:  # next 5 weekdays
        day += dt.timedelta(days=1)
        if day.weekday() >= 5:
            continue
        made += 1
        for d in DOCTORS:
            for t in TIMES:
                sid = f"{d['id']}-{day.isoformat()}-{t}"
                SLOTS[sid] = {"id": sid, "doctor_id": d["id"], "date": day.isoformat(),
                              "time": t, "booked": rnd.random() < 0.3}


_seed()


def _doctor(doctor_id):
    return next(d for d in DOCTORS if d["id"] == doctor_id)


def normalize_phone(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def snapshot():
    """Everything the dashboard needs."""
    doctors = [{**d, "slots": [s for s in SLOTS.values() if s["doctor_id"] == d["id"] and not s["booked"]]}
               for d in DOCTORS]
    return doctors, list(reversed(APPOINTMENTS))


def check_availability(doctor=None, speciality=None, date=None):
    docs = [d for d in DOCTORS
            if (not doctor or doctor.lower().replace("dr.", "").strip() in d["name"].lower())
            and (not speciality or speciality.lower() in d["speciality"].lower())]
    if not docs:
        return {"error": "No matching doctor.", "doctors": [f"{d['name']} ({d['speciality']})" for d in DOCTORS]}
    ids = {d["id"] for d in docs}
    free = [s for s in SLOTS.values() if s["doctor_id"] in ids and not s["booked"]
            and (not date or s["date"] == date)]
    free.sort(key=lambda s: (s["date"], s["time"]))
    return {"slots": [{"slot_id": s["id"], "doctor": _doctor(s["doctor_id"])["name"],
                       "speciality": _doctor(s["doctor_id"])["speciality"],
                       "date": s["date"], "time": s["time"]} for s in free[:10]],
            "note": "Showing at most 10 earliest slots." if len(free) > 10 else ""}


def book(slot_id, patient_name, phone, phone_confirmed):
    if not phone_confirmed:
        return {"error": "Read the phone number back to the caller and get a yes before booking."}
    phone = normalize_phone(phone)
    if not 10 <= len(phone) <= 13:
        return {"error": "Phone number must be 10 to 13 digits. Ask the caller to repeat it."}
    slot = SLOTS.get(slot_id)
    if not slot or slot["booked"]:
        return {"error": "That slot is not available. Check availability again."}
    if any(a["phone"] == phone and a["status"] == "booked" for a in APPOINTMENTS):
        return {"error": "This number already has an active appointment. Cancel it first."}
    slot["booked"] = True
    doc = _doctor(slot["doctor_id"])
    appt = {"id": f"A{next(_ids):03d}", "slot_id": slot_id, "patient": patient_name, "phone": phone,
            "doctor": doc["name"], "speciality": doc["speciality"], "date": slot["date"],
            "time": slot["time"], "status": "booked"}
    APPOINTMENTS.append(appt)
    return {"ok": True, "appointment": appt}


def cancel(phone, phone_confirmed):
    if not phone_confirmed:
        return {"error": "Read the phone number back to the caller and get a yes before cancelling."}
    phone = normalize_phone(phone)
    appt = next((a for a in APPOINTMENTS if a["phone"] == phone and a["status"] == "booked"), None)
    if not appt:
        return {"error": "No active appointment found for this number."}
    appt["status"] = "cancelled"
    SLOTS[appt["slot_id"]]["booked"] = False
    return {"ok": True, "appointment": appt}
