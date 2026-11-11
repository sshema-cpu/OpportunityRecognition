
# HopIN — TCU-themed student carpool prototype
# Render-ready classroom demonstration.
# Use fictional information only.

import os
import re
import uuid
import sqlite3
from datetime import datetime, date
from threading import RLock

import gradio as gr
import pandas as pd

DB_PATH = os.environ.get("HOPIN_DB_PATH", "hopin_demo.sqlite3")
lock = RLock()


def db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    return con


with db() as con:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS rides (
        ride_id TEXT PRIMARY KEY,
        driver TEXT NOT NULL,
        driver_email TEXT NOT NULL,
        destination TEXT NOT NULL,
        trip_date TEXT NOT NULL,
        depart_time TEXT NOT NULL,
        pickup TEXT NOT NULL,
        seats_total INTEGER NOT NULL,
        seats_left INTEGER NOT NULL,
        gas_share REAL NOT NULL,
        trip_type TEXT NOT NULL,
        return_time TEXT,
        stay_minutes INTEGER NOT NULL,
        wait_minutes INTEGER NOT NULL,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'OPEN'
    );

    CREATE TABLE IF NOT EXISTS bookings (
        booking_id TEXT PRIMARY KEY,
        ride_id TEXT NOT NULL,
        rider TEXT NOT NULL,
        rider_email TEXT NOT NULL,
        seats INTEGER NOT NULL,
        leg TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'BOOKED'
    );

    CREATE TABLE IF NOT EXISTS events (
        created_at TEXT,
        event TEXT,
        details TEXT
    );
    """)


def event(con, name, details):
    con.execute(
        "INSERT INTO events VALUES (?,?,?)",
        (
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            name,
            details
        )
    )


def demo_email(value):
    # Email formatting only. NOT actual TCU authentication.
    return bool(
        re.fullmatch(
            r"[^\s@]+@tcu\.edu",
            str(value).strip(),
            flags=re.I
        )
    )


def uid(n=7):
    return uuid.uuid4().hex[:n].upper()


def ride_rows(destination="", day="", max_gas=None):
    with db() as con:
        rows = con.execute("""
            SELECT * FROM rides
            WHERE status='OPEN' AND seats_left>0
            ORDER BY trip_date, depart_time
        """).fetchall()

    out = []

    for r in rows:
        if destination and destination.lower() not in r["destination"].lower():
            continue

        if day and day.strip() != r["trip_date"]:
            continue

        if max_gas is not None and float(r["gas_share"]) > float(max_gas):
            continue

        out.append([
            r["ride_id"],
            r["destination"],
            r["trip_date"],
            r["depart_time"],
            r["pickup"],
            r["driver"],
            r["seats_left"],
            f"${r['gas_share']:.2f}",
            r["trip_type"],
            r["return_time"] or "—",
            f"{r['stay_minutes']} min",
            f"{r['wait_minutes']} min"
        ])

    return pd.DataFrame(
        out,
        columns=[
            "Ride ID",
            "Destination",
            "Date",
            "Leave",
            "Pickup",
            "Driver (unverified)",
            "Seats",
            "Gas / rider",
            "Trip type",
            "Return leaves",
            "At destination",
            "Pickup grace"
        ]
    )


def search(destination, day, max_gas):
    if day and day.strip():
        try:
            datetime.strptime(day.strip(), "%Y-%m-%d")
        except ValueError:
            return ride_rows(), "Use YYYY-MM-DD for the date filter."

    return (
        ride_rows(destination.strip(), day.strip(), max_gas),
        "Showing matching rides. Profiles are NOT verified."
    )


def post(
    driver, email, destination, day, depart, pickup,
    seats, gas, trip_type, return_time, stay, wait, notes
):
    fields = [driver, email, destination, day, depart, pickup]

    if not all(str(x).strip() for x in fields):
        return "Please complete the required fields.", ride_rows()

    if not demo_email(email):
        return (
            "Use a demo @tcu.edu email format. "
            "This is NOT real verification.",
            ride_rows()
        )

    try:
        trip_day = datetime.strptime(day.strip(), "%Y-%m-%d").date()
        leave = datetime.strptime(depart.strip(), "%H:%M").time()

        if trip_day < date.today():
            raise ValueError("Date must be today or later.")

        seats = int(seats)
        gas = float(gas)
        stay = int(stay)
        wait = int(wait)

        if not 1 <= seats <= 7:
            raise ValueError("Seats must be between 1 and 7.")

        if gas < 0:
            raise ValueError("Gas contribution cannot be negative.")

        if not 0 <= stay <= 600:
            raise ValueError("Check destination duration.")

        if not 0 <= wait <= 60:
            raise ValueError("Waiting time must be 0–60 minutes.")

        if trip_type == "Round trip":
            ret = datetime.strptime(
                str(return_time).strip(), "%H:%M"
            ).time()

            if datetime.combine(trip_day, ret) <= datetime.combine(
                trip_day, leave
            ):
                raise ValueError(
                    "Return departure must be later than outbound departure."
                )

            minutes = (
                datetime.combine(trip_day, ret)
                - datetime.combine(trip_day, leave)
            ).total_seconds() / 60

            if stay > minutes:
                raise ValueError(
                    "Destination duration exceeds the return window."
                )

    except (ValueError, TypeError) as e:
        return (
            f"Check the details: {e}. Times use 24-hour HH:MM.",
            ride_rows()
        )

    rid = uid()

    with lock, db() as con:
        con.execute("""
            INSERT INTO rides VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
        """, (
            rid,
            driver.strip(),
            email.strip(),
            destination.strip(),
            day.strip(),
            depart.strip(),
            pickup.strip(),
            seats,
            seats,
            gas,
            trip_type,
            str(return_time).strip() if trip_type == "Round trip" else None,
            stay,
            wait,
            str(notes).strip(),
            "OPEN"
        ))

        event(con, "Ride posted", rid)

    return (
        f"Ride {rid} posted! {seats} seat(s). "
        "Driver identity is not verified.",
        ride_rows()
    )


def reserve(rid, rider, email, seats, leg):
    if (
        not str(rid).strip()
        or not str(rider).strip()
        or not demo_email(email)
    ):
        return (
            "Enter Ride ID, name and a demo @tcu.edu email. "
            "Email format is NOT identity verification.",
            ride_rows()
        )

    try:
        seats = int(seats)
        if seats < 1:
            raise ValueError
    except (TypeError, ValueError):
        return "Seats must be a positive whole number.", ride_rows()

    with lock, db() as con:
        r = con.execute(
            'SELECT * FROM rides WHERE ride_id=? AND status="OPEN"',
            (rid.strip().upper(),)
        ).fetchone()

        if not r:
            return "That ride is not available.", ride_rows()

        if seats > r["seats_left"]:
            return (
                f"Only {r['seats_left']} seat(s) left.",
                ride_rows()
            )

        if leg != "Outbound only" and r["trip_type"] != "Round trip":
            return (
                "This driver only offers a one-way trip.",
                ride_rows()
            )

        bid = uid(9)

        con.execute("""
            UPDATE rides
            SET seats_left=seats_left-?
            WHERE ride_id=?
        """, (seats, r["ride_id"]))

        con.execute("""
            INSERT INTO bookings VALUES (?,?,?,?,?,?,?)
        """, (
            bid,
            r["ride_id"],
            rider.strip(),
            email.strip(),
            seats,
            leg,
            "BOOKED"
        ))

        event(
            con,
            "Seat reserved",
            f'{bid} on {r["ride_id"]}'
        )

    return (
        f"Booked! Save confirmation {bid}. {leg}. "
        "No actual notification was sent.",
        ride_rows()
    )


def cancel_seat(bid):
    with lock, db() as con:
        b = con.execute("""
            SELECT * FROM bookings
            WHERE booking_id=? AND status="BOOKED"
        """, (str(bid).strip().upper(),)).fetchone()

        if not b:
            return "Active booking not found.", ride_rows()

        con.execute("""
            UPDATE bookings
            SET status="CANCELLED"
            WHERE booking_id=?
        """, (b["booking_id"],))

        con.execute("""
            UPDATE rides
            SET seats_left=MIN(seats_total,seats_left+?)
            WHERE ride_id=? AND status="OPEN"
        """, (b["seats"], b["ride_id"]))

        event(con, "Booking cancelled", b["booking_id"])

    return (
        f'Booking {b["booking_id"]} cancelled; '
        f'{b["seats"]} seat(s) reopened if the ride is active.',
        ride_rows()
    )


def cancel_trip(rid):
    with lock, db() as con:
        r = con.execute("""
            SELECT * FROM rides
            WHERE ride_id=? AND status="OPEN"
        """, (str(rid).strip().upper(),)).fetchone()

        if not r:
            return (
                "Open ride not found.",
                ride_rows(),
                pd.DataFrame(columns=["Booking", "Status"])
            )

        affected = con.execute("""
            SELECT booking_id FROM bookings
            WHERE ride_id=? AND status="BOOKED"
        """, (r["ride_id"],)).fetchall()

        con.execute("""
            UPDATE rides
            SET status="CANCELLED"
            WHERE ride_id=?
        """, (r["ride_id"],))

        con.execute("""
            UPDATE bookings
            SET status="DRIVER CANCELLED"
            WHERE ride_id=? AND status="BOOKED"
        """, (r["ride_id"],))

        event(con, "Driver cancelled ride", r["ride_id"])

    affected_table = pd.DataFrame(
        [
            {
                "Booking": x["booking_id"],
                "Status": "Would notify"
            }
            for x in affected
        ],
        columns=["Booking", "Status"]
    )

    return (
        f"Ride cancelled. {len(affected)} booking(s) "
        "flagged for notification; no real alerts were sent.",
        ride_rows(),
        affected_table
    )


def alternatives(destination):
    return (
        ride_rows(str(destination).strip()),
        "These are other currently listed rides, "
        "not guaranteed replacement transport."
    )


def recent_events():
    with db() as con:
        rows = con.execute("""
            SELECT created_at, event, details
            FROM events
            ORDER BY rowid DESC
            LIMIT 15
        """).fetchall()

    return pd.DataFrame(
        [dict(x) for x in rows],
        columns=["created_at", "event", "details"]
    )


# --------------------------------------------------
# TCU THEME — WHITE/PURPLE + BLACK/PURPLE
# --------------------------------------------------

CSS = """
:root {
    --hop-purple: #4D1979;
    --hop-purple2: #6B2DA1;
    --hop-light: #F5F0FB;
}

