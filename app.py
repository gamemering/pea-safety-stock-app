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

# --- ฟังก์ชันสำหรับส่งข้อความผ่าน LINE Messaging API ---
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
        return None

# --- ส่วนที่ 2: ฟังก์ชันสำหรับแกะเนื้อหาไฟล์ MB52 ของ SAP (Text Parser ตัวหลักที่คุณรันผ่าน) ---
def parse_mb52_txt(file_content):
    material_data = {}
    current_material = None
    lines = file_content.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if line.startswith('|'):
            line = line[1:].strip()
            
        match_mat = re.match(r'^(\d-\d{2}-\d{3}-\d{4})', line)
        if match_mat:
            raw_code = match_mat.group(1)
            current_material = raw_code.replace('-', '')
            if current_material not in material_data:
                material_data[current_material] = {'Total_Qty': 0.0, 'Qty_0021': 0.0}
            continue
            
        tokens = line.split()
        if len(tokens) >= 4:
            if tokens[3] in ['EA', 'KG', 'M', 'PAC', 'L']:
                sloc_id = tokens[0].strip()
                qty_str = tokens[2].replace(',', '')
                try:
                    qty = float(qty_str)
                    if current_material:
                        material_data[current_material]['Total_Qty'] += qty
                        if sloc_id == '0021':
                            material_data[current_material]['Qty_0021'] += qty
                except ValueError:
                    pass
                    
    parsed_list = []
    for k, v in material_data.items():
        parsed_list.append([k, v['Total_Qty'], v['Qty_0021']])
        
    df_parsed = pd.DataFrame(parsed_list, columns=['SAP_Code', 'Actual_Qty', 'Qty_0021'])
    return df_parsed


