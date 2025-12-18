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

# --- CONFIGURATION ---
COMPANY_NAME = "G P Group"
LOGO_PATH = "logo.png"

# Setup Google Scope
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# --- GOOGLE CONNECTION ---
def get_google_creds():
    creds_dict = st.secrets["gcp_service_account"]
    return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

def get_sheet_client():
    creds = get_google_creds()
    return gspread.authorize(creds)

def get_drive_service():
    creds = get_google_creds()
    return build('drive', 'v3', credentials=creds)

# --- DRIVE UPLOAD ---
def upload_file_to_drive(file_path, filename, mime_type):
    drive_service = get_drive_service()
    folder_id = st.secrets["drive_settings"]["folder_id"]
    file_metadata = {'name': filename, 'parents': [folder_id]}
    media = MediaFileUpload(file_path, mimetype=mime_type)
    file = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
    return file.get('webViewLink')

# --- DB FUNCTIONS ---
def init_db():
    try:
        client = get_sheet_client()
        sheet_url = st.secrets["drive_settings"]["sheet_url"]
        sh = client.open_by_url(sheet_url)
        
        try: sh.worksheet("DebitNotes")
        except:
            ws = sh.add_worksheet(title="DebitNotes", rows=1000, cols=10)
            ws.append_row(["ID", "Contractor Name", "Date", "Amount", "Reason", "Site Location", "Image Links", "PDF Link"])

        try: sh.worksheet("Contractors")
        except:
            ws = sh.add_worksheet(title="Contractors", rows=100, cols=2)
            ws.append_row(["ID", "Name"])

        try: sh.worksheet("Users")
        except:
            ws = sh.add_worksheet(title="Users", rows=50, cols=3)
            ws.append_row(["Username", "Password", "Role"])
            
    except Exception as e:
        st.error(f"DB Error: {e}")

def check_login(username, password):
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    try:
        ws = sh.worksheet("Users")
        records = ws.get_all_records()
        for user in records:
            if str(user['Username']) == str(username) and str(user['Password']) == str(password):
                return user['Role']
    except:
        return None
    return None

def add_user(username, password, role):
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    ws = sh.worksheet("Users")
    ws.append_row([username, password, role])

def get_contractors():
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    ws = sh.worksheet("Contractors")
    records = ws.get_all_records()
    return [r['Name'] for r in records]

def add_contractor(name):
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    ws = sh.worksheet("Contractors")
    existing = ws.col_values(2)
    if name in existing: return False
    ws.append_row([len(existing), name])
    return True

def save_debit_note(data):
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    ws = sh.worksheet("DebitNotes")
    note_id = int(datetime.now().timestamp())
    ws.append_row([
        note_id, data['contractor'], data['date'], data['amount'], 
        data['reason'], data['site'], data['img_links'], data['pdf_link']
    ])

def get_all_notes():
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    ws = sh.worksheet("DebitNotes")
    return pd.DataFrame(ws.get_all_records())

# --- PDF GENERATION ---
class PDF(FPDF):
    def header(self):
        if os.path.exists(LOGO_PATH):
            self.image(LOGO_PATH, 10, 8, 33)
        self.set_font('Arial', 'B', 15)
        self.cell(80)
        self.cell(30, 10, COMPANY_NAME, 0, 0, 'C')
        self.ln(20)
        self.line(10, 30, 200, 30)
        self.ln(10)

def generate_single_pdf(data):
    if not os.path.exists("temp"): os.makedirs("temp")
    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "DEBIT NOTE", ln=True, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    
    def add_row(label, value):
        pdf.set_font("Arial", "B", 12)
        pdf.cell(50, 10, label, border=1)
        pdf.set_font("Arial", size=12)
        pdf.cell(140, 10, str(value), border=1, ln=True)

    add_row("Contractor Name", data['contractor'])
    add_row("Date", data['date'])
    add_row("Site Location", data['site'])
    add_row("Amount Deducted", f"INR {data['amount']}")
    
    pdf.set_font("Arial", "B", 12)
    pdf.cell(50, 20, "Reason", border=1)
    pdf.set_font("Arial", size=12)
    pdf.multi_cell(140, 20, data['reason'], border=1)
    pdf.ln(10)

    if data['local_img_paths']:
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "Site Photographs:", ln=True)
        pdf.ln(5)
        for img_path in data['local_img_paths']:
            if os.path.exists(img_path):
                try:
                    pdf.image(img_path, w=90)
                    pdf.ln(5)
                except: pass
                
    filename = f"debit_note_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    save_path = os.path.join("temp", filename)
    pdf.output(save_path)
    return save_path