.gradio-container {
    max-width: 1180px !important;
    font-family: Inter, ui-sans-serif, system-ui, sans-serif !important;
}

.gradio-container:not(.dark) {
    background: #FFFFFF !important;
    color: #21152E !important;
}

.gradio-container.dark {
    background: #0C0911 !important;
    color: #F5ECFF !important;
}

#hero {
    background: linear-gradient(115deg, #351052, #67299B) !important;
    color: white !important;
    border-radius: 20px !important;
    padding: 24px 30px !important;
    box-shadow: 0 10px 28px #2D0D4A26;
}

#hero h1,
#hero h2,
#hero p,
#hero strong {
    color: white !important;
}

#hero h1 {
    font-size: 2.5rem !important;
    margin: 0 !important;
}

#hero p {
    opacity: .94;
}

.gradio-container button.primary {
    background: #4D1979 !important;
    color: white !important;
    border: 1px solid #6B2DA1 !important;
    border-radius: 10px !important;
}

.gradio-container button.primary:hover {
    background: #6B2DA1 !important;
}

.gradio-container:not(.dark) .tab-nav {
    border-bottom: 2px solid #E5D8F2 !important;
}

.gradio-container .tab-nav button.selected {
    color: #7B3BB5 !important;
    border-bottom: 3px solid #7B3BB5 !important;
}

