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

st.set_page_config(layout="wide", page_title="ระบบติดตาม Safety Stock คลังพัสดุ")
st.title("📦 ระบบแจ้งเตือนและติดตามเกณฑ์พัสดุสำรอง (Safety Stock)")
st.subheader("เปรียบเทียบเกณฑ์อนุมัติประจำปี 2569 กับ ยอดคงคลังปัจจุบัน (MB52)")

GOOGLE_SHEET_NAME = "pea_safety_stock_db"

# --- 🛡️ ฟังก์ชันเกราะป้องกันการแปลงตัวเลข ---
def to_int_safe(val):
    try:
        if pd.isna(val): 
            return 0
        return int(float(str(val).replace(',', '').strip()))
    except (ValueError, TypeError):
        return 0

# --- 1. ฟังก์ชันเชื่อมต่อ Google Sheets ---
def get_gspread_client():
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"❌ ระบบความปลอดภัยปฏิเสธการเชื่อมต่อ (Secrets Error): {e}")
        st.code(traceback.format_exc(), language='python')
        return None

# --- 🛠️ 1.1 ฟังก์ชันอัปเดต Google Sheets แบบดิบ (RAW) สยบ Error ---
def safe_update_sheet(sheet, data, range_name="A1"):
    # ทำความสะอาดข้อมูลทุกเซลล์ให้เป็น Python Type บริสุทธิ์
    clean_data = []
    for row in data:
        clean_row = []
        for val in row:
            if pd.isna(val):
                clean_row.append("")
            elif isinstance(val, (int, float)):
                if val == float('inf') or val == float('-inf'):
                    clean_row.append("")
                else:
                    clean_row.append(int(val) if float(val).is_integer() else float(val))
            else:
                clean_row.append(str(val))
        clean_data.append(clean_row)
        
    try:
        # บังคับ RAW ป้องกัน Google Sheets เอาเครื่องหมาย - หรือ = ไปแปลเป็นสูตรจนพัง
        sheet.update(values=clean_data, range_name=range_name, value_input_option="RAW")
    except TypeError:
        sheet.update(range_name, clean_data, value_input_option="RAW")

# --- 2. ฟังก์ชันส่ง LINE ---
def send_line_message(message_text, target_id):
    try:
        url = "https://api.line.me/v2/bot/message/push"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {st.secrets['line_channel_access_token']}"}
        payload = {"to": target_id, "messages": [{"type": "text", "text": message_text}]}
        return requests.post(url, headers=headers, data=json.dumps(payload)).status_code
    except Exception as e:
        return str(e)

# --- 3. แกะไฟล์เกณฑ์ Safety Stock จริง ---
def parse_safety_stock_csv(file_content):
    try:
        raw_df = pd.read_csv(io.StringIO(file_content), header=None, dtype=str).fillna('')
        sap_col_idx = -1
        wh_col_map = {}
        
        for idx, row in raw_df.iterrows():
            for col_idx, val in enumerate(row):
                val_str = str(val).strip()
                if re.match(r'^C\d{3}$', val_str):
                    wh_col_map[val_str] = col_idx
                if sap_col_idx == -1 and re.match(r'^\d{10}$', val_str):
                    sap_col_idx = col_idx
                    
        if sap_col_idx == -1 or not wh_col_map:
            st.error("❌ ไม่พบคอลัมน์รหัสพัสดุ 10 หลัก หรือรหัสคลัง (C010-C130)")
            return None
            
        parsed_rows = []
        counter = 1
        for idx, row in raw_df.iterrows():
            sap_code = str(row[sap_col_idx]).strip()
            if re.match(r'^\d{10}$', sap_code):
                row_data = {
                    'No': counter,
                    'SAP_Code': sap_code,
                    'Description': str(row[sap_col_idx + 1]).strip() if (sap_col_idx + 1) < len(row) else '',
                    'Unit': str(row[sap_col_idx + 2]).strip() if (sap_col_idx + 2) < len(row) else ''
                }
                for wh_code, col_id in wh_col_map.items():
                    qty_str = str(row[col_id]).replace(',', '').strip()
                    try:
                        row_data[wh_code] = int(float(qty_str)) if qty_str else 0
                    except ValueError:
                        row_data[wh_code] = 0
                parsed_rows.append(row_data)
                counter += 1
                
        if not parsed_rows:
            return None
            
        processed_df = pd.DataFrame(parsed_rows)
        final_cols = ['No', 'SAP_Code', 'Description', 'Unit'] + sorted(list(wh_col_map.keys()))
        return processed_df[final_cols]
    except Exception as e:
        st.error(f"❌ เกิดข้อผิดพลาดในการประมวลผลไฟล์เกณฑ์: {e}")
        st.code(traceback.format_exc(), language='python')
        return None

