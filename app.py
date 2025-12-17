import streamlit as st
import pandas as pd
from fpdf import FPDF
import os
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- CONFIGURATION ---
COMPANY_NAME = "G P Group"
LOGO_PATH = "logo.png"

# Setup Google Scope
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# --- CONNECT TO GOOGLE ---
def get_google_creds():
    """Load credentials from Streamlit secrets."""
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return creds

def get_sheet_client():
    creds = get_google_creds()
    return gspread.authorize(creds)

def get_drive_service():
    creds = get_google_creds()
    return build('drive', 'v3', credentials=creds)

# --- GOOGLE DRIVE FUNCTIONS ---
def upload_file_to_drive(file_path, filename, mime_type='application/pdf'):
    """Uploads a file to the specific Google Drive folder and returns the web view link."""
    drive_service = get_drive_service()
    folder_id = st.secrets["drive_settings"]["folder_id"]

    file_metadata = {
        'name': filename,
        'parents': [folder_id]
    }
    
    media = MediaFileUpload(file_path, mimetype=mime_type)
    
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webViewLink'
    ).execute()
    
    return file.get('webViewLink')

# --- GOOGLE SHEETS FUNCTIONS ---
def init_db():
    """Checks if the sheet has headers, adds them if not."""
    try:
        client = get_sheet_client()
        sheet_url = st.secrets["drive_settings"]["sheet_url"]
        sh = client.open_by_url(sheet_url)
        
        # We will use two worksheets: 'DebitNotes' and 'Contractors'
        try:
            ws_notes = sh.worksheet("DebitNotes")
        except:
            ws_notes = sh.add_worksheet(title="DebitNotes", rows=1000, cols=10)
            ws_notes.append_row(["ID", "Contractor Name", "Date", "Amount", "Reason", "Site Location", "Image Link", "PDF Link"])

        try:
            ws_contractors = sh.worksheet("Contractors")
        except:
            ws_contractors = sh.add_worksheet(title="Contractors", rows=100, cols=2)
            ws_contractors.append_row(["ID", "Name"])
            
    except Exception as e:
        st.error(f"Database Connection Error: {e}")

def add_contractor(name):
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    ws = sh.worksheet("Contractors")
    
    # Check if exists
    existing = ws.col_values(2)
    if name in existing:
        return False
    
    new_id = len(existing) # Simple auto-increment
    ws.append_row([new_id, name])
    return True

def get_contractors():
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    ws = sh.worksheet("Contractors")
    records = ws.get_all_records()
    return [r['Name'] for r in records]

def save_debit_note(data):
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    ws = sh.worksheet("DebitNotes")
    
    # Generate a simple ID based on timestamp
    note_id = int(datetime.now().timestamp())
    
    row = [
        note_id,
        data['contractor'],
        data['date'],
        data['amount'],
        data['reason'],
        data['site'],
        data['img_link'],
        data['pdf_link']
    ]
    ws.append_row(row)

def get_all_notes():
    client = get_sheet_client()
    sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
    ws = sh.worksheet("DebitNotes")
    return pd.DataFrame(ws.get_all_records())

# --- PDF GENERATION (UNCHANGED) ---
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

def generate_pdf(data):
    # Ensure temp dir exists locally for processing
    if not os.path.exists("temp"): os.makedirs("temp")
    
    pdf = PDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, f"DEBIT NOTE", ln=True, align='C')
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

    # Local image path for PDF inclusion
    if data['local_img_path'] and os.path.exists(data['local_img_path']):
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "Site Photograph:", ln=True)
        pdf.ln(5)
        try:
            pdf.image(data['local_img_path'], w=100) 
        except:
            pass

    filename = f"debit_note_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    save_path = os.path.join("temp", filename)
    pdf.output(save_path)
    return save_path

# --- MAIN APP UI ---
def main():
    st.set_page_config(page_title="G P Group Debit Notes", page_icon="🏗️")
    
    # Initialize DB (create sheets if they don't exist)
    if 'db_init' not in st.session_state:
        init_db()
        st.session_state['db_init'] = True

    st.sidebar.image(LOGO_PATH, width=150) if os.path.exists(LOGO_PATH) else None
    st.sidebar.title(COMPANY_NAME)
    menu = st.sidebar.radio("Navigate", ["Engineer View (Raise Note)", "Dashboard (History)", "Admin (Manage Contractors)"])

    if menu == "Admin (Manage Contractors)":
        st.header("👷 Manage Contractors")
        with st.form("add_contractor"):
            new_name = st.text_input("New Contractor Name")
            submitted = st.form_submit_button("Add Contractor")
            if submitted and new_name:
                if add_contractor(new_name):
                    st.success(f"Added {new_name}")
                    st.rerun()
                else:
                    st.error("Contractor already exists.")
        
        st.subheader("Master List")
        st.table(get_contractors())

    elif menu == "Engineer View (Raise Note)":
        st.header("📝 Raise New Debit Note")
        contractors = get_contractors()
        
        with st.form("debit_form"):
            col1, col2 = st.columns(2)
            with col1:
                contractor = st.selectbox("Select Contractor", contractors) if contractors else st.selectbox("Select", ["No Contractors"])
                amount = st.number_input("Amount", min_value=0.0)
            with col2:
                site_loc = st.text_input("Site Location")
                note_date = st.date_input("Date", datetime.now())

            reason = st.text_area("Reason")
            uploaded_file = st.file_uploader("Upload Site Photo", type=['png', 'jpg', 'jpeg'])
            submit_note = st.form_submit_button("Generate & Save")

            if submit_note:
                if not reason or not site_loc:
                    st.error("Fill all text fields.")
                else:
                    status = st.empty()
                    status.info("Processing...")
                    
                    # 1. Save Image Locally (for PDF) & Upload to Drive
                    img_drive_link = "No Image"
                    local_img_path = None
                    
                    if uploaded_file:
                        if not os.path.exists("temp"): os.makedirs("temp")
                        local_img_path = os.path.join("temp", uploaded_file.name)
                        with open(local_img_path, "wb") as f:
                            f.write(uploaded_file.getbuffer())
                        
                        status.info("Uploading image to Drive...")
                        img_drive_link = upload_file_to_drive(local_img_path, uploaded_file.name, uploaded_file.type)

                    # 2. Generate PDF Locally
                    status.info("Generating PDF...")
                    note_data = {
                        "contractor": contractor,
                        "date": str(note_date),
                        "amount": amount,
                        "reason": reason,
                        "site": site_loc,
                        "local_img_path": local_img_path
                    }
                    pdf_path = generate_pdf(note_data)

                    # 3. Upload PDF to Drive
                    status.info("Uploading PDF to Drive...")
                    pdf_drive_link = upload_file_to_drive(pdf_path, os.path.basename(pdf_path), 'application/pdf')

                    # 4. Save Record to Sheets
                    status.info("Saving to Database...")
                    final_data = note_data.copy()
                    final_data['img_link'] = img_drive_link
                    final_data['pdf_link'] = pdf_drive_link
                    save_debit_note(final_data)

                    status.success("Done! Debit Note Saved.")
                    st.markdown(f"[View PDF on Google Drive]({pdf_drive_link})")

    elif menu == "Dashboard (History)":
        st.header("📊 History")
        df = get_all_notes()
        if not df.empty:
            # Show link columns as clickable
            st.dataframe(
                df, 
                column_config={
                    "PDF Link": st.column_config.LinkColumn("PDF"),
                    "Image Link": st.column_config.LinkColumn("Photo")
                }
            )
        else:
            st.info("No records found.")

if __name__ == "__main__":
    main()