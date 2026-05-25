import streamlit as st
import pandas as pd
import os
import re
from google.oauth2.service_account import Credentials
import gspread

st.set_page_config(layout="wide", page_title="ระบบติดตาม Safety Stock คลังพัสดุ")

st.title("🔍 ตรวจสอบโครงสร้างไฟล์ SAP MB52 (Debug Mode)")

# 🔥 ระบุชื่อไฟล์ Google Sheets ที่อยู่บน Google Drive
GOOGLE_SHEET_NAME = "pea_safety_stock_db"

def get_gspread_client():
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        secret_creds = st.secrets["gcp_service_account"]
        creds = Credentials.from_service_account_info(secret_creds, scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ Secrets Error: {e}")
        return None

# ตรวจสอบไฟล์เกณฑ์หลัก
for file in os.listdir('.'):
    if (file.endswith('.txt') or file.endswith('.csv')) and not file.startswith('requirements'):
        try:
            with open(file, 'r', encoding='utf-8') as f:
                first = f.readline()
            if 'SAP_Code' in first:
                st.sidebar.success(f"📂 ตรวจพบไฟล์เกณฑ์: `{file}`")
        except: pass

uploaded_mb52 = st.sidebar.file_uploader("ลากวางไฟล์รายงานคงเหลือเพื่อตรวจสอบโครงสร้าง")

if uploaded_mb52 is not None:
    raw_bytes = uploaded_mb52.getvalue()
    try:
        string_data = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        string_data = raw_bytes.decode("cp874", errors="ignore")
        
    lines = string_data.split('\n')
    
    st.subheader("📋 วิเคราะห์เนื้อหา 15 บรรทัดแรกในไฟล์ของคุณ:")
    for i, line in enumerate(lines[:15]):
        st.code(f"บรรทัดที่ {i+1}: {repr(line)}")
        
    st.subheader("🛠️ ทดลองแกะด้วยระบบแบ่งคำพ้นช่องว่าง (Split Tokens):")
    for i, line in enumerate(lines[:15]):
        if line.strip():
            tokens = line.split()
            st.write(f"บรรทัดที่ {i+1} แกะได้ {len(tokens)} คำ -> {tokens}")
