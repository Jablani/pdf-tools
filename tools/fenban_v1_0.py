import streamlit as st
import os
import zipfile
import tempfile
import fitz  # PyMuPDF
import pandas as pd
import re
import io
from pathlib import Path
from collections import defaultdict

def extract_amzncc_from_page(page):
    """从 PDF 页面底部提取 AMZNCC 箱码"""
    text = page.get_text("text")
    # 匹配 AMZNCC 开头的箱码，通常在底部
    match = re.search(r'AMZNCC[A-Z0-9]+', text)
    return match.group(0) if match else None

def extract_upc_from_page(page):
    """从 PDF 页面提取 UPC/EAN (12-13位数字)"""
    text = page.get_text("text")
    # 优先匹配 "EAN :" 或 "UPC :" 后的数字
    labeled_match = re.search(r'(?:EAN|UPC)\s*:\s*(\d{12,13})', text, re.IGNORECASE)
    if labeled_match:
        return labeled_match.group(1)
    
    # 备选：匹配 12 或 13 位连续数字
    matches = re.findall(r'\b\d{12,13}\b', text)
    return matches[0] if matches else None

def process_logic(main_zip_file, freight_file, upc_sku_file):
    """核心处理逻辑"""
    log_info = [] # 用于在前端展示日志
    
    # 1. 加载映射表
    # UPC_SKU 表: productName (D列), productSku (C列)
    upc_sku_df = pd.read_excel(upc_sku_file)
    upc_to_sku = {}
    for i, row in upc_sku_df.iterrows():
        # 获取 D列 (index 3) 和 C列 (index 2)
        try:
            u_raw = row.iloc[3]
            s_raw = row.iloc[2]
            u = str(u_raw).strip().replace(".0", "") if pd.notna(u_raw) else None
            s = str(s_raw).strip() if pd.notna(s_raw) else "Unknown"
            if u:
                upc_to_sku[u] = s
        except Exception as e:
            log_info.append(f"⚠️ UPC_SKU 表第 {i+2} 行读取异常: {str(e)}")

    # Freight 表: AQ列(index 42)是SKU, AL列(index 37)是IB
    freight_df = pd.read_excel(freight_file)
    sku_to_ib = {}
    for i, row in freight_df.iterrows():
        try:
            s_raw = row.iloc[42]
            i_raw = row.iloc[37]
            s = str(s_raw).strip() if pd.notna(s_raw) else None
            ib = str(i_raw).strip() if pd.notna(i_raw) else "Unknown"
            if s:
                sku_to_ib[s] = ib
        except Exception as e:
            log_info.append(f"⚠️ Freight 表第 {i+2} 行读取异常: {str(e)}")

    output_excels = []
    total_logs = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        with zipfile.ZipFile(main_zip_file, 'r') as z:
            z.extractall(tmp_path)
        
        # 遍历所有 OBC 文件夹
        obc_folders = [f for f in tmp_path.iterdir() if f.is_dir() and f.name.startswith("OBC")]
        obc_count = len(obc_folders)
        unprocessed_folders = []
        
        for obc_folder in obc_folders:
            obc_name = obc_folder.name
            
            # A. 确定板数并分配字母
            pallet_pdfs = list(obc_folder.glob("palletLabels*.pdf"))
            pallet_count = 0
            for p in pallet_pdfs:
                doc = fitz.open(p)
                pallet_count += len(doc)
                doc.close()
            
            letters = [chr(i) for i in range(65, 65 + pallet_count)]
            
            # B. 读取 ASN 表关联
            asn_files = list(obc_folder.glob("ASN*.xlsx"))
            if not asn_files: 
                unprocessed_folders.append(obc_name)
                continue
            
            asn_df = pd.read_excel(asn_files[0])
            # A列板码, B列箱码
            unique_pallets = asn_df.iloc[:, 0].unique().tolist()
            pallet_to_letter = {code: letters[i] if i < len(letters) else f"Z{i}" for i, code in enumerate(unique_pallets)}
            carton_to_pallet = dict(zip(asn_df.iloc[:, 1].astype(str), asn_df.iloc[:, 0].astype(str)))

            # C. 遍历 carton PDF 提取箱码和 UPC
            carton_pdfs = list(obc_folder.glob("carton*.pdf"))
            data_rows = []
            
            for c_pdf in carton_pdfs:
                doc = fitz.open(c_pdf)
                for page in doc:
                    amzncc = extract_amzncc_from_page(page)
                    upc = extract_upc_from_page(page)
                    if amzncc and upc:
                        p_code = carton_to_pallet.get(amzncc)
                        letter = pallet_to_letter.get(p_code, "Unknown")
                        
                        # 清洗 UPC 字符串并进行比对
                        clean_upc = str(upc).strip().replace(".0", "")
                        sku = upc_to_sku.get(clean_upc, "Unknown")
                        
                        # 日志点：记录每次匹配
                        match_log = f"OBC: {obc_name} | PDF-UPC: '{clean_upc}' -> SKU: {sku}"
                        
                        # 如果通过 UPC 找到了 SKU，再去查找 IB
                        ib = "Unknown"
                        if sku != "Unknown":
                            ib = sku_to_ib.get(sku, "Unknown")
                            match_log += f" -> IB: {ib}"
                        else:
                            # 如果没匹配上，检查一下映射表里的键
                            nearby_keys = [k for k in upc_to_sku.keys() if clean_upc in k or k in clean_upc]
                            if nearby_keys:
                                match_log += f" (未匹配, 映射表里有类似键: {nearby_keys})"

                        total_logs.append(match_log)
                        data_rows.append({"IB": ib, "SKU": sku, "UPC": clean_upc, "箱数": 1, "字母板号": letter})
                doc.close()

            # D. 聚合生成 Excel
            if data_rows:
                df = pd.DataFrame(data_rows)
                res_df = df.groupby(["IB", "SKU", "UPC", "字母板号"], as_index=False)["箱数"].sum()
                res_df = res_df[["IB", "SKU", "UPC", "箱数", "字母板号"]]
                
                buf = io.BytesIO()
                res_df.to_excel(buf, index=False)
                output_excels.append((f"{obc_name}.xlsx", buf.getvalue()))
            else:
                unprocessed_folders.append(obc_name)

    processed_count = len(output_excels)
    unprocessed_count = obc_count - processed_count
    
    if output_excels:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for name, data in output_excels:
                zip_file.writestr(name, data)
        zip_data = zip_buffer.getvalue()
    else:
        zip_data = None

    return zip_data, total_logs, obc_count, processed_count, unprocessed_count, unprocessed_folders

