
"""HopIN — TCU-themed student carpool classroom prototype.

Features:
- Responsive purple-and-white interface
- Readable navigation tabs
- Calendar date pickers
- Exact departure time windows
- AM/PM time selectors
- Ride posting and searching
- Seat reservations and cancellations

Demo only: no real identity verification, driver screening,
payments, notifications, or authorization.
"""

import os
import re
import html
import uuid
import sqlite3
from datetime import date, datetime
from threading import RLock

import gradio as gr


# =========================================================
# CONFIGURATION
# =========================================================

DB = os.environ.get("HOPIN_DB_PATH", "hopin_v2_demo.sqlite3")
LOCK = RLock()

# Every 15 minutes, displayed in AM/PM format.
# Underlying values remain HH:MM for easy comparison.
TIMES = [
    (
        datetime.strptime(
            f"{hour:02d}:{minute:02d}", "%H:%M"
        ).strftime("%I:%M %p").lstrip("0"),
        f"{hour:02d}:{minute:02d}"
    )
    for hour in range(24)
    for minute in (0, 15, 30, 45)
]


def time_label(value):
    if not value:
        return "No return trip"

    return datetime.strptime(
        value, "%H:%M"
    ).strftime("%I:%M %p").lstrip("0")


# =========================================================
# DATABASE
# =========================================================

def connect():
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    return con


