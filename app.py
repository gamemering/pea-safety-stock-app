import streamlit as st
import pandas as pd
import os

st.set_page_config(layout="wide", page_title="ระบบติดตาม Safety Stock คลังพัสดุ")

st.title("📦 ระบบแจ้งเตือนและติดตามเกณฑ์พัสดุสำรอง (Safety Stock)")
st.subheader("เปรียบเทียบเกณฑ์อนุมัติประจำปี 2569 กับ ยอดคงคลังปัจจุบัน (MB52)")

# --- ส่วนที่ 1: การค้นหาและดึงข้อมูลฐานเกณฑ์ภายนอกแบบอัตโนมัติ (Auto-Detect File) ---

def find_safety_stock_file():
    """ฟังก์ชันสแกนหาไฟล์เกณฑ์พัสดุอัตโนมัติจากโครงสร้างภายใน โดยไม่สนใจชื่อไฟล์"""
    # สแกนหาไฟล์ทั้งหมดในโฟลเดอร์ปัจจุบันที่เปิดรันโปรแกรม
    for file in os.listdir('.'):
        if file.endswith('.txt') or file.endswith('.csv'):
            try:
                # ลองเปิดอ่านบรรทัดแรกเพื่อเช็คหัวคอลัมน์สำคัญ
                with open(file, 'r', encoding='utf-8') as f:
                    first_line = f.readline()
                # ถ้าพบคีย์เวิร์ดโครงสร้างหลัก ให้ถือว่าเป็นไฟล์เกณฑ์ Safety Stock ทันที
                if 'SAP_Code' in first_line and 'Total_N3' in first_line:
                    return file
            except Exception:
                continue
    return None

# เรียกตัวสแกนหาไฟล์อัตโนมัติ
detected_file = find_safety_stock_file()

@st.cache_data
def load_safety_stock_from_file(file_path):
    if file_path is not None:
        try:
            # อ่านไฟล์โดยกำหนด encoding ให้รองรับภาษาไทยในชื่อพัสดุ
            df = pd.read_csv(file_path, encoding='utf-8')
            return df
        except Exception as e:
            st.error(f"❌ เกิดข้อผิดพลาดในการอ่านไฟล์เกณฑ์: {e}")
            return None
    return None

# โหลดข้อมูลจากไฟล์ที่สแกนเจอ
df_safety = load_safety_stock_from_file(detected_file)

