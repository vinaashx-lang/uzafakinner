import streamlit as st
import sqlite3
import pandas as pd
import hashlib
import os
from datetime import datetime
from io import StringIO, BytesIO

# ============== CONFIG ==============
DB_PATH = "ccshop.db"
PRICE_PER_CARD = 10.0
ADMIN_USER = "admin"          # change after first run
ADMIN_PASS = "admin123"       # change after first run
# ====================================

st.set_page_config(
    page_title="CC Shop Pro | Savastan Style",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom dark professional CSS
st.markdown("""
<style>
    .stApp { background-color: #0e1117; color: #fafafa; }
    .main-header { font-size: 2.2rem; font-weight: 700; color: #00ff9d; margin-bottom: 0; }
    .sub-header { color: #888; font-size: 0.95rem; }
    .metric-card { background: #1a1f2e; padding: 15px; border-radius: 10px; border: 1px solid #2a2f3e; }
    .card-row { background: #161b22; padding: 8px 12px; border-radius: 6px; margin: 4px 0; border-left: 3px solid #00ff9d; }
    .sold { opacity: 0.5; text-decoration: line-through; }
    div[data-testid="stSidebar"] { background-color: #0d1117; }
    .stButton>button { background-color: #00ff9d; color: #000; font-weight: 600; border-radius: 6px; }
    .stButton>button:hover { background-color: #00cc7a; }
</style>
""", unsafe_allow_html=True)

# ============== DATABASE ==============
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        balance REAL DEFAULT 0.0,
        is_admin INTEGER DEFAULT 0,
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_line TEXT NOT NULL,
        bin TEXT,
        brand TEXT,
        card_type TEXT,
        country TEXT,
        bank TEXT,
        exp TEXT,
        cvv TEXT,
        name TEXT,
        address TEXT,
        price REAL DEFAULT 10.0,
        sold INTEGER DEFAULT 0,
        sold_to TEXT,
        sold_at TEXT,
        added_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        card_id INTEGER,
        amount REAL,
        type TEXT,
        created_at TEXT
    )''')
    # seed admin
    c.execute("SELECT * FROM users WHERE username=?", (ADMIN_USER,))
    if not c.fetchone():
        ph = hashlib.sha256(ADMIN_PASS.encode()).hexdigest()
        c.execute("INSERT INTO users (username, password_hash, balance, is_admin, created_at) VALUES (?,?,?,?,?)",
                  (ADMIN_USER, ph, 9999.0, 1, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def register_user(username, password):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO users (username, password_hash, balance, is_admin, created_at) VALUES (?,?,?,?,?)",
                     (username, hash_pw(password), 0.0, 0, datetime.now().isoformat()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def login_user(username, password):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=? AND password_hash=?",
                       (username, hash_pw(password))).fetchone()
    conn.close()
    return dict(row) if row else None

def get_user(username):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None

def update_balance(username, amount, reason="manual"):
    conn = get_conn()
    conn.execute("UPDATE users SET balance = balance + ? WHERE username=?", (amount, username))
    conn.execute("INSERT INTO transactions (username, amount, type, created_at) VALUES (?,?,?,?)",
                 (username, amount, reason, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def parse_card_line(line):
    """Parse common formats: num|mm|yy|cvv|name|addr or num|mm/yy|cvv etc."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = [p.strip() for p in line.replace("/", "|").split("|")]
    if len(parts) < 3:
        return None
    num = parts[0].replace(" ", "")
    if not num.isdigit() or len(num) < 13:
        return None
    bin6 = num[:6]
    exp = parts[1] if len(parts) > 1 else ""
    cvv = parts[2] if len(parts) > 2 else ""
    name = parts[3] if len(parts) > 3 else ""
    address = "|".join(parts[4:]) if len(parts) > 4 else ""
    # simple brand detect
    brand = "VISA" if num.startswith("4") else "MC" if num.startswith(("51","52","53","54","55","2221","2720")) else "AMEX" if num.startswith(("34","37")) else "OTHER"
    return {
        "full_line": line,
        "bin": bin6,
        "brand": brand,
        "card_type": "CREDIT",
        "country": "US",  # can enhance later
        "bank": "",
        "exp": exp,
        "cvv": cvv,
        "name": name,
        "address": address,
        "price": PRICE_PER_CARD
    }

def upload_cards(df_or_lines, source="upload"):
    conn = get_conn()
    added = 0
    for item in df_or_lines:
        if isinstance(item, dict):
            card = item
        else:
            card = parse_card_line(str(item))
        if not card:
            continue
        # avoid exact duplicates
        exists = conn.execute("SELECT id FROM cards WHERE full_line=?", (card["full_line"],)).fetchone()
        if exists:
            continue
        conn.execute('''INSERT INTO cards 
            (full_line, bin, brand, card_type, country, bank, exp, cvv, name, address, price, sold, added_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (card["full_line"], card["bin"], card["brand"], card["card_type"], card["country"],
             card["bank"], card["exp"], card["cvv"], card["name"], card["address"],
             card["price"], 0, datetime.now().isoformat()))
        added += 1
    conn.commit()
    conn.close()
    return added