with connect() as con:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS rides (
        id TEXT PRIMARY KEY,
        driver TEXT NOT NULL,
        email TEXT NOT NULL,
        destination TEXT NOT NULL,
        pickup TEXT NOT NULL,
        ride_date TEXT NOT NULL,
        depart TEXT NOT NULL,
        return_time TEXT,
        seats INTEGER NOT NULL,
        available INTEGER NOT NULL,
        gas REAL NOT NULL,
        wait INTEGER NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open'
    );

    CREATE TABLE IF NOT EXISTS bookings (
        id TEXT PRIMARY KEY,
        ride_id TEXT NOT NULL,
        rider TEXT NOT NULL,
        email TEXT NOT NULL,
        seats INTEGER NOT NULL,
        trip TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'booked',
        created TEXT NOT NULL
    );
    """)


# =========================================================
# HELPERS
# =========================================================

def clean(value):
    return str(value or "").strip()


def valid_email(value):
    return bool(
        re.fullmatch(
            r"[^\s@]+@tcu\.edu",
            clean(value),
            flags=re.I
        )
    )


def day_string(value):
    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    value = clean(value)

    if not value:
        return ""

    return date.fromisoformat(value[:10]).isoformat()


def new_id():
    return uuid.uuid4().hex[:8].upper()


def money(value):
    return f"${value:,.2f}"


# =========================================================
# SEARCH RIDES
# =========================================================

def list_rides(
    destination="",
    selected_day="",
    earliest="00:00",
    latest="23:45",
    max_gas=25
):
    try:
        day = day_string(selected_day)
    except ValueError:
        day = ""

    earliest = earliest or "00:00"
    latest = latest or "23:45"

    if earliest > latest:
        return []

    with connect() as con:
        rows = con.execute("""
            SELECT *
            FROM rides
            WHERE status='open' AND available>0
            ORDER BY ride_date, depart
        """).fetchall()

    output = []

    for ride in rows:
        if ride["ride_date"] < date.today().isoformat():
            continue

        if (
            clean(destination).lower()
            not in ride["destination"].lower()
        ):
            continue

        if day and ride["ride_date"] != day:
            continue

        if max_gas is not None:
            if ride["gas"] > float(max_gas):
                continue

        if not earliest <= ride["depart"] <= latest:
            continue

        output.append(ride)

    return output


# =========================================================
# RIDE CARDS
# =========================================================

def card_html(rows):
    if not rows:
        return """
        <div class="empty">
            <div class="empty-icon">🚙</div>
            <h3>No rides found yet</h3>
            <p>
                Try another destination or time,
                or be the first to offer a ride!
            </p>
        </div>
        """

    cards = []

    for ride in rows:
        destination = html.escape(ride["destination"])
        driver = html.escape(ride["driver"])
        pickup = html.escape(ride["pickup"])
        notes = html.escape(ride["notes"])

        departure_label = time_label(ride["depart"])

        if ride["return_time"]:
            return_label = (
                "↩ Return at "
                + time_label(ride["return_time"])
            )
        else:
            return_label = "→ One-way ride"

        note_html = (
            f'<p class="ride-note">{notes}</p>'
            if notes else ""
        )

        cards.append(f"""
        <article class="ride-card">

            <div class="ride-top">
                <span class="pill">
                    🚗 {ride['available']} seat(s) open
                </span>

                <span class="ride-price">
                    {money(ride['gas'])}
                    <small>suggested / seat</small>
                </span>
            </div>

            <h3>{destination}</h3>

            <div class="ride-meta">
                📅 {ride['ride_date']}
                &nbsp; · &nbsp;
                🕒 {departure_label}
            </div>

            <div class="ride-meta">
                📍 From {pickup}
            </div>

            <div class="ride-meta">
                {return_label}
            </div>

            <div class="ride-meta">
                ⏳ Pickup wait: up to {ride['wait']} min
            </div>

            <div class="ride-bottom">
                <span>
                    👤 {driver}
                    <small>(demo profile)</small>
                </span>

                <strong>
                    Ride code: {ride['id']}
                </strong>
            </div>

            {note_html}

        </article>
        """)

    return (
        '<div class="ride-grid">'
        + "".join(cards)
        + "</div>"
    )


def choices(rows):
    return [
        (
            f"{ride['destination']} · "
            f"{ride['ride_date']} at "
            f"{time_label(ride['depart'])} · "
            f"{ride['id']}",
            ride["id"]
        )
        for ride in rows
    ]


def search(
    destination,
    day,
    earliest,
    latest,
    budget
):
    if earliest and latest and earliest > latest:
        return (
            card_html([]),
            gr.update(choices=[], value=None),
            "Choose a latest departure that is "
            "after your earliest departure."
        )

    rows = list_rides(
        destination,
        day,
        earliest,
        latest,
        budget
    )

    return (
        card_html(rows),
        gr.update(
            choices=choices(rows),
            value=None
        ),
        f"Found {len(rows)} matching ride(s)."
    )


# =========================================================
# POST A RIDE
# =========================================================

def post(
    driver,
    email,
    destination,
    pickup,
    day,
    depart,
    ret,
    seats,
    gas,
    wait,
    notes
):
    required = (
        driver,
        email,
        destination,
        pickup,
        day,
        depart
    )

    if not all(clean(value) for value in required):
        return (
            "Please complete all required fields.",
            gr.update()
        )

    if not valid_email(email):
        return (
            "Use a demo email ending in @tcu.edu. "
            "This does not verify student status.",
            gr.update()
        )

    try:
        ride_day = day_string(day)

        if ride_day < date.today().isoformat():
            raise ValueError(
                "Choose today or a future date."
            )

        seats = int(seats)
        gas = float(gas)
        wait = int(wait)

        if not 1 <= seats <= 7:
            raise ValueError(
                "Choose between 1 and 7 seats."
            )

        if not 0 <= gas <= 100:
            raise ValueError(
                "Gas share must be between $0 and $100."
            )

        if not 0 <= wait <= 60:
            raise ValueError(
                "Waiting time must be 0–60 minutes."
            )

        if ret != "No return trip" and ret <= depart:
            raise ValueError(
                "Return time must be later than departure "
                "on the same day."
            )

    except (ValueError, TypeError) as error:
        return str(error), gr.update()

    ride_id = new_id()

    with LOCK, connect() as con:
        con.execute("""
            INSERT INTO rides (
                id, driver, email, destination,
                pickup, ride_date, depart,
                return_time, seats, available,
                gas, wait, notes, status
            )
            VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,'open'
            )
        """, (
            ride_id,
            clean(driver),
            clean(email),
            clean(destination),
            clean(pickup),
            ride_day,
            depart,
            None if ret == "No return trip" else ret,
            seats,
            seats,
            gas,
            wait,
            clean(notes)
        ))

    return (
        f"🎉 Your ride is posted! "
        f"Ride code: {ride_id}. "
        "Find it under Find a ride.",
        gr.update()
    )


# =========================================================
# BOOK A RIDE
# =========================================================

def book(
    ride_id,
    rider,
    email,
    seats,
    trip
):
    if not ride_id:
        return "Select an available ride first."

    if not clean(rider) or not valid_email(email):
        return (
            "Enter a display name and demo @tcu.edu email. "
            "Emails are not verified."
        )

    try:
        seats = int(seats)

        if seats < 1 or seats > 7:
            raise ValueError()

    except (ValueError, TypeError):
        return "Select 1–7 seats."

    with LOCK, connect() as con:
        ride = con.execute("""
            SELECT *
            FROM rides
            WHERE id=? AND status='open'
        """, (ride_id,)).fetchone()

        if not ride or ride["available"] < seats:
            return (
                "This ride no longer has enough seats. "
                "Refresh the results."
            )

        if (
            trip == "Going and returning"
            and not ride["return_time"]
        ):
            return (
                "This ride is one-way. "
                "Choose outbound only."
            )

        booking_id = new_id()

        con.execute("""
            UPDATE rides
            SET available=available-?
            WHERE id=?
        """, (seats, ride_id))

        con.execute("""
            INSERT INTO bookings
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            booking_id,
            ride_id,
            clean(rider),
            clean(email),
            seats,
            trip,
            "booked",
            datetime.now().isoformat(
                timespec="seconds"
            )
        ))

    return (
        f"🎉 Seat saved! Your booking code is "
        f"{booking_id}. Keep this code to view "
        "or cancel your demo booking. "
        "No message was sent."
    )