def generate_master_pdf(contractor, start_date, end_date, df):
    if not os.path.exists("temp"): os.makedirs("temp")
    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, f"MASTER DEBIT STATEMENT", ln=True, align='C')
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, f"Contractor: {contractor}", ln=True, align='C')
    pdf.cell(0, 10, f"Period: {start_date} to {end_date}", ln=True, align='C')
    pdf.ln(10)
    
    pdf.set_font("Arial", "B", 10)
    pdf.cell(30, 10, "Date", 1)
    pdf.cell(90, 10, "Reason / Site", 1)
    pdf.cell(30, 10, "Amount", 1)
    pdf.cell(40, 10, "Note ID", 1, ln=True)
    
    pdf.set_font("Arial", size=10)
    total_amount = 0
    for index, row in df.iterrows():
        try:
            pdf.cell(30, 10, str(row['Date']), 1)
            reason_short = str(row['Reason']).replace('\n', ' ')[:40]
            pdf.cell(90, 10, reason_short, 1)
            pdf.cell(30, 10, str(row['Amount']), 1)
            pdf.cell(40, 10, str(row['ID']), 1, ln=True)
            total_amount += float(row['Amount'])
        except: continue

    pdf.set_font("Arial", "B", 12)
    pdf.cell(120, 10, "Total Deductions:", 1, align='R')
    pdf.cell(30, 10, str(total_amount), 1, ln=True)
    
    filename = f"Master_Statement_{contractor}_{datetime.now().strftime('%Y%m%d')}.pdf"
    save_path = os.path.join("temp", filename)
    pdf.output(save_path)
    return save_path

# --- PROFESSIONAL CSS ---
def local_css():
    st.markdown("""
        <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        div.block-container {padding-top: 2rem;}

        /* Card Design */
        .css-card {
            background-color: #ffffff;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
            border-top: 3px solid #0F52BA;
        }
        
        .stButton>button {
            width: 100%;
            border-radius: 5px;
            font-weight: 600;
            height: 3em;
        }

        .section-header {
            color: #2c3e50;
            font-size: 1.1rem;
            font-weight: bold;
            margin-bottom: 15px;
            border-bottom: 1px solid #eee;
            padding-bottom: 5px;
        }
        </style>
        """, unsafe_allow_html=True)

def card_start():
    st.markdown('<div class="css-card">', unsafe_allow_html=True)
def card_end():
    st.markdown('</div>', unsafe_allow_html=True)

