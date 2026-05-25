import streamlit as st
import pandas as pd
import os
import re
import requests
import json
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
        if (file.endswith('.txt') or file.endswith('.csv')) and not file.startswith('requirements'):
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


# --- ส่วนที่ 2: ฟังก์ชันสำหรับส่งข้อความผ่าน LINE Messaging API ---
def send_line_message(message_text, target_id):
    try:
        url = "https://api.line.me/v2/bot/message/push"
        token = st.secrets["line_channel_access_token"]
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        payload = {
            "to": target_id,
            "messages": [
                {
                    "type": "text",
                    "text": message_text
                }
            ]
        }
        
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        return response.status_code
    except Exception as e:
        st.error(f"❌ ระบบไม่สามารถส่งไลน์ได้เนื่องจาก: {e}")
        return None


# --- ส่วนที่ 3: ฟังก์ชันแกะเนื้อหาไฟล์ SAP MB52 (อ้างอิงตามก้อนคำจากการตรวจสอบดักจับ) ---
def parse_mb52_txt(file_content):
    material_data = {}
    lines = file_content.split('\n')
    
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
            
        # ใช้การแบ่งก้อนคำด้วยช่องว่างว่างเปล่า
        tokens = line_clean.split()
        
        # ดักจับบรรทัดพัสดุจริง: ก้อนแรกต้องเป็นตัวเลขรหัสพัสดุ และมีความยาวคำเพียงพอ
        if len(tokens) >= 4:
            raw_mat = tokens[0].replace('-', '').strip() # ล้างขีดกลางกรณีหลุดมา
            
            # ตรวจสอบว่าคำแรกเป็นตัวเลขรหัสพัสดุจาก SAP แน่ ๆ
            if re.match(r'^\d+$', raw_mat):
                raw_sloc = tokens[1].strip()  # คลังย่อย SLoc (ก้อนที่ 2)
                raw_qty = tokens[3].strip()   # ยอดคงคลัง Unrestricted (ก้อนที่ 4)
                
                qty_str = raw_qty.replace(',', '')
                try:
                    qty = float(qty_str)
                except ValueError:
                    qty = 0.0
                    
                if raw_mat not in material_data:
                    material_data[raw_mat] = {'Total_Qty': 0.0, 'Qty_0021': 0.0}
                    
                material_data[raw_mat]['Total_Qty'] += qty
                if raw_sloc == '0021':
                    material_data[raw_mat]['Qty_0021'] += qty
                    
    parsed_list = []
    for k, v in material_data.items():
        parsed_list.append([k, v['Total_Qty'], v['Qty_0021']])
        
    df_parsed = pd.DataFrame(parsed_list, columns=['SAP_Code', 'Actual_Qty', 'Qty_0021'])
    return df_parsed


