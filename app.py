import streamlit as st
import pandas as pd
import os
import re
from google.oauth2.service_account import Credentials
import gspread

st.set_page_config(layout="wide", page_title="ระบบติดตาม Safety Stock คลังพัสดุ")

st.title("📦 ระบบแจ้งเตือนและติดตามเกณฑ์พัสดุสำรอง (Safety Stock)")
st.subheader("เปรียบเทียบเกณฑ์อนุมัติประจำปี 2569 กับ ยอดคงคลังปัจจุบัน (MB52)")

# 🔥 ระบุชื่อไฟล์ Google Sheets ที่อยู่บน Google Drive
GOOGLE_SHEET_NAME = "pea_safety_stock_db"

# --- ฟังก์ชันสำหรับเชื่อมต่อ Google Sheets ผ่านคีย์ลับใน Secrets ---
def get_gspread_client():
    try:
        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        secret_creds = st.secrets["gcp_service_account"]
        creds = Credentials.from_service_account_info(secret_creds, scopes=scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"❌ ระบบความปลอดภัยปฏิเสธการเชื่อมต่อ (Secrets Error): {e}")
        return None

# --- ส่วนที่ 1: ค้นหาไฟล์เกณฑ์ Safety Stock อัตโนมัติในโฟลเดอร์ GitHub ---
def find_safety_stock_file():
    for file in os.listdir('.'):
        if file.endswith('.txt') or file.endswith('.csv'):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    first_line = f.readline()
                if 'SAP_Code' in first_line and 'Total_N3' in first_line:
                    return file
            except Exception:
                continue
    return None

detected_file = find_safety_stock_file()

@st.cache_data
def load_safety_stock_from_file(file_path):
    if file_path is not None:
        try:
            df = pd.read_csv(file_path, encoding='utf-8')
            return df
        except Exception as e:
            st.error(f"❌ เกิดข้อผิดพลาดในการอ่านไฟล์เกณฑ์: {e}")
            return None
    return None

df_safety = load_safety_stock_from_file(detected_file)

# --- ส่วนที่ 2: ฟังก์ชันสำหรับแกะเนื้อหาไฟล์ MB52 ของ SAP + 🛠️ ฝังตัวดักจับ Debug ---
def parse_mb52_txt(file_content):
    material_data = {}
    lines = file_content.split('\n')
    
    # 🔍 สร้างกล่องดักจับ Log ชั่วคราวไปแสดงผลบนหน้าเว็บ
    debug_logs = []
    lines_checked = 0
    
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
            
        tokens = line_clean.split()
        
        # เก็บ Log เฉพาะ 10 บรรทัดแรกที่มีข้อมูลมาวิเคราะห์
        if lines_checked < 10:
            debug_logs.append(f"บรรทัดที่ {lines_checked+1} แยกได้ {len(tokens)} ก้อน -> คำแรก: '{tokens[0] if len(tokens)>0 else 'ไม่มี'}'")
            lines_checked += 1
            
        if len(tokens) >= 4:
            raw_code = tokens[0].replace('-', '').strip()
            
            if re.match(r'^\d+$', raw_code):
                sloc_id = tokens[1].strip()
                qty_str = tokens[3].replace(',', '').strip()
                try:
                    qty = float(qty_str)
                    if raw_code not in material_data:
                        material_data[raw_code] = {'Total_Qty': 0.0, 'Qty_0021': 0.0}
                    material_data[raw_code]['Total_Qty'] += qty
                    if sloc_id == '0021':
                        material_data[raw_code]['Qty_0021'] += qty
                except ValueError:
                    pass
                    
    # พ่นค่า Log ออกมาประจานหน้าเว็บชั่วคราว
    st.write("---")
    st.subheader("⚙️ รายงานการวิเคราะห์จากตัว Debug (ฝังชั่วคราว)")
    st.info(f"ระบบอ่านข้อมูลพัสดุสำเร็จเข้าตารางได้ทั้งหมด: {len(material_data)} รายการ")
    with st.expander("คลิกเพื่อดูโครงสร้างก้อนคำ 10 บรรทัดแรกที่โค้ดอ่านได้จริง"):
        for log in debug_logs:
            st.text(log)
    st.write("---")
                    
    parsed_list = []
    for k, v in material_data.items():
        parsed_list.append([k, v['Total_Qty'], v['Qty_0021']])
        
    df_parsed = pd.DataFrame(parsed_list, columns=['SAP_Code', 'Actual_Qty', 'Qty_0021'])
    return df_parsed


# เริ่มรันหน้าเว็บเมื่อไฟล์เกณฑ์พัสดุพร้อมใช้งาน
if df_safety is not None:
    st.sidebar.success(f"📂 ตรวจพบไฟล์เกณฑ์อัตโนมัติ: `{detected_file}`")
    
    st.
