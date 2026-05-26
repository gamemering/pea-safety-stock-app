import streamlit as st
import pandas as pd
import os
import re
import io
from google.oauth2.service_account import Credentials
import gspread
import requests  # ใช้สำหรับระบบส่ง LINE
import json      # ใช้สำหรับระบบส่ง LINE

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

# --- 📱 ฟังก์ชันสำหรับส่งข้อความผ่าน LINE Messaging API ---
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
        return str(e)

# --- 🛠️ ฟังก์ชันสำหรับแกะเนื้อหาไฟล์เกณฑ์ Safety Stock จริง (แกะฟอร์แมตซ้อนหัวแถว) ---
def parse_safety_stock_csv(file_content):
    try:
        raw_df = pd.read_csv(io.StringIO(file_content), header=None)
        header_row_idx = -1
        
        # ค้นหาแถวที่มีคำว่า 'รหัสพัสดุ' เพื่อตั้งต้นเป็นแถวหัวข้อ
        for idx, row in raw_df.iterrows():
            if any('รหัสพัสดุ' in str(val) for val in row):
                header_row_idx = idx
                break
        
        if header_row_idx == -1:
            return None
            
        row_labels = raw_df.iloc[header_row_idx].tolist()
        row_codes = raw_df.iloc[header_row_idx + 1].tolist()
        
        cols = []
        for i in range(len(row_labels)):
            lbl = str(row_labels[i]).strip()
            code = str(row_codes[i]).strip()
            if 'รหัสพัสดุ' in lbl:
                cols.append('SAP_Code')
            elif 'รายการ' in lbl:
                cols.append('Description')
            elif 'หน่วย' in lbl:
                cols.append('Unit')
            elif 'ที่' in lbl and i == 0:
                cols.append('No')
            elif code.startswith('C') and code[1:].isdigit():  # ดึงเฉพาะรหัสคลัง เช่น C010, C130
                cols.append(code)
            else:
                cols.append(f"Unused_{i}")
                
        raw_df.columns = cols
        data_df = raw_df.iloc[header_row_idx + 2:].copy()
        
        warehouse_cols = [c for c in cols if c.startswith('C') and c[1:].isdigit()]
        valid_cols = ['No', 'SAP_Code', 'Description', 'Unit'] + warehouse_cols
        data_df = data_df[[c for c in valid_cols if c in data_df.columns]]
        
        data_df = data_df.dropna(subset=['SAP_Code'])
        data_df['SAP_Code'] = data_df['SAP_Code'].astype(str).str.replace('.0', '', regex=False).str.strip()
        data_df = data_df[data_df['SAP_Code'] != '']
        
        # ปรับค่า NaN ในทุกคลังให้เป็น 0 เพื่อป้องกันระบบคำนวณพัง
        for col in warehouse_cols:
            data_df[col] = pd.to_numeric(data_df[col], errors='coerce').fillna(0).astype(int)
            
        return data_df
    except Exception as e:
        st.error(f"❌ เกิดข้อผิดพลาดในการประมวลผลไฟล์เกณฑ์พัสดุ: {e}")
        return None

# --- 📥 ฟังก์ชันสำหรับดึงข้อมูลเกณฑ์จาก Google Sheets ---
def load_safety_stock_from_sheets(client):
    try:
        sh = client.open(GOOGLE_SHEET_NAME)
        worksheet = sh.worksheet("Safety_Stock_Criteria")
        records = worksheet.get_all_records()
        if records:
            return pd.DataFrame(records)
    except Exception:
        return None
    return None

# --- ส่วนที่ 2: ฟังก์ชันสำหรับแกะเนื้อหาไฟล์ MB52 ของ SAP (Text Parser) ---
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


# เชื่อมต่อระบบ Sheets ตั้งต้น
client = get_gspread_client()
df_safety = None
if client is not None:
    df_safety = load_safety_stock_from_sheets(client)