# เริ่มรันหน้าเว็บหลักเมื่อไฟล์เกณฑ์พัสดุพร้อมใช้งาน
if df_safety is not None:
    st.sidebar.success(f"📂 ตรวจพบไฟล์เกณฑ์อัตโนมัติ: `{detected_file}`")
    
    st.sidebar.header("⚙️ เลือกคลังที่ต้องการตรวจสอบ")
    all_columns = df_safety.columns.tolist()
    warehouse_options = [col for col in all_columns if col not in ['No', 'Type', 'SAP_Code', 'Description', 'Unit', 'Total_N3']]
    
    warehouse_option = st.sidebar.selectbox(
        "เลือกพื้นที่คลังพัสดุเพื่อดูตาราง:",
        options=warehouse_options,
        help="เปลี่ยนตรงนี้เพื่อดูสรุปยอดและผลต่างของแต่ละคลัง"
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("📥 อัปเดตยอดคลังเข้าคลาวด์ถาวร")
    
    upload_target = st.sidebar.selectbox(
        "เลือกคลังปลายทางที่จะบันทึกไฟล์นี้:",
        options=warehouse_options,
        key="upload_target_select"
    )
    
    uploaded_mb52 = st.sidebar.file_uploader(
        f"ลากวางไฟล์รายงานคงเหลือของคลัง [{upload_target}] ที่นี่", 
        key=f"uploader_{upload_target}"
    )

    # 💾 ตรรกะการบันทึกข้อมูลและประมวลผลยิงไลน์กลุ่ม
    if uploaded_mb52 is not None:
        try:
            raw_bytes = uploaded_mb52.getvalue()
            try:
                string_data = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                string_data = raw_bytes.decode("cp874", errors="ignore")
                
            df_parsed = parse_mb52_txt(string_data)
            
            if not df_parsed.empty:
                with st.spinner(f"กำลังอัปเดตฐานข้อมูลถาวรของคลัง {upload_target} ลง Google Sheets..."):
                    client = get_gspread_client()
                    if client is not None:
                        sh = client.open(GOOGLE_SHEET_NAME)
                        try:
                            worksheet = sh.worksheet(upload_target)
                        except gspread.exceptions.WorksheetNotFound:
                            worksheet = sh.add_worksheet(title=upload_target, rows="1000", cols="5")
                        
                        worksheet.clear()
                        data_to_save = [df_parsed.columns.tolist()] + df_parsed.values.tolist()
                        worksheet.update('A1', data_to_save)
                        
                        st.sidebar.success(f"🚀 บันทึกข้อมูลคลัง **{upload_target}** สำเร็จ!")
                        st.cache_data.clear() 
            else:
                st.sidebar.warning("⚠️ ไม่พบข้อมูลโครงสร้างตารางพัสดุในไฟล์ที่อัปโหลด")
        except Exception as e:
            st.sidebar.error(f"❌ เกิดข้อผิดพลาดในการบันทึกข้อมูล: {e}")


    # --- ส่วนที่ 4: การดึงดาต้าจาก Google Sheets มาคำนวณโชว์ผล ---
    st.write(f"📊 กำลังแสดงยอดเปรียบเทียบคลัง: **{warehouse_option}**")
    
    df_mb52_clean = None
    client = get_gspread_client()
    if client is not None:
        try:
            sh = client.open(GOOGLE_SHEET_NAME)
            try:
                worksheet = sh.worksheet(warehouse_option)
                records = worksheet.get_all_records()
                if records:
                    df_mb52_clean = pd.DataFrame(records)
            except gspread.exceptions.WorksheetNotFound:
                df_mb52_clean = None
        except Exception:
            df_mb52_clean = None

    df_safety[warehouse_option] = df_safety[warehouse_option].fillna(0)

    if df_mb52_clean is not None and not df_mb52_clean.empty:
        df_safety['SAP_Code_Clean'] = df_safety['SAP_Code'].astype(str).str.replace('-', '').str.strip()
        df_mb52_clean['SAP_Code_Clean'] = df_mb52_clean['SAP_Code'].astype(str).str.replace('-', '').str.strip()

        df_merge = pd.merge(df_safety, df_mb52_clean, on='SAP_Code_Clean', how='left')
        df_merge['Actual_Qty'] = df_merge['Actual_Qty'].fillna(0)
        df_merge['Qty_0021'] = df_merge['Qty_0021'].fillna(0)

        df_result = pd.DataFrame()
        df_result['ลำดับ'] = df_merge['No']
        df_result['รหัสพัสดุ'] = df_merge['SAP_Code']
        df_result['ชื่อพัสดุ'] = df_merge['Description']
        df_result['จำนวนอุปกรณ์ในคลัง (รวมทุก SLoc)'] = df_merge['Actual_Qty'].round(0).astype(int)
        df_result['จำนวนอุปกรณ์ในคลัง (เฉพาะ 0021)'] = df_merge['Qty_0021'].round(0).astype(int)
        df_result['อนุมัติ safety stock'] = df_merge[warehouse_option].astype(int)
        df_result['คงเหลือ (ผลต่าง 0021)'] = df_result['จำนวนอุปกรณ์ในคลัง (เฉพาะ 0021)'] - df_result['อนุมัติ safety stock']

        def alert_low_stock(val):
            return 'background-color: #ffcccc; color: #cc0000; font-weight: bold;' if val < 0 else ''

        format_dict = {
            'จำนวนอุปกรณ์ในคลัง (รวมทุก SLoc)': '{:,}',
            'จำนวนอุปกรณ์ในคลัง (เฉพาะ 0021)': '{:,}',
            'อนุมัติ safety stock': '{:,}',
            'คงเหลือ (ผลต่าง 0021)': '{:,}'
        }

        styled_df = df_result.style.map(alert_low_stock, subset=['คงเหลือ (ผลต่าง 0021)']).format(format_dict)
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
        
        # กรองรายการที่ของขาด (ติดลบ)
        df_shortage = df_result[df_result['คงเหลือ (ผลต่าง 0021)'] < 0]
        shortage_0021 = len(df_shortage)
        
        if shortage_0021 > 0:
            st.error(f"🚨 Status คลัง **{warehouse_option}**: ตรวจพบพัสดุในคลังย่อย 0021 ต่ำกว่าเกณฑ์ความปลอดภัยจำนวน **{shortage_0021}** รายการ!")
            
            # --- ปุ่มกดสำหรับสั่งยิงไลน์หาผู้บริหารด้วยมือเพื่อความแม่นยำ ---
            st.markdown("### 📱 ระบบส่งรายงานเข้า LINE ผู้บริหาร")
            target_chat_id = st.text_input("กรอก LINE Group ID หรือ User ID ปลายทางที่ต้องการส่งข้อมูล:", value="", help="ระบุไอดีกลุ่มที่ได้จาก Webhook หรือระบบบอต")
            
            if st.button("🚀 ส่งรายงานพัสดุวิกฤตเข้า LINE ทันที"):
                if not target_chat_id:
                    st.warning("⚠️ กรุณากรอกรหัส ID ปลายทางก่อนกดส่งครับ")
                else:
                    # สร้างข้อความรายงานพัสดุขาด
                    line_msg = f"🚨 [รายงานพัสดุต่ำกว่าเกณฑ์ความปลอดภัย]\n📊 พื้นที่คลัง: {warehouse_option}\n\n📌 รายการพัสดุวิกฤต:\n"
                    for idx, row in enumerate(df_shortage.itertuples(), 1):
                        needed = abs(row._7) # จำนวนที่ขาดคลัง
                        line_msg += f"{idx}. รหัส: {row.รหัสพัสดุ}\n   {row.ชื่อพัสดุ}\n   ยอดคลังย่อย: {row._5} | เกณฑ์: {row.อนุมัติ_safety_stock}\n   ❌ ขาดอีก: {needed}\n"
                        line_msg += "----------------------------------\n"
                    
                    with st.spinner("กำลังส่งข้อมูลเข้าไลน์กลุ่ม..."):
                        status = send_line_message(line_msg, target_chat_id)
                        if status == 200:
                            st.success("✅ ส่งรายงานข้อมูลพัสดุวิกฤตเข้ากลุ่ม LINE เรียบร้อยแล้ว!")
                        else:
                            st.error(f"❌ ส่งไม่สำเร็จ (รหัสสถานะ: {status}) กรุณาตรวจสอบสิทธิ์บอตในกลุ่ม")
        else:
            st.success(f"✅ พัสดุทั้งหมดในคลังย่อย 0021 ของคลัง **{warehouse_option}** อยู่ในระดับที่ปลอดภัยครบถ้วน")
            
    else:
        st.info(f"📊 ยังไม่มีฐานข้อมูลถาวรของคลัง **{warehouse_option}** ใน Google Sheets (กรุณาเลือกคลังปลายทางด้านซ้ายและอัปโหลดไฟล์รายงานคงเหลือเพื่อตั้งต้นข้อมูล)")
        
        df_blank = pd.DataFrame()
        df_blank['ลำดับ'] = df_safety['No']
        df_blank['รหัสพัสดุ'] = df_safety['SAP_Code']
        df_blank['ชื่อพัสดุ'] = df_safety['Description']
        df_blank['หน่วยนับ'] = df_safety['Unit']
        df_blank['อนุมัติ safety stock'] = df_safety[warehouse_option].fillna(0).astype(int)
        st.dataframe(df_blank.style.format({'อนุมัติ safety stock': '{:,}'}), use_container_width=True, hide_index=True)

else:
    st.error("❌ ไม่พบไฟล์ฐานข้อมูลเกณฑ์พัสดุ (Safety Stock) ในระบบ")