.gradio-container:not(.dark) .form,
.gradio-container:not(.dark) .block {
    border-radius: 12px;
}

.gradio-container.dark .tab-nav button.selected {
    color: #CFA5FF !important;
    border-bottom-color: #A96DE1 !important;
}

#safety {
    border-left: 4px solid #8B5CC1 !important;
    padding: 12px 18px !important;
    border-radius: 8px !important;
}
"""


# --------------------------------------------------
# GRADIO WEBSITE
# --------------------------------------------------

with gr.Blocks(
    title="HopIN | TCU Campus Rides",
    css=CSS,
    theme=gr.themes.Soft(
        primary_hue="purple",
        neutral_hue="slate"
    )
) as demo:

    gr.Markdown("""
# 🐸 HopIN
### The ride already exists. The connection doesn't.

**TCU-inspired campus carpool prototype**

Share an existing trip, plan the timing, and reserve a seat.
""", elem_id="hero")

    gr.Markdown("""
**⚠️ CLASSROOM DEMO ONLY:**

No real TCU authentication, driver screening, payments,
emergency assistance, or messages.

Do not enter real personal data or arrange actual rides
through this demo.
""", elem_id="safety")

    gr.Markdown("""
**Appearance:** Use the Gradio theme switch to toggle
between white + TCU purple and black + purple.
""")

    # FIND A RIDE
    with gr.Tab("🔎 Find a Ride"):

        gr.Markdown("### Find a trip that fits your plans")

        with gr.Row():
            query = gr.Textbox(
                label="Destination",
                placeholder="Target, Clearfork, Stockyards..."
            )

            day_filter = gr.Textbox(
                label="Date (optional)",
                placeholder="YYYY-MM-DD"
            )

            max_gas = gr.Number(
                label="Max gas share ($)",
                value=20,
                minimum=0
            )

        find = gr.Button("Search rides", variant="primary")

        result = gr.Textbox(
            label="Search status",
            interactive=False
        )

        table = gr.Dataframe(
            value=ride_rows(),
            interactive=False,
            label="Available rides"
        )

        find.click(
            search,
            [query, day_filter, max_gas],
            [table, result]
        )

        gr.Markdown("### Reserve your seat")

        gr.Markdown("""