# --- 4. ดึงข้อมูลเกณฑ์จาก Google Sheets ---
def load_safety_stock_from_sheets(client):
    try:
        worksheet = client.open(GOOGLE_SHEET_NAME).worksheet("Safety_Stock_Criteria")
        records = worksheet.get_all_records()
        if records:
            df = pd.DataFrame(records)
            df['SAP_Code'] = df['SAP_Code'].astype(str).str.strip()
            df = df[df['SAP_Code'].str.match(r'^\d+$')]
            return df
    except Exception:
        pass
    return None

# --- 5. แกะไฟล์ MB52 ---
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
                material_data[current_material] = {'Total_Qty': 0.0, 'Qty_0021': 0.0}
            continue
        tokens = line.split()
        if len(tokens) >= 4 and tokens[3] in ['EA', 'KG', 'M', 'PAC', 'L']:
            try:
                qty = float(tokens[2].replace(',', ''))
                material_data[current_material]['Total_Qty'] += qty
                if tokens[0].strip() == '0021':
                    material_data[current_material]['Qty_0021'] += qty
            except Exception:
                pass
    return pd.DataFrame([[k, v['Total_Qty'], v['Qty_0021']] for k, v in material_data.items()], columns=['SAP_Code', 'Actual_Qty', 'Qty_0021'])

# ==================================================
# เริ่มระบบการทำงาน
# ==================================================
client = get_gspread_client()
df_safety = load_safety_stock_from_sheets(client) if client else None

st.sidebar.title("📁 แผงควบคุมฐานข้อมูล")
st.sidebar.header("📤 1. อัปเดตเกณฑ์ Safety Stock ประจำปี")
uploaded_safety_file = st.sidebar.file_uploader("ลากวางไฟล์เกณฑ์จริง (.csv) ที่นี่", key="uploader_master_safety")

if uploaded_safety_file:
    try:
        safety_string = uploaded_safety_file.getvalue().decode("utf-8", errors="ignore")
        with st.spinner("กำลังแกะโครงสร้างไฟล์จริงและบันทึกขึ้นระบบคลาวด์ถาวร..."):
            df_safety_parsed = parse_safety_stock_csv(safety_string)
            if df_safety_parsed is not None and not df_safety_parsed.empty:
                sh = client.open(GOOGLE_SHEET_NAME)
                try:
                    ws_master = sh.worksheet("Safety_Stock_Criteria")
                except gspread.exceptions.WorksheetNotFound:
                    ws_master = sh.add_worksheet(title="Safety_Stock_Criteria", rows="100", cols="20")
                
                ws_master.clear()
                master_data_save = [df_safety_parsed.columns.tolist()] + df_safety_parsed.fillna('').values.tolist()
                ws_master.resize(rows=max(ws_master.row_count, len(master_data_save)), cols=max(ws_master.col_count, len(master_data_save[0])))
                
                safe_update_sheet(ws_master, master_data_save)
                
                st.sidebar.success("✅ บันทึกเกณฑ์อ้างอิง Safety Stock ลงระบบคลาวด์ถาวรสำเร็จแล้ว!")
                st.cache_data.clear()
                st.rerun()
    except Exception as e:
        st.sidebar.error(f"❌ เกิดข้อผิดพลาดในระบบส่งฐานข้อมูล: {e}")
        with st.sidebar.expander("ดูโค้ด Error เชิงลึก"):
            st.code(traceback.format_exc(), language='python')

