import streamlit as st
import sqlite3
import hashlib
import secrets
import datetime as dt
import pandas as pd
import folium
from streamlit_folium import st_folium
from geopy.distance import geodesic

# =============================
# Page configuration
# =============================
st.set_page_config(
    page_title="PairBond - Stay Connected",
    page_icon="💕",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================
# Constants
# =============================
DATABASE_PATH = "pairbond.db"
DEFAULT_HOME = (40.712800, -74.006000)  # Example coords (NYC). Replace if you wish.
DEFAULT_WORK = (40.758900, -73.985100)  # Example coords (Times Sq). Replace if you wish.

# =============================
# Database helpers
# =============================
def get_conn():
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_database():
    """Initialize SQLite database with required tables."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pairs (
            pair_code TEXT PRIMARY KEY,
            pair_name TEXT NOT NULL,
            passphrase_hash TEXT NOT NULL,
            user1_name TEXT,
            user2_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair_code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            battery_level INTEGER,
            sharing_duration TEXT DEFAULT 'Indefinitely',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (pair_code) REFERENCES pairs (pair_code) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair_code TEXT NOT NULL,
            from_user TEXT NOT NULL,
            to_user TEXT NOT NULL,
            message TEXT DEFAULT 'Thinking of you 💖',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_read INTEGER DEFAULT 0,
            FOREIGN KEY (pair_code) REFERENCES pairs (pair_code) ON DELETE CASCADE
        )
        """
    )

    conn.commit()
    conn.close()

# Initialize DB on startup
init_database()

# =============================
# Utility functions
# =============================
def generate_pair_code() -> str:
    return f"PB-{secrets.randbelow(99999):05d}"

def hash_passphrase(passphrase: str) -> str:
    return hashlib.sha256(passphrase.encode()).hexdigest()

def create_pair(pair_name: str, passphrase: str, user_name: str) -> str:
    conn = get_conn()
    cur = conn.cursor()

    pair_code = generate_pair_code()
    while cur.execute("SELECT 1 FROM pairs WHERE pair_code = ?", (pair_code,)).fetchone():
        pair_code = generate_pair_code()

    pass_hash = hash_passphrase(passphrase)
    cur.execute(
        """
        INSERT INTO pairs (pair_code, pair_name, passphrase_hash, user1_name)
        VALUES (?, ?, ?, ?)
        """,
        (pair_code, pair_name, pass_hash, user_name),
    )

    conn.commit()
    conn.close()
    return pair_code

def join_pair(pair_code: str, passphrase: str, user_name: str):
    conn = get_conn()
    cur = conn.cursor()

    row = cur.execute(
        "SELECT pair_name, passphrase_hash, user1_name, user2_name FROM pairs WHERE pair_code = ?",
        (pair_code,),
    ).fetchone()

    if not row:
        conn.close()
        return False, "Pair code not found"

    pair_name, stored_hash, user1_name, user2_name = row

    if hash_passphrase(passphrase) != stored_hash:
        conn.close()
        return False, "Incorrect passphrase"

    if user2_name:
        conn.close()
        return False, "This pair is already complete"

    cur.execute("UPDATE pairs SET user2_name = ? WHERE pair_code = ?", (user_name, pair_code))
    conn.commit()
    conn.close()
    return True, f"Successfully joined {pair_name}!"

def authenticate_pair(pair_code: str, passphrase: str):
    conn = get_conn()
    cur = conn.cursor()

    row = cur.execute(
        "SELECT pair_name, passphrase_hash, user1_name, user2_name FROM pairs WHERE pair_code = ?",
        (pair_code,),
    ).fetchone()

    if not row:
        conn.close()
        return False, None, "Pair code not found"

    pair_name, stored_hash, user1_name, user2_name = row

    if hash_passphrase(passphrase) != stored_hash:
        conn.close()
        return False, None, "Incorrect passphrase"

    conn.close()
    return True, {"pair_name": pair_name, "user1_name": user1_name, "user2_name": user2_name}, "Authentication successful"

