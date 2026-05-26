import streamlit as st
import pandas as pd
import os
import re
from google.oauth2.service_account import Credentials
import gspread
import requests
import json

st.set_page_config(layout="wide", page_title="ระบบติดตาม Safety Stock คลังพัสดุ")

st.title("📦 ระบบแจ้งเตือนและติดตามเกณฑ์พัสดุสำรอง (Safety Stock)")
st.subheader("เปรียบเทียบเกณฑ์อนุมัติประจำปี 2569 กับ ยอดคงคลังปัจจุบัน (MB52)")

GOOGLE_SHEET_NAME = "pea_safety_stock_db"

def get_gspread_client():
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        secret_creds = st.secrets["gcp_service_account"]
        creds = Credentials.from_service_account_info(secret_creds, scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ ระบบความปลอดภัยปฏิเสธการเชื่อมต่อ (Secrets Error): {e}")
        return None

def send_line_message(message_text, target_id):
    try:
        url = "https://api.line.me/v2/bot/message/push"
        token = st.secrets["line_channel_access_token"]
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        payload = {"to": target_id, "messages": [{"type": "text", "text": message_text}]}
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        return response.status_code
    except Exception as e:
        return str(e)

# --- 🆕 ส่วนการอัปโหลดไฟล์ Safety Stock ใหม่ ---
st.sidebar.header("📤 อัปโหลดไฟล์ Safety Stock (CSV)")
uploaded_safety = st.sidebar.file_uploader("ลากไฟล์เกณฑ์ .csv มาวางที่นี่", type=['csv'])

if uploaded_safety is not None:
    df_new = pd.read_csv(uploaded_safety)
    client = get_gspread_client()
    if client:
        sh = client.open(GOOGLE_SHEET_NAME)
        try:
            ws = sh.worksheet("Safety_Stock_Master")
        except:
            ws = sh.add_worksheet(title="Safety_Stock_Master", rows="1000", cols="20")
        ws.clear()
        ws.update([df_new.columns.tolist()] + df_new.values.tolist())
        st.sidebar.success("✅ อัปโหลดเกณฑ์ Safety Stock ลง Google Sheets สำเร็จ!")

# --- โหลดข้อมูล Safety Stock จาก Google Sheets (แทน GitHub) ---
def load_safety_stock():
    client = get_gspread_client()
    if client:
        try:
            return pd.DataFrame(client.open(GOOGLE_SHEET_NAME).worksheet("Safety_Stock_Master").get_all_records())
        except:
            return None
    return None

df_safety = load_safety_stock()

# --- ส่วนที่ 2: ฟังก์ชันสำหรับแกะเนื้อหาไฟล์ MB52 ของ SAP ---
def parse_mb52_txt(file_content):
    material_data = {}
    current_material = None
    lines = file_content.split('\n')
    for line in lines:
        line = line.strip()
        if not line: continue
        if line.startswith('|'): line = line[1:].strip()
        match_mat = re.match(r'^(\d-\d{2}-\d{3}-\d{4})', line)
        if match_mat:
            current_material = match_mat.group(1).replace('-', '')
            if current_material not in material_data:
                material_data[current_material] = {'Total_Qty': 0.0, 'Qty_0021': 0.0}
            continue
        tokens = line.split()
        if len(tokens) >= 4 and tokens[3] in ['EA', 'KG', 'M', 'PAC', 'L']:
            sloc_id = tokens[0].strip()
            try:
                qty = float(tokens[2].replace(',', ''))
                if current_material:
                    material_data[current_material]['Total_Qty'] += qty
                    if sloc_id == '0021':
                        material_data[current_material]['Qty_0021'] += qty
            except: pass
    return pd.DataFrame([[k, v['Total_Qty'], v['Qty_0021']] for k, v in material_data.items()], columns=['SAP_Code', 'Actual_Qty', 'Qty_0021'])

# เริ่มรันหน้าเว็บเมื่อมีฐานข้อมูล Safety Stock
if df_safety is not None:
    st.sidebar.success("📂 โหลดฐานข้อมูล Safety Stock จาก Google Sheets สำเร็จ")
    warehouse_options = [col for col in df_safety.columns if col not in ['No', 'Type', 'SAP_Code', 'Description', 'Unit', 'Total_N3']]
    warehouse_option = st.sidebar.selectbox("เลือกคลังที่ต้องการตรวจสอบ:", options=warehouse_options)
    
    upload_target = st.sidebar.selectbox("เลือกคลังปลายทางเพื่อบันทึก:", options=warehouse_options)
    uploaded_mb52 = st.sidebar.file_uploader(f"ลากวางไฟล์ MB52.txt ของคลัง [{upload_target}]")

    if uploaded_mb52:
        df_parsed = parse_mb52_txt(uploaded_mb52.getvalue().decode("utf-8", errors="ignore"))
        client = get_gspread_client()
        sh = client.open(GOOGLE_SHEET_NAME)
        ws = sh.worksheet(upload_target)
        ws.clear()
        ws.update([df_parsed.columns.tolist()] + df_parsed.values.tolist())
        st.sidebar.success(f"🚀 บันทึกข้อมูล {upload_target} สำเร็จ!")

    # ส่วนแสดงตาราง
    st.write(f"📊 กำลังแสดงยอดเปรียบเทียบคลัง: **{warehouse_option}**")
    client = get_gspread_client()
    try:
        df_mb52 = pd.DataFrame(client.open(GOOGLE_SHEET_NAME).worksheet(warehouse_option).get_all_records())
        df_merge = pd.merge(df_safety, df_mb52, on='SAP_Code', how='left')
        st.dataframe(df_merge, use_container_width=True)
    except:
        st.info("📊 รอการอัปโหลดข้อมูล MB52")
else:
    st.error("⚠️ ยังไม่พบเกณฑ์อ้างอิง Safety Stock ในระบบคลาวด์ กรุณาลากวางไฟล์เกณฑ์จริง (.csv) ที่แถบควบคุมด้านซ้ายก่อนเพื่อเปิดใช้งานหน้าเว็บหลัก")