For round trips, select **Outbound + return** if you
plan to ride both legs.

The return schedule is set by the driver.

This demo tracks one seat allocation per booking,
not independent leg capacity.
""")

        with gr.Row():
            ride_id = gr.Textbox(label="Ride ID")

            rider = gr.Textbox(
                label="Rider display name",
                placeholder="Demo Rider"
            )

            rider_email = gr.Textbox(
                label="TCU-format email (not verified)",
                placeholder="demo@tcu.edu"
            )

        with gr.Row():
            qty = gr.Number(
                label="Seats",
                value=1,
                precision=0,
                minimum=1
            )

            leg = gr.Radio(
                ["Outbound only", "Outbound + return"],
                value="Outbound only",
                label="Reservation"
            )

        book = gr.Button("Reserve seat", variant="primary")

        book_status = gr.Textbox(
            label="Booking confirmation",
            interactive=False
        )

        book.click(
            reserve,
            [ride_id, rider, rider_email, qty, leg],
            [book_status, table]
        )

    # OFFER A RIDE
    with gr.Tab("🚗 Offer a Ride"):

        gr.Markdown("### Share a trip you already plan to make")

        with gr.Row():
            driver = gr.Textbox(
                label="Driver display name",
                placeholder="Demo Driver"
            )

            driver_email = gr.Textbox(
                label="TCU-format email (not verified)",
                placeholder="driver@tcu.edu"
            )

        with gr.Row():
            destination = gr.Textbox(
                label="Destination",
                placeholder="Target"
            )

            pickup = gr.Textbox(
                label="Pickup point",
                placeholder="Example: campus meeting point"
            )

        with gr.Row():
            trip_date = gr.Textbox(
                label="Date (YYYY-MM-DD)",
                value=date.today().isoformat()
            )

            depart = gr.Textbox(
                label="Departure time (24-hour)",
                value="14:00"
            )

            trip_type = gr.Radio(
                ["One-way", "Round trip"],
                value="Round trip",
                label="Trip type"
            )

            ret = gr.Textbox(
                label="Return departure (24-hour, if round trip)",
                value="16:00"
            )

        with gr.Row():
            stay = gr.Slider(
                0, 240,
                value=60,
                step=5,
                label="Expected time at destination (minutes)"
            )

            wait = gr.Slider(
                0, 60,
                value=10,
                step=5,
                label="Maximum pickup wait / grace period (minutes)"
            )

        with gr.Row():
            seats = gr.Number(
                label="Available seats",
                value=3,
                precision=0,
                minimum=1,
                maximum=7
            )

            gas = gr.Number(
                label="Suggested gas contribution per rider ($)",
                value=3,
                minimum=0
            )

        notes = gr.Textbox(
            label="Trip expectations / notes",
            placeholder="Meeting point, stops, return expectations...",
            lines=2
        )

        publish = gr.Button("Post ride", variant="primary")

        publish_status = gr.Textbox(
            label="Posting status",
            interactive=False
        )

        publish.click(
            post,
            [
                driver,
                driver_email,
                destination,
                trip_date,
                depart,
                pickup,
                seats,
                gas,
                trip_type,
                ret,
                stay,
                wait,
                notes
            ],
            [publish_status, table]
        )

    # MANAGE AND CANCEL
    with gr.Tab("↩️ Manage & Cancel"):

        gr.Markdown("### Cancel a rider reservation")

        booking_code = gr.Textbox(
            label="Booking confirmation code"
        )

        cancel_book = gr.Button(
            "Cancel my seat",
            variant="primary"
        )

        booking_cancel_status = gr.Textbox(
            label="Status",
            interactive=False
        )

        cancel_book.click(
            cancel_seat,
            [booking_code],
            [booking_cancel_status, table]
        )

        gr.Markdown(
            "### Cancel an entire ride "
            "(demo only — no authorization checks)"
        )

        cancel_id = gr.Textbox(label="Ride ID")

        cancel_driver = gr.Button("Cancel ride")

        ride_cancel_status = gr.Textbox(
            label="Status",
            interactive=False
        )

        affected = gr.Dataframe(
            interactive=False,
            label="Bookings that would need an alert"
        )

        cancel_driver.click(
            cancel_trip,
            [cancel_id],
            [ride_cancel_status, table, affected]
        )

        gr.Markdown("### Find alternatives")

        alt_dest = gr.Textbox(
            label="Destination for alternative rides"
        )

        alt_btn = gr.Button("Show other rides")

        alt_table = gr.Dataframe(
            interactive=False,
            label="Other available rides"
        )

        alt_note = gr.Textbox(
            label="Note",
            interactive=False
        )

        alt_btn.click(
            alternatives,
            [alt_dest],
            [alt_table, alt_note]
        )

    # TRUST AND ROADMAP
    with gr.Tab("🛡️ Trust & Roadmap"):

        gr.Markdown("""
