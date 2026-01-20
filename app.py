import streamlit as st
import pandas as pd
from fpdf import FPDF
import os
from datetime import datetime
import time
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image
from streamlit_drawable_canvas import st_canvas
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
    """Uploads file and makes it PUBLIC (Anyone with link can view)"""
    service = get_drive_service()
    folder_id = st.secrets["drive_settings"]["folder_id"]
    
    file_metadata = {'name': filename, 'parents': [folder_id]}
    media = MediaFileUpload(file_path, mimetype=mime_type)
    
    # 1. Upload
    file = service.files().create(
        body=file_metadata, media_body=media, fields='id, webViewLink', supportsAllDrives=True
    ).execute()
    file_id = file.get('id')
    
    # 2. Change Permission to 'Anyone with link' (Reader)
    try:
        permission = {'type': 'anyone', 'role': 'reader'}
        service.permissions().create(fileId=file_id, body=permission).execute()
    except: pass # Shared drives sometimes inherit permissions, which is fine
    
    return file.get('webViewLink')

# --- 3. DATABASE OPERATIONS ---
def init_db():
    try:
        client = get_sheet_client()
        sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
        tables = ["DebitNotes", "Contractors", "Users"]
        for name in tables:
            try: sh.worksheet(name)
            except: 
                ws = sh.add_worksheet(name, 100, 10)
                # Add basic headers if new
                if name == "Users": ws.append_row(["Username", "Password", "Role"])
                elif name == "Contractors": ws.append_row(["ID", "Name", "Details", "Email"])
                elif name == "DebitNotes": ws.append_row(["ID", "Contractor", "Date", "Amount", "Category", "Reason", "Site", "Images", "PDF", "User"])
    except Exception as e: st.error(f"DB Error: {e}")

def db_get(table):
    try:
        ws = get_sheet_client().open_by_url(st.secrets["drive_settings"]["sheet_url"]).worksheet(table)
        data = ws.get_all_values()
        if len(data) < 2: return pd.DataFrame(columns=data[0] if data else None)
        return pd.DataFrame(data[1:], columns=data[0])
    except: return pd.DataFrame()

def db_insert(table, row_data):
    ws = get_sheet_client().open_by_url(st.secrets["drive_settings"]["sheet_url"]).worksheet(table)
    ws.append_row(row_data)

# --- 4. IMAGE PROCESSING (COMPRESS + CANVAS) ---
def process_image(image_data, filename):
    """Compresses and saves image. Works for both Uploads and Canvas arrays."""
    if not os.path.exists("temp"): os.makedirs("temp")
    
    # If it's a Canvas numpy array
    if isinstance(image_data,  (pd.DataFrame,  dict)) or hasattr(image_data, 'shape'): 
        img = Image.fromarray(image_data.astype('uint8'), 'RGBA')
        img = img.convert('RGB')
    else:
        # Standard file upload
        img = Image.open(image_data)
        if img.mode != 'RGB': img = img.convert('RGB')

    # Resize (Max 1000px)
    max_width = 1000
    if img.width > max_width:
        ratio = max_width / float(img.width)
        img = img.resize((max_width, int(float(img.height) * ratio)), Image.Resampling.LANCZOS)
    
    path = f"temp/{int(datetime.now().timestamp())}_{filename}.jpg"
    img.save(path, "JPEG", quality=70)
    return path

# --- 5. SMART PDF ENGINE (2 IMAGES PER PAGE) ---
class PDF(FPDF):
    def header(self):
        if os.path.exists(LOGO_PATH): self.image(LOGO_PATH, 10, 8, 30)
        self.set_font('Helvetica', 'B', 20)
        self.set_text_color(50, 50, 50)
        self.cell(0, 15, COMPANY_NAME, 0, 1, 'C')
        self.ln(10)
    def footer(self):
        self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(150)
        self.cell(0, 10, f'Gen: {datetime.now().strftime("%Y-%m-%d")}', 0, 0, 'C')

