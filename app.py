import streamlit as st
import pandas as pd
from fpdf import FPDF
import os
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from streamlit_option_menu import option_menu
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import speech_recognition as sr
from streamlit_mic_recorder import mic_recorder
import io

# --- 1. CONFIGURATION ---
COMPANY_NAME = "G P Group"
LOGO_PATH = "logo.png"
REASON_CATEGORIES = ["Safety Violation", "Quality Issue", "Material Wastage", "Timeline Delay", "Site Misconduct", "Other"]

# --- 2. GOOGLE SERVICES ---
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

def get_creds():
    return Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=SCOPES)

def get_sheet_client():
    return gspread.authorize(get_creds())

def get_drive_service():
    return build('drive', 'v3', credentials=get_creds())

def upload_to_drive(file_path, filename, mime_type):
    service = get_drive_service()
    folder_id = st.secrets["drive_settings"]["folder_id"]
    file_metadata = {'name': filename, 'parents': [folder_id]}
    media = MediaFileUpload(file_path, mimetype=mime_type)
    file = service.files().create(
        body=file_metadata, media_body=media, fields='id, webViewLink', supportsAllDrives=True
    ).execute()
    return file.get('webViewLink')

# --- 3. VOICE TO TEXT ENGINE ---
def transcribe_audio(audio_bytes):
    r = sr.Recognizer()
    audio_file = io.BytesIO(audio_bytes)
    with sr.AudioFile(audio_file) as source:
        audio = r.record(source)
    try:
        return r.recognize_google(audio)
    except sr.UnknownValueError:
        return "Could not understand audio"
    except sr.RequestError:
        return "Speech service unavailable"

