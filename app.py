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

# 🔥 ชื่อ Google Sheets หลัก
GOOGLE_SHEET_NAME = "pea_safety_stock_db"
SAFETY_STOCK_SHEET = "safety_stock_master"   # Sheet สำหรับเก็บข้อมูลเกณฑ์ Safety Stock

# คอลัมน์คลังทั้งหมดที่ระบบรองรับ
WAREHOUSE_COLS = ['C010','C020','C030','C040','C050','C060','C070','C080','C090','C110','C120','C130']

# -----------------------------------------------------------------------
# ฟังก์ชันเชื่อมต่อ Google Sheets
# -----------------------------------------------------------------------
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

# -----------------------------------------------------------------------
# ฟังก์ชันส่ง LINE
# -----------------------------------------------------------------------
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
            "messages": [{"type": "text", "text": message_text}]
        }
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        return response.status_code
    except Exception as e:
        return str(e)

# -----------------------------------------------------------------------
# ฟังก์ชันแกะไฟล์ MB52 (.txt จาก SAP)
# -----------------------------------------------------------------------
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

# -----------------------------------------------------------------------
# ฟังก์ชันอ่านไฟล์ Excel Safety Stock และแปลงเป็น DataFrame มาตรฐาน
# -----------------------------------------------------------------------
def parse_safety_stock_excel(uploaded_file):
    try:
        col_names = [
            'No', 'No2', 'Type', 'SAP_Code', 'Description', 'Unit',
            'Price', 'Total_N3', 'Budget',
            'C010', 'C020', 'C030', 'C040', 'C050', 'C060',
            'C070', 'C080', 'C090', 'C110', 'C120', 'C130'
        ]

        df = pd.read_excel(
            uploaded_file,
            sheet_name=0,
            header=None,
            names=col_names,
            skiprows=5
        )

        def clean_sap(v):
            try:
                return str(int(float(v)))
            except Exception:
                return ''

        df['SAP_Code'] = df['SAP_Code'].apply(clean_sap)
        df = df[df['SAP_Code'].str.match(r'^\d{10}$')].reset_index(drop=True)

        if df.empty:
            st.error("❌ ไม่พบรหัสพัสดุ (SAP 10 หลัก) ในไฟล์ที่อัปโหลด — กรุณาตรวจสอบ sheet และโครงสร้างไฟล์")
            return None

        df['No'] = range(1, len(df) + 1)

        for wh in WAREHOUSE_COLS:
            if wh in df.columns:
                df[wh] = pd.to_numeric(df[wh], errors='coerce').fillna(0).astype(int)
            else:
                df[wh] = 0

        if 'Total_N3' in df.columns:
            df['Total_N3'] = pd.to_numeric(df['Total_N3'], errors='coerce').fillna(0)

        keep_cols = ['No', 'Type', 'SAP_Code', 'Description', 'Unit', 'Total_N3'] + WAREHOUSE_COLS
        keep_cols = [c for c in keep_cols if c in df.columns]
        df = df[keep_cols].reset_index(drop=True)

        return df

    except Exception as e:
        st.error(f"❌ เกิดข้อผิดพลาดในการอ่านไฟล์ Excel: {e}")
        return None

# -----------------------------------------------------------------------
# ฟังก์ชันโหลด Safety Stock จาก Google Sheets (cache 5 นาที)
# -----------------------------------------------------------------------
@st.cache_data(ttl=300)
def load_safety_stock_from_gsheet():
    client = get_gspread_client()
    if client is None:
        return None
    try:
        sh = client.open(GOOGLE_SHEET_NAME)
        try:
            ws = sh.worksheet(SAFETY_STOCK_SHEET)
        except gspread.exceptions.WorksheetNotFound:
            return None
        records = ws.get_all_records()
        if not records:
            return None
        df = pd.DataFrame(records)
        df['SAP_Code'] = df['SAP_Code'].astype(str).str.strip()
        for wh in WAREHOUSE_COLS:
            if wh in df.columns:
                df[wh] = pd.to_numeric(df[wh], errors='coerce').fillna(0).astype(int)
        return df
    except Exception as e:
        st.warning(f"⚠️ โหลด Safety Stock จาก Google Sheets ไม่ได้: {e}")
        return None