def update_location(
    pair_code: str,
    user_name: str,
    latitude: float,
    longitude: float,
    battery_level: int | None = None,
    sharing_duration: str = "Indefinitely",
):
    conn = get_conn()
    cur = conn.cursor()

    # Compute expires_at
    expires_at = None
    now = dt.datetime.now()
    if sharing_duration == "1 hour":
        expires_at = now + dt.timedelta(hours=1)
    elif sharing_duration == "Until tomorrow":
        end_of_tomorrow = (now + dt.timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
        expires_at = end_of_tomorrow

    # Determine user_id
    pair_row = cur.execute("SELECT user1_name, user2_name FROM pairs WHERE pair_code = ?", (pair_code,)).fetchone()
    if not pair_row:
        conn.close()
        raise ValueError("Pair not found")

    user1_name, user2_name = pair_row
    if user_name == user1_name:
        user_id = 1
    elif user_name == user2_name:
        user_id = 2
    else:
        user_id = 2  # fallback, avoids overwriting user1

    # Remove old location for this user
    cur.execute("DELETE FROM locations WHERE pair_code = ? AND user_id = ?", (pair_code, user_id))

    # Insert fresh
    cur.execute(
        """
        INSERT INTO locations (
            pair_code, user_id, user_name, latitude, longitude, battery_level, sharing_duration, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (pair_code, user_id, user_name, float(latitude), float(longitude), battery_level, sharing_duration, expires_at),
    )

    conn.commit()
    conn.close()

def cleanup_expired_locations(conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM locations WHERE expires_at IS NOT NULL AND expires_at < CURRENT_TIMESTAMP")
    conn.commit()

def get_locations(pair_code: str):
    conn = get_conn()
    cleanup_expired_locations(conn)
    cur = conn.cursor()

    rows = cur.execute(
        """
        SELECT user_id, user_name, latitude, longitude, battery_level, timestamp, sharing_duration
        FROM locations
        WHERE pair_code = ?
        ORDER BY timestamp DESC
        """,
        (pair_code,),
    ).fetchall()

    conn.close()
    return rows

def send_pulse(pair_code: str, from_user: str, to_user: str, message: str = "Thinking of you 💖"):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO notifications (pair_code, from_user, to_user, message) VALUES (?, ?, ?, ?)",
        (pair_code, from_user, to_user, message),
    )
    conn.commit()
    conn.close()

def get_unread_notifications(pair_code: str, user_name: str):
    conn = get_conn()
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT id, from_user, message, timestamp
        FROM notifications
        WHERE pair_code = ? AND to_user = ? AND is_read = 0
        ORDER BY timestamp DESC
        """,
        (pair_code, user_name),
    ).fetchall()

    # Mark them read
    cur.execute(
        "UPDATE notifications SET is_read = 1 WHERE pair_code = ? AND to_user = ? AND is_read = 0",
        (pair_code, user_name),
    )
    conn.commit()
    conn.close()
    return rows

# =============================
# Map rendering
# =============================
def _fmt_timeago(ts: str | dt.datetime) -> str:
    if isinstance(ts, str):
        try:
            t = dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return "Unknown"
    else:
        t = ts
    delta = dt.datetime.now() - t
    secs = max(0, int(delta.total_seconds()))
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"

def create_map(locations: list[tuple]) -> folium.Map:
    if not locations:
        return folium.Map(location=[DEFAULT_HOME[0], DEFAULT_HOME[1]], zoom_start=10)

    lats = [row[2] for row in locations]
    lons = [row[3] for row in locations]
    center = (sum(lats) / len(lats), sum(lons) / len(lons))
    m = folium.Map(location=[center[0], center[1]], zoom_start=12)

    for (user_id, user_name, lat, lon, battery, timestamp, duration) in locations:
        lines = [f"<b>{user_name}</b>", f"Updated: {_fmt_timeago(timestamp)}", f"Sharing: {duration}"]
        if battery is not None:
            lines.append(f"Battery: {battery}%")
        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup("<br>".join(lines), max_width=240),
            tooltip=user_name,
        ).add_to(m)

    if len(locations) == 2:
        coords = [(locations[0][2], locations[0][3]), (locations[1][2], locations[1][3])]
        folium.PolyLine(coords, weight=3, opacity=0.7).add_to(m)
        distance_km = geodesic(coords[0], coords[1]).kilometers
        midpoint = ((coords[0][0] + coords[1][0]) / 2, (coords[0][1] + coords[1][1]) / 2)
        folium.Marker(location=midpoint, popup=f"Distance: {distance_km:.2f} km").add_to(m)

    return m