def show_ui(u_info, callback):
    st.title("📂 分板处理工具 v1.0")
    st.info("作用：处理 OBC 压缩包、Freight 表和 UPC 映射表，生成分板 Excel。")

    col1, col2, col3 = st.columns(3)
    with col1:
        zip_file = st.file_uploader("1. 上传 OBC 压缩包", type=['zip'])
    with col2:
        freight_file = st.file_uploader("2. 上传 Freight 表 (AQ:SKU, AL:IB)", type=['xlsx'])
    with col3:
        upc_sku_file = st.file_uploader("3. 上传 UPC_SKU 映射表", type=['xlsx'])

    if st.button("开始处理", width='stretch'):
        if zip_file and freight_file and upc_sku_file:
            with st.spinner("正在解析并生成分板数据..."):
                try:
                    results, logs, obc_count, processed_count, unprocessed_count, unprocessed_folders = process_logic(zip_file, freight_file, upc_sku_file)
                    
                    if logs:
                        with st.expander("🔍 详细处理日志"):
                            for l in logs:
                                if "Unknown" in l:
                                    st.error(l)
                                else:
                                    st.text(l)

                    if results:
                        if unprocessed_count > 0:
                            st.success(f"共{obc_count}个OBC文件夹，成功处理{processed_count}个，未处理{unprocessed_count}个：{', '.join(unprocessed_folders)}")
                        else:
                            st.success(f"共{obc_count}个OBC文件夹，成功处理{processed_count}个，未处理{unprocessed_count}个")
                        st.download_button("📥 下载分板表格压缩包", results, file_name="fenban_results.zip", mime="application/zip")
                        callback(u_info['username'])
                    else:
                        st.warning("未能在压缩包内找到有效的 OBC 文件夹或匹配数据。")
                except Exception as e:
                    st.error(f"处理失败: {str(e)}")
        else:
            st.error("请上传所有必需的文件。")
