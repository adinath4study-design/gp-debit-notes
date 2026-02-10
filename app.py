import streamlit as st
import pandas as pd
from fpdf import FPDF
import os
from datetime import datetime
import time
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from streamlit_option_menu import option_menu
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import speech_recognition as sr
from streamlit_mic_recorder import mic_recorder
import io
from PIL import Image
from pypdf import PdfWriter
from streamlit_cropper import st_cropper
import re
import uuid  # Standard Lib: For unique filenames
import shutil # Standard Lib: For file deletion

# --- 1. CONFIGURATION ---
COMPANY_NAME = "G P Group"
LOGO_PATH = "logo.png"
PROFILE_PICS_DIR = "static/profile_pics" # Local Storage for Speed
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
    file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink, webContentLink', supportsAllDrives=True).execute()
    file_id = file.get('id')
    try:
        service.permissions().create(fileId=file_id, body={'type': 'anyone', 'role': 'reader'}, supportsAllDrives=True).execute()
    except: pass
    return file.get('webContentLink', file.get('webViewLink'))

# --- 3. HELPER FUNCTIONS ---
def init_filesystem():
    """Creates necessary directories on startup"""
    if not os.path.exists("temp"): os.makedirs("temp")
    if not os.path.exists(PROFILE_PICS_DIR): os.makedirs(PROFILE_PICS_DIR)

def save_profile_pic_local(image_input, old_filename=None):
    """
    Saves profile pic locally (WhatsApp Style).
    1. Resizes to 500x500
    2. Generates unique UUID filename
    3. Deletes old file to save space
    """
    # 1. Process Image
    if isinstance(image_input, bytes):
        img = Image.open(io.BytesIO(image_input))
    else:
        img = image_input # It's already a PIL Image from Cropper

    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
    
    # 2. Resize to 500x500 (WhatsApp Standard)
    img = img.resize((500, 500), Image.Resampling.LANCZOS)
    
    # 3. Generate Unique Filename
    unique_name = f"{uuid.uuid4().hex}.jpg"
    save_path = os.path.join(PROFILE_PICS_DIR, unique_name)
    
    # 4. Save
    img.save(save_path, "JPEG", quality=85, optimize=True)
    
    # 5. Clean up old image
    if old_filename and old_filename != "None" and old_filename is not None:
        old_path = os.path.join(PROFILE_PICS_DIR, old_filename)
        if os.path.exists(old_path):
            try: os.remove(old_path)
            except: pass
            
    return unique_name

def safe_profile_display(filename):
    """Returns the path to the profile pic, or None"""
    if filename and filename != "None":
        path = os.path.join(PROFILE_PICS_DIR, filename)
        if os.path.exists(path):
            return path
    return None 

def safe_image(image_source, width=None, caption=None):
    """Safely renders an image."""
    try:
        if not image_source: return
        if not str(image_source).startswith('http'):
            if not os.path.exists(image_source): return 
        if width: st.image(image_source, width=width, caption=caption)
        else: st.image(image_source, caption=caption)
    except: pass

def get_file_id_from_url(url):
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    return match.group(1) if match else None

def download_pdf_from_drive(drive_link):
    file_id = get_file_id_from_url(drive_link)
    if not file_id: return None
    service = get_drive_service()
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False: status, done = downloader.next_chunk()
    return fh

def merge_pdfs(pdf_links):
    merger = PdfWriter()
    for link in pdf_links:
        if str(link).startswith('http'):
            pdf_bytes = download_pdf_from_drive(link)
            if pdf_bytes: merger.append(pdf_bytes)
    output = io.BytesIO()
    merger.write(output)
    return output.getvalue()

def compress_image(image_input):
    if not os.path.exists("temp"): os.makedirs("temp")
    if isinstance(image_input, Image.Image):
        img = image_input
        filename = f"crop_{int(datetime.now().timestamp())}.jpg"
    elif isinstance(image_input, bytes):
        img = Image.open(io.BytesIO(image_input))
        filename = f"cam_{int(datetime.now().timestamp())}.jpg"
    else:
        img = Image.open(image_input)
        filename = image_input.name

    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
    max_width = 1000
    if img.width > max_width:
        ratio = max_width / float(img.width)
        new_height = int(float(img.height) * ratio)
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
    
    save_path = f"temp/compressed_{int(datetime.now().timestamp())}_{filename}"
    img.save(save_path, "JPEG", quality=65, optimize=True)
    return save_path

