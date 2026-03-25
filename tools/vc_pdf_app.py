import streamlit as st
import os
import re
import fitz  # PyMuPDF
import io
import zipfile
import tempfile
import shutil

st.set_page_config(page_title="VC PDF 标签处理", layout="wide")

def create_separator_page(doc, ob_name, label_type, index_str, w, h):
    """创建间隔页 - 动态计算字号和居中位置"""
    page = doc.new_page(width=w, height=h)
    
    # 设置字体和参数
    text = ob_name  # OB 编号为主要文本
    label = label_type  # PLT 或 CTNS
    index_info = index_str  # 序号信息
    
    max_font_size = 80
    margin = 50
    width = w
    height = h
    standard_bold_font = "helvetica-bold"
    
    # 1. 动态计算合适的字号（以较长的 OB 编号为基准缩放）
    available_width = width - (margin * 2)
    font_size = max_font_size
    
    try:
        text_width = fitz.get_text_length(text, fontname=standard_bold_font, fontsize=font_size)
    except:
        standard_bold_font = "helvetica-bold"
        text_width = fitz.get_text_length(text, fontname=standard_bold_font, fontsize=font_size)
    
    if text_width > available_width:
        font_size = max_font_size * (available_width / text_width)
    
    # 2. 精确计算居中位置（Label、OB编号、序号三行分布）
    # 第一行：Label (PLT / CTNS)
    label_size = max(font_size * 0.55, 24)
    label_width = fitz.get_text_length(label, fontname=standard_bold_font, fontsize=label_size)
    x_lb = (width - label_width) / 2
    y_lb = (height / 2) - (font_size * 0.5)
    
    page.insert_text((x_lb, y_lb), label, fontsize=label_size, fontname=standard_bold_font, color=(0, 0, 0))

    # 第二行：OB 编号 (居中)
    final_text_width = fitz.get_text_length(text, fontname=standard_bold_font, fontsize=font_size)
    x = (width - final_text_width) / 2
    y = y_lb + font_size * 1.2  # 在 Label 下方
    
    page.insert_text((x, y), text, fontsize=font_size, fontname=standard_bold_font, color=(0, 0, 0))

    # 第三行：A/B 序号
    index_size = label_size
    index_width = fitz.get_text_length(index_info, fontname=standard_bold_font, fontsize=index_size)
    x_idx = (width - index_width) / 2
    y_idx = height - index_size - 20  
    page.insert_text((x_idx, y_idx), index_info, fontsize=index_size, fontname=standard_bold_font, color=(0, 0, 0))

def show_ui(user_info, update_usage_callback):
    st.title("🏷️ VC 板标+箱标处理")
    st.markdown("""
    **功能说明：**
    1. 遍历 `OB` 文件夹，获取 `palletLabels_*.pdf` 和 `cartonLabels*.pdf`。
    2. 插入对应的间隔页 (PLT / CTNS)，并自动添加序号。
    3. 处理结果保存为 `A-B-OB号.pdf`。
    """)

    st.info("提示：请确保 ZIP 内的文件结构符合：`OBxxxx/palletLabels_*.pdf` 和 `OBxxxx/cartonLabels*.pdf`。")
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    if today > user_info['expiry_date']:
        st.error("❌ 账号已过期")
        return
    if user_info['used_count'] >= user_info['total_limit']:
        st.error("❌ 使用次数已耗尽")
        return
    
    uploaded_file = st.file_uploader("上传 ZIP", type="zip")
    if uploaded_file and st.button("处理 ZIP"):
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(io.BytesIO(uploaded_file.read()), 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            items = os.listdir(temp_dir)
            ob_folders = sorted([f for f in items if os.path.isdir(os.path.join(temp_dir, f)) and f.upper().startswith("OB")])
            total_count = len(ob_folders)
            
            if total_count == 0:
                st.warning("ZIP 中未找到 OB 开头的文件夹。")
            else:
                st.info(f"检测到 {total_count} 个待处理任务...")
                processed_files = []
                for i, ob_name in enumerate(ob_folders):
                    current_index = i + 1
                    index_str = f"{current_index}/{total_count}"
                    file_index_str = f"{current_index}-{total_count}"
                    folder_path = os.path.join(temp_dir, ob_name)
                    files = os.listdir(folder_path)
                    
                    pallet_file = next((f for f in files if f.startswith("palletLabels_") and f.endswith(".pdf")), None)
                    carton_file = next((f for f in files if f.startswith("cartonLabels") and f.endswith(".pdf")), None)
                    numeric_file = next((f for f in files if re.match(r'^\d+\.pdf$', f)), None)
                    
                    if not pallet_file or not carton_file:
                        st.warning(f"⏭️ {ob_name} 缺失必要文件 (pallet 或 carton)，已跳过。")
                        continue

                    output_doc = fitz.open()
                    # 1. Pallet
                    with fitz.open(os.path.join(folder_path, pallet_file)) as p_doc:
                        base_rect = p_doc[0].rect
                        w, h = base_rect.width, base_rect.height
                        output_doc.insert_pdf(p_doc)
                    
                    # 2. 纯数字 PDF
                    if numeric_file:
                        with fitz.open(os.path.join(folder_path, numeric_file)) as n_doc:
                            for i_page in range(len(n_doc)):
                                new_p = output_doc.new_page(width=w, height=h)
                                new_p.show_pdf_page(base_rect, n_doc, i_page)
                    
                    # 3. 第一个间隔页
                    create_separator_page(output_doc, ob_name, "PLT", index_str, w, h)
                    # 4. Carton
                    with fitz.open(os.path.join(folder_path, carton_file)) as c_doc:
                        output_doc.insert_pdf(c_doc)
                    # 5. 第二个间隔页
                    create_separator_page(output_doc, ob_name, "CTNS", index_str, w, h)
                    
                    output_filename = f"{file_index_str}-{ob_name}.pdf"
                    buffer = io.BytesIO()
                    output_doc.save(buffer, garbage=3, deflate=True)
                    output_doc.close()
                    buffer.seek(0)
                    processed_files.append((output_filename, buffer))
                    st.write(f"✅ [{index_str}] 已处理: {output_filename}")
                
                if processed_files:
                    # 创建一个 ZIP 文件包含所有处理后的 PDF
                    output_zip = io.BytesIO()
                    with zipfile.ZipFile(output_zip, 'w') as zf:
                        for filename, buffer in processed_files:
                            zf.writestr(filename, buffer.getvalue())
                    output_zip.seek(0)
                    st.download_button(
                        label="下载处理后的 ZIP 文件",
                        data=output_zip,
                        file_name="processed_vc_pdfs.zip",
                        mime="application/zip"
                    )
                    st.success("所有任务处理完毕！请下载 ZIP 文件。")
                    update_usage_callback(user_info['username'])
                else:
                    st.warning("没有成功处理任何文件。")

if __name__ == "__main__":
    show_ui()
