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
    # Load credentials securely from Streamlit Secrets
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
    """Uploads file to Google Drive and returns the Web View Link."""
    drive_service = get_drive_service()
    folder_id = st.secrets["drive_settings"]["folder_id"]
    
    file_metadata = {'name': filename, 'parents': [folder_id]}
    media = MediaFileUpload(file_path, mimetype=mime_type)
    
    file = drive_service.files().create(
        body=file_metadata, 
        media_body=media, 
        fields='id, webViewLink'
    ).execute()
    
    return file.get('webViewLink')

# --- DB FUNCTIONS ---
def init_db():
    """Ensures all necessary sheets exist."""
    try:
        client = get_sheet_client()
        sheet_url = st.secrets["drive_settings"]["sheet_url"]
        sh = client.open_by_url(sheet_url)
        
        # Check for DebitNotes Sheet
        try: sh.worksheet("DebitNotes")
        except:
            ws = sh.add_worksheet(title="DebitNotes", rows=1000, cols=10)
            ws.append_row(["ID", "Contractor Name", "Date", "Amount", "Reason", "Site Location", "Image Links", "PDF Link"])

        # Check for Contractors Sheet
        try: sh.worksheet("Contractors")
        except:
            ws = sh.add_worksheet(title="Contractors", rows=100, cols=2)
            ws.append_row(["ID", "Name"])

        # Check for Users Sheet
        try: sh.worksheet("Users")
        except:
            ws = sh.add_worksheet(title="Users", rows=50, cols=3)
            ws.append_row(["Username", "Password", "Role"])
            
    except Exception as e:
        st.error(f"Database Connection Error: {e}")

def check_login(username, password):
    """Verifies user credentials against the Google Sheet."""
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    try:
        ws = sh.worksheet("Users")
        records = ws.get_all_records()
        for user in records:
            # Force string comparison to avoid type errors
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
    existing = ws.col_values(2) # Column B is Names
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
    
    # Details Table
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
    
    # Reason
    pdf.set_font("Arial", "B", 12)
    pdf.cell(50, 20, "Reason", border=1)
    pdf.set_font("Arial", size=12)
    pdf.multi_cell(140, 20, data['reason'], border=1)
    pdf.ln(10)

    # Photos
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
    """Generates a summary statement PDF for a contractor."""
    if not os.path.exists("temp"): os.makedirs("temp")
    pdf = PDF()
    pdf.add_page()
    
    # Header
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, f"MASTER DEBIT STATEMENT", ln=True, align='C')
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, f"Contractor: {contractor}", ln=True, align='C')
    pdf.cell(0, 10, f"Period: {start_date} to {end_date}", ln=True, align='C')
    pdf.ln(10)
    
    # Summary Table Header
    pdf.set_font("Arial", "B", 10)
    pdf.cell(30, 10, "Date", 1)
    pdf.cell(90, 10, "Reason / Site", 1)
    pdf.cell(30, 10, "Amount", 1)
    pdf.cell(40, 10, "Note ID", 1, ln=True)
    
    # Table Content
    pdf.set_font("Arial", size=10)
    total_amount = 0
    for index, row in df.iterrows():
        try:
            pdf.cell(30, 10, str(row['Date']), 1)
            # Truncate reason to fit table
            reason_short = str(row['Reason']).replace('\n', ' ')[:40]
            pdf.cell(90, 10, reason_short, 1)
            pdf.cell(30, 10, str(row['Amount']), 1)
            pdf.cell(40, 10, str(row['ID']), 1, ln=True)
            total_amount += float(row['Amount'])
        except:
            continue

    # Total
    pdf.set_font("Arial", "B", 12)
    pdf.cell(120, 10, "Total Deductions:", 1, align='R')
    pdf.cell(30, 10, str(total_amount), 1, ln=True)
    
    filename = f"Master_Statement_{contractor}_{datetime.now().strftime('%Y%m%d')}.pdf"
    save_path = os.path.join("temp", filename)
    pdf.output(save_path)
    return save_path