def transcribe_audio(audio_bytes):
    r = sr.Recognizer()
    audio_file = io.BytesIO(audio_bytes)
    try:
        with sr.AudioFile(audio_file) as source: audio = r.record(source)
        return r.recognize_google(audio)
    except: return "Could not understand audio"

def send_email_with_pdf(to_emails, subject, body, attachment_path):
    if not to_emails or "email_settings" not in st.secrets: return False
    sender_email = st.secrets["email_settings"]["sender_email"]
    password = st.secrets["email_settings"]["app_password"]
    msg = MIMEMultipart(); msg['From'] = sender_email; msg['To'] = ", ".join(to_emails); msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as attachment:
            part = MIMEBase("application", "octet-stream"); part.set_payload(attachment.read())
        encoders.encode_base64(part); part.add_header("Content-Disposition", f"attachment; filename= {os.path.basename(attachment_path)}"); msg.attach(part)
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587); server.starttls(); server.login(sender_email, password)
        server.sendmail(sender_email, to_emails, msg.as_string()); server.quit(); return True
    except: return False

# --- 4. DATABASE OPERATIONS ---
def init_db():
    try:
        client = get_sheet_client()
        sh = client.open_by_url(st.secrets["drive_settings"]["sheet_url"])
        tables = {
            "DebitNotes": ["ID", "Contractor Name", "Date", "Amount", "Category", "Reason", "Site Location", "Image Links", "PDF Link", "SubmittedBy"],
            "Contractors": ["ID", "Name", "Details", "Email"],
            "Users": ["Username", "Password", "Role", "ProfilePic"], # NOW STORES LOCAL FILENAME
            "Notifications": ["ID", "Message", "Timestamp", "Type"]
        }
        for name, headers in tables.items():
            try: 
                ws = sh.worksheet(name)
                curr = ws.row_values(1)
                if len(curr) < len(headers):
                    ws.resize(cols=len(headers))
                    for i, h in enumerate(headers): 
                        if i >= len(curr): ws.update_cell(1, i+1, h)
            except: 
                ws = sh.add_worksheet(name, 100, len(headers)); ws.append_row(headers)
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

def db_update_user(old_username, new_username, new_password, new_pic_filename):
    ws = get_sheet_client().open_by_url(st.secrets["drive_settings"]["sheet_url"]).worksheet("Users")
    try:
        cell = ws.find(old_username)
        if new_password: ws.update_cell(cell.row, 2, new_password)
        if new_pic_filename: ws.update_cell(cell.row, 4, new_pic_filename)
        if new_username and new_username != old_username: ws.update_cell(cell.row, 1, new_username)
        return True
    except: return False

def db_delete_row(table, col_name, value):
    ws = get_sheet_client().open_by_url(st.secrets["drive_settings"]["sheet_url"]).worksheet(table)
    try:
        cell = ws.find(str(value)); ws.delete_rows(cell.row); return True
    except: return False

# --- 5. PDF ENGINE ---
class PDF(FPDF):
    def header(self):
        if os.path.exists(LOGO_PATH): self.image(LOGO_PATH, 10, 8, 30)
        self.set_font('Helvetica', 'B', 20); self.set_text_color(50, 50, 50); self.cell(0, 15, COMPANY_NAME, 0, 1, 'C'); self.ln(10)
    def footer(self):
        self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(150); self.cell(0, 10, f'Generated by {st.session_state.get("username", "System")}', 0, 0, 'C')