def get_available_cards(filters=None):
    conn = get_conn()
    query = "SELECT * FROM cards WHERE sold=0"
    params = []
    if filters:
        if filters.get("bin"):
            query += " AND bin LIKE ?"
            params.append(f"{filters['bin']}%")
        if filters.get("brand") and filters["brand"] != "ALL":
            query += " AND brand=?"
            params.append(filters["brand"])
        if filters.get("country") and filters["country"] != "ALL":
            query += " AND country=?"
            params.append(filters["country"])
    query += " ORDER BY id DESC LIMIT 500"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def buy_card(username, card_id):
    conn = get_conn()
    user = conn.execute("SELECT balance FROM users WHERE username=?", (username,)).fetchone()
    card = conn.execute("SELECT * FROM cards WHERE id=? AND sold=0", (card_id,)).fetchone()
    if not user or not card:
        conn.close()
        return False, "Card not available or user error"
    if user["balance"] < PRICE_PER_CARD:
        conn.close()
        return False, "Insufficient balance"
    # deduct + mark sold
    conn.execute("UPDATE users SET balance = balance - ? WHERE username=?", (PRICE_PER_CARD, username))
    conn.execute("UPDATE cards SET sold=1, sold_to=?, sold_at=? WHERE id=?",
                 (username, datetime.now().isoformat(), card_id))
    conn.execute("INSERT INTO transactions (username, card_id, amount, type, created_at) VALUES (?,?,?,?,?)",
                 (username, card_id, -PRICE_PER_CARD, "purchase", datetime.now().isoformat()))
    conn.commit()
    full = dict(card)
    conn.close()
    return True, full

def get_stats():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    available = conn.execute("SELECT COUNT(*) FROM cards WHERE sold=0").fetchone()[0]
    sold = total - available
    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return total, available, sold, users

# ============== INIT ==============
init_db()

# ============== SESSION STATE ==============
if "user" not in st.session_state:
    st.session_state.user = None
if "page" not in st.session_state:
    st.session_state.page = "login"

# ============== AUTH UI ==============
def login_page():
    st.markdown('<p class="main-header">💳 CC SHOP PRO</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Professional Carding Marketplace • $10 / line • Instant Delivery</p>', unsafe_allow_html=True)
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Login")
        u = st.text_input("Username", key="login_u")
        p = st.text_input("Password", type="password", key="login_p")
        if st.button("Login", use_container_width=True):
            user = login_user(u, p)
            if user:
                st.session_state.user = user
                st.session_state.page = "shop"
                st.rerun()
            else:
                st.error("Invalid credentials")
    with col2:
        st.subheader("Register")
        nu = st.text_input("New Username", key="reg_u")
        np = st.text_input("New Password", type="password", key="reg_p")
        if st.button("Create Account", use_container_width=True):
            if register_user(nu, np):
                st.success("Account created. Login now.")
            else:
                st.error("Username taken")

# ============== SHOP UI ==============
def shop_page():
    user = get_user(st.session_state.user["username"])  # refresh balance
    st.session_state.user = user

    # Sidebar
    with st.sidebar:
        st.markdown(f"### 👤 {user['username']}")
        st.metric("Balance", f"${user['balance']:.2f}")
        if st.button("🔄 Refresh Balance"):
            st.rerun()
        st.divider()
        if user["is_admin"]:
            if st.button("🛠️ Admin Panel", use_container_width=True):
                st.session_state.page = "admin"
                st.rerun()
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state.user = None
            st.session_state.page = "login"
            st.rerun()

    st.markdown('<p class="main-header">💳 LIVE STOCK</p>', unsafe_allow_html=True)
    total, available, sold, _ = get_stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Cards", total)
    c2.metric("Available", available)
    c3.metric("Sold", sold)
    c4.metric("Price / Line", f"${PRICE_PER_CARD}")

    # Filters
    st.subheader("Filters")
    f1, f2, f3, f4 = st.columns(4)
    bin_f = f1.text_input("BIN (6 digits)", placeholder="411111")
    brand_f = f2.selectbox("Brand", ["ALL", "VISA", "MC", "AMEX", "OTHER"])
    country_f = f3.selectbox("Country", ["ALL", "US", "CA", "UK", "EU", "OTHER"])
    search = f4.button("Search / Refresh", use_container_width=True)

    filters = {"bin": bin_f, "brand": brand_f, "country": country_f}
    cards = get_available_cards(filters)

    st.write(f"**{len(cards)} cards shown** (max 500)")

    if not cards:
        st.info("No cards match filters. Upload stock in Admin.")
        return

    # Table header
    st.markdown("| ID | BIN | Brand | Exp | Country | Price | Action |")
    st.markdown("|----|-----|-------|-----|---------|-------|--------|")

    for card in cards:
        col1, col2, col3, col4, col5, col6, col7 = st.columns([1, 2, 1.5, 1.5, 1.5, 1, 2])
        with col1: st.write(card["id"])
        with col2: st.code(card["bin"], language=None)
        with col3: st.write(card["brand"])
        with col4: st.write(card["exp"])
        with col5: st.write(card["country"])
        with col6: st.write(f"${card['price']}")
        with col7:
            if st.button("BUY $10", key=f"buy_{card['id']}", use_container_width=True):
                ok, result = buy_card(user["username"], card["id"])
                if ok:
                    st.success("PURCHASED — Full line below (copy now)")
                    st.code(result["full_line"], language=None)
                    st.balloons()
                    st.rerun()
                else:
                    st.error(result)

