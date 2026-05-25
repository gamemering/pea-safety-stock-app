import streamlit as st
import pandas as pd
import os
import re

st.set_page_config(layout="wide", page_title="ระบบติดตาม Safety Stock คลังพัสดุ")

st.title("📦 ระบบแจ้งเตือนและติดตามเกณฑ์พัสดุสำรอง (Safety Stock)")
st.subheader("เปรียบเทียบเกณฑ์อนุมัติประจำปี 2569 กับ ยอดคงคลังปัจจุบัน (MB52)")

# --- ส่วนที่ 1: ค้นหาไฟล์เกณฑ์ Safety Stock อัตโนมัติในโฟลเดอร์ ---
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


# --- ส่วนที่ 2: ฟังก์ชันสำหรับแกะเนื้อหาไฟล์ MB52 ของ SAP (Text Parser) ---
def parse_mb52_txt(file_content):
    """ฟังก์ชันแกะโครงสร้างข้อความ MB52 และรวมยอดพัสดุแยกตามรหัสพัสดุ"""
    material_qty = {}
    current_material = None
    
    lines = file_content.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # 1. ตรวจจับรหัสพัสดุ (เช่น 1-00-001-0001)
        match_mat = re.match(r'^(\d-\d{2}-\d{3}-\d{4})', line)
        if match_mat:
            raw_code = match_mat.group(1)
            current_material = raw_code.replace('-', '')
            continue
            
        # 2. ตรวจจับบรรทัดจำนวนพัสดุ (เช็คจากหน่วยนับมาตรฐาน)
        tokens = line.split()
        if len(tokens) >= 4:
            if tokens[3] in ['EA', 'KG', 'M', 'PAC', 'L']:
                qty_str = tokens[2].replace(',', '')
                try:
                    qty = float(qty_str)
                    if current_material:
                        material_qty[current_material] = material_qty.get(current_material, 0.0) + qty
                except ValueError:
                    pass
                    
    df_parsed = pd.DataFrame(list(material_qty.items()), columns=['SAP_Code', 'Actual_Qty'])
    return df_parsed


# --- ส่วนที่ 3: ระบบหน่วยความจำแยกฐานข้อมูล (Session State) ---
# สร้างคลังข้อมูลจำลอง 13 คลังไว้ในระบบหากยังไม่มี
if 'warehouse_db' not in st.session_state:
    st.session_state['warehouse_db'] = {}  # โครงสร้างเก็บข้อมูลแบบ {'C010': df, 'C020': df, ...}