# =============================
# App
# =============================
def main():
    # Session state
    st.session_state.setdefault("logged_in", False)
    st.session_state.setdefault("pair_code", None)
    st.session_state.setdefault("current_user", None)
    st.session_state.setdefault("pair_info", None)

    # Auth views
    if not st.session_state["logged_in"]:
        st.title("💕 PairBond")
        st.subheader("Stay connected with your special someone")

        tab1, tab2, tab3 = st.tabs(["Join Existing Pair", "Create New Pair", "Login to Existing Pair"])

        with tab1:
            st.header("Join a Pair")
            with st.form("join_pair_form"):
                pair_code = st.text_input("Pair Code (e.g., PB-12345)")
                passphrase = st.text_input("Passphrase", type="password")
                user_name = st.text_input("Your Name")
                submitted = st.form_submit_button("Join Pair")

            if submitted:
                if pair_code and passphrase and user_name:
                    ok, msg = join_pair(pair_code.upper(), passphrase, user_name)
                    if ok:
                        st.success(msg)
                        st.session_state.update(
                            logged_in=True,
                            pair_code=pair_code.upper(),
                            current_user=user_name,
                        )
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.error("Please fill in all fields")

        with tab2:
            st.header("Create New Pair")
            with st.form("create_pair_form"):
                pair_name = st.text_input("Pair Name (e.g., 'Alex & Sam')")
                pass1 = st.text_input("Create Passphrase", type="password")
                pass2 = st.text_input("Confirm Passphrase", type="password")
                user_name = st.text_input("Your Name")
                submitted = st.form_submit_button("Create Pair")

            if submitted:
                if pair_name and pass1 and user_name:
                    if pass1 == pass2:
                        code = create_pair(pair_name, pass1, user_name)
                        st.success(f"Pair created! Your pair code is: **{code}**")
                        st.info("Share this code and passphrase with your partner so they can join!")
                        st.session_state.update(
                            logged_in=True,
                            pair_code=code,
                            current_user=user_name,
                            pair_info={"pair_name": pair_name, "user1_name": user_name, "user2_name": None},
                        )
                        st.rerun()
                    else:
                        st.error("Passphrases don't match")
                else:
                    st.error("Please fill in all fields")

        with tab3:
            st.header("Login to Existing Pair")
            with st.form("login_form"):
                pair_code = st.text_input("Your Pair Code")
                passphrase = st.text_input("Passphrase", type="password")
                submitted = st.form_submit_button("Login")

            if submitted:
                if pair_code and passphrase:
                    ok, info, msg = authenticate_pair(pair_code.upper(), passphrase)
                    if ok:
                        st.success(msg)
                        user_options = [n for n in [info["user1_name"], info["user2_name"]] if n]
                        if len(user_options) == 1:
                            selected = user_options[0]
                            st.info(f"Logging in as {selected}")
                        else:
                            selected = st.selectbox("Select your identity:", user_options)

                        if selected:
                            st.session_state.update(
                                logged_in=True,
                                pair_code=pair_code.upper(),
                                current_user=selected,
                                pair_info=info,
                            )
                            st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.error("Please enter both pair code and passphrase")

        return  # Not logged in -> stop here

    # ======= Logged-in main UI =======
    pair_title = st.session_state["pair_info"]["pair_name"] if st.session_state.get("pair_info") else "PairBond"
    st.title(f"💕 {pair_title}")
    st.caption(f"Welcome back, {st.session_state['current_user']}!")

    # Sidebar
    with st.sidebar:
        st.header("Controls")

        # Location share
        st.subheader("📍 Share Location")
        with st.form("location_form"):
            st.write("Enter your current location:")
            latitude = st.number_input("Latitude", format="%.6f", help="e.g., 40.712800")
            longitude = st.number_input("Longitude", format="%.6f", help="e.g., -74.006000")
            battery = st.slider("Battery Level (%)", 0, 100, 50)
            duration = st.selectbox("Share for:", ["1 hour", "Until tomorrow", "Indefinitely"])
            submitted = st.form_submit_button("Update My Location")

        if submitted:
            update_location(
                st.session_state["pair_code"],
                st.session_state["current_user"],
                latitude,
                longitude,
                battery,
                duration,
            )
            st.success("Location updated!")
            st.rerun()

        st.write("---")
        st.subheader("📍 Quick Locations")
        if st.button("🏠 Home"):
            lat, lon = DEFAULT_HOME
            update_location(st.session_state["pair_code"], st.session_state["current_user"], lat, lon, 75, "Indefinitely")
            st.success("Set to Home!")
            st.rerun()
        if st.button("💼 Work"):
            lat, lon = DEFAULT_WORK
            update_location(st.session_state["pair_code"], st.session_state["current_user"], lat, lon, 60, "Until tomorrow")
            st.success("Set to Work!")
            st.rerun()

        st.write("---")
        st.subheader("💖 Send Love")
        partner_name = None
        if st.session_state.get("pair_info"):
            if st.session_state["current_user"] == st.session_state["pair_info"].get("user1_name"):
                partner_name = st.session_state["pair_info"].get("user2_name")
            else:
                partner_name = st.session_state["pair_info"].get("user1_name")

        if partner_name:
            pulse_msg = st.text_input("Message", value="Thinking of you 💖")
            if st.button("Send a Pulse 💖"):
                send_pulse(st.session_state["pair_code"], st.session_state["current_user"], partner_name, pulse_msg)
                st.success(f"Pulse sent to {partner_name}!")
                st.balloons()
        else:
            st.info("Waiting for your partner to join...")

        st.write("---")
        st.subheader("⚙️ Settings")
        if st.button("Logout"):
            for key in ["logged_in", "pair_code", "current_user", "pair_info"]:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

        st.info(f"Pair Code: **{st.session_state['pair_code']}**")

    # Main area
    col1, col2 = st.columns([3, 1])

    with col1:
        st.subheader("🗺️ Live Map")
        locs = get_locations(st.session_state["pair_code"])
        if locs:
            m = create_map(locs)
            st_folium(m, width=750, height=520)

            st.subheader("📊 Location Status")
            for (user_id, user_name, lat, lon, battery, timestamp, duration) in locs:
                with st.expander(f"{user_name}'s Location"):
                    a, b = st.columns(2)
                    with a:
                        st.write(f"**Coordinates:** {lat:.4f}, {lon:.4f}")
                        st.write(f"**Updated:** {timestamp}")
                    with b:
                        st.write(f"**Sharing:** {duration}")
                        if battery is not None:
                            st.write(f"**Battery:** {battery}%")
        else:
            st.info("No locations shared yet. Use the sidebar to share your location!")
            m = folium.Map(location=[DEFAULT_HOME[0], DEFAULT_HOME[1]], zoom_start=2)
            st_folium(m, width=750, height=520)

    with col2:
        st.subheader("💌 Notifications")
        # Fetch unread (also marks them as read)
        unread = get_unread_notifications(st.session_state["pair_code"], st.session_state["current_user"])
        if unread:
            for (_id, from_user, message, ts) in unread:
                st.toast(f"💖 {from_user}: {message}")
                st.balloons()

        # Show last 20 notifications
        conn = get_conn()
        cur = conn.cursor()
        recent = cur.execute(
            """
            SELECT from_user, to_user, message, timestamp
            FROM notifications
            WHERE pair_code = ?
            ORDER BY timestamp DESC
            LIMIT 20
            """,
            (st.session_state["pair_code"],),
        ).fetchall()
        conn.close()

        if recent:
            df = pd.DataFrame(recent, columns=["From", "To", "Message", "Time"])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.caption("No notifications yet.")

if __name__ == "__main__":
    main()