# --- MAIN APP ---
def main():
    st.set_page_config(page_title="G P Group", page_icon="🏗️", layout="wide")
    local_css()
    
    if 'db_checked' not in st.session_state:
        init_db()
        st.session_state['db_checked'] = True

    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
        st.session_state['role'] = None
        st.session_state['username'] = None

    # --- LOGIN ---
    if not st.session_state['logged_in']:
        col1, col2, col3 = st.columns([1,2,1])
        with col2:
            card_start()
            st.image(LOGO_PATH, width=200) if os.path.exists(LOGO_PATH) else None
            st.title("Login Portal")
            with st.form("login"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                submit = st.form_submit_button("Sign In")
                
                if submit:
                    role = check_login(username, password)
                    if role:
                        st.session_state['logged_in'] = True
                        st.session_state['role'] = role
                        st.session_state['username'] = username
                        st.rerun()
                    else:
                        st.error("Invalid credentials.")
            card_end()
        return

    # --- NAVIGATION ---
    with st.sidebar:
        st.image(LOGO_PATH, width=150) if os.path.exists(LOGO_PATH) else None
        st.caption(f"Logged in as: {st.session_state['username']}")
        
        options = ["Raise Debit Note", "Dashboard", "Logout"]
        # Updated Icons: Clipboard for notes, Search for dashboard, Door for logout
        icons = ["clipboard-plus", "search", "door-open"]
        
        if st.session_state['role'] == "Admin":
            options.insert(2, "User Management")
            icons.insert(2, "person-badge") # ID Badge icon
            options.insert(3, "Manage Contractors")
            icons.insert(3, "cone-striped") # Construction cone icon

        selected = option_menu(
            "Menu", 
            options, 
            icons=icons, 
            menu_icon="list", 
            default_index=0,
            styles={
                "nav-link-selected": {"background-color": "#0F52BA"},
            }
        )

    if selected == "Logout":
        st.session_state['logged_in'] = False
        st.rerun()

    # --- RAISE NOTE ---
    if selected == "Raise Debit Note":
        st.title("New Debit Note")
        contractors = get_contractors()
        
        with st.form("debit_form"):
            st.markdown('<div class="section-header">Project Details</div>', unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            with c1: contractor = st.selectbox("Contractor", contractors) if contractors else st.selectbox("Select", ["None"])
            with c2: date_val = st.date_input("Date")

            st.markdown('<br><div class="section-header">Deduction Details</div>', unsafe_allow_html=True)
            c3, c4 = st.columns(2)
            with c3: site_loc = st.text_input("Site Location")
            with c4: amount = st.number_input("Amount (INR)", min_value=0.0, step=100.0)
            reason = st.text_area("Reason")
            
            st.markdown('<br><div class="section-header">Attachments</div>', unsafe_allow_html=True)
            uploaded_files = st.file_uploader("Upload Photos", accept_multiple_files=True, type=['png', 'jpg'])
            
            st.markdown("---")
            submitted = st.form_submit_button("Submit & Generate PDF")
            
            if submitted:
                if not reason or not site_loc:
                    st.error("Please fill in all text fields.")
                else:
                    status = st.info("Processing...")
                    drive_links = []
                    local_paths = []
                    if uploaded_files:
                        if not os.path.exists("temp"): os.makedirs("temp")
                        for f in uploaded_files:
                            path = os.path.join("temp", f.name)
                            with open(path, "wb") as wb: wb.write(f.getbuffer())
                            local_paths.append(path)
                            link = upload_file_to_drive(path, f.name, f.type)
                            drive_links.append(link)
                    
                    note_data = {"contractor": contractor, "date": str(date_val), "amount": amount, "reason": reason, "site": site_loc, "local_img_paths": local_paths}
                    pdf_path = generate_single_pdf(note_data)
                    pdf_link = upload_file_to_drive(pdf_path, os.path.basename(pdf_path), 'application/pdf')
                    
                    save_debit_note({**note_data, "img_links": ",".join(drive_links), "pdf_link": pdf_link})
                    status.success("Done!")

    # --- DASHBOARD ---
    elif selected == "Dashboard":
        st.title("Record Search")
        df = get_all_notes()
        
        if not df.empty:
            # SEARCH BAR & FILTER SECTION
            card_start()
            st.markdown('<div class="section-header">Find Records</div>', unsafe_allow_html=True)
            
            # 1. Text Search
            search_term = st.text_input("🔍 Search Keyword (e.g. 'Tiles', '3rd Floor')", placeholder="Type to search...").lower()
            
            # 2. Dropdown Filters
            c1, c2 = st.columns(2)
            all_contractors = df['Contractor Name'].unique().tolist()
            selected_contractors = c1.multiselect("Filter by Contractor", all_contractors, default=all_contractors)
            
            df['Date'] = pd.to_datetime(df['Date'])
            min_d, max_d = df['Date'].min().date(), df['Date'].max().date()
            date_range = c2.date_input("Filter by Date", [min_d, max_d])
            card_end()

            # APPLY FILTERS
            # A. Filter by Contractor
            mask = (df['Contractor Name'].isin(selected_contractors))
            
            # B. Filter by Date
            if len(date_range) == 2:
                mask = mask & (df['Date'].dt.date >= date_range[0]) & (df['Date'].dt.date <= date_range[1])
            
            filtered_df = df.loc[mask]

            # C. Filter by Search Keyword (searches in Reason or Location)
            if search_term:
                filtered_df = filtered_df[
                    filtered_df['Reason'].str.lower().str.contains(search_term) | 
                    filtered_df['Site Location'].str.lower().str.contains(search_term)
                ]
            
            # DISPLAY RESULTS
            st.dataframe(filtered_df[['Date', 'Contractor Name', 'Amount', 'Reason', 'PDF Link']], use_container_width=True)
            
            # MASTER PDF
            if len(selected_contractors) == 1:
                if st.button(f"Download Statement for {selected_contractors[0]}"):
                    pdf_path = generate_master_pdf(selected_contractors[0], str(date_range[0]), str(date_range[1]), filtered_df)
                    with open(pdf_path, "rb") as f:
                        st.download_button("Download PDF", f, file_name=os.path.basename(pdf_path))
        else:
            st.info("No records found.")

    # --- USER MANAGEMENT ---
    elif selected == "User Management" and st.session_state['role'] == "Admin":
        st.title("User Management")
        card_start()
        st.subheader("Add User")
        with st.form("add_user"):
            c1, c2, c3 = st.columns(3)
            new_u = c1.text_input("Username")
            new_p = c2.text_input("Password", type="password")
            new_r = c3.selectbox("Role", ["Engineer", "Admin"])
            if st.form_submit_button("Add"):
                add_user(new_u, new_p, new_r)
                st.success(f"Added {new_u}")
        card_end()

    # --- CONTRACTORS ---
    elif selected == "Manage Contractors" and st.session_state['role'] == "Admin":
        st.title("Contractor List")
        c1, c2 = st.columns([1, 2])
        with c1:
            card_start()
            st.subheader("Add New")
            with st.form("new_contractor"):
                new_c = st.text_input("Name")
                if st.form_submit_button("Add"):
                    if add_contractor(new_c): st.success("Added"); st.rerun()
                    else: st.error("Exists")
            card_end()
        with c2:
            st.table(get_contractors())

if __name__ == "__main__":
    main()