def create_pdf(type, data):
    if not os.path.exists("temp"): os.makedirs("temp")
    pdf = PDF(); pdf.add_page(); pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Helvetica", "B", 16); pdf.set_fill_color(240, 240, 240)
    title = "DEBIT NOTE" if type == "receipt" else "STATEMENT OF ACCOUNT"
    pdf.cell(0, 12, title, 0, 1, 'C', fill=True); pdf.ln(10)
    
    if type == "receipt":
        pdf.set_font("Helvetica", "", 12)
        fields = [("Contractor", data['contractor']), ("Date", str(data['date'])), ("Site Location", data['site']), ("Category", data['category']), ("Amount", f"INR {data['amount']}")]
        for label, value in fields:
            pdf.set_font("Helvetica", "B", 12); pdf.cell(50, 8, label, "B"); pdf.set_font("Helvetica", "", 12); pdf.cell(140, 8, str(value), "B", 1)
        pdf.ln(8); pdf.set_font("Helvetica", "B", 12); pdf.cell(0, 10, "Description / Reason:", 0, 1); pdf.set_font("Helvetica", "", 11); pdf.multi_cell(0, 6, data['reason']); pdf.ln(5)
        
        if data.get('local_img_paths'):
            pdf.set_font("Helvetica", "B", 12); pdf.cell(0, 10, "Evidence:", 0, 1)
            img_paths = [p for p in data['local_img_paths'] if os.path.exists(p)]
            box_w, box_h = 90, 75
            for i in range(0, len(img_paths), 2):
                if 270 - pdf.get_y() < 85: pdf.add_page()
                y_pos = pdf.get_y()
                with Image.open(img_paths[i]) as img: aspect = img.height/img.width
                if aspect > (box_h/box_w): pdf.image(img_paths[i], x=10, y=y_pos, h=box_h)
                else: pdf.image(img_paths[i], x=10, y=y_pos, w=box_w)
                if i+1 < len(img_paths):
                    with Image.open(img_paths[i+1]) as img: aspect = img.height/img.width
                    if aspect > (box_h/box_w): pdf.image(img_paths[i+1], x=105, y=y_pos, h=box_h)
                    else: pdf.image(img_paths[i+1], x=105, y=y_pos, w=box_w)
                pdf.ln(80)
        
        if data.get('signature_path') and os.path.exists(data['signature_path']):
            if 280 - pdf.get_y() < 40: pdf.add_page()
            pdf.ln(5); pdf.set_font("Helvetica", "B", 10); sig_y = pdf.get_y(); pdf.set_x(130); pdf.cell(60, 5, "Authorized Signature:", 0, 1, 'C')
            try: pdf.image(data['signature_path'], x=145, y=sig_y+6, w=30)
            except: pass
            pdf.set_y(sig_y+30); pdf.set_x(130); pdf.cell(60, 5, f"Engineer: {st.session_state.get('username')}", 0, 1, 'C')
        filename = f"DebitNote_{int(datetime.now().timestamp())}.pdf"
    else:
        pdf.set_font("Helvetica", "", 12); pdf.cell(0, 8, f"Contractor: {data['contractor']}", 0, 1)
        pdf.cell(0, 8, f"Period: {data['start']} to {data['end']}", 0, 1); pdf.ln(5)
        pdf.set_font("Helvetica", "B", 10); pdf.set_fill_color(50, 50, 50); pdf.set_text_color(255)
        headers = ["Date", "Category", "Reason", "Amount"]
        for h, w in zip(headers, [30, 40, 90, 30]): pdf.cell(w, 10, h, 1, 0, 'C', True)
        pdf.ln(); pdf.set_text_color(0); pdf.set_font("Helvetica", "", 9)
        total = 0
        for _, row in data['df'].iterrows():
            pdf.cell(30, 10, str(row['Date']), 1); pdf.cell(40, 10, str(row.get('Category', '-'))[:20], 1)
            pdf.cell(90, 10, str(row['Reason'])[:50], 1); pdf.cell(30, 10, str(row['Amount']), 1, 1)
            total += float(row['Amount'])
        pdf.ln(5); pdf.set_font("Helvetica", "B", 12)
        pdf.cell(160, 10, "Total Deductions:", 0, 0, 'R'); pdf.cell(30, 10, f"INR {total}", 0, 1, 'L')
        filename = f"Statement_{data['contractor']}.pdf"

    path = f"temp/{filename}"; pdf.output(path); return path

# --- 6. UI ---
THEMES = { "Corporate Blue": {"bg": "#f4f6f9", "card": "rgba(255, 255, 255, 0.9)", "text": "#1e293b", "primary": "#0F52BA", "accent": "#3b82f6"} }
def inject_css():
    t = THEMES["Corporate Blue"]
    st.markdown(f"""<style>.stApp {{ background-color: {t['bg']}; color: {t['text']}; }}
        .glass-card {{background: {t['card']}; backdrop-filter: blur(10px); border-radius: 16px; padding: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); margin-bottom: 24px;}}
        .stButton>button {{background: linear-gradient(135deg, {t['primary']} 0%, {t['accent']} 100%); color: white; border: none;}}
        .profile-pic {{border-radius: 50%; width: 100px; height: 100px; object-fit: cover; display: block; margin-left: auto; margin-right: auto; border: 3px solid {t['primary']};}}
        </style>""", unsafe_allow_html=True)