# เริ่มรันหน้าเว็บเมื่อไฟล์เกณฑ์พัสดุพร้อมใช้งาน
if df_safety is not None:
    st.sidebar.success(f"📂 ตรวจพบไฟล์เกณฑ์อัตโนมัติ: `{detected_file}`")
    
    # ⚙️ การตั้งค่าเลือกคลังหลักเพื่อดูรายงาน
    st.sidebar.header("⚙️ เลือกคลังที่ต้องการตรวจสอบ")
    all_columns = df_safety.columns.tolist()
    warehouse_options = [col for col in all_columns if col not in ['No', 'Type', 'SAP_Code', 'Description', 'Unit', 'Total_N3']]
    
    warehouse_option = st.sidebar.selectbox(
        "เลือกพื้นที่คลังพัสดุเพื่อดูตาราง:",
        options=warehouse_options,
        help="เปลี่ยนตรงนี้เพื่อดูสรุปยอดและผลต่างของแต่ละคลัง"
    )

    # 📥 โซนอัปโหลดไฟล์แยกตามคลัง
    st.sidebar.markdown("---")
    st.sidebar.subheader("📥 อัปเดตยอดคลังเข้าฐานข้อมูล")
    
    # ดรอปดาวน์เลือกก่อนว่าจะเอายอดไฟล์ txt นี้ เข้าไปเก็บไว้ที่คลังรหัสไหน
    upload_target = st.sidebar.selectbox(
        "เลือกคลังปลายทางที่จะบันทึกไฟล์นี้:",
        options=warehouse_options,
        key="upload_target_select"
    )
    
    uploaded_mb52 = st.sidebar.file_uploader(
        f"ลากวางไฟล์ MB52.txt ของคลัง [{upload_target}] ที่นี่", 
        key=f"uploader_{upload_target}"  # เปลี่ยน key อัตโนมัติตามคลังที่เลือกเพื่อป้องกันตัวอัปโหลดเอ๋อ
    )

    # บันทึกข้อมูลลงฐานข้อมูลแยกคลังตามที่ระบุ
    if uploaded_mb52 is not None:
        try:
            raw_bytes = uploaded_mb52.getvalue()
            try:
                string_data = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                string_data = raw_bytes.decode("cp874", errors="ignore")
                
            # แปลงไฟล์ txt ออกมาเป็นตารางสรุปยอดจริง
            df_parsed = parse_mb52_txt(string_data)
            
            if not df_parsed.empty:
                # บันทึกเข้าสู่ฐานข้อมูลประหลักประจำคลังนั้นๆ ในเซสชัน
                st.session_state['warehouse_db'][upload_target] = df_parsed
                st.sidebar.success(f"💾 บันทึกยอดจริงเข้าสู่คลัง **{upload_target}** เรียบร้อย! (พบ {len(df_parsed)} รายการ)")
            else:
                st.sidebar.warning("⚠️ ไม่พบข้อมูลพัสดุในไฟล์ที่อัปโหลด")
        except Exception as e:
            st.sidebar.error(f"❌ เกิดข้อผิดพลาดในการบันทึกข้อมูล: {e}")


    # --- ส่วนที่ 4: การประมวลผลดึงข้อมูลตามคลังที่เลือกมาแสดงผล ---
    st.write(f"📊 กำลังแสดงยอดเปรียบเทียบคลัง: **{warehouse_option}**")
    
    # ดึงฐานข้อมูลเฉพาะของคลังที่กำลังเลือกดูอยู่ปัจจุบัน
    df_mb52_clean = st.session_state['warehouse_db'].get(warehouse_option, None)

    if df_mb52_clean is not None and not df_mb52_clean.empty:
        df_safety['SAP_Code'] = df_safety['SAP_Code'].astype(str).str.strip()
        df_mb52_clean['SAP_Code'] = df_mb52_clean['SAP_Code'].astype(str).str.strip()

        # นำเกณฑ์มาชนกับยอดคลังจริงเจาะจงเฉพาะคลังนั้นๆ
        df_merge = pd.merge(df_safety, df_mb52_clean, on='SAP_Code', how='left')
        df_merge['Actual_Qty'] = df_merge['Actual_Qty'].fillna(0)

        # จัดโครงสร้างตาราง 7 คอลัมน์ตามมาตรฐาน
        df_result = pd.DataFrame()
        df_result['ลำดับ'] = df_merge['No']
        df_result['รหัสพัสดุ'] = df_merge['SAP_Code']
        df_result['ชื่อพัสดุ'] = df_merge['Description']
        df_result['อนุมัติ safety stock'] = df_merge[warehouse_option]
        df_result['จำนวนอุปกรณ์ในคลัง'] = df_merge['Actual_Qty']
        
        # คำนวณคงเหลือ (ผลต่าง)
        df_result['คงเหลือ (ผลต่าง)'] = df_result['จำนวนอุปกรณ์ในคลัง'] - df_result['อนุมัติ safety stock']
        
        # คำนวณเปอร์เซ็นต์
        df_result['เปอร์เซ็นต์ (%)'] = df_result.apply(
            lambda r: round((r['คงเหลือ (ผลต่าง)'] / r['อนุมัติ safety stock'] * 100), 2) if r['อนุมัติ safety stock'] > 0 else 0.0, 
            axis=1
        )

        def alert_low_stock(val):
            return 'background-color: #ffcccc; color: #cc0000; font-weight: bold;' if val < 0 else ''

        # แสดงตารางพร้อมย้อมสีแถวที่ของขาด
        styled_df = df_result.style.map(alert_low_stock, subset=['คงเหลือ (ผลต่าง)'])
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
        
        # แดชบอร์ดสรุปแจ้งเตือน
        shortage_items = df_result[df_result['คงเหลือ (ผลต่าง)'] < 0]
        if not shortage_items.empty:
            st.error(f"🚨 คลัง **{warehouse_option}** มีพัสดุต่ำกว่าเกณฑ์ความปลอดภัยจำนวน **{len(shortage_items)}** รายการ! กรุณาเปิดใบ PR ด่วน")
        else:
            st.success(f"✅ พัสดุทั้งหมดในคลัง **{warehouse_option}** อยู่ในระดับที่ปลอดภัยครบถ้วน")
            
    else:
        # กรณีที่เลือกคลังนั้นแล้วแต่ยังไม่มีการอัปโหลดไฟล์เข้ามา
        st.info(f"ℹ️ ยังไม่มีการอัปเดตข้อมูลยอดคงคลังจริงของคลัง **{warehouse_option}** เข้ามาในระบบ (ตารางด้านล่างแสดงเฉพาะเกณฑ์ที่ตั้งไว้)")
        st.dataframe(df_safety[['No', 'SAP_Code', 'Description', 'Unit', warehouse_option]], use_container_width=True, hide_index=True)

else:
    st.error("❌ ไม่พบไฟล์ฐานข้อมูลเกณฑ์พัสดุ (Safety Stock) ในระบบ")
