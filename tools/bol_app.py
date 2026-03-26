import streamlit as st
import os
import io
import fitz  # PyMuPDF
import zipfile
from datetime import datetime

def add_ob_label(page, ob_name):
    page_width = page.rect.width
    font_size = 18
    bold_font = "helv"  # 使用标准内置字体
    approx_text_width = len(ob_name) * font_size * 0.62
    margin_right = 15
    margin_top = 70
    text_x = page_width - margin_right - approx_text_width
    text_y = margin_top
    page.insert_text((text_x, text_y), ob_name, fontname=bold_font, fontsize=font_size, color=(0, 0, 0))

def process_bol(ob_name, bol_bytes):
    src_doc = fitz.open(stream=bol_bytes, filetype="pdf")
    processed_doc = fitz.open()
    for page_index in range(len(src_doc)):
        processed_doc.insert_pdf(src_doc, from_page=page_index, to_page=page_index)
        labeled_page = processed_doc[-1]
        add_ob_label(labeled_page, ob_name)
        
        tmp_buf = io.BytesIO()
        tmp_doc = fitz.open()
        tmp_doc.insert_pdf(processed_doc, from_page=len(processed_doc) - 1, to_page=len(processed_doc) - 1)
        tmp_doc.save(tmp_buf)
        tmp_doc.close()
        tmp_buf.seek(0)
        copy_doc = fitz.open(stream=tmp_buf, filetype="pdf")
        processed_doc.insert_pdf(copy_doc)
        copy_doc.close()
    src_doc.close()
    return processed_doc

def show_ui(user_info, update_usage_callback):
    st.title("📄 BOL PDF 批量处理 (Process BOL 2.0)")
    st.markdown("""
    **功能说明：**
    1. 查找 `OB` 文件夹下的 `BOL.PDF`。
    2. 在右上角标注 `OB` 编号，并复制一页（原页+标注页）。
    3. 合并所有处理后的 BOL 为一个 PDF。
    """)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    if today > user_info['expiry_date']:
        st.error("❌ 账号已过期")
        return
    if user_info['used_count'] >= user_info['total_limit']:
        st.error("❌ 使用次数已耗尽")
        return
    
    # 侧边栏配置
    uploaded_file = st.file_uploader("上传 ZIP", type="zip")
    if uploaded_file and st.button("解压并处理"):
        with zipfile.ZipFile(uploaded_file, 'r') as z:
            all_files = z.namelist()
            ob_dirs = set()
            for f in all_files:
                parts = f.split('/')
                for p in parts:
                    if p.upper().startswith("OB"):
                        ob_dirs.add(p); break
            
            if not ob_dirs:
                st.error("ZIP 中未检测到 OB 文件夹。")
            else:
                all_docs = []
                for ob_name in sorted(list(ob_dirs)):
                    bol_path = next((f for f in all_files if f.upper().endswith(f"{ob_name.upper()}/BOL.PDF")), None)
                    if bol_path:
                        with z.open(bol_path) as f:
                            doc = process_bol(ob_name, f.read())
                            all_docs.append(doc)
                        update_usage_callback(user_info['username'])
                        st.write(f"✅ 处理完成: {ob_name}")
                
                if all_docs:
                    merged = fitz.open()
                    for d in all_docs:
                        merged.insert_pdf(d)
                        d.close()
                    st.download_button(label="下载 BOL_ALL.pdf", data=merged.tobytes(), file_name="BOL_ALL.pdf", mime="application/pdf")
                    merged.close()

if __name__ == "__main__":
    show_ui()
