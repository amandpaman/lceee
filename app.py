import streamlit as st
import sqlite3
import hashlib
import secrets
import time
import datetime
import pandas as pd
import folium
from streamlit_folium import st_folium
import threading
from geopy.distance import geodesic
import json

# Page configuration
st.set_page_config(
    page_title="PairBond - Stay Connected",
    page_icon="💕",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Database setup
DATABASE_PATH = "pairbond.db"

def init_database():
    """Initialize SQLite database with required tables"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Pairs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pairs (
            pair_code TEXT PRIMARY KEY,
            pair_name TEXT NOT NULL,
            passphrase_hash TEXT NOT NULL,
            user1_name TEXT,
            user2_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Locations table
    cursor.execute('''
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
            FOREIGN KEY (pair_code) REFERENCES pairs (pair_code)
        )
    ''')
    
    # Notifications table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair_code TEXT NOT NULL,
            from_user TEXT NOT NULL,
            to_user TEXT NOT NULL,
            message TEXT DEFAULT 'Thinking of you 💖',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_read BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (pair_code) REFERENCES pairs (pair_code)
        )
    ''')
    
    conn.commit()
    conn.close()

def generate_pair_code():
    """Generate a unique pair code"""
    return f"PB-{secrets.randbelow(99999):05d}"

def hash_passphrase(passphrase):
    """Hash passphrase for secure storage"""
    return hashlib.sha256(passphrase.encode()).hexdigest()

def create_pair(pair_name, passphrase, user_name):
    """Create a new pair"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    pair_code = generate_pair_code()
    # Ensure unique pair code
    while cursor.execute("SELECT pair_code FROM pairs WHERE pair_code = ?", (pair_code,)).fetchone():
        pair_code = generate_pair_code()
    
    passphrase_hash = hash_passphrase(passphrase)
    
    cursor.execute('''
        INSERT INTO pairs (pair_code, pair_name, passphrase_hash, user1_name)
        VALUES (?, ?, ?, ?)
    ''', (pair_code, pair_name, passphrase_hash, user_name))
    
    conn.commit()
    conn.close()
    return pair_code

def join_pair(pair_code, passphrase, user_name):
    """Join an existing pair"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    result = cursor.execute('''
        SELECT pair_name, passphrase_hash, user1_name, user2_name 
        FROM pairs WHERE pair_code = ?
    ''', (pair_code,)).fetchone()
    
    if not result:
        conn.close()
        return False, "Pair code not found"
    
    pair_name, stored_hash, user1_name, user2_name = result
    
    if hash_passphrase(passphrase) != stored_hash:
        conn.close()
        return False, "Incorrect passphrase"
    
    if user2_name:
        conn.close()
        return False, "This pair is already complete"
    
    cursor.execute('''
        UPDATE pairs SET user2_name = ? WHERE pair_code = ?
    ''', (user_name, pair_code))
    
    conn.commit()
    conn.close()
    return True, f"Successfully joined {pair_name}!"

def authenticate_pair(pair_code, passphrase):
    """Authenticate existing pair members"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    result = cursor.execute('''
        SELECT pair_name, passphrase_hash, user1_name, user2_name 
        FROM pairs WHERE pair_code = ?
    ''', (pair_code,)).fetchone()
    
    if not result:
        conn.close()
        return False, None, "Pair code not found"
    
    pair_name, stored_hash, user1_name, user2_name = result
    
    if hash_passphrase(passphrase) != stored_hash:
        conn.close()
        return False, None, "Incorrect passphrase"
    
    conn.close()
    return True, {"pair_name": pair_name, "user1_name": user1_name, "user2_name": user2_name}, "Authentication successful"

def update_location(pair_code, user_name, latitude, longitude, battery_level=None, sharing_duration="Indefinitely"):
    """Update user location"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Calculate expiration time
    expires_at = None
    if sharing_duration != "Indefinitely":
        now = datetime.datetime.now()
        if sharing_duration == "1 hour":
            expires_at = now + datetime.timedelta(hours=1)
        elif sharing_duration == "Until tomorrow":
            expires_at = now.replace(hour=23, minute=59, second=59) + datetime.timedelta(days=1)
    
    # Determine user_id
    pair_info = cursor.execute('''
        SELECT user1_name, user2_name FROM pairs WHERE pair_code = ?
    ''', (pair_code,)).fetchone()
    
    user_id = 1 if pair_info[0] == user_name else 2
    
    # Delete old location for this user
    cursor.execute('''
        DELETE FROM locations WHERE pair_code = ? AND user_id = ?
    ''', (pair_code, user_id))
    
    # Insert new location
    cursor.execute('''
        INSERT INTO locations 
        (pair_code, user_id, user_name, latitude, longitude, battery_level, sharing_duration, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (pair_code, user_id, user_name, latitude, longitude, battery_level, sharing_duration, expires_at))
    
    conn.commit()
    conn.close()

def get_locations(pair_code):
    """Get current locations for the pair"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Clean expired locations first
    cursor.execute('''
        DELETE FROM locations 
        WHERE expires_at IS NOT NULL AND expires_at < CURRENT_TIMESTAMP
    ''')
    
    locations = cursor.execute('''
        SELECT user_id, user_name, latitude, longitude, battery_level, timestamp, sharing_duration
        FROM locations 
        WHERE pair_code = ?
        ORDER BY timestamp DESC
    ''', (pair_code,)).fetchall()
    
    conn.commit()
    conn.close()
    return locations

def send_pulse(pair_code, from_user, to_user, message="Thinking of you 💖"):
    """Send a pulse notification"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO notifications (pair_code, from_user, to_user, message)
        VALUES (?, ?, ?, ?)
    ''', (pair_code, from_user, to_user, message))
    
    conn.commit()
    conn.close()

def get_unread_notifications(pair_code, user_name):
    """Get unread notifications for a user"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    notifications = cursor.execute('''
        SELECT id, from_user, message, timestamp
        FROM notifications 
        WHERE pair_code = ? AND to_user = ? AND is_read = FALSE
        ORDER BY timestamp DESC
    ''', (pair_code, user_name)).fetchall()
    
    # Mark as read
    cursor.execute('''
        UPDATE notifications 
        SET is_read = TRUE 
        WHERE pair_code = ? AND to_user = ? AND is_read = FALSE
    ''', (pair_code, user_name))
    
    conn.commit()
    conn.close()
    return notifications

def create_map(locations):
    """Create a Folium map with locations"""
    if not locations:
        # Default map centered on a neutral location
        m = folium.Map(location=[40.7128, -74.0060], zoom_start=10)
        return m
    
    # Calculate center point
    if len(locations) == 1:
        center_lat = locations[0][2]
        center_lon = locations[0][3]
    else:
        lats = [loc[2] for loc in locations]
        lons = [loc[3] for loc in locations]
        center_lat = sum(lats) / len(lats)
        center_lon = sum(lons) / len(lons)
    
    m = folium.Map(location=[center_lat, center_lon], zoom_start=12)
    
    # Add markers for each user
    colors = ['red', 'blue']
    icons = ['heart', 'star']
    
    for i, location in enumerate(locations):
        user_id, user_name, lat, lon, battery, timestamp, duration = location
        
        # Calculate time since update
        try:
            last_update = datetime.datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
            time_diff = datetime.datetime.now() - last_update
            if time_diff.seconds < 60:
                time_str = f"{time_diff.seconds} seconds ago"
            elif time_diff.seconds < 3600:
                time_str = f"{time_diff.seconds // 60} minutes ago"
            else:
                time_str = f"{time_diff.seconds // 3600} hours ago"
        except:
            time_str = "Unknown"
        
        # Create popup text
        popup_text = f"""
        <b>{user_name}</b><br>
        Updated: {time_str}<br>
        Duration: {duration}
        """
        if battery:
            popup_text += f"<br>Battery: {battery}%"
        
        folium.Marker(
            location=[lat, lon],
            popup=folium.Popup(popup_text, max_width=200),
            tooltip=user_name,
            icon=folium.Icon(color=colors[i % 2], icon=icons[i % 2])
        ).add_to(m)
    
    # If two locations, add a line between them and show distance
    if len(locations) == 2:
        coords = [(locations[0][2], locations[0][3]), (locations[1][2], locations[1][3])]
        folium.PolyLine(coords, color='purple', weight=2, opacity=0.7).add_to(m)
        
        # Calculate distance
        distance = geodesic(coords[0], coords[1]).kilometers
        midpoint = ((coords[0][0] + coords[1][0])/2, (coords[0][1] + coords[1][1])/2)
        
        folium.Marker(
            location=midpoint,
            popup=f"Distance: {distance:.2f} km",
            icon=folium.Icon(color='purple', icon='info-sign')
        ).add_to(m)
    
    return m

# Initialize database
init_database()

# Main app logic
def main():
    # Initialize session state
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'pair_code' not in st.session_state:
        st.session_state.pair_code = None
    if 'current_user' not in st.session_state:
        st.session_state.current_user = None
    if 'pair_info' not in st.session_state:
        st.session_state.pair_info = None
    if 'last_notification_check' not in st.session_state:
        st.session_state.last_notification_check = 0
    
    # Authentication flow
    if not st.session_state.logged_in:
        st.title("💕 PairBond")
        st.subheader("Stay connected with your special someone")
        
        tab1, tab2, tab3 = st.tabs(["Join Existing Pair", "Create New Pair", "Login to Existing Pair"])
        
        with tab1:
            st.header("Join a Pair")
            with st.form("join_pair_form"):
                pair_code = st.text_input("Pair Code (e.g., PB-12345)")
                passphrase = st.text_input("Passphrase", type="password")
                user_name = st.text_input("Your Name")
                
                if st.form_submit_button("Join Pair"):
                    if pair_code and passphrase and user_name:
                        success, message = join_pair(pair_code.upper(), passphrase, user_name)
                        if success:
                            st.success(message)
                            st.session_state.logged_in = True
                            st.session_state.pair_code = pair_code.upper()
                            st.session_state.current_user = user_name
                            st.rerun()
                        else:
                            st.error(message)
                    else:
                        st.error("Please fill in all fields")
        
        with tab2:
            st.header("Create New Pair")
            with st.form("create_pair_form"):
                pair_name = st.text_input("Pair Name (e.g., 'Alex & Sam')")
                passphrase = st.text_input("Create Passphrase", type="password")
                confirm_passphrase = st.text_input("Confirm Passphrase", type="password")
                user_name = st.text_input("Your Name")
                
                if st.form_submit_button("Create Pair"):
                    if pair_name and passphrase and user_name:
                        if passphrase == confirm_passphrase:
                            pair_code = create_pair(pair_name, passphrase, user_name)
                            st.success(f"Pair created! Your pair code is: **{pair_code}**")
                            st.info("Share this code and passphrase with your partner so they can join!")
                            st.session_state.logged_in = True
                            st.session_state.pair_code = pair_code
                            st.session_state.current_user = user_name
                            st.session_state.pair_info = {"pair_name": pair_name, "user1_name": user_name, "user2_name": None}
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
                
                if st.form_submit_button("Login"):
                    if pair_code and passphrase:
                        success, pair_info, message = authenticate_pair(pair_code.upper(), passphrase)
                        if success:
                            st.success(message)
                            
                            # Let user choose their identity
                            user_options = [name for name in [pair_info['user1_name'], pair_info['user2_name']] if name]
                            if len(user_options) == 1:
                                selected_user = user_options[0]
                                st.info(f"Logging in as {selected_user}")
                            else:
                                selected_user = st.selectbox("Select your identity:", user_options)
                            
                            if selected_user:
                                st.session_state.logged_in = True
                                st.session_state.pair_code = pair_code.upper()
                                st.session_state.current_user = selected_user
                                st.session_state.pair_info = pair_info
                                st.rerun()
                        else:
                            st.error(message)
                    else:
                        st.error("Please enter both pair code and passphrase")
    
    else:
        # Main app interface
        st.title(f"💕 {st.session_state.pair_info['pair_name'] if st.session_state.pair_info else 'PairBond'}")
        st.caption(f"Welcome back, {st.session_state.current_user}!")
        
        # Sidebar controls
        with st.sidebar:
            st.header("Controls")
            
            # Location update section
            st.subheader("📍 Share Location")
            
            # Manual location input (since we can't access geolocation directly)
            with st.form("location_form"):
                st.write("Enter your current location:")
                latitude = st.number_input("Latitude", format="%.6f", help="e.g., 40.712800")
                longitude = st.number_input("Longitude", format="%.6f", help="e.g., -74.006000")
                battery_level = st.slider("Battery Level (%)", 0, 100, 50)
                sharing_duration = st.selectbox("Share for:", ["1 hour", "Until tomorrow", "Indefinitely"])
                
                if st.form_submit_button("Update My Location"):
                    if latitude and longitude:
                        update_location(
                            st.session_state.pair_code, 
                            st.session_state.current_user,
                            latitude, longitude, battery_level, sharing_duration
                        )
                        st.success("Location updated!")
                        st.rerun()
                    else:
                        st.error("Please enter valid coordinates")
            
            st.write("---")
            
            # Quick location presets
            st.subheader("📍 Quick Locations")
            if st.button("🏠 Home"):
                # You can set default home coordinates
                update_location(st.session_state.pair_code, st.session_state.current_user, 40.712800, -74.006000, 75, "Indefinitely")
                st.success("Set to Home!")
                st.rerun()
            
            if st.button("💼 Work"):
                # You can set default work coordinates
                update_location(st.session_state.pair_code, st.session_state.current_user, 40.758900, -73.985100, 60, "Until tomorrow")
                st.success("Set to Work!")
                st.rerun()
            
            st.write("---")
            
            # Pulse section
            st.subheader("💖 Send Love")
            partner_name = None
            if st.session_state.pair_info:
                if st.session_state.current_user == st.session_state.pair_info['user1_name']:
                    partner_name = st.session_state.pair_info['user2_name']
                else:
                    partner_name = st.session_state.pair_info['user1_name']
            
            if partner_name:
                if st.button("Send a Pulse 💖"):
                    send_pulse(st.session_state.pair_code, st.session_state.current_user, partner_name)
                    st.success(f"Pulse sent to {partner_name}!")
                    time.sleep(1)  # Brief delay for effect
                    st.balloons()
            else:
                st.info("Waiting for your partner to join...")
            
            st.write("---")
            
            # Settings
            st.subheader("⚙️ Settings")
            if st.button("Logout"):
                for key in ['logged_in', 'pair_code', 'current_user', 'pair_info']:
                    if key in st.session_state:
                        del st.session_state[key]
                st.rerun()
            
            st.info(f"Pair Code: **{st.session_state.pair_code}**")
        
        # Main map area
        col1, col2 = st.columns([3, 1])
        
        with col1:
            st.subheader("🗺️ Live Map")
            
            # Get current locations
            locations = get_locations(st.session_state.pair_code)
            
            if locations:
                # Create and display map
                map_obj = create_map(locations)
                st_folium(map_obj, width=700, height=500)
                
                # Show location info
                st.subheader("📊 Location Status")
                for location in locations:
                    user_id, user_name, lat, lon, battery, timestamp, duration = location
                    
                    with st.expander(f"{user_name}'s Location"):
                        col_a, col_b = st.columns(2)
                        with col_a:
                            st.write(f"**Coordinates:** {lat:.4f}, {lon:.4f}")
                            st.write(f"**Updated:** {timestamp}")
                        with col_b:
                            st.write(f"**Sharing:** {duration}")
                            if battery:
                                st.write(f"**Battery:** {battery}%")
            else:
                st.info("No locations shared yet. Use the sidebar to share your location!")
                # Show default map
                default_map = folium.Map(location=[40.7128, -74.0060], zoom_start=2)
                st_folium(default_map, width=700, height=500)
        
        with col2:
            st.subheader("💌 Notifications")
            
            # Check for new notifications
            current_time = time.time()
            if current_time - st.session_state.last_notification_check > 30:  # Check every 30 seconds
                notifications = get_unread_notifications(st.session_state.pair_code, st.session_state.current_user)
                if notifications:
                    for notif in notifications:
                        notif_id, from_user, message, timestamp = notif
                        st.toast(f"💖 {from_user}: {message}", icon='💕')
                        st.balloons()
                
                st.session_state.last_notification_check = current_time
            
            # Auto-refresh every 60 seconds
            time.sleep(1)
            st.rerun()

if __name__ == "__main__":
    main()