def card_start(): st.markdown('<div class="glass-card">', unsafe_allow_html=True)
def card_end(): st.markdown('</div>', unsafe_allow_html=True)
def reset_form():
    st.session_state['dn_site'] = ""; st.session_state['dn_amt'] = 0.0; st.session_state['dn_reason'] = ""; st.session_state['voice_text'] = ""; st.session_state['uploader_key'] += 1; st.session_state['cam_buffer'] = []

# --- 7. MAIN APP ---
def main():
    st.set_page_config(page_title="GP Portal", page_icon="🏗️", layout="wide")
    if 'uploader_key' not in st.session_state: st.session_state['uploader_key'] = 0
    if 'cam_buffer' not in st.session_state: st.session_state['cam_buffer'] = []
    if 'cam_counter' not in st.session_state: st.session_state['cam_counter'] = 0
    if 'user_pic' not in st.session_state: st.session_state['user_pic'] = None
    
    if 'auth' not in st.session_state:
        params = st.query_params
        if "user" in params and "role" in params:
            st.session_state['auth'] = True; st.session_state['username'] = params["user"]; st.session_state['role'] = params["role"]
        else: st.session_state['auth'] = False

    inject_css(); 
    if 'db_init' not in st.session_state: init_db(); init_filesystem(); st.session_state['db_init'] = True

    # Login
    if not st.session_state['auth']:
        c1,c2,c3=st.columns([1,1,1])
        with c2: 
            card_start(); st.title("Login"); u=st.text_input("User").strip(); p=st.text_input("Pass", type="password").strip()
            if st.button("Log In"):
                users=db_get("Users")
                match = users[(users['Username'].astype(str).str.strip().str.lower() == u.lower()) & (users['Password'].astype(str).str.strip() == p)]
                if not match.empty:
                    st.session_state['auth']=True; st.session_state['username']=match.iloc[0]['Username']; st.session_state['role']=match.iloc[0]['Role']
                    if 'ProfilePic' in match.columns:
                        st.session_state['user_pic'] = match.iloc[0]['ProfilePic']
                    st.query_params["user"]=match.iloc[0]['Username']; st.query_params["role"]=match.iloc[0]['Role']; st.rerun()
                else: st.error("Invalid")
            card_end()
        return

    # Sidebar (Display Local Profile Pic)
    with st.sidebar:
        dp_path = safe_profile_display(st.session_state.get('user_pic'))
        if dp_path:
            st.image(dp_path, width=100) # Simple streamlit image because it's local now
        else:
            # Fallback to Logo or generic user icon
            if os.path.exists(LOGO_PATH): st.image(LOGO_PATH, width=80)
            else: st.markdown(f'<div style="display:flex;justify-content:center;font-size:80px;">👤</div>', unsafe_allow_html=True)
        
        st.markdown(f"<h3 style='text-align: center;'>{st.session_state['username']}</h3>", unsafe_allow_html=True)
        st.divider()
        
        opts=["Dashboard", "Raise Debit Note", "My Profile"]
        if st.session_state['role']=="Admin": opts+=["Contractors", "User Management"]
        sel=option_menu("Nav", opts, icons=['grid', 'file-text', 'person-circle', 'building', 'people'])
        if st.button("Logout"): st.session_state['auth']=False; st.query_params.clear(); st.rerun()

    # --- DASHBOARD ---
    if sel == "Dashboard":
        st.title("Dashboard")
        df = db_get("DebitNotes"); cons = db_get("Contractors")
        
        if not df.empty:
            df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce')
            m1, m2, m3 = st.columns(3)
            m1.metric("Total", f"₹{df['Amount'].sum():,.0f}"); m2.metric("Count", len(df)); m3.metric("Last", df['Date'].max())
            
            c1, c2 = st.columns(2)
            with c1: card_start(); st.subheader("Category Breakdown"); st.bar_chart(df.groupby('Category')['Amount'].sum() if 'Category' in df.columns else []); card_end()
            with c2: card_start(); st.subheader("Top Contractors"); st.bar_chart(df.groupby('Contractor Name')['Amount'].sum()); card_end()
            
            card_start()
            c1, c2 = st.columns([2, 1])
            con_options = ["All"] + cons['Name'].tolist() if not cons.empty else ["All"]
            search_con = c1.selectbox("Filter Contractor", con_options)
            if search_con != "All": df = df[df['Contractor Name'] == search_con]
            df = df.sort_values(by="Date", ascending=False)
            card_end()
            
            st.subheader("Records")
            for i, row in df.iterrows():
                with st.expander(f"{row['Date']} | {row['Contractor Name']} | ₹{row['Amount']}"):
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"**Reason:** {row['Reason']}")
                    if str(row['PDF Link']).startswith('http'): c1.link_button("View PDF", row['PDF Link'])
                    if st.session_state['role'] == "Admin" or row['SubmittedBy'] == st.session_state['username']:
                        if c2.button("🗑️ Delete", key=f"del_{row['ID']}"):
                            if db_delete_row("DebitNotes", "ID", row['ID']): st.success("Deleted!"); time.sleep(1); st.rerun()

        st.markdown("---")
        if st.button("📥 Download Tools (Statement / Merge)"): st.session_state['show_gen'] = True
        if st.session_state.get('show_gen'):
            card_start(); st.subheader("Download Center")
            mc = st.selectbox("Contractor", df['Contractor Name'].unique())
            mdr = st.date_input("Period", [])
            col_a, col_b = st.columns(2)
            if col_a.button("📄 Account Statement"):
                mask = (df['Contractor Name'] == mc) & (pd.to_datetime(df['Date']).dt.date >= mdr[0]) & (pd.to_datetime(df['Date']).dt.date <= mdr[1])
                f_df = df[mask]
                if not f_df.empty:
                    path = create_pdf("statement", {"contractor": mc, "start": mdr[0], "end": mdr[1], "df": f_df})
                    with open(path, "rb") as f: st.download_button("Download Statement PDF", f, file_name="Statement.pdf")
            if col_b.button("📚 Merge All Debit Notes"):
                mask = (df['Contractor Name'] == mc) & (pd.to_datetime(df['Date']).dt.date >= mdr[0]) & (pd.to_datetime(df['Date']).dt.date <= mdr[1])
                f_df = df[mask]
                links = f_df['PDF Link'].tolist()
                valid_links = [l for l in links if str(l).startswith('http')]
                if valid_links:
                    with st.spinner(f"Merging {len(valid_links)} PDFs..."):
                        try:
                            merged_bytes = merge_pdfs(valid_links)
                            st.download_button("Download Merged Bundle", merged_bytes, file_name="Merged_Debit_Notes.pdf", mime="application/pdf")
                        except Exception as e: st.error(f"Merge failed: {e}")
                else: st.warning("No PDF links found.")
            card_end()

    # --- MY PROFILE (LOCAL STORAGE) ---
    elif sel == "My Profile":
        st.title("My Profile"); card_start()
        
        # Display Current Photo (from local)
        dp_path = safe_profile_display(st.session_state.get('user_pic'))
        if dp_path:
             st.image(dp_path, width=200, caption="Current Profile Picture")
        else:
             st.markdown(f'<div style="display:flex;justify-content:center;font-size:100px;">👤</div>', unsafe_allow_html=True)
        
        st.markdown(f"<h2 style='text-align: center;'>{st.session_state['username']}</h2>", unsafe_allow_html=True)
        st.divider()

        # Step 1: Upload & Crop
        st.write("📸 **Update Profile Photo**")
        pic_file = st.file_uploader("Upload New Image", type=['jpg', 'png'])
        cropped_img = None
        if pic_file:
            st.caption("Adjust box to crop face:")
            cropped_img = st_cropper(Image.open(pic_file), aspect_ratio=1, boxColor='#0000FF', key='crop')
            st.caption("Preview:")
            st.image(cropped_img, width=150)
        
        st.divider()

        # Step 2: Edit Text Details
        st.write("✏️ **Edit Details**")
        new_user = st.text_input("Username", value=st.session_state['username'])
        new_pass = st.text_input("New Password", type="password")

        # Step 3: Save Button
        if st.button("💾 Save Profile Changes"):
            new_pic_filename = None
            if cropped_img:
                # Save Local with UUID
                new_pic_filename = save_profile_pic_local(cropped_img, st.session_state.get('user_pic'))
            
            # Update DB (Stores FILENAME, not URL)
            if db_update_user(st.session_state['username'], new_user, new_pass, new_pic_filename):
                st.success("Updated Successfully!"); time.sleep(2); st.session_state['auth'] = False; st.query_params.clear(); st.rerun()
            else: st.error("Update Failed")
        
        card_end()

    # --- RAISE DEBIT NOTE ---
    elif sel == "Raise Debit Note":
        st.title("Raise Debit Note"); card_start()
        
        st.write("🎙️ **Voice Description**")
        audio = mic_recorder(start_prompt="Click to Speak", stop_prompt="Stop Recording", key='recorder', format='wav')
        if audio: 
            with st.spinner("Transcribing..."):
                text = transcribe_audio(audio['bytes'])
                st.session_state['voice_text'] = text
                if "Error" in text or "Could not" in text: st.warning(text)
                else: st.success("Captured!")

        with st.form("dn_form"):
            cons = db_get("Contractors"); c_list = cons['Name'].tolist() if not cons.empty else []
            c1, c2 = st.columns(2); con = c1.selectbox("Contractor", c_list); dt = c2.date_input("Date")
            c3, c4 = st.columns(2); cat = c3.selectbox("Category", REASON_CATEGORIES); amt = c4.number_input("Amount (INR)", min_value=0.0, key="dn_amt")
            site = st.text_input("Site Location", key="dn_site"); reason = st.text_area("Reason", value=st.session_state.get('voice_text', ''), key="dn_reason")
            
            st.markdown("---"); st.write("**📸 Photos**")
            if st.session_state['cam_buffer']:
                cols = st.columns(min(len(st.session_state['cam_buffer']), 4) or 1)
                for idx, img_bytes in enumerate(st.session_state['cam_buffer']):
                    if idx < 4: cols[idx].image(img_bytes, width=100)
                if st.form_submit_button("Clear Photos"): st.session_state['cam_buffer'] = []; st.rerun()
            cam_img = st.camera_input("Take Photo", key=f"camera_{st.session_state['cam_counter']}")
            if cam_img: st.session_state['cam_buffer'].append(cam_img.getvalue()); st.session_state['cam_counter'] += 1; st.rerun()
            files = st.file_uploader("Or Upload", accept_multiple_files=True, key=f"uploader_{st.session_state['uploader_key']}")
            
            st.markdown("---"); st.write("**✍️ Signature**"); sig_file = st.file_uploader("Upload Sig", type=['png', 'jpg'], key="sig_up")
            
            if st.form_submit_button("Submit & Email"):
                imgs, links = [], []
                for b in st.session_state['cam_buffer']: cp = compress_image(b); imgs.append(cp); links.append(upload_to_drive(cp, "cam.jpg", "image/jpeg"))
                if files:
                    for f in files: cp = compress_image(f); imgs.append(cp); links.append(upload_to_drive(cp, f.name, "image/jpeg"))
                sig_path = compress_image(sig_file) if sig_file else None
                data = {"contractor": con, "date": str(dt), "amount": amt, "category": cat, "reason": reason, "site": site, "local_img_paths": imgs, "signature_path": sig_path}
                pdf_path = create_pdf("receipt", data); pdf_link = upload_to_drive(pdf_path, os.path.basename(pdf_path), "application/pdf")
                db_insert("DebitNotes", [int(datetime.now().timestamp()), con, str(dt), amt, cat, reason, site, ",".join(links), pdf_link, st.session_state['username']])
                con_row = cons[cons['Name'] == con]
                if not con_row.empty and 'Email' in con_row.columns and str(con_row.iloc[0]['Email']) != "":
                    send_email_with_pdf([con_row.iloc[0]['Email']], f"Debit Note - {con}", f"Debit Note Raised (INR {amt})", pdf_path); st.toast("Email sent")
                st.session_state['cam_buffer'] = []; reset_form(); time.sleep(1); st.rerun()
        card_end()

    # --- ADMIN PAGES ---
    elif sel == "Contractors" and st.session_state['role'] == "Admin":
        st.title("Contractors"); c1, c2 = st.columns([1, 2])
        with c1:
            with st.form("add_c"):
                n = st.text_input("Name"); e = st.text_input("Email"); d = st.text_input("Details")
                if st.form_submit_button("Add"): db_insert("Contractors", [int(datetime.now().timestamp()), n, d, e]); st.rerun()
        with c2: st.dataframe(db_get("Contractors"), use_container_width=True)

    elif sel == "User Management" and st.session_state['role'] == "Admin":
        st.title("Users"); c1, c2 = st.columns(2)
        with c1:
            with st.form("add_u"):
                u = st.text_input("User"); p = st.text_input("Pass", type="password"); r = st.selectbox("Role", ["Engineer", "Admin"])
                if st.form_submit_button("Add"): db_insert("Users", [u, p, r]); st.rerun()
        with c2: st.dataframe(db_get("Users"), use_container_width=True)

if __name__ == "__main__":
    main()