def create_pdf(data):
    pdf = PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # Title & Details
    pdf.set_font("Helvetica", "B", 16); pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 12, "DEBIT NOTE", 0, 1, 'C', fill=True); pdf.ln(5)
    
    pdf.set_font("Helvetica", "", 12)
    fields = [("Contractor", data['contractor']), ("Date", str(data['date'])), 
              ("Site", data['site']), ("Category", data['category']), ("Amount", f"INR {data['amount']}")]
    
    for l, v in fields:
        pdf.set_font("Helvetica", "B", 12); pdf.cell(40, 8, l, "B")
        pdf.set_font("Helvetica", "", 12); pdf.cell(150, 8, str(v), "B", 1)
    
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 12); pdf.cell(0, 10, "Description:", 0, 1)
    pdf.set_font("Helvetica", "", 11); pdf.multi_cell(0, 6, data['reason'])
    pdf.ln(5)

    # --- SMART IMAGE LAYOUT ---
    if data['local_img_paths']:
        pdf.set_font("Helvetica", "B", 12); pdf.cell(0, 10, "Evidence:", 0, 1)
        
        # We calculate space. A4 height ~297mm. Footer ~15mm. 
        # Image height we want ~80mm. 
        
        for i, img_path in enumerate(data['local_img_paths']):
            if not os.path.exists(img_path): continue
            
            # Check remaining space on page
            current_y = pdf.get_y()
            space_left = 280 - current_y 
            
            # If less than 85mm left, add new page
            if space_left < 85: 
                pdf.add_page()
            
            # Place Image (Height constrained to 80mm to fit 2-3 on a page)
            try: pdf.image(img_path, x=15, h=80); pdf.ln(5)
            except: pass

    path = f"temp/DebitNote_{int(datetime.now().timestamp())}.pdf"
    pdf.output(path)
    return path

# --- 6. FORM RESET CALLBACK ---
def clear_form():
    st.session_state['f_amt'] = 0.0
    st.session_state['f_site'] = ""
    st.session_state['f_reason'] = ""
    st.session_state['uploader_key'] += 1 # Resets file uploader
    st.session_state['canvas_key'] += 1   # Resets canvas