# =========================================================
# VIEW BOOKING
# =========================================================

def lookup_booking(code):
    with connect() as con:
        row = con.execute("""
            SELECT
                b.*,
                r.destination,
                r.ride_date,
                r.depart,
                r.return_time,
                r.pickup
            FROM bookings b
            JOIN rides r ON b.ride_id=r.id
            WHERE b.id=?
        """, (
            clean(code).upper(),
        )).fetchone()

    if not row:
        return "No booking found. Check the code."

    return (
        f"**{row['destination']}** · "
        f"{row['ride_date']} at "
        f"{time_label(row['depart'])}\n\n"
        f"Pickup: {row['pickup']}\n\n"
        f"Reservation: {row['trip']} · "
        f"{row['seats']} seat(s)\n\n"
        f"Status: **{row['status'].title()}**\n\n"
        f"Booking code: `{row['id']}`"
    )


# =========================================================
# CANCEL BOOKING
# =========================================================

def cancel_booking(code):
    code = clean(code).upper()

    with LOCK, connect() as con:
        booking = con.execute("""
            SELECT *
            FROM bookings
            WHERE id=? AND status='booked'
        """, (code,)).fetchone()

        if not booking:
            return (
                "No active booking found for that code."
            )

        con.execute("""
            UPDATE bookings
            SET status='cancelled'
            WHERE id=?
        """, (code,))

        con.execute("""
            UPDATE rides
            SET available=MIN(seats,available+?)
            WHERE id=? AND status='open'
        """, (
            booking["seats"],
            booking["ride_id"]
        ))

    return (
        f"Booking {code} cancelled. "
        "The seats are available again "
        "if the ride is still open."
    )


# =========================================================
# STYLING
# =========================================================

