import gradio as gr
import sqlite3, uuid
from datetime import datetime

DB="hopin.db"
def db():
    c=sqlite3.connect(DB,check_same_thread=False); c.row_factory=sqlite3.Row; return c

def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS rides(id TEXT PRIMARY KEY,driver TEXT,destination TEXT,ride_date TEXT,ride_time TEXT,pickup TEXT,total_seats INTEGER,seats_left INTEGER,gas_note TEXT,notes TEXT,status TEXT);
        CREATE TABLE IF NOT EXISTS bookings(id TEXT PRIMARY KEY,ride_id TEXT,rider TEXT,contact TEXT,seats INTEGER,status TEXT);
        """)
        if c.execute("SELECT COUNT(*) FROM rides").fetchone()[0]==0:
            c.executemany("INSERT INTO rides VALUES(?,?,?,?,?,?,?,?,?,?,?)",[
              ("MAYA01","Maya","Trader Joe's","2026-09-27","11:00","North Dorm Loop",3,3,"Split gas","Returning around 1 PM","open"),
              ("JORD02","Jordan","Trader Joe's","2026-09-27","11:30","Main Gate",2,2,"$3 suggested","Quick grocery run","open"),
              ("PRIY03","Priya","Downtown Fort Worth","2026-09-26","19:00","South Lot",4,4,"Split gas","Dinner downtown","open")])
init()

def board(q=""):
    with db() as c:
        if q.strip():
            rows=c.execute("SELECT * FROM rides WHERE status='open' AND seats_left>0 AND lower(destination) LIKE ? ORDER BY ride_date,ride_time",(f"%{q.lower().strip()}%",)).fetchall()
        else:
            rows=c.execute("SELECT * FROM rides WHERE status='open' AND seats_left>0 ORDER BY ride_date,ride_time").fetchall()
    if not rows: return "<div class='empty'><h3>No matching rides yet.</h3><p>Try another destination or offer a ride.</p></div>"
    return "".join(f"""<div class="ride-card"><div class="top"><b>🚗 {r['driver']}</b><span>{r['seats_left']} seat(s) left</span></div>
    <h3>{r['destination']}</h3><p>📅 {r['ride_date']} • 🕒 {r['ride_time']}</p><p>📍 {r['pickup']}</p>
    <p>⛽ {r['gas_note']}</p><small>Ride ID: <b>{r['id']}</b></small></div>""" for r in rows)

def post(driver,dest,date,time,pickup,seats,gas,notes):
    if not all(str(x).strip() for x in [driver,dest,date,time,pickup]): return "⚠️ Complete all required fields.",board()
    try:
        seats=int(seats); datetime.strptime(date,"%Y-%m-%d"); datetime.strptime(time,"%H:%M")
        if not 1<=seats<=7: raise ValueError
    except: return "⚠️ Use YYYY-MM-DD, 24-hour HH:MM, and 1–7 seats.",board()
    rid=uuid.uuid4().hex[:6].upper()
    with db() as c: c.execute("INSERT INTO rides VALUES(?,?,?,?,?,?,?,?,?,?,?)",(rid,driver.strip(),dest.strip(),date,time,pickup.strip(),seats,seats,gas.strip() or "Split gas",notes.strip(),"open"))
    return f"✅ Ride {rid} posted!",board()

def join(rid,rider,contact,seats):
    rid=rid.strip().upper()
    if not rid or not rider.strip() or not contact.strip(): return "⚠️ Enter Ride ID, name, and contact.",board()
    try: seats=int(seats)
    except: return "⚠️ Enter a valid seat count.",board()
    with db() as c:
        r=c.execute("SELECT * FROM rides WHERE id=?",(rid,)).fetchone()
        if not r or r["status"]!="open": return "❌ Ride unavailable.",board()
        if seats<1 or seats>r["seats_left"]: return f"❌ Only {r['seats_left']} seat(s) remain.",board()
        bid=uuid.uuid4().hex[:8].upper(); left=r["seats_left"]-seats
        c.execute("UPDATE rides SET seats_left=?,status=? WHERE id=?",(left,"full" if left==0 else "open",rid))
        c.execute("INSERT INTO bookings VALUES(?,?,?,?,?,?)",(bid,rid,rider.strip(),contact.strip(),seats,"booked"))
    return f"🙌 You're in! Confirmation: {bid}. Save this code.",board()

def cancel_booking(bid):
    bid=bid.strip().upper()
    with db() as c:
        b=c.execute("SELECT * FROM bookings WHERE id=? AND status='booked'",(bid,)).fetchone()
        if not b: return "❌ Active booking not found.",board()
        r=c.execute("SELECT * FROM rides WHERE id=?",(b["ride_id"],)).fetchone()
        c.execute("UPDATE bookings SET status='cancelled' WHERE id=?",(bid,))
        if r and r["status"]!="cancelled":
            c.execute("UPDATE rides SET seats_left=?,status='open' WHERE id=?",(min(r["total_seats"],r["seats_left"]+b["seats"]),r["id"]))
    return "↩️ Seat cancelled and reopened.",board()

def cancel_ride(rid):
    rid=rid.strip().upper()
    with db() as c:
        r=c.execute("SELECT * FROM rides WHERE id=? AND status!='cancelled'",(rid,)).fetchone()
        if not r: return "❌ Active ride not found.",board()
        n=c.execute("SELECT COUNT(*) FROM bookings WHERE ride_id=? AND status='booked'",(rid,)).fetchone()[0]
        c.execute("UPDATE rides SET status='cancelled' WHERE id=?",(rid,))
        c.execute("UPDATE bookings SET status='driver_cancelled' WHERE ride_id=? AND status='booked'",(rid,))
        alt=c.execute("SELECT * FROM rides WHERE id!=? AND status='open' AND seats_left>0 AND lower(destination)=lower(?) ORDER BY ride_date,ride_time LIMIT 1",(rid,r["destination"])).fetchone()
    msg=f"⛔ Ride cancelled. {n} rider(s) would be notified."
    if alt: msg+=f" Suggested alternative: {alt['driver']} at {alt['ride_time']} (Ride {alt['id']})."
    return msg,board()

CSS=""".gradio-container{max-width:900px!important;margin:auto!important}.hero{text-align:center;padding:25px}.hero h1{font-size:42px}.ride-card{border:1px solid #ddd;border-radius:18px;padding:18px;margin:12px 0}.top{display:flex;justify-content:space-between}.top span{background:#eee;padding:5px 10px;border-radius:999px}.empty{text-align:center;padding:30px;border:1px dashed #aaa;border-radius:18px}"""

with gr.Blocks(css=CSS,title="HopIN") as demo:
    gr.HTML("<div class='hero'><h1>🐸 HopIN</h1><p>Campus rides. Shared. <b>Class prototype — use demo information only.</b></p></div>")
    with gr.Tab("🔍 Find a Ride"):
        q=gr.Textbox(label="Where do you want to go?",placeholder="Trader Joe's, Downtown Fort Worth...")
        search=gr.Button("Search",variant="primary"); listing=gr.HTML(board())
        search.click(board,q,listing)
        gr.Markdown("### Hop in")
        rid=gr.Textbox(label="Ride ID"); rider=gr.Textbox(label="Your name")
        contact=gr.Textbox(label="Contact",placeholder="Use demo information only")
        n=gr.Number(label="Seats",value=1,precision=0); b=gr.Button("Hop In",variant="primary"); msg=gr.Textbox(label="Status")
        b.click(join,[rid,rider,contact,n],[msg,listing])
    with gr.Tab("➕ Offer a Ride"):
        driver=gr.Textbox(label="Driver name *"); dest=gr.Textbox(label="Destination *")
        date=gr.Textbox(label="Date *",placeholder="2026-09-27"); time=gr.Textbox(label="Time *",placeholder="11:00")
        pickup=gr.Textbox(label="Pickup point *"); seats=gr.Number(label="Open seats",value=3,precision=0)
        gas=gr.Textbox(label="Gas contribution",value="Split gas"); notes=gr.Textbox(label="Notes")
        p=gr.Button("Post Ride",variant="primary"); pm=gr.Textbox(label="Status")
        p.click(post,[driver,dest,date,time,pickup,seats,gas,notes],[pm,listing])
    with gr.Tab("🎟 Manage"):
        gr.Markdown("### Rider cancellation"); bid=gr.Textbox(label="Confirmation code"); cb=gr.Button("Cancel My Seat"); cm=gr.Textbox(label="Status")
        cb.click(cancel_booking,bid,[cm,listing])
        gr.Markdown("### Driver cancellation"); crid=gr.Textbox(label="Ride ID"); cr=gr.Button("Cancel Entire Ride"); crm=gr.Textbox(label="Status")
        cr.click(cancel_ride,crid,[crm,listing])
    with gr.Tab("ℹ️ About"):
        gr.Markdown("""### Prototype flow
Drivers post trips they are already taking → riders search and reserve → seats automatically decrease → rider cancellations restore seats → driver cancellations close the trip and look for another matching ride.

**Prototype only:** no real payments, TCU authentication, GPS, driver verification, or real notifications. Use demo data only.""")
if __name__=="__main__": demo.launch()
