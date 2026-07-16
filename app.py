import streamlit as st
import sqlite3
import pandas as pd
import hashlib
import io
from datetime import datetime
from contextlib import contextmanager

# ============== CONFIG ==============
DB_PATH = "shop.db"
CARD_PRICE = 10.0
ADMIN_USER = "admin"
ADMIN_PASS = "adminpass"  # change after first run

st.set_page_config(
    page_title="CC Shop Pro | Premium Dumps",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for pro look
st.markdown("""
<style>
    .main-header {font-size: 2.2rem; font-weight: 700; color: #00d4ff; margin-bottom: 0.2rem;}
    .sub-header {color: #8899aa; font-size: 0.95rem; margin-bottom: 1.5rem;}
    .balance-box {background: linear-gradient(135deg, #1a1f2e, #0e1117); border: 1px solid #00d4ff33; border-radius: 10px; padding: 1rem; text-align: center;}
    .card-row {background: #1a1f2e; border-radius: 8px; padding: 0.8rem; margin: 0.4rem 0; border-left: 3px solid #00d4ff;}
    .stButton>button {width: 100%; border-radius: 6px;}
    .success-box {background: #0d3320; border: 1px solid #00ff8833; padding: 1rem; border-radius: 8px;}
    div[data-testid="stDataFrame"] {border-radius: 8px;}
</style>
""", unsafe_allow_html=True)

# ============== DB HELPERS ==============
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                balance REAL DEFAULT 0.0,
                is_admin INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_data TEXT NOT NULL,
                bin TEXT,
                last4 TEXT,
                exp TEXT,
                cvv TEXT,
                name TEXT,
                country TEXT,
                price REAL DEFAULT 10.0,
                sold INTEGER DEFAULT 0,
                sold_to INTEGER,
                sold_at TEXT,
                uploaded_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                card_id INTEGER,
                price REAL,
                purchased_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(card_id) REFERENCES cards(id)
            )
        """)
        # seed admin if missing
        c.execute("SELECT id FROM users WHERE username = ?", (ADMIN_USER,))
        if not c.fetchone():
            ph = hashlib.sha256(ADMIN_PASS.encode()).hexdigest()
            c.execute(
                "INSERT INTO users (username, password_hash, balance, is_admin, created_at) VALUES (?, ?, 0, 1, ?)",
                (ADMIN_USER, ph, datetime.utcnow().isoformat())
            )

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def verify_user(username: str, password: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND password_hash = ?",
            (username, hash_pw(password))
        ).fetchone()
        return dict(row) if row else None

def register_user(username: str, password: str) -> tuple[bool, str]:
    if len(username) < 3 or len(password) < 4:
        return False, "Username >=3 chars, password >=4"
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, balance, is_admin, created_at) VALUES (?, ?, 0, 0, ?)",
                (username, hash_pw(password), datetime.utcnow().isoformat())
            )
        return True, "Registered successfully. Login now."
    except sqlite3.IntegrityError:
        return False, "Username already exists"

def get_user(user_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

def update_balance(user_id: int, amount: float, mode="add"):
    with get_db() as conn:
        if mode == "add":
            conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id))
        else:
            conn.execute("UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?", (amount, user_id, amount))

def parse_card_line(line: str) -> dict | None:
    line = line.strip()
    if not line or len(line) < 10:
        return None
    parts = [p.strip() for p in line.replace(",", "|").split("|")]
    if len(parts) < 3:
        # try space or just number
        parts = line.split()
    num = parts[0].replace(" ", "").replace("-", "")
    if not num.isdigit() or len(num) < 13 or len(num) > 19:
        return None
    bin6 = num[:6]
    last4 = num[-4:]
    exp = parts[1] if len(parts) > 1 else ""
    cvv = parts[2] if len(parts) > 2 else ""
    name = parts[3] if len(parts) > 3 else ""
    country = parts[-1] if len(parts) > 5 else ""
    return {
        "full_data": line,
        "bin": bin6,
        "last4": last4,
        "exp": exp,
        "cvv": cvv,
        "name": name,
        "country": country,
        "price": CARD_PRICE
    }

def upload_cards(file_bytes: bytes, filename: str) -> int:
    count = 0
    lines = []
    if filename.lower().endswith(".txt"):
        text = file_bytes.decode("utf-8", errors="ignore")
        lines = text.splitlines()
    elif filename.lower().endswith((".csv", ".xlsx", ".xls")):
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(file_bytes), header=None, dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str)
        # join all columns with |
        for _, row in df.iterrows():
            line = "|".join([str(x) for x in row.values if pd.notna(x) and str(x).strip()])
            lines.append(line)
    else:
        return 0

    with get_db() as conn:
        for line in lines:
            parsed = parse_card_line(line)
            if parsed:
                conn.execute("""
                    INSERT INTO cards (full_data, bin, last4, exp, cvv, name, country, price, sold, uploaded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """, (
                    parsed["full_data"], parsed["bin"], parsed["last4"],
                    parsed["exp"], parsed["cvv"], parsed["name"], parsed["country"],
                    parsed["price"], datetime.utcnow().isoformat()
                ))
                count += 1
    return count

def get_available_cards(limit=100, bin_filter=None):
    with get_db() as conn:
        if bin_filter:
            rows = conn.execute(
                "SELECT id, bin, last4, exp, country, price FROM cards WHERE sold=0 AND bin LIKE ? ORDER BY id DESC LIMIT ?",
                (f"{bin_filter}%", limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, bin, last4, exp, country, price FROM cards WHERE sold=0 ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

def buy_card(user_id: int, card_id: int) -> tuple[bool, str, str | None]:
    with get_db() as conn:
        user = conn.execute("SELECT balance FROM users WHERE id=?", (user_id,)).fetchone()
        card = conn.execute("SELECT * FROM cards WHERE id=? AND sold=0", (card_id,)).fetchone()
        if not card:
            return False, "Card already sold or not found", None
        if user["balance"] < CARD_PRICE:
            return False, f"Insufficient balance. Need ${CARD_PRICE}", None
        # deduct + mark sold
        conn.execute("UPDATE users SET balance = balance - ? WHERE id=?", (CARD_PRICE, user_id))
        conn.execute(
            "UPDATE cards SET sold=1, sold_to=?, sold_at=? WHERE id=?",
            (user_id, datetime.utcnow().isoformat(), card_id)
        )
        conn.execute(
            "INSERT INTO purchases (user_id, card_id, price, purchased_at) VALUES (?, ?, ?, ?)",
            (user_id, card_id, CARD_PRICE, datetime.utcnow().isoformat())
        )
        return True, "Purchase successful", card["full_data"]

def get_user_purchases(user_id: int):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.full_data, p.purchased_at, p.price
            FROM purchases p JOIN cards c ON p.card_id = c.id
            WHERE p.user_id = ? ORDER BY p.purchased_at DESC
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]

def get_all_users():
    with get_db() as conn:
        rows = conn.execute("SELECT id, username, balance, is_admin, created_at FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]

def set_user_balance(user_id: int, new_balance: float):
    with get_db() as conn:
        conn.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user_id))

def count_stock():
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM cards").fetchone()["c"]
        avail = conn.execute("SELECT COUNT(*) as c FROM cards WHERE sold=0").fetchone()["c"]
        return total, avail

# ============== INIT ==============
init_db()

# ============== SESSION STATE ==============
if "user" not in st.session_state:
    st.session_state.user = None
if "page" not in st.session_state:
    st.session_state.page = "shop"

# ============== AUTH UI ==============
def login_page():
    st.markdown('<div class="main-header">💳 CC Shop Pro</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Premium dumps • Instant delivery • $10 / line</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Login")
        u = st.text_input("Username", key="login_u")
        p = st.text_input("Password", type="password", key="login_p")
        if st.button("Login", type="primary"):
            user = verify_user(u, p)
            if user:
                st.session_state.user = user
                st.rerun()
            else:
                st.error("Invalid credentials")
    with col2:
        st.subheader("Register")
        nu = st.text_input("New Username", key="reg_u")
        np = st.text_input("New Password", type="password", key="reg_p")
        if st.button("Create Account"):
            ok, msg = register_user(nu, np)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

# ============== MAIN APP ==============
if st.session_state.user is None:
    login_page()
    st.stop()

user = st.session_state.user
# refresh balance
user = get_user(user["id"])
st.session_state.user = user

# Sidebar
with st.sidebar:
    st.markdown(f"**Logged in as:** `{user['username']}`")
    st.markdown(f'<div class="balance-box"><b>Balance</b><br><span style="font-size:1.6rem;color:#00d4ff">${user["balance"]:.2f}</span></div>', unsafe_allow_html=True)
    st.divider()
    page = st.radio("Navigation", ["Shop", "My Purchases", "Admin Panel"] if user["is_admin"] else ["Shop", "My Purchases"], index=0)
    if st.button("Logout"):
        st.session_state.user = None
        st.rerun()

# ============== SHOP PAGE ==============
if page == "Shop":
    st.markdown('<div class="main-header">Available Cards</div>', unsafe_allow_html=True)
    total, avail = count_stock()
    st.caption(f"Stock: {avail} available / {total} total • Price: ${CARD_PRICE:.0f} per card")

    colf1, colf2 = st.columns([2, 1])
    with colf1:
        bin_filter = st.text_input("Filter by BIN (optional)", placeholder="e.g. 414720", max_chars=6)
    with colf2:
        limit = st.number_input("Show", min_value=10, max_value=500, value=50, step=10)

    cards = get_available_cards(limit=limit, bin_filter=bin_filter.strip() if bin_filter else None)

    if not cards:
        st.info("No cards available matching filter. Admin can upload more.")
    else:
        # header
        h1, h2, h3, h4, h5, h6 = st.columns([1.2, 1, 1, 1.5, 1, 1.2])
        h1.markdown("**BIN**")
        h2.markdown("**Last4**")
        h3.markdown("**EXP**")
        h4.markdown("**Country**")
        h5.markdown("**Price**")
        h6.markdown("**Action**")

        for card in cards:
            c1, c2, c3, c4, c5, c6 = st.columns([1.2, 1, 1, 1.5, 1, 1.2])
            c1.write(card["bin"] or "—")
            c2.write(card["last4"] or "—")
            c3.write(card["exp"] or "—")
            c4.write(card["country"] or "—")
            c5.write(f"${card['price']:.0f}")
            if c6.button("Buy", key=f"buy_{card['id']}", type="primary"):
                ok, msg, full = buy_card(user["id"], card["id"])
                if ok:
                    st.success(msg)
                    st.markdown(f'<div class="success-box"><b>Your card (copy now):</b><br><code>{full}</code></div>', unsafe_allow_html=True)
                    st.balloons()
                    st.rerun()
                else:
                    st.error(msg)

# ============== MY PURCHASES ==============
elif page == "My Purchases":
    st.markdown('<div class="main-header">My Purchases</div>', unsafe_allow_html=True)
    purchases = get_user_purchases(user["id"])
    if not purchases:
        st.info("No purchases yet. Go to Shop and buy cards.")
    else:
        for p in purchases:
            st.markdown(f"""
            <div class="card-row">
                <code>{p['full_data']}</code><br>
                <small style="color:#8899aa">Bought: {p['purchased_at'][:19]} • ${p['price']:.0f}</small>
            </div>
            """, unsafe_allow_html=True)

# ============== ADMIN PANEL ==============
elif page == "Admin Panel" and user["is_admin"]:
    st.markdown('<div class="main-header">Admin Panel</div>', unsafe_allow_html=True)
    total, avail = count_stock()
    st.metric("Available Stock", avail, f"Total ever: {total}")

    tab1, tab2, tab3 = st.tabs(["Upload Cards", "Manage Users / Balance", "Stock Overview"])

    with tab1:
        st.subheader("Bulk Upload (TXT / CSV / XLSX)")
        st.caption("Format examples:\n`4111111111111111|12|25|123|John Doe|123 St|City|NY|10001|US`\nor any | or comma separated. First field = PAN.")
        uploaded = st.file_uploader("Choose file", type=["txt", "csv", "xlsx", "xls"])
        if uploaded and st.button("Process Upload", type="primary"):
            with st.spinner("Parsing & inserting..."):
                n = upload_cards(uploaded.getvalue(), uploaded.name)
            st.success(f"Successfully added {n} valid cards.")
            st.rerun()

        st.divider()
        st.subheader("Manual single add")
        manual = st.text_area("Paste one or more lines (one per line)")
        if st.button("Add Manual Lines") and manual:
            count = 0
            with get_db() as conn:
                for line in manual.splitlines():
                    parsed = parse_card_line(line)
                    if parsed:
                        conn.execute("""
                            INSERT INTO cards (full_data, bin, last4, exp, cvv, name, country, price, sold, uploaded_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                        """, (
                            parsed["full_data"], parsed["bin"], parsed["last4"],
                            parsed["exp"], parsed["cvv"], parsed["name"], parsed["country"],
                            CARD_PRICE, datetime.utcnow().isoformat()
                        ))
                        count += 1
            st.success(f"Added {count} cards")
            st.rerun()

    with tab2:
        st.subheader("User Balances")
        users = get_all_users()
        dfu = pd.DataFrame(users)
        st.dataframe(dfu, use_container_width=True, hide_index=True)

        st.subheader("Set / Adjust Balance")
        uid = st.number_input("User ID", min_value=1, step=1)
        new_bal = st.number_input("New Balance ($)", min_value=0.0, step=10.0, value=0.0)
        if st.button("Update Balance", type="primary"):
            set_user_balance(int(uid), float(new_bal))
            st.success(f"User {uid} balance set to ${new_bal:.2f}")
            st.rerun()

        st.subheader("Quick Add Funds")
        uid2 = st.number_input("User ID (add)", min_value=1, step=1, key="adduid")
        add_amt = st.number_input("Amount to add", min_value=1.0, step=10.0, value=50.0)
        if st.button("Add Funds"):
            update_balance(int(uid2), float(add_amt), mode="add")
            st.success(f"Added ${add_amt:.2f} to user {uid2}")
            st.rerun()

    with tab3:
        st.subheader("Recent / Available Cards (preview)")
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, bin, last4, exp, country, sold, uploaded_at FROM cards ORDER BY id DESC LIMIT 100"
            ).fetchall()
        if rows:
            st.dataframe(pd.DataFrame([dict(r) for r in rows]), use_container_width=True, hide_index=True)
        else:
            st.info("No cards yet.")

        if st.button("Clear ALL sold cards (cleanup)", type="secondary"):
            with get_db() as conn:
                conn.execute("DELETE FROM cards WHERE sold=1")
            st.success("Sold cards removed")
            st.rerun()

# Footer
st.divider()
st.caption("CC Shop Pro • $10 fixed • Admin: upload TXT/CSV/XLSX • Balances managed in real time • Built with Streamlit + SQLite")
