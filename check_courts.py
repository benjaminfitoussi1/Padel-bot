"""
Eva's padel court checker
-------------------------
Asks 4 London clubs which padel courts are free on upcoming weekends,
between the times you choose, and prints what it finds.

Run it from Terminal with:   python3 check_courts.py
"""

import json                       # reads the data the websites send back
import os                         # finds files on your computer
import urllib.request             # lets Python visit a web address
from datetime import date, timedelta


# ============================================================
# 1. SETTINGS  -- change these to change what the bot looks for
# ============================================================

EARLIEST_START = "09:00"   # don't show games starting before this
FINISH_BY      = "20:00"   # the game must be over by this time
DAYS_TO_CHECK  = 8         # how many days ahead to look
DAYS_I_WANT    = ["Saturday", "Sunday"]

# Your private ntfy channel (subscribe to this exact name in the ntfy app)
NTFY_CHANNEL   = "padel-eva-0867025160"

# File where the bot remembers what it already told you about
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_slots.json")

# Royal Parks clubs: name -> the code their website uses
ROYAL_PARKS = {
    "Regent's Park": "the-regents-park-courts",
    "Hyde Park":     "hyde-park-courts",
}

# Playtomic clubs: name -> (club code, booking page)
PLAYTOMIC = {
    "Paddington Social Club": (
        "0cf8af2e-6122-4c75-8195-c5ab4de58922",
        "https://playtomic.com/clubs/padel-social-club-paddington",
    ),
    "Earl's Court Padel Social Club": (
        "1c97a3d1-ded7-4c4b-808e-8c37bb1b2a1f",
        "https://playtomic.com/clubs/padel-social-club-earls-court",
    ),
}


# ============================================================
# 2. HELPERS  -- small tools the rest of the script uses
# ============================================================

def get_json(url, extra_headers=None):
    """Visit a web address and return its answer as Python data."""
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def to_minutes(hhmm):
    """Turn '09:30' (or '09:30:00') into 570, so times are easy to compare."""
    hours, minutes = hhmm.split(":")[:2]
    return int(hours) * 60 + int(minutes)


def fits_my_times(start, duration_minutes):
    """True if a game starting at `start` fits between my earliest/finish times."""
    begins = to_minutes(start)
    ends = begins + duration_minutes
    return begins >= to_minutes(EARLIEST_START) and ends <= to_minutes(FINISH_BY)


# ============================================================
# 3. ASKING EACH BOOKING SYSTEM
# ============================================================

def check_royal_parks(club_name, venue_code, day):
    """Return the free slots at a Royal Parks club on a given day."""
    url = (f"https://flow.onl/api/activities/venue/{venue_code}"
           f"/activity/padel/v2/times?date={day.isoformat()}")
    # Flow runs booking sites for many organisations; these headers tell it
    # we're asking on behalf of the Royal Parks site (like a browser does).
    royal_parks_site = "https://sportsandleisureroyalparks.bookings.flow.onl"
    answer = get_json(url, {"Origin": royal_parks_site,
                            "Referer": royal_parks_site + "/"})

    found = []
    for slot in answer.get("data", []):
        start = slot["starts_at"]["format_24_hour"]           # e.g. "10:00"
        is_free = slot["action_to_show"]["status"] == "BOOK"  # "FULL" if taken
        if is_free and fits_my_times(start, 60):
            found.append({
                "club": club_name,
                "day": day,
                "time": start,
                "length": "60 min",
                "detail": f"{slot['spaces']} court(s) free",
                "link": ("https://sportsandleisureroyalparks.bookings.flow.onl/location/"
                         f"{venue_code}/padel/{day.isoformat()}/by-time"),
            })
    return found