# ตรวจสอบว่าโหลดไฟล์เกณฑ์สำเร็จหรือไม่ก่อนทำงานต่อ
if df_safety is not None:
    # แสดงชื่อไฟล์ที่ระบบสแกนเจอให้ผู้ใช้ทราบที่ Sidebar
    st.sidebar.success(f"📂 ตรวจพบไฟล์เกณฑ์อัตโนมัติ: `{detected_file}`")
    
    # Sidebar: ตัวเลือกคลัง (ดึงรายชื่อคลังมาจากหัวตารางโดยอัตโนมัติ ตั้งแต่คอลัมน์ที่ 5 เป็นต้นไป)
    st.sidebar.header("⚙️ ตั้งค่าคลังพัสดุ")
    all_columns = df_safety.columns.tolist()
    warehouse_options = [col for col in all_columns if col not in ['No', 'Type', 'SAP_Code', 'Description', 'Unit']]
    
    warehouse_option = st.sidebar.selectbox(
        "เลือกพื้นที่หรือคลังที่ต้องการตรวจสอบ:",
        options=warehouse_options
    )

    # Sidebar: ตัวช่วยอัปโหลดไฟล์ MB52 จาก SAP
    st.sidebar.markdown("---")
    st.sidebar.subheader("📥 อัปเดตยอดคลังจาก SAP")
    uploaded_mb52 = st.sidebar.file_uploader("อัปโหลดไฟล์รายงาน MB52 (Excel หรือ CSV)", type=["xlsx", "csv"])

    # การจัดการข้อมูล MB52
    if uploaded_mb52 is not None:
        try:
            if uploaded_mb52.name.endswith('.csv'):
                df_mb52 = pd.read_csv(uploaded_mb52)
            else:
                df_mb52 = pd.read_excel(uploaded_mb52)
        except Exception as e:
            st.error(f"❌ ไม่สามารถอ่านไฟล์ MB52 ได้: {e}")
            df_mb52 = None
    else:
        st.sidebar.info("💡 กรุณาอัปโหลดไฟล์ MB52 เพื่อนำยอดคงคลังปัจจุบันมาเปรียบเทียบ")
        df_mb52 = None

    # --- ส่วนที่ 2: การคำนวณและประมวลผลตารางเมื่อไฟล์พร้อมทั้งคู่ ---
    if df_mb52 is not None:
        # ทำความสะอาดรหัสพัสดุ ป้องกัน Error จากช่องว่างหรือประเภทตัวแปรที่ไม่ตรงกัน
        df_safety['SAP_Code'] = df_safety['SAP_Code'].astype(str).str.strip()
        
        # สมมติว่าคอลัมน์ใน MB52 ของคุณมีคำว่า 'Material' หรือ 'SAP_Code' 
        # (คุณสามารถเปลี่ยนชื่อ 'Material' ให้ตรงกับหัวข้อในไฟล์จริงของคุณได้ในบรรทัดด้านล่าง)
        mb52_code_col = 'Material' if 'Material' in df_mb52.columns else df_mb52.columns[0]
        mb52_qty_col = 'Unrestricted' if 'Unrestricted' in df_mb52.columns else df_mb52.columns[1]
        
        df_mb52_clean = df_mb52[[mb52_code_col, mb52_qty_col]].copy()
        df_mb52_clean.columns = ['SAP_Code', 'Actual_Qty']
        df_mb52_clean['SAP_Code'] = df_mb52_clean['SAP_Code'].astype(str).str.strip()

        # รวมข้อมูลด้วยรหัสพัสดุ
        df_merge = pd.merge(df_safety, df_mb52_clean, on='SAP_Code', how='left')
        df_merge['Actual_Qty'] = df_merge['Actual_Qty'].fillna(0)

        # จัดฟอร์แมตตาราง 7 แถวตามโครงสร้างที่คุณต้องการ
        df_result = pd.DataFrame()
        df_result['ลำดับ'] = df_merge['No']
        df_result['รหัสพัสดุ'] = df_merge['SAP_Code']
        df_result['ชื่อพัสดุ'] = df_merge['Description']
        df_result['อนุมัติ safety stock'] = df_merge[warehouse_option]
        df_result['จำนวนอุปกรณ์ในคลัง'] = df_merge['Actual_Qty']
        
        # คำนวณคงเหลือ (ผลต่าง) และ เปอร์เซ็นต์ (%)
        df_result['คงเหลือ (ผลต่าง)'] = df_result['จำนวนอุปกรณ์ในคลัง'] - df_result['อนุมัติ safety stock']
        
        # สูตรหาเปอร์เซ็นต์ส่วนขาดเกินเทียบกับเกณฑ์ความปลอดภัย
        df_result['เปอร์เซ็นต์ (%)'] = ((df_result['คงเหลือ (ผลต่าง)'] / df_result['อนุมัติ safety stock']) * 100).round(2)
        df_result['เปอร์เซ็นต์ (%)'] = df_result['เปอร์เซ็นต์ (%)'].fillna(0)

        st.write(f"📊 กำลังแสดงยอดเปรียบเทียบคลัง: **{warehouse_option}**")

        # ตกแต่งสีสันแจ้งเตือนพัสดุขาดแคลน (ติดลบ = สีแดง)
        def alert_low_stock(val):
            return 'background-color: #ffcccc; color: #cc0000; font-weight: bold;' if val < 0 else ''

        styled_df = df_result.style.applymap(alert_low_stock, subset=['คงเหลือ (ผลต่าง)'])
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
        
        # แสดงสรุปด่วน
        shortage_count = len(df_result[df_result['คงเหลือ (ผลต่าง)'] < 0])
        if shortage_count > 0:
            st.error(f"🚨 มีพัสดุต่ำกว่าเกณฑ์ความปลอดภัยทั้งหมด {shortage_count} รายการ! กรุณาตรวจสอบแถวสีแดง")
        else:
            st.success("✅ พัสดุทุกรายการมีจำนวนเพียงพอและปลอดภัยดีครับ")
            
    else:
        # หากยังไม่ได้อัปโหลด MB52 ให้แสดงเฉพาะตารางเกณฑ์ที่มีอยู่ในไฟล์ที่ตรวจเจอไปก่อน
        st.info("📊 ตารางแสดงเกณฑ์ Safety Stock ล่าสุดจากไฟล์ฐานข้อมูล (กรุณาอัปโหลดไฟล์ MB52 เพื่อคำนวณยอดส่วนต่าง)")
        st.dataframe(df_safety[['No', 'SAP_Code', 'Description', 'Unit', warehouse_option]], use_container_width=True, hide_index=True)

else:
    # แสดงข้อความเตือนเมื่อหาไฟล์เกณฑ์ที่มีโครงสร้างแบบเดียวกันไม่เจอเลยในโฟลเดอร์
    st.error("❌ ไม่พบไฟล์เกณฑ์พัสดุ (Safety Stock) ในโฟลเดอร์ระบบ กรุณานำไฟล์ข้อความ (.txt หรือ .csv) มาวางไว้ในโฟลเดอร์เดียวกับโค้ด")