### What the prototype does today

- Posts and searches rides by destination and date.
- Filters rides by suggested gas contribution.
- Shows driver-set trip type and return departure.
- Shows expected time at destination.
- Shows maximum pickup waiting time.
- Reserves seats and prevents overbooking.
- Reopens seats after rider cancellations.
- Marks cancelled rides and identifies affected bookings.
- Stores demo ride and booking information in SQLite.

### What must be built before real use

**1. TCU student authentication**

University-approved sign-in and verified student profiles.

The current email-format check is not verification.

**2. Driver eligibility and vehicle verification**

Driver's license, insurance, vehicle information,
driving-record checks, and required approvals.

**3. Independent outbound and return reservations**

Separate capacity and booking records for each trip leg.

**4. Real-time notifications**

Email, push, or SMS integration for reservations,
cancellations, and schedule changes.

**5. Better ride matching**

Matching by destination, departure time, pickup location,
return windows, and availability.

**6. Secure data storage and administration**

Managed database, access controls, audit logs,
reporting, and privacy protections.

**7. Driver waiting and duration rules**

Clearly defined pickup grace periods, no-show procedures,
and policies for unexpected delays.

### Safety notice

This demonstration does not verify identity,
ensure passenger safety, or provide transportation.

Do not use it to arrange real rides.
""")

    # ACTIVITY
    with gr.Tab("📋 Demo Activity"):

        gr.Markdown("""
Recent demonstration activity.

No actual email or text messages are sent.
""")

        refresh = gr.Button("Refresh activity")

        activity = gr.Dataframe(
            value=recent_events(),
            interactive=False,
            label="Recent actions"
        )

        refresh.click(
            recent_events,
            outputs=activity
        )


# --------------------------------------------------
# RENDER DEPLOYMENT
# --------------------------------------------------

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", "7860")),
        share=False
    )