CSS = """
:root {
    color-scheme: light;
}

html,
body,
.gradio-container {
    background: #faf8ff !important;
    color: #261d33 !important;
}

.gradio-container {
    max-width: 1100px !important;
    margin: auto !important;
    padding: 12px 18px 40px !important;
    font-family:
        ui-sans-serif,
        system-ui,
        -apple-system,
        sans-serif !important;
}

.gradio-container * {
    box-sizing: border-box;
}

#hero {
    background: linear-gradient(
        120deg,
        #401164 0%,
        #6b2ba0 75%,
        #8a50bb 100%
    );
    border-radius: 24px;
    padding: 32px 34px;
    margin: 8px 0 20px;
    box-shadow: 0 12px 28px #4d197925;
}

#hero,
#hero * {
    color: white !important;
}

#hero h1 {
    font-size: clamp(2rem, 5vw, 3.1rem);
    margin: 0 0 8px !important;
    font-weight: 800;
}

#hero p {
    font-size: clamp(.95rem, 2vw, 1.1rem);
    margin: 4px 0 !important;
}

#hero .sub {
    opacity: .9;
}

/* Readable navigation tabs */
.gradio-container .tab-nav {
    display: flex !important;
    flex-wrap: wrap !important;
    gap: 8px !important;
    padding-bottom: 8px !important;
    border-bottom: 1px solid #e5d8f0 !important;
}

.gradio-container .tab-nav button {
    background: #f0e6fa !important;
    color: #4d1979 !important;
    border: 1px solid #e1d0ee !important;
    border-radius: 12px !important;
    padding: 11px 17px !important;
    font-weight: 700 !important;
    opacity: 1 !important;
    text-shadow: none !important;
}

.gradio-container .tab-nav button,
.gradio-container .tab-nav button * {
    color: #4d1979 !important;
    opacity: 1 !important;
}

.gradio-container .tab-nav button:hover {
    background: #dfc9f2 !important;
    color: #351052 !important;
}

.gradio-container .tab-nav button.selected {
    background: #4d1979 !important;
    color: #ffffff !important;
    border-color: #4d1979 !important;
}

.gradio-container .tab-nav button.selected,
.gradio-container .tab-nav button.selected * {
    color: #ffffff !important;
}

.gradio-container .block,
.gradio-container .form {
    border-radius: 14px !important;
}

.gradio-container button.primary {
    background: #4d1979 !important;
    color: white !important;
    border: 1px solid #4d1979 !important;
    border-radius: 12px !important;
    font-weight: 700 !important;
}

.gradio-container button.primary:hover {
    background: #6c3299 !important;
}

.gradio-container input,
.gradio-container textarea,
.gradio-container select {
    background: #ffffff !important;
    color: #21182c !important;
    border-radius: 9px !important;
}

.gradio-container label,
.gradio-container .label-wrap,
.gradio-container .prose,
.gradio-container .prose p,
.gradio-container .prose h2,
.gradio-container .prose h3 {
    color: #30223f !important;
}

/* Ride cards */
.ride-grid {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(min(100%, 290px), 1fr));
    gap: 15px;
    margin: 12px 0 22px;
}

.ride-card {
    background: #ffffff;
    border: 1px solid #e7d9f2;
    border-radius: 18px;
    padding: 20px;
    box-shadow: 0 5px 16px #4d19790c;
    min-width: 0;
}

.ride-top,
.ride-bottom {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
}

.pill {
    background: #efe6f8;
    color: #4d1979;
    font-size: .77rem;
    padding: 6px 10px;
    border-radius: 999px;
    font-weight: 700;
}

.ride-price {
    font-size: 1.15rem;
    font-weight: 800;
    color: #4d1979;
}

.ride-price small {
    font-size: .65rem;
    display: block;
    color: #72637d;
    font-weight: 500;
}

.ride-card h3 {
    color: #2b153c;
    font-size: 1.35rem;
    margin: 16px 0 12px;
    overflow-wrap: anywhere;
}

.ride-meta {
    color: #574b63;
    font-size: .88rem;
    margin: 8px 0;
    overflow-wrap: anywhere;
}

.ride-bottom {
    border-top: 1px solid #eee7f3;
    margin-top: 16px;
    padding-top: 13px;
    color: #47364f;
    font-size: .8rem;
}

.ride-bottom strong {
    color: #4d1979;
}

.ride-bottom small {
    color: #887b90;
}

.ride-note {
    color: #61516d;
    font-size: .83rem;
    margin: 12px 0 0;
}

.empty {
    background: #ffffff;
    border: 2px dashed #e3d2f1;
    border-radius: 18px;
    padding: 30px 20px;
    text-align: center;
    color: #6c5c77;
}

.empty-icon {
    font-size: 2rem;
}

.empty h3 {
    color: #3c2154;
    margin: 8px 0;
}

.empty p {
    margin: 5px 0;
}

#footnote {
    padding: 14px 18px;
    background: #f0eaf7;
    border-radius: 12px;
    font-size: .84rem;
    margin-top: 24px;
}

#footnote,
#footnote * {
    color: #5a4968 !important;
}

/* Mobile-friendly layout */
@media (max-width: 640px) {
    .gradio-container {
        padding: 8px 10px 25px !important;
    }

    #hero {
        padding: 24px 19px;
        border-radius: 18px;
    }

    .ride-card {
        padding: 16px;
    }

    .gradio-container .tab-nav {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 7px !important;
    }

    .gradio-container .tab-nav button {
        width: 100% !important;
        padding: 11px 6px !important;
        font-size: 0.82rem !important;
        text-align: center !important;
    }
}
"""