# --- CSS STYLING ---
def local_css():
    st.markdown("""
        <style>
        /* Hide Streamlit default UI elements */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        
        /* Full width button */
        .stButton>button {width: 100%; border-radius: 8px;}
        
        /* Sidebar customization */
        [data-testid="stSidebar"] {background-color: #f8f9fa;}
        </style>
        """, unsafe_allow_html=True)

# --- MAIN APP ---
def main():
    st.set_page_config(page_title="G P Group", page_icon="🏗️", layout="wide")
    local_css()
    
    # Initialize DB (One time check)
    if 'db_checked' not in st.session_state:
        init_db()
        st.session_state['db_checked'] = True

    # Initialize Login State
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
        st.session_state['role'] = None
        st.session_state['username'] = None

    # --- LOGIN SCREEN ---
    if not st.session_state['logged_in']:
        col1, col2, col3 = st.columns([1,2,1])
        with col2:
            st.image(LOGO_PATH, width=200) if os.path.exists(LOGO_PATH) else None
            st.title("G P Group Login")
            with st.form("login"):
                username = st.text_input("Username")
                password = st.text_input("Password", type="password")
                submit = st.form_submit_button("Login")
                
                if submit:
                    role = check_login(username, password)
                    if role:
                        st.session_state['logged_in'] = True
                        st.session_state['role'] = role
                        st.session_state['username'] = username
                        st.rerun()
                    else:
                        st.error("Invalid credentials. Check Users sheet.")
        return

    # --- MAIN NAVIGATION (Only shows after login) ---
    with st.sidebar:
        st.image(LOGO_PATH, width=150) if os.path.exists(LOGO_PATH) else None
        st.write(f"Logged in as: **{st.session_state['username']}** ({st.session_state['role']})")
        
        # Menu options based on Role
        options = ["Raise Debit Note", "Dashboard", "Logout"]
        icons = ["pencil-square", "bar-chart-line", "box-arrow-right"]
        
        # Admin gets extra options
        if st.session_state['role'] == "Admin":
            options.insert(2, "User Management")
            icons.insert(2, "people")
            options.insert(3, "Manage Contractors")
            icons.insert(3, "buildings")

        selected = option_menu(
            "Navigation", 
            options, 
            icons=icons, 
            menu_icon="cast", 
            default_index=0,
            styles={"nav-link-selected": {"background-color": "#d32f2f"}}
        )

    # --- LOGOUT LOGIC ---
    if selected == "Logout":
        st.session_state['logged_in'] = False
        st.session_state['role'] = None
        st.session_state['username'] = None
        st.rerun()

    # --- PAGE: RAISE NOTE ---
    if selected == "Raise Debit Note":
        st.title("📝 New Debit Note")
        contractors = get_contractors()
        
        with st.form("debit_form"):
            c1, c2 = st.columns(2)
            contractor = c1.selectbox("Contractor", contractors) if contractors else c1.selectbox("No Contractors", [])
            date_val = c2.date_input("Date")
            
            c3, c4 = st.columns(2)
            amount = c3.number_input("Amount (INR)", min_value=0.0, step=500.0)
            site_loc = c4.text_input("Site Location")
            
            reason = st.text_area("Reason for Deduction")
            
            # Multiple Files Support
            uploaded_files = st.file_uploader("Proof Photos", accept_multiple_files=True, type=['png', 'jpg'])
            
            submitted = st.form_submit_button("🚀 Submit & Generate PDF")
            
            if submitted:
                if not reason or not site_loc:
                    st.error("Missing fields")
                else:
                    status = st.empty()
                    status.info("Processing Photos...")
                    
                    # Handle Photos
                    drive_links = []
                    local_paths = []
                    if uploaded_files:
                        if not os.path.exists("temp"): os.makedirs("temp")
                        for f in uploaded_files:
                            path = os.path.join("temp", f.name)
                            with open(path, "wb") as wb:
                                wb.write(f.getbuffer())
                            local_paths.append(path)
                            link = upload_file_to_drive(path, f.name, f.type)
                            drive_links.append(link)
                    
                    status.info("Generating PDF...")
                    note_data = {
                        "contractor": contractor, "date": str(date_val), "amount": amount,
                        "reason": reason, "site": site_loc, "local_img_paths": local_paths
                    }
                    pdf_path = generate_single_pdf(note_data)
                    
                    status.info("Uploading PDF...")
                    pdf_link = upload_file_to_drive(pdf_path, os.path.basename(pdf_path), 'application/pdf')
                    
                    save_debit_note({
                        **note_data,
                        "img_links": ",".join(drive_links),
                        "pdf_link": pdf_link
                    })
                    status.success("Debit Note Saved!")

    # --- PAGE: DASHBOARD ---
    elif selected == "Dashboard":
        st.title("📊 Debit Dashboard")
        df = get_all_notes()
        
        if not df.empty:
            # Filters
            st.subheader("Filters")
            c1, c2 = st.columns(2)
            
            # Contractor Filter
            all_contractors = df['Contractor Name'].unique().tolist()
            selected_contractors = c1.multiselect("Filter by Contractor", all_contractors, default=all_contractors)
            
            # Date Filter
            df['Date'] = pd.to_datetime(df['Date']) # Convert to datetime
            min_date = df['Date'].min().date()
            max_date = df['Date'].max().date()
            date_range = c2.date_input("Date Range", [min_date, max_date])

            # Apply Filter
            mask = (df['Contractor Name'].isin(selected_contractors))
            if len(date_range) == 2:
                mask = mask & (df['Date'].dt.date >= date_range[0]) & (df['Date'].dt.date <= date_range[1])
            
            filtered_df = df.loc[mask]
            
            st.dataframe(filtered_df[['Date', 'Contractor Name', 'Amount', 'Reason', 'PDF Link']])
            
            # Master PDF Logic
            st.divider()
            st.subheader("📑 Generate Master Statement")
            
            # Only show button if exactly ONE contractor is selected
            if len(selected_contractors) == 1:
                if st.button(f"Create Master PDF for {selected_contractors[0]}"):
                    if not filtered_df.empty:
                        pdf_path = generate_master_pdf(
                            selected_contractors[0], 
                            str(date_range[0]), 
                            str(date_range[1]), 
                            filtered_df
                        )
                        with open(pdf_path, "rb") as f:
                            st.download_button("Download Master PDF", f, file_name=os.path.basename(pdf_path))
                    else:
                        st.warning("No data found for this selection.")
            else:
                st.info("To generate a Master PDF, please select exactly ONE contractor in the filter above.")
        else:
            st.info("No records found.")

    # --- PAGE: USER MANAGEMENT (Admin Only) ---
    elif selected == "User Management" and st.session_state['role'] == "Admin":
        st.header("👥 User Management")
        
        # Add User Form
        with st.form("add_user"):
            new_u = st.text_input("New Username")
            new_p = st.text_input("New Password", type="password")
            new_r = st.selectbox("Role", ["Engineer", "Admin"])
            if st.form_submit_button("Add User"):
                add_user(new_u, new_p, new_r)
                st.success(f"User {new_u} added!")

    # --- PAGE: MANAGE CONTRACTORS (Admin Only) ---
    elif selected == "Manage Contractors" and st.session_state['role'] == "Admin":
        st.header("🏗️ Manage Contractors")
        
        # Add Contractor Form
        with st.form("new_contractor"):
            new_c = st.text_input("New Contractor Name")
            if st.form_submit_button("Add Contractor"):
                if add_contractor(new_c): 
                    st.success("Added")
                    st.rerun()
                else: 
                    st.error("Contractor already exists")
        
        # Show List
        st.subheader("Current Contractors")
        st.table(get_contractors())

if __name__ == "__main__":
    main()