def check_playtomic(club_name, club_code, booking_page, day):
    """Return the free slots at a Playtomic club on a given day."""
    d = day.isoformat()
    # Playtomic has two addresses: its website, and the one its phone app uses.
    # The website one blocks some servers (like GitHub's), so if it refuses
    # us we try the app one instead.
    website_url = (f"https://playtomic.com/api/clubs/availability"
                   f"?tenant_id={club_code}&date={d}&sport_id=PADEL")
    app_url = (f"https://api.playtomic.io/v1/availability?user_id=me&sport_id=PADEL"
               f"&tenant_id={club_code}"
               f"&local_start_min={d}T00:00:00&local_start_max={d}T23:59:59")
    try:
        courts = get_json(website_url)   # a list: one entry per court with free slots
    except Exception as website_error:
        try:
            courts = get_json(app_url)
        except Exception as app_error:
            raise Exception(f"website said '{website_error}', app address said '{app_error}'")

    # Several courts can be free at the same time, so count them per time+length
    counts = {}
    prices = {}
    for court in courts:
        for slot in court["slots"]:
            start = slot["start_time"][:5]          # "10:00:00" -> "10:00"
            length = slot["duration"]               # 60 or 90
            if fits_my_times(start, length):
                key = (start, length)
                counts[key] = counts.get(key, 0) + 1
                prices[key] = slot.get("price", "")

    found = []
    for (start, length), n in sorted(counts.items()):
        found.append({
            "club": club_name,
            "day": day,
            "time": start,
            "length": f"{length} min",
            "detail": f"{n} court(s), {prices[(start, length)]}",
            "link": booking_page,
        })
    return found


# ============================================================
# 4. MEMORY + PHONE ALERTS
# ============================================================

def slot_id(s):
    """A short label that identifies one slot, e.g. "Hyde Park|2026-10-17|20:00|60 min"."""
    return f"{s['club']}|{s['day'].isoformat()}|{s['time']}|{s['length']}"


def load_memory():
    """Read the list of slots we saw last time (empty the very first time)."""
    if not os.path.exists(MEMORY_FILE):
        return set()
    with open(MEMORY_FILE) as f:
        return set(json.load(f))


def save_memory(ids):
    """Write down the slots we see now, for next time."""
    with open(MEMORY_FILE, "w") as f:
        json.dump(sorted(ids), f, indent=1)


def describe(s):
    return f"{s['day'].strftime('%a %d %b')} {s['time']} ({s['length']}) - {s['club']}"


def send_phone_alert(new_slots):
    """Send ONE notification to your phone listing the new slots."""
    if len(new_slots) == 1:
        title = "Padel court free: " + describe(new_slots[0])
    else:
        title = f"{len(new_slots)} new padel slots free"
    message = "\n".join(describe(s) + " - " + s["detail"] for s in new_slots[:15])

    request = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_CHANNEL}",
        data=message.encode("utf-8"),
        headers={
            "Title": title.encode("ascii", "ignore").decode(),  # titles must be plain text
            "Click": new_slots[0]["link"],    # tapping the alert opens this booking page
            "Tags": "tennis",                 # shows a little ball emoji
            "Priority": "high",
        },
    )
    urllib.request.urlopen(request, timeout=20)


# ============================================================
# 5. MAIN  -- go through each day and each club
# ============================================================

def find_free_courts():
    all_slots = []
    today = date.today()

    for i in range(DAYS_TO_CHECK):
        day = today + timedelta(days=i)
        if day.strftime("%A") not in DAYS_I_WANT:
            continue  # skip weekdays

        for name, code in ROYAL_PARKS.items():
            try:
                all_slots += check_royal_parks(name, code, day)
            except Exception as error:
                print(f"  ! Couldn't check {name} on {day}: {error}")

        for name, (code, page) in PLAYTOMIC.items():
            try:
                all_slots += check_playtomic(name, code, page, day)
            except Exception as error:
                print(f"  ! Couldn't check {name} on {day}: {error}")

    return all_slots


if __name__ == "__main__":
    print(f"Looking for courts {EARLIEST_START}-{FINISH_BY} on {', '.join(DAYS_I_WANT)}...\n")
    slots = find_free_courts()

    if not slots:
        print("Nothing free right now. 😞")
    for s in slots:
        print(f"✅ {describe(s)} - {s['detail']}")
        print(f"   Book: {s['link']}")

    # Only alert about slots that weren't free last time we checked
    seen_before = load_memory()
    new_slots = [s for s in slots if slot_id(s) not in seen_before]

    if new_slots:
        send_phone_alert(new_slots)
        print(f"\n📱 Sent a phone alert about {len(new_slots)} new slot(s).")
    else:
        print("\nNo NEW slots since last check, so no alert sent.")

    # Remember what's free now. If a slot gets booked and later frees up
    # again (a cancellation!), it will count as new and you'll be alerted.
    save_memory({slot_id(s) for s in slots})