# =========================================================
# WEBSITE
# =========================================================

with gr.Blocks(
    title="HopIN | TCU Campus Rides",
    css=CSS,
    theme=gr.themes.Soft(
        primary_hue="purple",
        neutral_hue="slate"
    )
) as demo:

    gr.HTML("""
    <div id="hero">
        <h1>🐸 HopIN</h1>
        <p>
            <strong>
                The ride already exists.
                The connection doesn't.
            </strong>
        </p>
        <p class="sub">
            Going somewhere? Share the journey. 💜
        </p>
    </div>
    """)

    with gr.Tabs():

        # ---------------------------------------------
        # FIND A RIDE
        # ---------------------------------------------

        with gr.Tab("🔎 Find a ride"):

            gr.Markdown("## Where are we going?")

            with gr.Row():
                q_destination = gr.Textbox(
                    label="Destination",
                    placeholder="Try Target, Clearfork, or H-E-B",
                    scale=2
                )

                q_date = gr.DateTime(
                    label="Travel date (tap for calendar)",
                    include_time=False,
                    type="string",
                    value=None,
                    scale=1
                )

            with gr.Row():
                q_earliest = gr.Dropdown(
                    choices=TIMES,
                    value="00:00",
                    label="Earliest departure",
                    interactive=True,
                    filterable=True
                )

                q_latest = gr.Dropdown(
                    choices=TIMES,
                    value="23:45",
                    label="Latest departure",
                    interactive=True,
                    filterable=True
                )

            q_budget = gr.Slider(
                0,
                25,
                value=25,
                step=1,
                label="Max suggested gas share ($)"
            )

            search_btn = gr.Button(
                "✨ Find rides",
                variant="primary"
            )

            count = gr.Markdown(
                "See what trips students have shared below."
            )

            cards = gr.HTML(
                value=card_html(list_rides())
            )

            gr.Markdown("### 💺 Save a seat")

            gr.Markdown(
                "Choose a ride below, then enter a "
                "**fictional** name and email for this demo."
            )

            ride_select = gr.Dropdown(
                choices=choices(list_rides()),
                label="Choose a ride",
                interactive=True
            )

            with gr.Row():
                rider_name = gr.Textbox(
                    label="Your display name",
                    placeholder="Demo Student"
                )

                rider_email = gr.Textbox(
                    label="Demo TCU email",
                    placeholder="student@tcu.edu"
                )

            with gr.Row():
                book_seats = gr.Dropdown(
                    [1, 2, 3, 4, 5, 6, 7],
                    value=1,
                    label="Seats needed"
                )

                book_trip = gr.Radio(
                    [
                        "Outbound only",
                        "Going and returning"
                    ],
                    value="Outbound only",
                    label="Trip plan"
                )

            book_btn = gr.Button(
                "💜 Reserve my seat",
                variant="primary"
            )

            booking_message = gr.Textbox(
                label="Booking confirmation",
                interactive=False,
                lines=3
            )

            search_btn.click(
                search,
                [
                    q_destination,
                    q_date,
                    q_earliest,
                    q_latest,
                    q_budget
                ],
                [
                    cards,
                    ride_select,
                    count
                ]
            )

            book_btn.click(
                book,
                [
                    ride_select,
                    rider_name,
                    rider_email,
                    book_seats,
                    book_trip
                ],
                [booking_message]
            ).then(
                search,
                [
                    q_destination,
                    q_date,
                    q_earliest,
                    q_latest,
                    q_budget
                ],
                [
                    cards,
                    ride_select,
                    count
                ]
            )

        # ---------------------------------------------
        # OFFER A RIDE
        # ---------------------------------------------

        with gr.Tab("🚗 Offer a ride"):

            gr.Markdown("## Have an extra seat? 💜")

            gr.Markdown(
                "Already heading out? Share the details "
                "so another student can join your trip."
            )

            with gr.Row():
                driver_name = gr.Textbox(
                    label="Driver display name",
                    placeholder="Demo Driver"
                )

                driver_email = gr.Textbox(
                    label="Demo TCU email",
                    placeholder="driver@tcu.edu"
                )

            with gr.Row():
                dest = gr.Textbox(
                    label="Where are you going?",
                    placeholder="Target on West 7th"
                )

                pickup = gr.Textbox(
                    label="Where should riders meet?",
                    placeholder="Campus pickup point"
                )

            with gr.Row():
                travel_date = gr.DateTime(
                    label="Departure date (tap for calendar)",
                    include_time=False,
                    type="string",
                    value=date.today().isoformat()
                )

                depart = gr.Dropdown(
                    choices=TIMES,
                    value="14:00",
                    label="What time are you leaving?",
                    interactive=True,
                    filterable=True
                )

                ret = gr.Dropdown(
                    choices=[
                        ("No return trip", "No return trip")
                    ] + TIMES,
                    value="No return trip",
                    label="What time will you head back?",
                    interactive=True,
                    filterable=True
                )

            with gr.Row():
                offer_seats = gr.Slider(
                    1,
                    7,
                    value=3,
                    step=1,
                    label="Seats available"
                )

                gas = gr.Slider(
                    0,
                    20,
                    value=3,
                    step=1,
                    label="Suggested gas share ($ / seat)"
                )

                wait = gr.Slider(
                    0,
                    60,
                    value=10,
                    step=5,
                    label="Minutes willing to wait at pickup"
                )

            notes = gr.Textbox(
                label="Anything else riders should know?",
                placeholder=(
                    "e.g. I plan to shop for about an hour."
                ),
                lines=2
            )

            post_btn = gr.Button(
                "🚀 Post my ride",
                variant="primary"
            )

            post_message = gr.Textbox(
                label="Your ride",
                interactive=False,
                lines=2
            )

            post_btn.click(
                post,
                [
                    driver_name,
                    driver_email,
                    dest,
                    pickup,
                    travel_date,
                    depart,
                    ret,
                    offer_seats,
                    gas,
                    wait,
                    notes
                ],
                [
                    post_message,
                    ride_select
                ]
            ).then(
                search,
                [
                    q_destination,
                    q_date,
                    q_earliest,
                    q_latest,
                    q_budget
                ],
                [
                    cards,
                    ride_select,
                    count
                ]
            )

        # ---------------------------------------------
        # MY BOOKING
        # ---------------------------------------------

        with gr.Tab("🎟️ My booking"):

            gr.Markdown("## Keep track of your seat")

            gr.Markdown(
                "Use the booking code shown "
                "when you reserved a seat."
            )

            lookup_code = gr.Textbox(
                label="Booking code",
                placeholder="e.g. A1B2C3D4"
            )

            with gr.Row():
                lookup_btn = gr.Button(
                    "View booking",
                    variant="primary"
                )

                cancel_btn = gr.Button(
                    "Cancel my booking",
                    variant="secondary"
                )

            lookup_result = gr.Markdown(
                "Your booking details will appear here."
            )

            lookup_btn.click(
                lookup_booking,
                [lookup_code],
                [lookup_result]
            )

            cancel_btn.click(
                cancel_booking,
                [lookup_code],
                [lookup_result]
            ).then(
                search,
                [
                    q_destination,
                    q_date,
                    q_earliest,
                    q_latest,
                    q_budget
                ],
                [
                    cards,
                    ride_select,
                    count
                ]
            )

        # ---------------------------------------------
        # ABOUT HOPIN
        # ---------------------------------------------

        with gr.Tab("💜 About HopIN"):

            gr.Markdown("""
## A little less hassle, a little more community

HopIN is a student-built idea for connecting
people who are already going somewhere with
classmates who need a ride.

The goal is to make shared trips easier to plan,
with clear pickup points, departure times,
return plans, and available seats.

### What works in this demo

- Search for available rides
- Post a new ride
- Select travel dates using a calendar
- Choose exact departure time ranges
- Choose exact departure and return times
- View available seats
- Reserve a seat
- Get a booking confirmation code
- View or cancel a booking

### What still needs to be built

- University-approved student authentication
- Driver and vehicle verification
- Driver-only ride management
- Separate outbound and return seat tracking
- Real cancellation notifications
- Secure production data storage
- Safety and insurance review

**This is a classroom prototype, not an
approved transportation service.**
""")

    gr.Markdown("""
**Demo only:** Use made-up names and email addresses.
TCU identities and drivers are not verified.
No actual notifications are sent, and rides
should not be arranged through this prototype.
""", elem_id="footnote")


# =========================================================
# RENDER STARTUP
# =========================================================

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(
            os.environ.get("PORT", "7860")
        ),
        share=False
    )