# สร้างเมนูด้านซ้าย (Sidebar)
st.sidebar.title("📁 แผงควบคุมฐานข้อมูล")

# --- 🆕 ส่วนอัปโหลดไฟล์เกณฑ์อ้างอิง Safety Stock ใหม่ประจำปี ---
st.sidebar.header("📤 1. อัปเดตเกณฑ์ Safety Stock ประจำปี")
uploaded_safety_file = st.sidebar.file_uploader("ลากวางไฟล์เกณฑ์จริง (.csv) ที่นี่", key="uploader_master_safety")

if uploaded_safety_file is not None:
    try:
        safety_bytes = uploaded_safety_file.getvalue()
        try:
            safety_string = safety_bytes.decode("utf-8")
        except UnicodeDecodeError:
            safety_string = safety_bytes.decode("cp874", errors="ignore")
            
        with st.spinner("กำลังแกะโครงสร้างไฟล์จริงและบันทึกขึ้นระบบคลาวด์ถาวร..."):
            df_safety_parsed = parse_safety_stock_csv(safety_string)
            if df_safety_parsed is not None and not df_safety_parsed.empty:
                if client is not None:
                    sh = client.open(GOOGLE_SHEET_NAME)
                    try:
                        ws_master = sh.worksheet("Safety_Stock_Criteria")
                    except gspread.exceptions.WorksheetNotFound:
                        ws_master = sh.add_worksheet(title="Safety_Stock_Criteria", rows="2000", cols="20")
                    
                    ws_master.clear()
                    master_data_save = [df_safety_parsed.columns.tolist()] + df_safety_parsed.values.tolist()
                    ws_master.update('A1', master_data_save)
                    
                    st.sidebar.success("✅ บันทึกเกณฑ์อ้างอิง Safety Stock ลงระบบคลาวด์ถาวรสำเร็จแล้ว!")
                    st.cache_data.clear()
                    st.rerun()
            else:
                st.sidebar.error("❌ ไฟล์ไม่ถูกต้อง หรือไม่พบหัวข้อรหัสพัสดุตามเงื่อนไข")
    except Exception as e:
        st.sidebar.error(f"❌ เกิดข้อผิดพลาด: {e}")


