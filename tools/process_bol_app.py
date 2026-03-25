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

def show_ui():
    st.title("📄 BOL PDF 批量处理 (Process BOL 2.0)")
    st.markdown("""
    **功能说明：**
    1. 查找 `OB` 文件夹下的 `BOL.PDF`。
    2. 在右上角标注 `OB` 编号，并复制一页（原页+标注页）。
    3. 合并所有处理后的 BOL 为一个 PDF。
    """)

    # 侧边栏配置
    mode = st.sidebar.selectbox("选择运行模式", ["本地路径处理", "上传 ZIP 处理"])

    if mode == "本地路径处理":
        default_path = "/Volumes/MacOpenclaw/VC"
        base_dir = st.text_input("输入待处理的目录路径 (包含 OB 文件夹)", value=default_path)
        if st.button("开始处理"):
            if not os.path.exists(base_dir):
                st.error(f"路径不存在: {base_dir}")
            else:
                items = sorted(os.listdir(base_dir))
                ob_folders = [f for f in items if os.path.isdir(os.path.join(base_dir, f)) and f.upper().startswith("OB")]
                
                if not ob_folders:
                    st.warning("未找到 OB 开头的文件夹。")
                else:
                    st.info(f"找到 {len(ob_folders)} 个 OB 文件夹，开始处理...")
                    all_docs = []
                    progress_bar = st.progress(0)
                    
                    for idx, ob_name in enumerate(ob_folders):
                        folder_path = os.path.join(base_dir, ob_name)
                        bol_file = None
                        for fname in os.listdir(folder_path):
                            if fname.upper() == "BOL.PDF":
                                bol_file = os.path.join(folder_path, fname)
                                break
                        
                        if bol_file:
                            try:
                                with open(bol_file, "rb") as f:
                                    doc = process_bol(ob_name, f.read())
                                    all_docs.append(doc)
                                st.write(f"✅ 处理完成: {ob_name}")
                            except Exception as e:
                                st.error(f"❌ {ob_name} 处理失败: {e}")
                        else:
                            st.warning(f"⏭️ {ob_name} 目录下未找到 BOL.PDF，已跳过。")
                        
                        progress_bar.progress((idx + 1) / len(ob_folders))
                    
                    if all_docs:
                        merged = fitz.open()
                        for d in all_docs:
                            merged.insert_pdf(d)
                            d.close()
                        
                        out_bytes = merged.tobytes()
                        merged.close()
                        st.success("全部处理完成！")
                        st.download_button(label="下载合并后的 BOL_ALL.pdf", data=out_bytes, file_name="BOL_ALL.pdf", mime="application/pdf")

    elif mode == "上传 ZIP 处理":
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