if df_safety is not None and not df_safety.empty:
    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ 2. เลือกคลังที่ต้องการตรวจสอบ")
    warehouse_options = [col for col in df_safety.columns if str(col).startswith('C') and str(col)[1:].isdigit()]
    
    if not warehouse_options:
        st.error("❌ ไม่พบรหัสคลังพัสดุในฐานข้อมูล กรุณาอัปโหลดไฟล์เกณฑ์ใหม่อีกครั้ง")
        st.stop()
        
    warehouse_option = st.sidebar.selectbox("เลือกพื้นที่คลังพัสดุเพื่อดูตาราง:", options=warehouse_options)

    st.sidebar.markdown("---")
    st.sidebar.subheader("📥 3. อัปเดตยอดคลังเข้าคลาวด์ถาวร")
    upload_target = st.sidebar.selectbox("เลือกคลังปลายทางที่จะบันทึกไฟล์นี้:", options=warehouse_options, key="upload_target_select")
    uploaded_mb52 = st.sidebar.file_uploader(f"ลากวางไฟล์ MB52.txt ของคลัง [{upload_target}] ที่นี่", key="uploader_mb52")

    if uploaded_mb52:
        string_data = uploaded_mb52.getvalue().decode("utf-8", errors="ignore")
        df_parsed = parse_mb52_txt(string_data)
        if not df_parsed.empty:
            with st.spinner(f"กำลังอัปเดตฐานข้อมูลถาวรของคลัง {upload_target}..."):
                try:
                    sh = client.open(GOOGLE_SHEET_NAME)
                    try:
                        worksheet = sh.worksheet(upload_target)
                    except gspread.exceptions.WorksheetNotFound:
                        worksheet = sh.add_worksheet(title=upload_target, rows="100", cols="5")
                    worksheet.clear()
                    data_to_save = [df_parsed.columns.tolist()] + df_parsed.values.tolist()
                    worksheet.resize(rows=max(worksheet.row_count, len(data_to_save)), cols=max(worksheet.col_count, len(data_to_save[0])))
                    
                    safe_update_sheet(worksheet, data_to_save)
                    st.sidebar.success(f"🚀 บันทึกข้อมูลคลัง **{upload_target}** ลง Google Sheets สำเร็จ!")

                    # --- วิเคราะห์ผลต่างและสร้างชีตสรุป ---
                    df_safety_line = df_safety.copy()
                    df_parsed_line = df_parsed.copy()
                    
                    df_merge_auto = pd.merge(df_safety_line, df_parsed_line, on='SAP_Code', how='left')
                    df_merge_auto['Qty_0021'] = df_merge_auto['Qty_0021'].apply(to_int_safe)
                    df_merge_auto[upload_target] = df_merge_auto[upload_target].apply(to_int_safe)
                    df_merge_auto['คงเหลือ_0021'] = df_merge_auto['Qty_0021'] - df_merge_auto[upload_target]
                    df_shortage_auto = df_merge_auto[df_merge_auto['คงเหลือ_0021'] < 0]

                    summary_ws_title = f"สรุป_{upload_target}"
                    try:
                        summary_worksheet = sh.worksheet(summary_ws_title)
                    except gspread.exceptions.WorksheetNotFound:
                        summary_worksheet = sh.add_worksheet(title=summary_ws_title, rows="100", cols="5")
                    summary_worksheet.clear()
                    
                    if not df_shortage_auto.empty:
                        df_summary_sheet = pd.DataFrame({
                            'รหัสพัสดุ': df_shortage_auto['SAP_Code'],
                            'ชื่อพัสดุ': df_shortage_auto['Description'],
                            'ยอดคงคลังย่อย 0021': df_shortage_auto['Qty_0021'].astype(int),
                            'เกณฑ์ Safety Stock': df_shortage_auto[upload_target].astype(int),
                            'จำนวนที่ขาด (ผลต่าง)': (df_shortage_auto[upload_target] - df_shortage_auto['Qty_0021']).astype(int)
                        })
                        summary_data_to_save = [df_summary_sheet.columns.tolist()] + df_summary_sheet.values.tolist()
                    else:
                        summary_data_to_save = [["สถานะคลัง", "✅ ปลอดภัยครบถ้วน ไม่มีพัสดุต่ำกว่าเกณฑ์"]]
                    
                    summary_worksheet.resize(rows=max(summary_worksheet.row_count, len(summary_data_to_save)), cols=max(summary_worksheet.col_count, len(summary_data_to_save[0])))
                    safe_update_sheet(summary_worksheet, summary_data_to_save)
                    st.cache_data.clear()

                    # --- ส่ง LINE อัตโนมัติ ---
                    if "line_group_id" in st.secrets and not df_shortage_auto.empty:
                        total_shortage = len(df_shortage_auto)
                        line_msg = f"🚨 [รายงานแจ้งเตือนพัสดุต่ำกว่าเกณฑ์]\n📊 พื้นที่คลัง: {upload_target}\n⚠️ รายการวิกฤต: {total_shortage} รายการ\n\n"
                        for idx, row in enumerate(df_shortage_auto.iterrows(), 1):
                            data = row[1]
                            line_msg += f"{idx}. {data['SAP_Code']} - {data['Description']}\n   ยอด: {int(data['Qty_0021'])} | เกณฑ์: {int(data[upload_target])} | ❌ ขาด: {int(data[upload_target] - data['Qty_0021'])}\n---\n"
                            if idx >= 15:
                                line_msg += f"🔺 มีรายการต่ำกว่าเกณฑ์อีก {total_shortage - 15} รายการ\n"
                                break
                        line_msg += f"\n🟢 ดูตารางสรุปทั้งหมดได้ที่นี่:\n{sh.url}"
                        send_line_message(line_msg, st.secrets["line_group_id"])
                        
                except Exception as e:
                    st.sidebar.error(f"❌ เกิดข้อผิดพลาดตอนอัปเดตไฟล์ MB52: {e}")
                    with st.sidebar.expander("ดูโค้ด Error เชิงลึก"):
                        st.code(traceback.format_exc(), language='python')

    # --- ปุ่มส่ง LINE ด้วยมือ ---
    st.sidebar.markdown("---")
    st.sidebar.subheader("📢 ส่งรายงานสรุปซ้ำเข้า LINE")
    st.sidebar.info("💡 **คำอธิบายระบบสำหรับพนักงาน:**\nหากต้องการส่งรายงานผลต่างของคลังพัสดุที่เลือกอยู่ ณ ปัจจุบัน เข้ากลุ่ม LINE ผู้บริหารซ้ำอีกครั้ง สามารถกดปุ่มด้านล่างนี้ได้เลยครับ")
    
    if st.sidebar.button("🔄 สั่งส่งผลสรุปเข้า LINE อีกครั้ง"):
        with st.spinner(f"กำลังส่งไลน์รายงานคลัง {warehouse_option}..."):
            try:
                sh = client.open(GOOGLE_SHEET_NAME)
                records = sh.worksheet(warehouse_option).get_all_records()
                if records:
                    df_resend = pd.DataFrame(records)
                    df_s_resend = df_safety.copy()
                    df_resend['SAP_Code'] = df_resend['SAP_Code'].astype(str).str.strip()
                    
                    df_m = pd.merge(df_s_resend, df_resend, on='SAP_Code', how='left')
                    df_m['Qty_0021'] = df_m['Qty_0021'].apply(to_int_safe)
                    df_m[warehouse_option] = df_m[warehouse_option].apply(to_int_safe)
                    df_m['ขาด'] = df_m['Qty_0021'] - df_m[warehouse_option]
                    df_short = df_m[df_m['ขาด'] < 0]
                    
                    if "line_group_id" in st.secrets:
                        if not df_short.empty:
                            total = len(df_short)
                            msg = f"🚨 [ส่งซ้ำ: พัสดุต่ำกว่าเกณฑ์]\n📊 คลัง: {warehouse_option}\n⚠️ วิกฤต: {total} รายการ\n\n"
                            for idx, row in enumerate(df_short.iterrows(), 1):
                                data = row[1]
                                msg += f"{idx}. {data['SAP_Code']} - {data['Description']}\n   ยอด: {int(data['Qty_0021'])} | เกณฑ์: {int(data[warehouse_option])} | ❌ ขาด: {int(data[warehouse_option] - data['Qty_0021'])}\n---\n"
                                if idx >= 15:
                                    msg += f"🔺 มีต่ำกว่าเกณฑ์อีก {total - 15} รายการ\n"
                                    break
                            msg += f"\n🟢 ลิงก์ตารางสรุป:\n{sh.url}"
                            send_line_message(msg, st.secrets["line_group_id"])
                            st.sidebar.success(f"📱 ส่งรายงานคลัง {warehouse_option} ซ้ำเข้า LINE สำเร็จ!")
                        else:
                            msg = f"✅ [ส่งซ้ำ: สถานะคลังพัสดุ]\n📊 คลัง: {warehouse_option}\n👍 สถานะปกติ: ไม่มีพัสดุต่ำกว่าเกณฑ์\n\n🔗 ลิงก์ตารางสรุป:\n{sh.url}"
                            send_line_message(msg, st.secrets["line_group_id"])
                            st.sidebar.success(f"📱 ส่งสถานะปกติเข้า LINE สำเร็จ!")
            except Exception as e:
                st.sidebar.error(f"❌ ดึงข้อมูลส่งไลน์ไม่สำเร็จ: {e}")
                with st.sidebar.expander("ดูโค้ด Error เชิงลึก"):
                    st.code(traceback.format_exc(), language='python')

    # --- โชว์ตารางบนหน้าเว็บหลัก ---
    st.write(f"📊 กำลังแสดงยอดเปรียบเทียบคลัง: **{warehouse_option}**")
    df_mb52_clean = None
    try:
        records = client.open(GOOGLE_SHEET_NAME).worksheet(warehouse_option).get_all_records()
        if records: df_mb52_clean = pd.DataFrame(records)
    except Exception:
        pass

    if df_mb52_clean is not None and not df_mb52_clean.empty:
        df_mb52_clean['SAP_Code'] = df_mb52_clean['SAP_Code'].astype(str).str.strip()
        
        df_merge = pd.merge(df_safety, df_mb52_clean, on='SAP_Code', how='left')
        
        df_result = pd.DataFrame({
            'ลำดับ': df_merge['No'],
            'รหัสพัสดุ': df_merge['SAP_Code'],
            'ชื่อพัสดุ': df_merge['Description'],
            'จำนวนอุปกรณ์ในคลัง (รวมทุก SLoc)': df_merge['Actual_Qty'].apply(to_int_safe),
            'จำนวนอุปกรณ์ในคลัง (เฉพาะ 0021)': df_merge['Qty_0021'].apply(to_int_safe),
            'อนุมัติ safety stock': df_merge[warehouse_option].apply(to_int_safe)
        })
        df_result['คงเหลือ (ผลต่าง 0021)'] = df_result['จำนวนอุปกรณ์ในคลัง (เฉพาะ 0021)'] - df_result['อนุมัติ safety stock']

        def alert_low_stock(val):
            return 'background-color: #ffcccc; color: #cc0000; font-weight: bold;' if val < 0 else ''
        
        st.dataframe(df_result.style.map(alert_low_stock, subset=['คงเหลือ (ผลต่าง 0021)']).format('{:,}', subset=['จำนวนอุปกรณ์ในคลัง (รวมทุก SLoc)', 'จำนวนอุปกรณ์ในคลัง (เฉพาะ 0021)', 'อนุมัติ safety stock', 'คงเหลือ (ผลต่าง 0021)']), use_container_width=True, hide_index=True)
        
        shortage = len(df_result[df_result['คงเหลือ (ผลต่าง 0021)'] < 0])
        if shortage > 0:
            st.error(f"🚨 Status คลัง **{warehouse_option}**: ตรวจพบพัสดุวิกฤตจำนวน **{shortage}** รายการ!")
        else:
            st.success(f"✅ พัสดุทั้งหมดในคลัง **{warehouse_option}** อยู่ในระดับที่ปลอดภัย")
    else:
        st.info(f"📊 ยังไม่มีข้อมูลดิบของคลัง **{warehouse_option}** (กรุณาเลือกคลังด้านซ้ายและอัปโหลดไฟล์ MB52)")
else:
    st.info("⚠️ ยังไม่พบเกณฑ์อ้างอิง Safety Stock ในระบบคลาวด์ กรุณาลากวางไฟล์เกณฑ์จริง (.csv) ที่แถบควบคุมด้านซ้ายก่อนเพื่อเปิดใช้งานหน้าเว็บหลัก")