# ============== ADMIN PANEL ==============
def admin_page():
    user = st.session_state.user
    if not user or not user["is_admin"]:
        st.error("Admin only")
        st.session_state.page = "shop"
        st.rerun()
        return

    with st.sidebar:
        st.markdown("### 🛠️ ADMIN")
        if st.button("← Back to Shop"):
            st.session_state.page = "shop"
            st.rerun()
        if st.button("Logout"):
            st.session_state.user = None
            st.session_state.page = "login"
            st.rerun()

    st.markdown('<p class="main-header">🛠️ ADMIN PANEL</p>', unsafe_allow_html=True)
    total, available, sold, users_count = get_stats()
    st.metric("Stock", f"{available} available / {total} total | {users_count} users")

    tab1, tab2, tab3 = st.tabs(["📤 Upload Cards", "💰 Manage Balances", "📊 Users & Stats"])

    with tab1:
        st.subheader("Bulk Upload (TXT / CSV / XLSX)")
        st.caption("Formats supported: `number|mm|yy|cvv|name|address` or `number|mm/yy|cvv` — one per line or columns")
        uploaded = st.file_uploader("Choose file", type=["txt", "csv", "xlsx", "xls"])
        if uploaded:
            try:
                if uploaded.name.endswith(".txt"):
                    content = uploaded.getvalue().decode("utf-8", errors="ignore")
                    lines = content.splitlines()
                    added = upload_cards(lines)
                elif uploaded.name.endswith(".csv"):
                    df = pd.read_csv(uploaded, header=None)
                    # try to join columns into lines or take first column
                    if df.shape[1] >= 4:
                        lines = df.apply(lambda r: "|".join(r.astype(str).tolist()), axis=1).tolist()
                    else:
                        lines = df.iloc[:, 0].astype(str).tolist()
                    added = upload_cards(lines)
                else:  # xlsx
                    df = pd.read_excel(uploaded, header=None)
                    if df.shape[1] >= 4:
                        lines = df.apply(lambda r: "|".join(r.astype(str).tolist()), axis=1).tolist()
                    else:
                        lines = df.iloc[:, 0].astype(str).tolist()
                    added = upload_cards(lines)
                st.success(f"Added {added} new unique cards to stock.")
            except Exception as e:
                st.error(f"Parse error: {e}")

        st.divider()
        st.subheader("Manual single line add")
        manual = st.text_area("Paste lines (one per row)")
        if st.button("Add Manual Lines"):
            lines = manual.splitlines()
            added = upload_cards(lines)
            st.success(f"Added {added} cards")

    with tab2:
        st.subheader("User Balance Management")
        conn = get_conn()
        users_df = pd.read_sql("SELECT username, balance, is_admin, created_at FROM users ORDER BY balance DESC", conn)
        conn.close()
        st.dataframe(users_df, use_container_width=True)

        target = st.text_input("Username to adjust")
        amount = st.number_input("Amount (+ add / - remove)", value=0.0, step=10.0)
        if st.button("Update Balance"):
            if target:
                update_balance(target, amount, "admin_adjust")
                st.success(f"Updated {target} by ${amount}")
                st.rerun()
            else:
                st.warning("Enter username")

    with tab3:
        st.subheader("Recent Transactions")
        conn = get_conn()
        tx = pd.read_sql("SELECT * FROM transactions ORDER BY id DESC LIMIT 100", conn)
        conn.close()
        st.dataframe(tx, use_container_width=True)

# ============== ROUTER ==============
if st.session_state.user is None:
    login_page()
else:
    if st.session_state.page == "admin":
        admin_page()
    else:
        shop_page()