# --- 4. EMAIL ENGINE ---
def send_email_with_pdf(to_emails, subject, body, attachment_path):
    if not to_emails or "email_settings" not in st.secrets: return False
    
    sender_email = st.secrets["email_settings"]["sender_email"]
    password = st.secrets["email_settings"]["app_password"]

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = ", ".join(to_emails)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as attachment:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename= {os.path.basename(attachment_path)}")
        msg.attach(part)

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, password)
        server.sendmail(sender_email, to_emails, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        st.error(f"Email Error: {e}")
        return False

# --- 5. DATABASE OPERATIONS ---
def init_db():
    try:
        client = get_sheet_client()
        sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
        
        tables = {
            "DebitNotes": ["ID", "Contractor Name", "Date", "Amount", "Category", "Reason", "Site Location", "Image Links", "PDF Link", "SubmittedBy"],
            "Contractors": ["ID", "Name", "Details", "Email"],
            "Users": ["Username", "Password", "Role"],
            "Notifications": ["ID", "Message", "Timestamp", "Type"]
        }
        
        for name, headers in tables.items():
            try: 
                ws = sh.worksheet(name)
                # Auto-Add Missing Columns
                curr = ws.row_values(1)
                if len(curr) < len(headers):
                    ws.resize(cols=len(headers))
                    for i, h in enumerate(headers):
                        if i >= len(curr): ws.update_cell(1, i+1, h)
            except: 
                ws = sh.add_worksheet(name, 100, len(headers))
                ws.append_row(headers)
    except Exception as e: st.error(f"Database Error: {e}")

def db_get(table):
    return pd.DataFrame(get_sheet_client().open_by_url(st.secrets["drive_settings"]["sheet_url"]).worksheet(table).get_all_records())

def db_insert(table, row_data):
    ws = get_sheet_client().open_by_url(st.secrets["drive_settings"]["sheet_url"]).worksheet(table)
    ws.append_row(row_data)

def db_delete(table, col_name, value):
    ws = get_sheet_client().open_by_url(st.secrets["drive_settings"]["sheet_url"]).worksheet(table)
    try:
        cell = ws.find(str(value))
        ws.delete_rows(cell.row)
        return True
    except: return False

def db_update_cell(table, search_val, col_idx, new_val):
    ws = get_sheet_client().open_by_url(st.secrets["drive_settings"]["sheet_url"]).worksheet(table)
    try:
        cell = ws.find(str(search_val))
        ws.update_cell(cell.row, col_idx, new_val)
        return True
    except: return False

# --- 6. PDF ENGINE ---
class PDF(FPDF):
    def header(self):
        if os.path.exists(LOGO_PATH): self.image(LOGO_PATH, 10, 8, 30)
        self.set_font('Helvetica', 'B', 20)
        self.set_text_color(50, 50, 50)
        self.cell(0, 15, COMPANY_NAME, 0, 1, 'C')
        self.ln(10)
        
    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(150)
        self.cell(0, 10, f'Generated by {st.session_state.get("username", "System")}', 0, 0, 'C')

def create_pdf(type, data):
    if not os.path.exists("temp"): os.makedirs("temp")
    pdf = PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_fill_color(240, 240, 240)
    title = "DEBIT NOTE" if type == "receipt" else "STATEMENT OF ACCOUNT"
    pdf.cell(0, 12, title, 0, 1, 'C', fill=True)
    pdf.ln(10)
    
    if type == "receipt":
        pdf.set_font("Helvetica", "", 12)
        fields = [
            ("Contractor", data['contractor']),
            ("Date", str(data['date'])),
            ("Site Location", data['site']),
            ("Category", data['category']),
            ("Amount", f"INR {data['amount']}")
        ]
        for label, value in fields:
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(50, 8, label, "B")
            pdf.set_font("Helvetica", "", 12)
            pdf.cell(140, 8, str(value), "B", 1)
        
        pdf.ln(8)
        pdf.set_font("Helvetica", "B", 12); pdf.cell(0, 10, "Description / Reason:", 0, 1)
        pdf.set_font("Helvetica", "", 11); pdf.multi_cell(0, 6, data['reason'])
        pdf.ln(5)
        
        if data.get('local_img_paths'):
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 10, "Proof of Deduction:", 0, 1)
            for p in data['local_img_paths']:
                if os.path.exists(p):
                    if pdf.get_y() > 200: pdf.add_page()
                    try: pdf.image(p, x=15, w=100); pdf.ln(5)
                    except: pass
        filename = f"DebitNote_{int(datetime.now().timestamp())}.pdf"

    else: 
        pdf.set_font("Helvetica", "", 12)
        pdf.cell(0, 8, f"Contractor: {data['contractor']}", 0, 1)
        pdf.cell(0, 8, f"Period: {data['start']} to {data['end']}", 0, 1)
        pdf.ln(5)
        
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_fill_color(50, 50, 50); pdf.set_text_color(255)
        headers = ["Date", "Category", "Reason", "Amount"]
        for h, w in zip(headers, [30, 40, 90, 30]):
            pdf.cell(w, 10, h, 1, 0, 'C', True)
        pdf.ln()
        
        pdf.set_text_color(0); pdf.set_font("Helvetica", "", 9)
        total = 0
        for _, row in data['df'].iterrows():
            pdf.cell(30, 10, str(row['Date']), 1)
            pdf.cell(40, 10, str(row.get('Category', '-'))[:20], 1)
            pdf.cell(90, 10, str(row['Reason'])[:50], 1)
            pdf.cell(30, 10, str(row['Amount']), 1, 1)
            total += float(row['Amount'])
            
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(160, 10, "Total Deductions:", 0, 0, 'R')
        pdf.cell(30, 10, f"INR {total}", 0, 1, 'L')
        filename = f"Statement_{data['contractor']}.pdf"

    path = f"temp/{filename}"
    pdf.output(path)
    return path