# -----------------------------------------------------------------------
# ฟังก์ชันบันทึก Safety Stock ลง Google Sheets
# -----------------------------------------------------------------------
def save_safety_stock_to_gsheet(df_safety):
    client = get_gspread_client()
    if client is None:
        return False
    try:
        sh = client.open(GOOGLE_SHEET_NAME)
        try:
            ws = sh.worksheet(SAFETY_STOCK_SHEET)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=SAFETY_STOCK_SHEET, rows="500", cols="20")
        ws.clear()
        data_to_save = [df_safety.columns.tolist()] + df_safety.values.tolist()
        ws.update('A1', data_to_save)
        return True
    except Exception as e:
        st.error(f"❌ บันทึก Safety Stock ลง Google Sheets ไม่สำเร็จ: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════
#  SIDEBAR — อัปโหลด Safety Stock Excel
# ═══════════════════════════════════════════════════════════════════════
st.sidebar.header("📋 ฐานข้อมูลเกณฑ์ Safety Stock")

with st.sidebar.expander("📤 อัปโหลด / อัปเดตไฟล์เกณฑ์ Safety Stock (.xlsx)", expanded=False):
    st.info(
        "อัปโหลดไฟล์ Excel เกณฑ์ Safety Stock ที่ได้รับอนุมัติ\n"
        "ระบบจะบันทึกข้อมูลลง Google Sheets ทันทีและใช้เป็นฐานข้อมูลหลัก"
    )
    uploaded_safety_excel = st.file_uploader(
        "เลือกไฟล์ Excel เกณฑ์ Safety Stock",
        type=["xlsx", "xls"],
        key="safety_stock_uploader"
    )

    if uploaded_safety_excel is not None:
        with st.spinner("กำลังอ่านและตรวจสอบไฟล์..."):
            df_new_safety = parse_safety_stock_excel(uploaded_safety_excel)

        if df_new_safety is not None:
            st.success(f"✅ อ่านไฟล์สำเร็จ — พบพัสดุทั้งหมด **{len(df_new_safety):,}** รายการ")
            preview_cols = [c for c in ['SAP_Code', 'Description', 'Unit'] + WAREHOUSE_COLS[:3]
                            if c in df_new_safety.columns]
            st.dataframe(df_new_safety[preview_cols].head(5),
                         use_container_width=True, hide_index=True)

            if st.button("💾 บันทึกเกณฑ์นี้เป็นฐานข้อมูลหลัก", key="save_safety_btn"):
                with st.spinner("กำลังบันทึกลง Google Sheets..."):
                    ok = save_safety_stock_to_gsheet(df_new_safety)
                if ok:
                    st.success("🚀 บันทึก Safety Stock ลง Google Sheets สำเร็จ!")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("❌ บันทึกไม่สำเร็จ — ตรวจสอบการเชื่อมต่อ Google Sheets")

# ═══════════════════════════════════════════════════════════════════════
#  โหลด Safety Stock จาก Google Sheets
# ═══════════════════════════════════════════════════════════════════════
df_safety = load_safety_stock_from_gsheet()

# ═══════════════════════════════════════════════════════════════════════
#  SIDEBAR — เลือกคลัง + อัปโหลด MB52 (จุดควบคุมเดี่ยว)
# ═══════════════════════════════════════════════════════════════════════
if df_safety is not None:
    st.sidebar.success(f"✅ โหลดเกณฑ์ Safety Stock จาก Google Sheets แล้ว ({len(df_safety):,} รายการ)")

    warehouse_options = [wh for wh in WAREHOUSE_COLS if wh in df_safety.columns]

    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ เลือกคลังที่ต้องการตรวจสอบ")
    
    warehouse_option = st.sidebar.selectbox(
        "เลือกพื้นที่คลังพัสดุเพื่อดูตาราง:",
        options=warehouse_options,
        help="เปลี่ยนตรงนี้เพื่อดูสรุปยอดและผลต่างของแต่ละคลัง"
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("📥 อัปเดตยอดคลัง MB52 เข้าคลาวด์")

    uploaded_mb52 = st.sidebar.file_uploader(
        f"ลากวางไฟล์ MB52.txt ของคลัง [{warehouse_option}] ที่นี่",
        key=f"uploader_{warehouse_option}"
    )

    # -----------------------------------------------------------------------
    # ตรรกะบันทึก MB52 ลง Google Sheets + สรุป + แจ้งเตือน LINE
    # -----------------------------------------------------------------------
    if uploaded_mb52 is not None:
        try:
            raw_bytes = uploaded_mb52.getvalue()
            try:
                string_data = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                string_data = raw_bytes.decode("cp874", errors="ignore")

            df_parsed = parse_mb52_txt(string_data)

            if not df_parsed.empty:
                with st.spinner(f"กำลังอัปเดตฐานข้อมูลคลัง {warehouse_option} ลง Google Sheets..."):
                    client = get_gspread_client()
                    if client is not None:
                        sh = client.open(GOOGLE_SHEET_NAME)

                        # 1. บันทึกข้อมูลดิบ MB52 ตรงเข้าคลังที่เลือกอยู่
                        try:
                            worksheet = sh.worksheet(warehouse_option)
                        except gspread.exceptions.WorksheetNotFound:
                            worksheet = sh.add_worksheet(title=warehouse_option, rows="1000", cols="5")
                        worksheet.clear()
                        data_to_save = [df_parsed.columns.tolist()] + df_parsed.values.tolist()
                        worksheet.update('A1', data_to_save)
                        st.sidebar.success(f"🚀 บันทึกข้อมูลคลัง **{warehouse_option}** สำเร็จ!")

                        # 2. คำนวณและบันทึก Sheet สรุป
                        df_safety_line = df_safety.copy()
                        df_parsed_line = df_parsed.copy()
                        df_safety_line['SAP_Code'] = df_safety_line['SAP_Code'].astype(str).str.strip()
                        df_parsed_line['SAP_Code'] = df_parsed_line['SAP_Code'].astype(str).str.strip()

                        df_merge_auto = pd.merge(df_safety_line, df_parsed_line, on='SAP_Code', how='left')
                        df_merge_auto['Qty_0021'] = pd.to_numeric(df_merge_auto['Qty_0021'], errors='coerce').fillna(0)
                        df_merge_auto[warehouse_option] = pd.to_numeric(df_merge_auto[warehouse_option], errors='coerce').fillna(0)
                        df_merge_auto['คงเหลือ_0021'] = df_merge_auto['Qty_0021'] - df_merge_auto[warehouse_option]
                        df_shortage_auto = df_merge_auto[df_merge_auto['คงเหลือ_0021'] < 0]

                        summary_ws_title = f"สรุป_{warehouse_option}"
                        try:
                            summary_worksheet = sh.worksheet(summary_ws_title)
                        except gspread.exceptions.WorksheetNotFound:
                            summary_worksheet = sh.add_worksheet(title=summary_ws_title, rows="500", cols="5")
                        summary_worksheet.clear()

                        if not df_shortage_auto.empty:
                            df_sum = pd.DataFrame({
                                'รหัสพัสดุ': df_shortage_auto['SAP_Code'],
                                'ชื่อพัสดุ': df_shortage_auto['Description'],
                                'ยอดคงคลังย่อย 0021': df_shortage_auto['Qty_0021'].astype(int),
                                'เกณฑ์ Safety Stock': df_shortage_auto[warehouse_option].astype(int),
                                'จำนวนที่ขาด': (df_shortage_auto[warehouse_option] - df_shortage_auto['Qty_0021']).astype(int)
                            })
                            summary_worksheet.update('A1', [df_sum.columns.tolist()] + df_sum.values.tolist())
                        else:
                            summary_worksheet.update('A1', [["สถานะคลัง", "✅ ปลอดภัยครบถ้วน"]])

                        st.sidebar.success(f"📊 อัปเดตแผ่นงานสรุป **{summary_ws_title}** เรียบร้อย!")
                        st.cache_data.clear()

                        # 3. 🎯 ส่ง LINE แจ้งเตือนอัตโนมัติ (ปรับให้เด้งเข้าชีตสรุปตรงๆ ด้วยการแนบ #gid)
                        if "line_group_id" in st.secrets and not df_shortage_auto.empty:
                            total_shortage = len(df_shortage_auto)
                            line_msg = (
                                f"🚨 [รายงานแจ้งเตือนพัสดุต่ำกว่าเกณฑ์ Safety Stock]\n"
                                f"📊 พื้นที่คลังพัสดุ: {warehouse_option}\n"
                                f"⚠️ ตรวจพบรายการวิกฤต: {total_shortage} รายการ\n\n"
                                f"📌 รายการพัสดุวิกฤต:\n"
                            )
                            for idx, (_, row) in enumerate(df_shortage_auto.iterrows(), 1):
                                current_0021 = int(row['Qty_0021'])
                                limit_stock = int(row[warehouse_option])
                                needed_qty = limit_stock - current_0021
                                line_msg += (
                                    f"{idx}. รหัส: {row['SAP_Code']}\n"
                                    f"   {row['Description']}\n"
                                    f"   ยอดคลังย่อย: {current_0021} | เกณฑ์: {limit_stock}\n"
                                    f"   ❌ ขาดอีก: {needed_qty}\n"
                                    f"----------------------------------\n"
                                )
                                if idx >= 15:
                                    line_msg += f"🔺 และอีก {total_shortage - 15} รายการ ตรวจสอบเพิ่มเติมบนระบบเว็บ\n"
                                    break
                            
                            # 🔗 ล็อกพิกัด URL ให้เจาะจงเฉพาะชีตสรุปคลังตัวนี้โดยตรง
                            summary_sheet_url = f"{sh.url}#gid={summary_worksheet.id}"
                            line_msg += f"\n🟢 ผู้บริหารสามารถเปิดดูตารางสรุปของคลังนี้บน Google Sheets ได้ทันทีที่ลิงก์นี้ครับ:\n{summary_sheet_url}"
                            
                            status_code = send_line_message(line_msg, st.secrets["line_group_id"])
                            if status_code == 200:
                                st.sidebar.success("📱 ส่งรายงานเข้ากลุ่ม LINE สำเร็จ!")
                            else:
                                st.sidebar.warning(f"⚠️ LINE ส่งไม่สำเร็จ (Code: {status_code})")
            else:
                st.sidebar.warning("⚠️ ไม่พบข้อมูลพัสดุในไฟล์ที่อัปโหลด")
        except Exception as e:
            st.sidebar.error(f"❌ เกิดข้อผิดพลาด: {e}")

    # -----------------------------------------------------------------------
    # ปุ่มส่งรายงาน LINE ซ้ำ
    # -----------------------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("📢 ส่งรายงานสรุปซ้ำเข้า LINE")
    st.sidebar.info(
        "💡 กดปุ่มด้านล่างเพื่อส่งรายงานสถานะคลังที่เลือกอยู่เข้ากลุ่ม LINE อีกครั้ง\n"
        "(เช่น สมาชิกอ่านไม่ทัน หรือต้องการแจ้งย้ำ)"
    )

    if st.sidebar.button("🔄 ส่งผลสรุปเข้า LINE อีกครั้ง", key="resend_summary_to_line"):
        with st.spinner(f"กำลังดึงข้อมูลและส่งไลน์คลัง {warehouse_option}..."):
            client = get_gspread_client()
            if client is not None:
                try:
                    sh = client.open(GOOGLE_SHEET_NAME)
                    worksheet = sh.worksheet(warehouse_option)
                    records = worksheet.get_all_records()

                    if records:
                        df_mb52_resend = pd.DataFrame(records)
                        df_safety_resend = df_safety.copy()
                        df_safety_resend['SAP_Code'] = df_safety_resend['SAP_Code'].astype(str).str.strip()
                        df_mb52_resend['SAP_Code'] = df_mb52_resend['SAP_Code'].astype(str).str.strip()

                        df_merge_resend = pd.merge(df_safety_resend, df_mb52_resend, on='SAP_Code', how='left')
                        df_merge_resend['Qty_0021'] = pd.to_numeric(df_merge_resend['Qty_0021'], errors='coerce').fillna(0)
                        df_merge_resend[warehouse_option] = pd.to_numeric(df_merge_resend[warehouse_option], errors='coerce').fillna(0)
                        df_merge_resend['คงเหลือ_0021'] = df_merge_resend['Qty_0021'] - df_merge_resend[warehouse_option]
                        df_shortage_resend = df_merge_resend[df_merge_resend['คงเหลือ_0021'] < 0]

                        # 🎯 ดึง ID ของหน้าชีตสรุปมาเตรียมล็อกลิงก์ปุ่มกดส่งซ้ำ
                        summary_ws_title = f"สรุป_{warehouse_option}"
                        try:
                            target_sum_ws = sh.worksheet(summary_ws_title)
                            summary_sheet_url = f"{sh.url}#gid={target_sum_ws.id}"
                        except:
                            summary_sheet_url = sh.url

                        if "line_group_id" in st.secrets:
                            if not df_shortage_resend.empty:
                                total = len(df_shortage_resend)
                                line_msg = (
                                    f"🚨 [ส่งซ้ำ: รายงานพัสดุต่ำกว่าเกณฑ์]\n"
                                    f"📊 คลัง: {warehouse_option}\n"
                                    f"⚠️ รายการวิกฤต: {total} รายการ\n\n"
                                )
                                for idx, (_, row) in enumerate(df_shortage_resend.iterrows(), 1):
                                    c = int(row['Qty_0021'])
                                    s = int(row[warehouse_option])
                                    line_msg += (
                                        f"{idx}. {row['SAP_Code']} | {row['Description']}\n"
                                        f"   คลังย่อย: {c} | เกณฑ์: {s} | ❌ ขาด: {s - c}\n"
                                        f"----------------------------------\n"
                                    )
                                    if idx >= 15:
                                        line_msg += f"🔺 และอีก {total - 15} รายการ\n"
                                        break
                                line_msg += f"\n🟢 Google Sheets ชีตสรุปคลัง:\n{summary_sheet_url}"
                            else:
                                line_msg = (
                                    f"✅ [ส่งซ้ำ: รายงานสถานะคลัง]\n"
                                    f"📊 คลัง: {warehouse_option}\n"
                                    f"👍 พัสดุทั้งหมดอยู่ในระดับปลอดภัย\n"
                                    f"🟢 Google Sheets ชีตสรุปคลัง:\n{summary_sheet_url}"
                                )

                            status_code = send_line_message(line_msg, st.secrets["line_group_id"])
                            if status_code == 200:
                                st.sidebar.success(f"📱 ส่งรายงานคลัง **{warehouse_option}** เข้า LINE สำเร็จ!")
                            else:
                                st.sidebar.error(f"❌ ส่งไลน์ไม่สำเร็จ (Code: {status_code})")
                    else:
                        st.sidebar.warning(f"⚠️ คลัง **{warehouse_option}** ยังไม่มีฐานข้อมูล MB52 ในระบบ")
                except gspread.exceptions.WorksheetNotFound:
                    st.sidebar.warning(f"⚠️ ยังไม่เคยอัปโหลด MB52 ของคลัง **{warehouse_option}**")
                except Exception as e:
                    st.sidebar.error(f"❌ เกิดข้อผิดพลาด: {e}")

    # ═══════════════════════════════════════════════════════════════════
    #  ส่วนแสดงผลหลัก — เปรียบเทียบยอดคลัง vs เกณฑ์
    # ═══════════════════════════════════════════════════════════════════
    st.write(f"📊 กำลังแสดงยอดเปรียบเทียบคลัง: **{warehouse_option}**")

    df_mb52_clean = None
    client = get_gspread_client()
    if client is not None:
        try:
            sh = client.open(GOOGLE_SHEET_NAME)
            worksheet = sh.worksheet(warehouse_option)
            records = worksheet.get_all_records()
            if records:
                df_mb52_clean = pd.DataFrame(records)
        except gspread.exceptions.WorksheetNotFound:
            df_mb52_clean = None
        except Exception:
            df_mb52_clean = None

    # ป้องกัน NaN ในคอลัมน์คลัง
    df_safety[warehouse_option] = pd.to_numeric(df_safety[warehouse_option], errors='coerce').fillna(0)

    if df_mb52_clean is not None and not df_mb52_clean.empty:
        df_safety['SAP_Code'] = df_safety['SAP_Code'].astype(str).str.strip()
        df_mb52_clean['SAP_Code'] = df_mb52_clean['SAP_Code'].astype(str).str.strip()

        df_merge = pd.merge(df_safety, df_mb52_clean, on='SAP_Code', how='left')
        df_merge['Actual_Qty'] = pd.to_numeric(df_merge['Actual_Qty'], errors='coerce').fillna(0)
        df_merge['Qty_0021'] = pd.to_numeric(df_merge['Qty_0021'], errors='coerce').fillna(0)

        df_result = pd.DataFrame({
            'ลำดับ': df_merge['No'] if 'No' in df_merge.columns else range(1, len(df_merge) + 1),
            'รหัสพัสดุ': df_merge['SAP_Code'],
            'ชื่อพัสดุ': df_merge['Description'],
            'จำนวนอุปกรณ์ในคลัง (รวมทุก SLoc)': df_merge['Actual_Qty'].round(0).astype(int)
            'จำนวนอุปกรณ์ในคลัง (เฉพาะ 0021)': df_merge['Qty_0021'].round(0).astype(int),
            'อนุมัติ safety stock': df_merge[warehouse_option].astype(int),
            'คงเหลือ (ผลต่าง 0021)': (df_merge['Qty_0021'].round(0).astype(int) - df_merge[warehouse_option].astype(int))
        })

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
        st.info(f"📊 ยังไม่มีฐานข้อมูลถาวรของคลัง **{warehouse_option}** ใน Google Sheets (กรุณาลากวางไฟล์ MB52 เพื่อตั้งต้นข้อมูล)")

        df_blank = pd.DataFrame({
            'ลำดับ': df_safety['No'],
            'รหัสพัสดุ': df_safety['SAP_Code'],
            'ชื่อพัสดุ': df_safety['Description'],
            'หน่วยนับ': df_safety['Unit'],
            'อนุมัติ safety stock': df_safety[warehouse_option].fillna(0).astype(int)
        })
        st.dataframe(df_blank.style.format({'อนุมัติ safety stock': '{:,}'}), use_container_width=True, hide_index=True)

else:
    # ยังไม่มีข้อมูลเกณฑ์ Safety Stock เลย
    st.warning(
        "⚠️ ยังไม่มีข้อมูลเกณฑ์ Safety Stock ในระบบ\n\n"
        "**กรุณาอัปโหลดไฟล์ Excel เกณฑ์ Safety Stock ผ่านเมนูด้านซ้ายก่อน**\n\n"
        "_(เปิดส่วน 📤 อัปโหลด / อัปเดตไฟล์เกณฑ์ Safety Stock ในแถบด้านซ้าย)_"
    )
