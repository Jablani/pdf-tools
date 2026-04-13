import io
import re
import zipfile
from pathlib import Path
from typing import Dict, List

import fitz  # PyMuPDF


def natural_sort_key(value: str):
    parts = re.split(r'(\d+)', value)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def parse_zip_pdf_files(zip_file) -> Dict[str, bytes]:
    pdf_files = {}

    with zipfile.ZipFile(zip_file, 'r') as zip_ref:
        for file_name in zip_ref.namelist():
            if file_name.lower().endswith('.pdf'):
                with zip_ref.open(file_name) as file:
                    pdf_files[file_name] = file.read()

    return pdf_files


def build_footer_string(filename: str, total_pages: int) -> str:
    base_name = Path(filename).stem
    replaced = base_name.replace(' ', '*')
    return f'{replaced}(total {total_pages} order)'


def calculate_optimal_font_size(text: str, rect: fitz.Rect) -> float:
    max_font_size = 50
    min_font_size = 8
    available_width = rect.width * 0.95

    for font_size in range(max_font_size, min_font_size - 1, -1):
        lines = wrap_text(text, available_width, font_size)
        if len(lines) <= 2:
            return font_size

    return min_font_size


def wrap_text(text: str, max_width: float, font_size: float) -> List[str]:
    lines = []
    current_line = ''

    for char in text:
        test_line = current_line + char
        if fitz.get_text_length(test_line, fontsize=font_size) <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = char

    if current_line:
        lines.append(current_line)

    if len(lines) >= 2 and len(lines[-1]) == 1 and lines[-1] == ')':
        combined = lines[-2] + lines[-1]
        if fitz.get_text_length(combined, fontsize=font_size) <= max_width:
            lines[-2] = combined
            lines.pop()

    return lines


def flatten_pdf_page(doc, page_index):
    old_page = doc[page_index]
    rect = old_page.rect
    new_doc = fitz.open()
    new_page = new_doc.new_page(width=rect.width, height=rect.height)
    new_page.show_pdf_page(new_page.rect, doc, page_index)
    for annot in new_page.annots():
        new_page.delete_annot(annot)
    return new_doc


def add_footer_to_last_page(doc, footer_text):
    if doc.page_count == 0:
        return
    last_index = doc.page_count - 1
    flat_doc = flatten_pdf_page(doc, last_index)
    flat_page = flat_doc[0]
    width = flat_page.rect.width
    height = flat_page.rect.height
    additional_height = height * 0.15
    new_height = height + additional_height
    final_doc = fitz.open()
    final_page = final_doc.new_page(width=width, height=new_height)
    final_page.show_pdf_page(fitz.Rect(0, 0, width, height), flat_doc, 0)
    margin = 20
    footer_rect = fitz.Rect(margin, height + 5, width - margin, new_height - 5)
    font_size = calculate_optimal_font_size(footer_text, footer_rect)
    font_size = min(font_size + 2, 40)
    text_lines = wrap_text(footer_text, footer_rect.width, font_size)
    if len(text_lines) > 2:
        text_lines = text_lines[:2]
    total_text_height = font_size * len(text_lines) * 1.2
    y_start = footer_rect.y0 + (footer_rect.height - total_text_height) / 2 + font_size
    for i, line in enumerate(text_lines):
        final_page.insert_text(
            (footer_rect.x0, y_start + i * font_size * 1.2),
            line,
            fontsize=font_size,
            color=(0, 0, 0)
        )
    doc.delete_page(last_index)
    doc.insert_pdf(final_doc)
    flat_doc.close()
    final_doc.close()


def create_pdf_with_footer(pdf_content, filename):
    doc = fitz.open(stream=pdf_content, filetype='pdf')
    total_pages = doc.page_count
    footer_text = build_footer_string(filename, total_pages)
    add_footer_to_last_page(doc, footer_text)
    output_buffer = io.BytesIO()
    doc.save(output_buffer)
    doc.close()
    return output_buffer.getvalue()


def create_zip_from_pdfs(pdf_files, output_name='output.zip'):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_archive:
        for filename, content in pdf_files.items():
            zip_archive.writestr(filename, content)
    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def process_feeder_dropship_zip(zip_file):
    pdf_files = parse_zip_pdf_files(zip_file)
    pdf_count = len(pdf_files)
    folders = set()
    warnings = []
    for filename in pdf_files:
        path = Path(filename)
        parent = str(path.parent)
        if parent != '.':
            folders.add(parent)
        if re.search(r' {2,}', filename):
            warnings.append(filename)
    folder_count = len(folders)
    renamed_pdfs = {}
    sorted_items = sorted(
        pdf_files.items(),
        key=lambda item: natural_sort_key(Path(item[0]).name)
    )
    for index, (original_name, content) in enumerate(sorted_items, start=1):
        base_name = Path(original_name).name
        new_name = f'{index}.{base_name}'
        renamed_pdfs[new_name] = create_pdf_with_footer(content, new_name)
    output_zip_bytes = create_zip_from_pdfs(renamed_pdfs, output_name='feeder_dropship_output.zip')
    stats = {'pdf_count': pdf_count, 'folder_count': folder_count, 'warnings': warnings}
    return output_zip_bytes, stats


def show_ui(user_info, update_usage_callback):
    import streamlit as st
    if 'result' not in st.session_state:
        st.session_state.result = None
    
    st.title('喂食器一件代发')
    st.markdown('''
    **说明：**
    1. 上传包含PDF的ZIP文件。
    2. PDF将被编号。
    3. 备注将被添加到最后一页。
    ''')
    zip_file = st.file_uploader('上传PDF ZIP', type=['zip'])
    if zip_file and st.button('处理', type='primary', use_container_width=True):
        with st.spinner('处理中...'):
            try:
                output_zip_bytes, stats = process_feeder_dropship_zip(zip_file)
                st.session_state.result = {'zip': output_zip_bytes, 'stats': stats}
                update_usage_callback(user_info['username'])
                st.success('完成！')
            except Exception as e:
                st.error(f'错误: {str(e)}')
                st.exception(e)

    if st.session_state.result:
        stats = st.session_state.result['stats']
        st.subheader("📋 分析结果")
        st.info(f"共处理{stats['folder_count']}个文件夹，{stats['pdf_count']}个pdf文档")
        if stats['warnings']:
            for w in stats['warnings']:
                st.warning(f"请检查{w}的文件名是否包含多余空格")
        st.download_button(
            label='下载ZIP',
            data=st.session_state.result['zip'],
            file_name='feeder_dropship_output.zip',
            mime='application/zip',
            use_container_width=True
        )