# เริ่มรันหน้าเว็บเมื่อไฟล์เกณฑ์พัสดุพร้อมใช้งาน
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
        f"ลากวางไฟล์ MB52.txt ของคลัง [{upload_target}] ที่นี่", 
        key=f"uploader_{upload_target}"
    )

    # 💾 ตรรกะการบันทึกข้อมูลลง Google Sheets ถาวร + 📱 ระบบส่ง LINE อัตโนมัติหลังบันทึกเสร็จ
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
                        
                        st.sidebar.success(f"🚀 บันทึกข้อมูลคลัง **{upload_target}** ลง Google Sheets สำเร็จ!")
                        st.cache_data.clear() 
                        
                        # --- 📱 ระบบวิเคราะห์และยิงไลน์กลุ่มอัตโนมัติเบื้องหลังทันที (ซ่อมแซมตรรกะผลต่างให้เสถียร) ---
                        if "line_group_id" in st.secrets:
                            df_safety_line = df_safety.copy()
                            df_parsed_line = df_parsed.copy()
                            
                            df_safety_line['SAP_Code'] = df_safety_line['SAP_Code'].astype(str).str.strip()
                            df_parsed_line['SAP_Code'] = df_parsed_line['SAP_Code'].astype(str).str.strip()
                            
                            # ปรับมาใช้ตรรกะเชื่อมตารางตรง ๆ ตามรูปแบบของส่วนที่ 4 หน้าเว็บแอป
                            df_merge_auto = pd.merge(df_safety_line, df_parsed_line, on='SAP_Code', how='left')
                            
                            # ล้างปัญหาฟอร์แมตตัวแปร บังคับแปลงเป็นตัวเลขเพื่อความแม่นยำในการคำนวณผลต่าง
                            df_merge_auto['Qty_0021'] = pd.to_numeric(df_merge_auto['Qty_0021'], errors='coerce').fillna(0)
                            df_merge_auto[upload_target] = pd.to_numeric(df_merge_auto[upload_target], errors='coerce').fillna(0)
                            
                            # คำนวณหาค่าผลต่างติดลบ (ของขาดแคลน)
                            df_merge_auto['คงเหลือ_0021'] = df_merge_auto['Qty_0021'] - df_merge_auto[upload_target]
                            df_shortage_auto = df_merge_auto[df_merge_auto['คงเหลือ_0021'] < 0]
                            
                            if not df_shortage_auto.empty:
                                total_shortage = len(df_shortage_auto)
                                line_msg = f"🚨 [รายงานแจ้งเตือนพัสดุต่ำกว่าเกณฑ์ Safety Stock]\n📊 พื้นที่คลังพัสดุ: {upload_target}\n⚠️ ตรวจพบรายการวิกฤตทั้งหมด: {total_shortage} รายการ\n\n📌 รายการพัสดุวิกฤตและยอดผลต่างที่ขาดคลัง:\n"
                                
                                # วนลูปสกัดข้อมูลผลต่างพัสดุวิกฤตเพื่อเตรียมยิงเข้า LINE กลุ่ม
                                for idx, row in enumerate(df_shortage_auto.iterrows(), 1):
                                    data = row[1]
                                    current_0021 = int(data['Qty_0021'])
                                    limit_stock = int(data[upload_target])
                                    needed_qty = limit_stock - current_0021
                                    
                                    line_msg += f"{idx}. รหัส: {data['SAP_Code']}\n"
                                    line_msg += f"   {data['Description']}\n"
                                    line_msg += f"   ยอดคลังย่อย: {current_0021} | เกณฑ์อนุมัติ: {limit_stock}\n"
                                    line_msg += f"   ❌ ผลต่าง (ขาดอีก): {needed_qty}\n"
                                    line_msg += "----------------------------------\n"
                                    
                                    # ป้องกันการยิงตัวอักษรเกินโควตา 5,000 ตัวของ LINE (ถ้าของขาดเยอะเกิน 25 รายการ ให้ตัดสรุปยอดท้ายข้อความ)
                                    if idx >= 25:
                                        line_msg += f"🔺 และยังมีรายการอื่น ๆ ที่ต่ำกว่าเกณฑ์อีก {total_shortage - 25} รายการ ตรวจสอบเพิ่มเติมได้บนระบบหน้าเว็บครับ\n"
                                        break
                                        
                                status_code = send_line_message(line_msg, st.secrets["line_group_id"])
                                if status_code == 200:
                                    st.sidebar.success("📱 ส่งสัญญาณแจ้งเตือนและรายการผลต่างเข้า LINE แล้ว!")
                                else:
                                    st.sidebar.warning(f"⚠️ บันทึกสำเร็จ แต่ไลน์ไม่ส่ง (LINE API Code: {status_code})")
            else:
                st.sidebar.warning("⚠️ ไม่พบข้อมูลพัสดุในไฟล์ที่อัปโหลด")
        except Exception as e:
            st.sidebar.error(f"❌ เกิดข้อผิดพลาดในการบันทึกข้อมูลลงแผ่นงาน: {e}")


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

    # เคลียร์ปัญหาเกณฑ์พัสดุใน GitHub มีค่าว่างเปล่า (NaN) ป้องกันการพังในทุก ๆ จุดคำนวณ
    df_safety[warehouse_option] = df_safety[warehouse_option].fillna(0)

    if df_mb52_clean is not None and not df_mb52_clean.empty:
        df_safety['SAP_Code'] = df_safety['SAP_Code'].astype(str).str.strip()
        df_mb52_clean['SAP_Code'] = df_mb52_clean['SAP_Code'].astype(str).str.strip()

        df_merge = pd.merge(df_safety, df_mb52_clean, on='SAP_Code', how='left')
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
        
        shortage_0021 = len(df_result[df_result['คงเหลือ (ผลต่าง 0021)'] < 0])
        
        if shortage_0021 > 0:
            st.error(f"🚨 สถานะคลัง **{warehouse_option}**: ตรวจพบพัสดุในคลังย่อย 0021 ต่ำกว่าเกณฑ์ความปลอดภัยจำนวน **{shortage_0021}** รายการ!")
        else:
            st.success(f"✅ พัสดุทั้งหมดในคลังย่อย 0021 ของคลัง **{warehouse_option}** อยู่ในระดับที่ปลอดภัยครบถ้วน")
            
    else:
        st.info(f"📊 ยังไม่มีฐานข้อมูลถาวรของคลัง **{warehouse_option}** ใน Google Sheets (กรุณาเลือกคลังปลายทางด้านซ้ายและอัปโหลดไฟล์ MB52 เพื่อตั้งต้นข้อมูล)")
        
        df_blank = pd.DataFrame()
        df_blank['ลำดับ'] = df_safety['No']
        df_blank['รหัสพัสดุ'] = df_safety['SAP_Code']
        df_blank['ชื่อพัสดุ'] = df_safety['Description']
        df_blank['หน่วยนับ'] = df_safety['Unit']
        df_blank['อนุมัติ safety stock'] = df_safety[warehouse_option].fillna(0).astype(int)
        st.dataframe(df_blank.style.format({'อนุมัติ safety stock': '{:,}'}), use_container_width=True, hide_index=True)

else:
    st.error("❌ ไม่พบไฟล์ฐานข้อมูลเกณฑ์พัสดุ (Safety Stock) ในระบบ")