# --- 7. NOTIFICATION LOGIC ---
def notify_users(message, type="info"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nid = int(datetime.now().timestamp())
    db_insert("Notifications", [nid, message, ts, type])

def check_notifications():
    try:
        notifs = db_get("Notifications")
        if notifs.empty: return []
        notifs = notifs.sort_values(by="ID", ascending=False)
        
        latest_id = int(notifs.iloc[0]['ID'])
        if 'last_seen_notif' not in st.session_state:
            st.session_state['last_seen_notif'] = latest_id
        elif latest_id > st.session_state['last_seen_notif']:
            msg = notifs.iloc[0]['Message']
            icon = "🚨" if notifs.iloc[0]['Type'] == "alert" else "📢"
            st.toast(f"{icon} {msg}", icon=icon)
            st.session_state['last_seen_notif'] = latest_id
            
        return notifs.head(5).to_dict('records')
    except: return []

# --- 8. THEME & UI ---
THEMES = {
    "Corporate Blue": {"bg": "#f4f6f9", "card": "rgba(255, 255, 255, 0.9)", "text": "#1e293b", "primary": "#0F52BA", "accent": "#3b82f6"},
    "Dark Mode": {"bg": "#0f172a", "card": "rgba(30, 41, 59, 0.8)", "text": "#f8fafc", "primary": "#3b82f6", "accent": "#60a5fa"},
    "Industrial": {"bg": "#292524", "card": "rgba(68, 64, 60, 0.9)", "text": "#fafaf9", "primary": "#f59e0b", "accent": "#fbbf24"},
    "Forest": {"bg": "#ecfdf5", "card": "rgba(255, 255, 255, 0.8)", "text": "#064e3b", "primary": "#059669", "accent": "#34d399"},
    "High Contrast": {"bg": "#ffffff", "card": "#ffffff", "text": "#000000", "primary": "#000000", "accent": "#000000"}
}

def inject_css(theme_name):
    t = THEMES[theme_name]
    st.markdown(f"""
    <style>
        .stApp {{ background-color: {t['bg']}; color: {t['text']}; }}
        .glass-card {{
            background: {t['card']}; backdrop-filter: blur(10px);
            border-radius: 16px; padding: 24px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.1); margin-bottom: 24px;
        }}
        .stButton>button {{
            background: linear-gradient(135deg, {t['primary']} 0%, {t['accent']} 100%);
            color: white; border: none; padding: 10px 24px; border-radius: 8px; font-weight: 600; width: 100%;
        }}
        .stTextInput input, .stNumberInput input, .stDateInput input, .stSelectbox div[data-baseweb="select"] {{
            border-radius: 8px; border: 1px solid {t['primary']}40;
        }}
        h1, h2, h3 {{ color: {t['text']} !important; }}
    </style>
    """, unsafe_allow_html=True)

def card_start(): st.markdown('<div class="glass-card">', unsafe_allow_html=True)
def card_end(): st.markdown('</div>', unsafe_allow_html=True)

# --- 9. MAIN APP ---
def main():
    st.set_page_config(page_title="GP Group Portal", page_icon="🏗️", layout="wide")
    
    if 'theme' not in st.session_state: st.session_state['theme'] = "Corporate Blue"
    if 'auth' not in st.session_state: st.session_state['auth'] = False
    
    inject_css(st.session_state['theme'])
    if 'db_init' not in st.session_state: init_db(); st.session_state['db_init'] = True

    # --- AUTH ---
    if not st.session_state['auth']:
        c1, c2, c3 = st.columns([1,1,1])
        with c2:
            card_start()
            st.title("G P Portal Login")
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            if st.button("Login"):
                users = db_get("Users")
                match = users[(users['Username'].astype(str)==u) & (users['Password'].astype(str)==p)]
                if not match.empty:
                    st.session_state['auth'] = True
                    st.session_state['role'] = match.iloc[0]['Role']
                    st.session_state['username'] = u
                    st.rerun()
                else: st.error("Invalid Credentials")
            card_end()
        return

    # --- TOP BAR & NOTIFICATIONS ---
    recent_notifs = check_notifications()
    
    col_logo, col_space, col_user = st.columns([1, 4, 2])
    with col_logo:
        if os.path.exists(LOGO_PATH): st.image(LOGO_PATH, width=120)
    with col_user:
        with st.expander(f"👤 {st.session_state['username']} ({st.session_state['role']})"):
            if st.button("Logout"): st.session_state['auth'] = False; st.rerun()
            st.session_state['theme'] = st.selectbox("Theme", list(THEMES.keys()), index=list(THEMES.keys()).index(st.session_state['theme']))

    # --- SIDEBAR ---
    with st.sidebar:
        st.title("Navigation")
        opts = ["Dashboard", "Raise Debit Note"]
        icons = ["grid-fill", "file-earmark-plus-fill"]
        if st.session_state['role'] == "Admin":
            opts += ["Contractors", "User Management"]
            icons += ["building-fill", "people-fill"]
        sel = option_menu("Menu", opts, icons=icons, styles={"nav-link-selected": {"background-color": THEMES[st.session_state['theme']]['primary']}})
        
        st.markdown("---")
        st.subheader("🔔 Notification Center")
        if recent_notifs:
            for n in recent_notifs:
                icon = "🚨" if n['Type'] == 'alert' else "📢"
                st.caption(f"{icon} {n['Message']}")
                st.text(f"{n['Timestamp']}")
                st.divider()
        else: st.caption("No recent alerts")

    # --- DASHBOARD (ANALYTICS) ---
    if sel == "Dashboard":
        st.title("Dashboard")
        df = db_get("DebitNotes")
        cons = db_get("Contractors")
        
        # A. High Level Metrics
        if not df.empty:
            df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce')
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Deductions", f"₹{df['Amount'].sum():,.0f}")
            m2.metric("Total Notes", len(df))
            m3.metric("Last Update", df['Date'].max() if not df.empty else "-")

        # B. Smart Analytics
        if not df.empty:
            c1, c2 = st.columns(2)
            with c1:
                card_start()
                st.subheader("Category Breakdown")
                if 'Category' in df.columns:
                    st.bar_chart(df.groupby('Category')['Amount'].sum(), color=THEMES[st.session_state['theme']]['primary'])
                card_end()
            with c2:
                card_start()
                st.subheader("Top Contractors")
                st.bar_chart(df.groupby('Contractor Name')['Amount'].sum(), color=THEMES[st.session_state['theme']]['accent'])
                card_end()

        # C. Search
        card_start()
        c1, c2 = st.columns([2, 1])
        con_options = ["All"] + cons['Name'].tolist() if not cons.empty else ["All"]
        search_con = c1.selectbox("Filter", con_options)
        
        if not df.empty:
            if search_con != "All": df = df[df['Contractor Name'] == search_con]
            df = df.sort_values(by="Date", ascending=False)
        card_end()

        # D. Activity
        st.subheader("Recent Activity")
        if not df.empty:
            for i, row in df.head(5).iterrows():
                card_start()
                rc1, rc2, rc3 = st.columns([2, 1, 1])
                with rc1:
                    st.markdown(f"**{row['Contractor Name']}**")
                    st.caption(f"{row.get('Category', '-')} • {row['Date']}")
                with rc2: st.markdown(f"**₹ {row['Amount']}**")
                with rc3:
                    if str(row['PDF Link']).startswith('http'): st.link_button("View PDF", row['PDF Link'])
                card_end()
        else: st.info("No records.")

    # --- RAISE DEBIT NOTE (VOICE + CATEGORIES) ---
    elif sel == "Raise Debit Note":
        st.title("Raise Debit Note")
        card_start()
        
        if 'latest_pdf_path' not in st.session_state: st.session_state['latest_pdf_path'] = None
        if 'voice_text' not in st.session_state: st.session_state['voice_text'] = ""

        # VOICE RECORDER (Outside Form)
        st.markdown("**🎙️ Voice Description:**")
        audio = mic_recorder(start_prompt="Record", stop_prompt="Stop", key='recorder')
        if audio:
            st.session_state['voice_text'] = transcribe_audio(audio['bytes'])
            st.success(f"Transcribed: {st.session_state['voice_text']}")

        with st.form("raise_form"):
            cons = db_get("Contractors")
            c_list = cons['Name'].tolist() if not cons.empty else []
            
            c1, c2 = st.columns(2)
            con = c1.selectbox("Contractor", c_list)
            dt = c2.date_input("Date")
            
            c3, c4 = st.columns(2)
            cat = c3.selectbox("Reason Category", REASON_CATEGORIES)
            amt = c4.number_input("Amount (INR)", min_value=0.0)
            
            site = st.text_input("Site Location")
            
            # Use Voice Text if available, else empty
            reason_default = st.session_state.get('voice_text', '')
            reason = st.text_area("Reason (Type or Record above)", value=reason_default)
            
            files = st.file_uploader("Proof", accept_multiple_files=True)
            send_email = st.checkbox("Email PDF to Contractor?", value=True)
            
            submitted = st.form_submit_button("Submit & Notify")
            
            if submitted:
                # 1. Upload
                imgs, links = [], []
                if files:
                    for f in files:
                        if not os.path.exists("temp"): os.makedirs("temp")
                        p = f"temp/{f.name}"
                        with open(p, "wb") as w: w.write(f.getbuffer())
                        imgs.append(p)
                        links.append(upload_to_drive(p, f.name, f.type))
                
                # 2. Process
                data = {"contractor": con, "date": str(dt), "amount": amt, "category": cat, "reason": reason, "site": site, "local_img_paths": imgs}
                pdf_path = create_pdf("receipt", data)
                pdf_link = upload_to_drive(pdf_path, os.path.basename(pdf_path), "application/pdf")
                
                # 3. DB
                note_id = int(datetime.now().timestamp())
                row = [note_id, con, str(dt), amt, cat, reason, site, ",".join(links), pdf_link, st.session_state['username']]
                db_insert("DebitNotes", row)
                
                # 4. Notify (Pop-up + Email)
                notify_users(f"New Note: {con} charged ₹{amt} for {cat}", type="alert")
                
                if send_email:
                    con_row = cons[cons['Name'] == con]
                    if not con_row.empty and 'Email' in con_row.columns and str(con_row.iloc[0]['Email']) != "":
                        to = [con_row.iloc[0]['Email']]
                        body = f"Debit note raised for {cat}.\nAmount: {amt}\nPDF Attached."
                        send_email_with_pdf(to, f"Debit Note - {con}", body, pdf_path)
                        st.toast("Email sent!")

                st.session_state['latest_pdf_path'] = pdf_path
                st.session_state['voice_text'] = "" # Reset voice text
                st.success("Raised Successfully!")

        if st.session_state['latest_pdf_path']:
            st.markdown("---")
            with open(st.session_state['latest_pdf_path'], "rb") as f:
                st.download_button("📥 Download PDF", f, file_name="DebitNote.pdf")
        card_end()

    # --- ADMIN PAGES ---
    elif sel == "Contractors" and st.session_state['role'] == "Admin":
        st.title("Contractor Management")
        c1, c2 = st.columns([1, 2])
        with c1:
            card_start()
            with st.form("add_con"):
                n = st.text_input("Name")
                e = st.text_input("Email")
                d = st.text_input("Details")
                if st.form_submit_button("Add"):
                    db_insert("Contractors", [int(datetime.now().timestamp()), n, d, e])
                    st.success("Added"); st.rerun()
            card_end()
        with c2:
            card_start()
            st.dataframe(db_get("Contractors"), use_container_width=True)
            card_end()

    elif sel == "User Management" and st.session_state['role'] == "Admin":
        st.title("User Management")
        c1, c2 = st.columns(2)
        with c1:
            card_start()
            with st.form("add_u"):
                u = st.text_input("Username")
                p = st.text_input("Password", type="password")
                r = st.selectbox("Role", ["Engineer", "Admin"])
                if st.form_submit_button("Create"):
                    db_insert("Users", [u, p, r])
                    st.success("User Created"); st.rerun()
            card_end()
        with c2:
            card_start()
            users = db_get("Users")
            if not users.empty:
                t = st.selectbox("Select User", users['Username'])
                if st.button("Delete"):
                    db_delete("Users", "Username", t)
                    st.rerun()
        card_start()
        st.dataframe(users, use_container_width=True)
        card_end()

if __name__ == "__main__":
    main()