# --- 7. MAIN APP ---
def main():
    st.set_page_config(page_title="GP Portal", page_icon="🏗️", layout="wide", initial_sidebar_state="collapsed")
    
    # Init Session State
    if 'auth' not in st.session_state: 
        # CHECK URL QUERY PARAMS FOR PERSISTENCE
        params = st.query_params
        if "user" in params and "role" in params:
            st.session_state['auth'] = True
            st.session_state['username'] = params["user"]
            st.session_state['role'] = params["role"]
        else:
            st.session_state['auth'] = False

    if 'db_init' not in st.session_state: init_db(); st.session_state['db_init'] = True
    if 'uploader_key' not in st.session_state: st.session_state['uploader_key'] = 0
    if 'canvas_key' not in st.session_state: st.session_state['canvas_key'] = 0

    # --- LOGIN SCREEN ---
    if not st.session_state['auth']:
        c1, c2, c3 = st.columns([1,1,1])
        with c2:
            st.title("Login")
            u = st.text_input("User")
            p = st.text_input("Pass", type="password")
            if st.button("Log In"):
                users = db_get("Users")
                match = users[(users['Username']==u) & (users['Password']==p)]
                if not match.empty:
                    st.session_state['auth'] = True
                    st.session_state['username'] = u
                    st.session_state['role'] = match.iloc[0]['Role']
                    # SET URL PARAMS SO REFRESH DOESN'T LOGOUT
                    st.query_params["user"] = u
                    st.query_params["role"] = match.iloc[0]['Role']
                    st.rerun()
                else: st.error("Wrong pass")
        return

    # --- MOBILE-FRIENDLY SIDEBAR MENU ---
    # This automatically becomes a Hamburger Menu on mobile
    with st.sidebar:
        if os.path.exists(LOGO_PATH): st.image(LOGO_PATH)
        st.write(f"👤 **{st.session_state['username']}**")
        
        # Navigation
        menu_options = ["Dashboard", "Raise Debit Note"]
        if st.session_state['role'] == "Admin": menu_options += ["Contractors", "User Management"]
        
        sel = st.radio("Go to:", menu_options)
        
        st.divider()
        if st.button("Logout"):
            st.session_state['auth'] = False
            st.query_params.clear() # Clear URL so they stay logged out
            st.rerun()

    # --- DASHBOARD ---
    if sel == "Dashboard":
        st.title("Dashboard")
        df = db_get("DebitNotes")
        
        # Search & PDF View
        search = st.selectbox("Filter Contractor", ["All"] + list(df['Contractor'].unique()) if not df.empty else [])
        if not df.empty and search != "All": df = df[df['Contractor'] == search]
        
        if not df.empty:
            for i, row in df.iterrows():
                with st.expander(f"{row['Date']} - {row['Contractor']} (₹{row['Amount']})"):
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"**Reason:** {row['Reason']}")
                    # PUBLIC PDF LINK
                    if str(row['PDF']).startswith("http"):
                        c2.link_button("📄 View PDF", row['PDF'])
                    else: c2.caption("No PDF")

    # --- RAISE DEBIT NOTE (WITH PAINT & RESET) ---
    elif sel == "Raise Debit Note":
        st.title("Raise Debit Note")
        
        # CONTRACTOR SELECT
        cons = db_get("Contractors")
        con_list = cons['Name'].tolist() if not cons.empty else []
        con = st.selectbox("Contractor", con_list)
        
        # PAINT TOOL EXPANDER
        with st.expander("🎨 Open Camera/Paint Tool (Mark Images)"):
            # Canvas allows drawing on uploaded background
            bg_image = st.file_uploader("Upload Image to Mark:", type=["png", "jpg"], key="canvas_bg")
            stroke_color = st.color_picker("Pen Color", "#FF0000")
            canvas_result = st_canvas(
                fill_color="rgba(255, 165, 0, 0.3)",
                stroke_width=3,
                stroke_color=stroke_color,
                background_image=Image.open(bg_image) if bg_image else None,
                update_streamlit=True,
                height=400,
                drawing_mode="freedraw",
                key=f"canvas_{st.session_state['canvas_key']}"
            )
        
        # MAIN FORM
        with st.form("dn_form"):
            c1, c2 = st.columns(2)
            dt = c1.date_input("Date")
            cat = c2.selectbox("Category", REASON_CATEGORIES)
            
            # Using Session State Keys for Reset
            site = st.text_input("Site", key="f_site")
            amt = st.number_input("Amount", min_value=0.0, key="f_amt")
            reason = st.text_area("Reason", key="f_reason")
            
            # Standard Uploads
            files = st.file_uploader("Add Extra Photos", accept_multiple_files=True, key=f"uploader_{st.session_state['uploader_key']}")
            
            if st.form_submit_button("Submit"):
                # 1. Process Images
                imgs = []
                # Add Canvas Drawing if exists
                if canvas_result.image_data is not None and bg_image:
                    imgs.append(process_image(canvas_result.image_data, "markup"))
                
                # Add Standard Uploads
                if files:
                    for f in files: imgs.append(process_image(f, f.name))
                
                # Upload to Drive
                links = [upload_to_drive(p, os.path.basename(p), "image/jpeg") for p in imgs]
                
                # 2. PDF & DB
                data = {"contractor": con, "date": dt, "amount": amt, "category": cat, "reason": reason, "site": site, "local_img_paths": imgs}
                pdf_path = create_pdf(data)
                pdf_link = upload_to_drive(pdf_path, os.path.basename(pdf_path), "application/pdf")
                
                db_insert("DebitNotes", [int(datetime.now().timestamp()), con, str(dt), amt, cat, reason, site, ",".join(links), pdf_link, st.session_state['username']])
                
                st.success("Saved!")
                
                # 3. RESET FORM
                clear_form()
                time.sleep(1)
                st.rerun()

    # --- ADMIN ---
    elif sel == "Contractors" and st.session_state['role'] == "Admin":
        st.title("Add Contractor")
        with st.form("add_c"):
            n = st.text_input("Name"); d = st.text_input("Details"); e = st.text_input("Email")
            if st.form_submit_button("Add"):
                db_insert("Contractors", [int(datetime.now().timestamp()), n, d, e])
                st.success("Added"); st.rerun()
                
    elif sel == "User Management" and st.session_state['role'] == "Admin":
        st.title("Add User")
        with st.form("add_u"):
            u = st.text_input("Username"); p = st.text_input("Password"); r = st.selectbox("Role", ["Engineer", "Admin"])
            if st.form_submit_button("Create"):
                db_insert("Users", [u, p, r])
                st.success("Created"); st.rerun()

if __name__ == "__main__":
    main()