# เช็คความพร้อมของเกณฑ์อ้างอิงก่อนเริ่มทำงานส่วนที่เหลือ
if df_safety is not None:
    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ 2. เลือกคลังที่ต้องการตรวจสอบ")
    
    # ดึงรายชื่อคลังอัตโนมัติเฉพาะคอลัมน์ที่ขึ้นต้นด้วยตัว C
    warehouse_options = [col for col in df_safety.columns if str(col).startswith('C') and str(col)[1:].isdigit()]
    
    warehouse_option = st.sidebar.selectbox(
        "เลือกพื้นที่คลังพัสดุเพื่อดูตาราง:",
        options=warehouse_options,
        help="เปลี่ยนตรงนี้เพื่อดูสรุปยอดและผลต่างของแต่ละคลัง"
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("📥 3. อัปเดตยอดคลังเข้าคลาวด์ถาวร")
    
    upload_target = st.sidebar.selectbox(
        "เลือกคลังปลายทางที่จะบันทึกไฟล์นี้:",
        options=warehouse_options,
        key="upload_target_select"
    )
    
    uploaded_mb52 = st.sidebar.file_uploader(
        f"ลากวางไฟล์ MB52.txt ของคลัง [{upload_target}] ที่นี่", 
        key=f"uploader_{upload_target}"
    )

    # 💾 ตรรกะการบันทึกข้อมูลคงคลังดิบ + สร้างแผ่นงานสรุปดูง่ายแยกต่างหาก + ส่ง LINE
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
                        
                        # สร้างสถิติข้อมูลวิกฤตแยกเป็นอีกหนึ่ง Sheet
                        df_safety_line = df_safety.copy()
                        df_parsed_line = df_parsed.copy()
                        df_safety_line['SAP_Code'] = df_safety_line['SAP_Code'].astype(str).str.strip()
                        df_parsed_line['SAP_Code'] = df_parsed_line['SAP_Code'].astype(str).str.strip()
                        
                        df_merge_auto = pd.merge(df_safety_line, df_parsed_line, on='SAP_Code', how='left')
                        df_merge_auto['Qty_0021'] = pd.to_numeric(df_merge_auto['Qty_0021'], errors='coerce').fillna(0)
                        df_merge_auto[upload_target] = pd.to_numeric(df_merge_auto[upload_target], errors='coerce').fillna(0)
                        df_merge_auto['คงเหลือ_0021'] = df_merge_auto['Qty_0021'] - df_merge_auto[upload_target]
                        df_shortage_auto = df_merge_auto[df_merge_auto['คงเหลือ_0021'] < 0]
                        
                        summary_ws_title = f"สรุป_{upload_target}"
                        try:
                            summary_worksheet = sh.worksheet(summary_ws_title)
                        except gspread.exceptions.WorksheetNotFound:
                            summary_worksheet = sh.add_worksheet(title=summary_ws_title, rows="500", cols="5")
                        
                        summary_worksheet.clear()
                        if not df_shortage_auto.empty:
                            df_summary_sheet = pd.DataFrame()
                            df_summary_sheet['รหัสพัสดุ'] = df_shortage_auto['SAP_Code']
                            df_summary_sheet['ชื่อพัสดุ'] = df_shortage_auto['Description']
                            df_summary_sheet['ยอดคงคลังย่อย 0021'] = df_shortage_auto['Qty_0021'].astype(int)
                            df_summary_sheet['เกณฑ์ Safety Stock'] = df_shortage_auto[upload_target].astype(int)
                            df_summary_sheet['จำนวนที่ขาด (ผลต่าง)'] = (df_shortage_auto[upload_target] - df_shortage_auto['Qty_0021']).astype(int)
                            
                            summary_data_to_save = [df_summary_sheet.columns.tolist()] + df_summary_sheet.values.tolist()
                            summary_worksheet.update('A1', summary_data_to_save)
                        else:
                            summary_worksheet.update('A1', [["สถานะคลัง", "✅ ปลอดภัยครบถ้วน ไม่มีพัสดุต่ำกว่าเกณฑ์"]])
                        
                        st.sidebar.success(f"📊 อัปเดตแผ่นงานสรุป **{summary_ws_title}** แยกต่างหากเรียบร้อย!")
                        st.cache_data.clear() 
                        
                        # ส่ง LINE แจ้งเตือนอัตโนมัติ
                        if "line_group_id" in st.secrets:
                            if not df_shortage_auto.empty:
                                total_shortage = len(df_shortage_auto)
                                line_msg = f"🚨 [รายงานแจ้งเตือนพัสดุต่ำกว่าเกณฑ์ Safety Stock]\n📊 พื้นที่คลังพัสดุ: {upload_target}\n⚠️ ตรวจพบรายการวิกฤตทั้งหมด: {total_shortage} รายการ\n\n📌 รายการพัสดุวิกฤตและยอดผลต่างที่ขาดคลัง:\n"
                                
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
                                    
                                    if idx >= 15:
                                        line_msg += f"🔺 และยังมีรายการอื่น ๆ ที่ต่ำกว่าเกณฑ์อีก {total_shortage - 15} รายการ ตรวจสอบเพิ่มเติมได้บนระบบหน้าเว็บครับ\n"
                                        break
                                
                                google_sheet_url = sh.url
                                line_msg += f"\n🟢 ผู้บริหารสามารถเปิดดูตารางสรุปฐานข้อมูลทั้งหมดบน Google Sheets ได้ที่ลิงก์ด้านล่างนี้เลยครับ:\n{google_sheet_url}"
                                        
                                status_code = send_line_message(line_msg, st.secrets["line_group_id"])
                                if status_code == 200:
                                    st.sidebar.success("📱 ออโต้ไลน์ส่งสรุปรายงานพร้อมลิงก์เข้ากลุ่มสำเร็จ!")
                                else:
                                    st.sidebar.warning(f"⚠️ ไลน์อัตโนมัติไม่ส่ง (Code: {status_code})")
            else:
                st.sidebar.warning("⚠️ ไม่พบข้อมูลพัสดุในไฟล์ที่อัปโหลด")
        except Exception as e:
            st.sidebar.error(f"❌ เกิดข้อผิดพลาดในการบันทึกข้อมูลลงแผ่นงาน: {e}")

    # --- 🔄 ปุ่มส่งผลสรุปอีกครั้ง พร้อมคำอธิบายสำหรับพนักงาน ---
    st.sidebar.markdown("---")
    st.sidebar.subheader("📢 ส่งรายงานสรุปซ้ำเข้า LINE")
    st.sidebar.info(
        "💡 **คำอธิบายระบบสำหรับพนักงาน:**\n\n"
        "เมื่อมีการลากวางไฟล์เพื่ออัปเดตยอดคงเหลือด้านบน ระบบจะคำนวณและส่งข้อความแจ้งเตือนเข้า LINE กลุ่มผู้บริหารให้โดยอัตโนมัติทันทีอยู่แล้ว\n\n"
        "🚨 **แต่ในกรณีที่ต้องการส่งข้อมูลผลสรุปของคลังพัสดุที่เลือกอยู่ ณ ปัจจุบัน เข้ากลุ่ม LINE อีกครั้ง** "
        "สามารถกดปุ่มด้านล่างนี้เพื่อสั่งส่งซ้ำได้ทันทีครับ"
    )
    
    if st.sidebar.button("🔄 สั่งส่งผลสรุปเข้า LINE อีกครั้ง", key="resend_summary_to_line"):
        with st.spinner(f"กำลังดึงข้อมูลล่าสุดและเตรียมส่งไลน์คลัง {warehouse_option}..."):
            if client is not None:
                try:
                    sh = client.open(GOOGLE_SHEET_NAME)
                    worksheet = sh.worksheet(warehouse_option)
                    records = worksheet.get_all_records()
                    
                    if records:
                        df_mb52_clean_resend = pd.DataFrame(records)
                        df_safety_resend = df_safety.copy()
                        
                        df_safety_resend['SAP_Code'] = df_safety_resend['SAP_Code'].astype(str).str.strip()
                        df_mb52_clean_resend['SAP_Code'] = df_mb52_clean_resend['SAP_Code'].astype(str).str.strip()
                        
                        df_merge_resend = pd.merge(df_safety_resend, df_mb52_clean_resend, on='SAP_Code', how='left')
                        df_merge_resend['Qty_0021'] = pd.to_numeric(df_merge_resend['Qty_0021'], errors='coerce').fillna(0)
                        df_merge_resend[warehouse_option] = pd.to_numeric(df_merge_resend[warehouse_option], errors='coerce').fillna(0)
                        df_merge_resend['คงเหลือ_0021'] = df_merge_resend['Qty_0021'] - df_merge_resend[warehouse_option]
                        
                        df_shortage_resend = df_merge_resend[df_merge_resend['คงเหลือ_0021'] < 0]
                        
                        if "line_group_id" in st.secrets:
                            if not df_shortage_resend.empty:
                                total_shortage = len(df_shortage_resend)
                                line_msg = f"🚨 [ส่งซ้ำ: รายงานแจ้งเตือนพัสดุต่ำกว่าเกณฑ์ Safety Stock]\n📊 พื้นที่คลังพัสดุ: {warehouse_option}\n⚠️ ตรวจพบรายการวิกฤตทั้งหมด: {total_shortage} รายการ\n\n📌 รายการพัสดุวิกฤตและยอดผลต่างที่ขาดคลัง:\n"
                                
                                for idx, row in enumerate(df_shortage_resend.iterrows(), 1):
                                    data = row[1]
                                    current_0021 = int(data['Qty_0021'])
                                    limit_stock = int(data[warehouse_option])
                                    needed_qty = limit_stock - current_0021
                                    
                                    line_msg += f"{idx}. รหัส: {data['SAP_Code']}\n"
                                    line_msg += f"   {data['Description']}\n"
                                    line_msg += f"   ยอดคลังย่อย: {current_0021} | เกณฑ์อนุมัติ: {limit_stock}\n"
                                    line_msg += f"   ❌ ผลต่าง (ขาดอีก): {needed_qty}\n"
                                    line_msg += "----------------------------------\n"
                                    
                                    if idx >= 15:
                                        line_msg += f"🔺 และยังมีรายการอื่น ๆ ที่ต่ำกว่าเกณฑ์อีก {total_shortage - 15} รายการ ตรวจสอบเพิ่มเติมได้บนระบบหน้าเว็บครับ\n"
                                        break
                                
                                google_sheet_url = sh.url
                                line_msg += f"\n🟢 ผู้บริหารสามารถเปิดดูตารางสรุปฐานข้อมูลทั้งหมดบน Google Sheets ได้ที่ลิงก์ด้านล่างนี้เลยครับ:\n{google_sheet_url}"
                                
                                status_code = send_line_message(line_msg, st.secrets["line_group_id"])
                                if status_code == 200:
                                    st.sidebar.success(f"📱 ส่งรายงานคลัง **{warehouse_option}** ซ้ำเข้ากลุ่ม LINE สำเร็จ!")
                                else:
                                    st.sidebar.error(f"❌ ส่งไลน์ไม่สำเร็จ Code: {status_code}")
                            else:
                                line_msg = f"✅ [ส่งซ้ำ: รายงานสถานะคลังพัสดุ]\n📊 พื้นที่คลังพัสดุ: {warehouse_option}\n👍 สถานะปกติ: พัสดุทั้งหมดในคลังย่อย 0021 อยู่ในระดับที่ปลอดภัยครบถ้วน\n\n🔗 ลิงก์ Google Sheets:\n{sh.url}"
                                status_code = send_line_message(line_msg, st.secrets["line_group_id"])
                                if status_code == 200:
                                    st.sidebar.success(f"📱 ส่งสถานะปกติของคลัง **{warehouse_option}** เข้ากลุ่ม LINE สำเร็จ!")
                except Exception as e:
                    st.sidebar.error(f"❌ ระบบเกิดข้อผิดพลาดในการดึงข้อมูลส่งไลน์: {e}")


    # --- ส่วนที่ 4: การดึงดาต้าจาก Google Sheets มาคำนวณโชว์ผลหน้าเว็บ ---
    st.write(f"📊 กำลังแสดงยอดเปรียบเทียบคลัง: **{warehouse_option}**")
    
    df_mb52_clean = None
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
        df_result['คงเหลือ (ผลต่าง 0021)'] = df_result['จำนวนอุปกรณ์ในคลัง (เฉพาะ 0021)'] - df_merge[warehouse_option].astype(int)

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
            st.error(f"🚨 Status คลัง **{warehouse_option}**: ตรวจพบพัสดุในคลังย่อย 0021 ต่ำกว่าเกณฑ์ความปลอดภัยจำนวน **{shortage_0021}** รายการ!")
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
    st.info("⚠️ ยังไม่พบเกณฑ์อ้างอิง Safety Stock ในระบบคลาวด์ กรุณาลากวางไฟล์เกณฑ์จริง (.csv) ที่แถบควบคุมด้านซ้ายก่อนเพื่อเปิดใช้งานหน้าเว็บหลัก")
