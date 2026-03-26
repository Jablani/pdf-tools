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

def extract_obc_code(page_text):
    """从页面文本的第一行解析OBC码"""
    lines = page_text.strip().split('\n')
    if lines:
        first_line = lines[0].strip()
        # OBC码通常在第一行，格式如: OBC0262603240TG
        if first_line.startswith('OBC'):
            return first_line
    return None

def find_bulk_picking_list_page(bulk_picking_pdf_bytes, ob_name):
    """
    从BulkPickingList PDF中找到对应OBC的页面
    返回该页面的副本文档和页码
    """
    try:
        doc = fitz.open(stream=bulk_picking_pdf_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()
            obc_code = extract_obc_code(text)
            if obc_code and obc_code.upper() == ob_name.upper():
                # 找到对应的OBC，创建一个包含该页的新文档
                new_doc = fitz.open()
                new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
                doc.close()
                return new_doc
        doc.close()
    except Exception as e:
        st.error(f"处理BulkPickingList PDF出错: {str(e)}")
    return None

def process_bol(ob_name, bol_bytes, bulk_picking_pdf_bytes=None):
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
    
    # 如果提供了BulkPickingList PDF，添加对应的Freight Pick List页面
    if bulk_picking_pdf_bytes:
        bulk_doc = find_bulk_picking_list_page(bulk_picking_pdf_bytes, ob_name)
        if bulk_doc:
            # 插入找到的页面
            processed_doc.insert_pdf(bulk_doc)
            bulk_doc.close()
    
    return processed_doc

def show_ui(user_info, update_usage_callback):
    st.title("📄 BOL PDF 批量处理 (Process BOL 2.0)")
    st.markdown("""
    **功能说明：**
    1. 查找 `OB` 文件夹下的 `BOL.PDF`。
    2. 在右上角标注 `OB` 编号，并复制一页（原页+标注页）。
    3. （可选）上传 BulkPickingList PDF，自动提取对应 OBC 的 Freight Pick List 页面并插入。
    4. 合并所有处理后的 BOL 为一个 PDF。
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
    col1, col2 = st.columns(2)
    with col1:
        uploaded_zip = st.file_uploader("上传 BOL ZIP", type="zip", key="bol_zip")
    with col2:
        uploaded_bulk_picking = st.file_uploader("上传 BulkPickingList PDF（可选）", type="pdf", key="bulk_picking")
    
    if uploaded_zip and st.button("解压并处理"):
        bulk_picking_bytes = None
        if uploaded_bulk_picking:
            bulk_picking_bytes = uploaded_bulk_picking.read()
        
        with zipfile.ZipFile(uploaded_zip, 'r') as z:
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
                            doc = process_bol(ob_name, f.read(), bulk_picking_bytes)
                            all_docs.append(doc)
                        
                        status_text = f"✅ 处理完成: {ob_name}"
                        if bulk_picking_bytes:
                            status_text += " (已添加 Freight Pick List)"
                        st.write(status_text)
                update_usage_callback(user_info['username'])
                if all_docs:
                    merged = fitz.open()
                    for d in all_docs:
                        merged.insert_pdf(d)
                        d.close()
                    st.download_button(label="下载 BOL_ALL.pdf", data=merged.tobytes(), file_name="BOL_ALL.pdf", mime="application/pdf")
                    merged.close()

if __name__ == "__main__":
    show_ui()
