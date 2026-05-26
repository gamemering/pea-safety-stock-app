import streamlit as st
import pandas as pd
import os
import re
import io
import traceback
from google.oauth2.service_account import Credentials
import gspread
import requests
import json

st.set_page_config(layout="wide", page_title="ระบบติดตาม Safety Stock")
st.title("📦 ระบบติดตาม Safety Stock คลังพัสดุ")

GOOGLE_SHEET_NAME = "pea_safety_stock_db"
SAFETY_STOCK_SHEET = "safety_stock_master"

# --- ฟังก์ชันพื้นฐาน ---
def get_gspread_client():
    try:
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], 
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ เชื่อมต่อ Google Sheets ไม่สำเร็จ: {e}")
        return None

def safe_update_sheet(sheet, df):
    # ปัดกวาดข้อมูล: แปลงเป็น string, เติม ' หน้าค่าที่Sheetsอาจมองเป็นสูตร
    df_clean = df.astype(str).replace(['nan', 'NaN', 'None', '<NA>', 'inf', '-inf'], '')
    data = [df_clean.columns.tolist()] + df_clean.values.tolist()
    final_data = [[f"'{val}" if str(val).startswith(('=', '+', '-')) else val for val in row] for row in data]
    
    sheet.clear()
    sheet.resize(rows=max(len(final_data), 100), cols=max(len(final_data[0]), 10))
    try:
        sheet.update(range_name='A1', values=final_data, value_input_option='USER_ENTERED')
    except TypeError:
        sheet.update(final_data, 'A1', value_input_option='USER_ENTERED')

def to_int_safe(val):
    try: return int(float(str(val).replace(',', '').strip()))
    except: return 0

# --- แกะ MB52 ---
def parse_mb52_txt(file_content):
    material_data = {}
    current_material = None
    for line in file_content.split('\n'):
        line = line.strip()
        if not line: continue
        if line.startswith('|'): line = line[1:].strip()
        match_mat = re.match(r'^(\d-\d{2}-\d{3}-\d{4})', line)
        if match_mat:
            current_material = match_mat.group(1).replace('-', '')
            if current_material not in material_data:
                material_data[current_material] = {'Actual_Qty': 0.0, 'Qty_0021': 0.0}
            continue
        tokens = line.split()
        if len(tokens) >= 4 and tokens[3] in ['EA', 'KG', 'M', 'PAC', 'L']:
            try:
                qty = float(tokens[2].replace(',', ''))
                material_data[current_material]['Actual_Qty'] += qty
                if tokens[0].strip() == '0021':
                    material_data[current_material]['Qty_0021'] += qty
            except: pass
    return pd.DataFrame([[k, v['Actual_Qty'], v['Qty_0021']] for k, v in material_data.items()], columns=['SAP_Code', 'Actual_Qty', 'Qty_0021'])

# --- 1. จัดการฐานข้อมูล Safety Stock (Sidebar) ---
st.sidebar.header("📋 ฐานข้อมูลเกณฑ์")
uploaded_safety = st.sidebar.file_uploader("อัปโหลดไฟล์ Excel Safety Stock", type=['xlsx', 'xls', 'csv'])
if uploaded_safety:
    try:
        if uploaded_safety.name.endswith('.csv'): df_safety = pd.read_csv(uploaded_safety)
        else: df_safety = pd.read_excel(uploaded_safety)
        
        client = get_gspread_client()
        if client:
            ws = client.open(GOOGLE_SHEET_NAME).worksheet(SAFETY_STOCK_SHEET)
            safe_update_sheet(ws, df_safety)
            st.sidebar.success("✅ อัปเดตฐานข้อมูล Safety Stock สำเร็จ!")
            st.rerun()
    except Exception as e: st.sidebar.error(f"❌ อัปโหลดไฟล์เกณฑ์ไม่สำเร็จ: {e}")

# --- 2. เลือกคลัง และ อัปโหลด MB52 ---
client = get_gspread_client()
if client:
    try:
        df_safety = pd.DataFrame(client.open(GOOGLE_SHEET_NAME).worksheet(SAFETY_STOCK_SHEET).get_all_records())
        warehouse_options = [c for c in df_safety.columns if c.startswith('C') and c[1:].isdigit()]
        
        st.sidebar.markdown("---")
        st.sidebar.header("⚙️ ตรวจสอบคลัง")
        selected_wh = st.sidebar.selectbox("เลือกคลังที่ต้องการตรวจสอบ:", options=warehouse_options)
        
        st.sidebar.markdown("---")
        st.sidebar.subheader(f"📥 อัปโหลด MB52: {selected_wh}")
        uploaded_mb52 = st.sidebar.file_uploader(f"ไฟล์สำหรับคลัง {selected_wh}", key="mb52_upload")
        
        if uploaded_mb52:
            df_parsed = parse_mb52_txt(uploaded_mb52.getvalue().decode("utf-8", errors="ignore"))
            ws = client.open(GOOGLE_SHEET_NAME).worksheet(selected_wh)
            safe_update_sheet(ws, df_parsed)
            st.sidebar.success(f"🚀 บันทึกข้อมูล {selected_wh} เรียบร้อย!")
            st.rerun()

        # --- แสดงผลเปรียบเทียบ ---
        st.write(f"### 📊 ข้อมูลเปรียบเทียบ: {selected_wh}")
        df_mb52 = pd.DataFrame(client.open(GOOGLE_SHEET_NAME).worksheet(selected_wh).get_all_records())
        
        # Merge & Compare
        df_merge = pd.merge(df_safety[['SAP_Code', 'Description', selected_wh]], df_mb52, on='SAP_Code', how='left')
        df_merge['Qty_0021'] = df_merge['Qty_0021'].fillna(0).astype(int)
        df_merge['Safety_Stock'] = pd.to_numeric(df_merge[selected_wh], errors='coerce').fillna(0).astype(int)
        df_merge['คงเหลือ'] = df_merge['Qty_0021'] - df_merge['Safety_Stock']
        
        # Highlight
        def highlight_shortage(val):
            return 'background-color: #ffcccc' if val < 0 else ''
            
        st.dataframe(df_merge.style.applymap(highlight_shortage, subset=['คงเหลือ']), use_container_width=True)
        
    except Exception as e:
        st.info("⚠️ กรุณาอัปโหลดฐานข้อมูล Safety Stock ที่แถบด้านซ้ายก่อน")
        with st.expander("ดู Error เชิงลึก"):
            st.code(traceback.format_exc())
