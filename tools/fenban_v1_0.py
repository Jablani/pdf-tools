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

def clean_cell(value):
    """清洗 Excel 单元格，避免数字条码被读成 123.0。"""
    if pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text or None

def make_letter(index):
    """生成 A, B, ... Z, AA, AB 形式的板号。"""
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result

def letter_sort_key(letter):
    if not letter or letter == "Unknown":
        return (9999, "")
    total = 0
    for char in str(letter):
        if not char.isalpha():
            return (9998, str(letter))
        total = total * 26 + (ord(char.upper()) - 64)
    return (total, str(letter))

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

def extract_carton_no_from_page(page):
    text = page.get_text("text")
    match = re.search(r'Carton#:\s*(\d+)', text, re.IGNORECASE)
    return int(match.group(1)) if match else None

def extract_obc_upc_sku_from_filenames(obc_folder):
    """从 OBC 内商品 PDF 文件名提取 (原SKU) 和 UPC，优先级高于全局 UPC_SKU 表。"""
    mapping = {}
    for pdf_path in obc_folder.glob("*.pdf"):
        name = pdf_path.name
        lower_name = name.lower()
        if lower_name.startswith(("cartonlabels", "palletlabels")) or lower_name == "bol.pdf":
            continue

        sku_match = re.search(r'原SKU[:：]\s*([^）)]+)', name)
        upc_match = re.search(r'(\d{12,13})', name)
        if sku_match and upc_match:
            sku = sku_match.group(1).strip()
            upc = upc_match.group(1).strip()
            mapping[upc] = sku
    return mapping

def unique_append(target, value):
    if value and value not in target:
        target.append(value)

def is_hidden_zip_artifact(path):
    return any(part.startswith(".") or part == "__MACOSX" for part in path.parts)

def is_valid_obc_folder(folder):
    return (
        folder.is_dir()
        and folder.name.startswith("OBC")
        and not is_hidden_zip_artifact(folder)
        and (
            any(folder.glob("ASN*.xlsx"))
            or any(folder.glob("carton*.pdf"))
            or any(folder.glob("palletLabels*.pdf"))
        )
    )

def allocate_ib_for_rows(rows, allocations, obc_name, sku, total_logs):
    """按 Freight 箱数把同一个 OBC+SKU 下的箱子分摊给多个 IB。"""
    if not rows:
        return []

    if not allocations:
        for row in rows:
            row["IB"] = "Unknown"
        total_logs.append(f"❌ 未找到 Freight 匹配 | OBC: {obc_name} | SKU: {sku} | 分板表: {len(rows)} 箱")
        return rows

    sorted_rows = sorted(
        rows,
        key=lambda r: (
            letter_sort_key(r.get("字母板号")),
            r.get("carton_no") if r.get("carton_no") is not None else 999999,
            r.get("page_index", 999999),
        ),
    )

    expanded_ibs = []
    for item in allocations:
        qty = int(item.get("qty") or 0)
        if qty > 0:
            expanded_ibs.extend([item["ib"]] * qty)

    if len(allocations) > 1:
        detail = ", ".join(f"{item['ib']}={int(item.get('qty') or 0)}箱" for item in allocations)
        total_logs.append(f"ℹ️ 多IB分摊 | OBC: {obc_name} | SKU: {sku} | {detail}")

    for index, row in enumerate(sorted_rows):
        row["IB"] = expanded_ibs[index] if index < len(expanded_ibs) else "Unknown"

    if len(sorted_rows) > len(expanded_ibs):
        total_logs.append(
            f"❌ Freight 箱数不足 | OBC: {obc_name} | SKU: {sku} | "
            f"分板表: {len(sorted_rows)} 箱 | Freight: {len(expanded_ibs)} 箱"
        )

    return sorted_rows

def process_logic(main_zip_file, freight_file, upc_sku_file):
    """核心处理逻辑"""
    log_info = [] # 用于在前端展示日志
    
    # 1. 加载映射表
    # UPC_SKU 表: productName (D列), productSku (C列)
    upc_sku_df = pd.read_excel(upc_sku_file)
    obc_upc_to_sku = {}
    upc_to_skus = defaultdict(list)
    for i, row in upc_sku_df.iterrows():
        # 获取 B列 OBC、D列 UPC/productName 和 C列 SKU/productSku
        try:
            obc = clean_cell(row.iloc[1]) if len(row) > 1 else None
            u = clean_cell(row.iloc[3]) if len(row) > 3 else None
            s = clean_cell(row.iloc[2]) if len(row) > 2 else None
            if u and s:
                if obc:
                    obc_upc_to_sku[(obc, u)] = s
                unique_append(upc_to_skus[u], s)
        except Exception as e:
            log_info.append(f"⚠️ UPC_SKU 表第 {i+2} 行读取异常: {str(e)}")

    # Freight 表: A列是OBC, AQ列(index 42)是SKU, AL列(index 37)是IB, AO列(index 40)是出库箱数
    freight_df = pd.read_excel(freight_file)
    freight_allocations = defaultdict(list)
    freight_expected_by_obc = defaultdict(int)
    freight_expected_by_obc_sku = defaultdict(int)
    for i, row in freight_df.iterrows():
        try:
            obc = clean_cell(row.iloc[0]) if len(row) > 0 else None
            s = clean_cell(row.iloc[42]) if len(row) > 42 else None
            ib = clean_cell(row.iloc[37]) if len(row) > 37 else "Unknown"
            qty_raw = row.iloc[40] if len(row) > 40 else 0
            qty = int(float(qty_raw)) if pd.notna(qty_raw) else 0
            if obc and s:
                freight_allocations[(obc, s)].append({"ib": ib or "Unknown", "qty": qty, "row": i})
                freight_expected_by_obc[obc] += qty
                freight_expected_by_obc_sku[(obc, s)] += qty
        except Exception as e:
            log_info.append(f"⚠️ Freight 表第 {i+2} 行读取异常: {str(e)}")

    output_excels = []
    total_logs = list(log_info)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        with zipfile.ZipFile(main_zip_file, 'r') as z:
            z.extractall(tmp_path)
        
        # 遍历所有 OBC 文件夹
        obc_folders = sorted(
            [f for f in tmp_path.rglob("OBC*") if is_valid_obc_folder(f)],
            key=lambda p: str(p),
        )
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
            
            letters = [make_letter(i) for i in range(pallet_count)]
            
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
            filename_upc_to_sku = extract_obc_upc_sku_from_filenames(obc_folder)

            # C. 遍历 carton PDF 提取箱码和 UPC
            carton_pdfs = list(obc_folder.glob("carton*.pdf"))
            raw_rows = []
            
            for c_pdf in carton_pdfs:
                doc = fitz.open(c_pdf)
                for page_index, page in enumerate(doc):
                    amzncc = extract_amzncc_from_page(page)
                    upc = extract_upc_from_page(page)
                    if amzncc and upc:
                        p_code = carton_to_pallet.get(amzncc)
                        letter = pallet_to_letter.get(p_code, "Unknown")
                        
                        # 清洗 UPC 字符串并进行比对
                        clean_upc = clean_cell(upc)
                        sku = (
                            filename_upc_to_sku.get(clean_upc)
                            or obc_upc_to_sku.get((obc_name, clean_upc))
                        )
                        if not sku:
                            candidates = upc_to_skus.get(clean_upc, [])
                            if len(candidates) == 1:
                                sku = candidates[0]
                            elif len(candidates) > 1:
                                freight_skus = [
                                    item for item in candidates
                                    if (obc_name, item) in freight_allocations
                                ]
                                if len(freight_skus) == 1:
                                    sku = freight_skus[0]
                                else:
                                    sku = "Unknown"
                            else:
                                sku = "Unknown"
                        
                        # 日志点：记录每次匹配
                        match_log = f"OBC: {obc_name} | PDF-UPC: '{clean_upc}' -> SKU: {sku}"
                        
                        if sku != "Unknown":
                            allocations = freight_allocations.get((obc_name, sku), [])
                            if allocations:
                                ibs = ", ".join(item["ib"] for item in allocations)
                                match_log += f" -> Freight IB: {ibs}"
                            else:
                                match_log += " -> Freight IB: Unknown"
                        else:
                            # 如果没匹配上，检查一下映射表里的键
                            nearby_keys = [k for k in upc_to_skus.keys() if clean_upc in k or k in clean_upc]
                            if nearby_keys:
                                match_log += f" (未匹配, 映射表里有类似键: {nearby_keys})"

                        total_logs.append(match_log)
                        raw_rows.append({
                            "IB": "Unknown",
                            "SKU": sku,
                            "UPC": clean_upc,
                            "箱数": 1,
                            "字母板号": letter,
                            "carton_no": extract_carton_no_from_page(page),
                            "page_index": page_index,
                        })
                doc.close()

            data_rows = []
            rows_by_sku = defaultdict(list)
            for row in raw_rows:
                rows_by_sku[row["SKU"]].append(row)

            for sku, rows in rows_by_sku.items():
                if sku == "Unknown":
                    data_rows.extend(rows)
                    continue
                allocations = freight_allocations.get((obc_name, sku), [])
                data_rows.extend(allocate_ib_for_rows(rows, allocations, obc_name, sku, total_logs))

            # D. 聚合生成 Excel
            if data_rows:
                df = pd.DataFrame(data_rows)
                res_df = df.groupby(["IB", "SKU", "UPC", "字母板号"], as_index=False)["箱数"].sum()
                res_df = res_df[["IB", "SKU", "UPC", "箱数", "字母板号"]]

                output_total = int(res_df["箱数"].sum())
                freight_total = int(freight_expected_by_obc.get(obc_name, 0))
                if output_total != freight_total:
                    total_logs.append(
                        f"❌ 箱数不一致 | OBC: {obc_name} | 分板表: {output_total} 箱 | "
                        f"Freight: {freight_total} 箱 | 差异: {output_total - freight_total} 箱"
                    )

                output_by_sku = df.groupby("SKU")["箱数"].sum().to_dict()
                freight_skus = {
                    sku for (freight_obc, sku), expected in freight_expected_by_obc_sku.items()
                    if freight_obc == obc_name and expected
                }
                for sku in sorted(set(output_by_sku) | freight_skus):
                    output_qty = int(output_by_sku.get(sku, 0))
                    freight_qty = int(freight_expected_by_obc_sku.get((obc_name, sku), 0))
                    if output_qty != freight_qty:
                        total_logs.append(
                            f"❌ SKU箱数不一致 | OBC: {obc_name} | SKU: {sku} | "
                            f"分板表: {output_qty} 箱 | Freight: {freight_qty} 箱 | 差异: {output_qty - freight_qty} 箱"
                        )
                
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
                    
                    if results:
                        st.download_button("📥 下载分板表格压缩包", results, file_name="fenban_results.zip", mime="application/zip")

                        if unprocessed_count > 0:
                            st.success(f"共{obc_count}个OBC文件夹，成功处理{processed_count}个，未处理{unprocessed_count}个：{', '.join(unprocessed_folders)}")
                        else:
                            st.success(f"共{obc_count}个OBC文件夹，成功处理{processed_count}个，未处理{unprocessed_count}个")

                        if logs:
                            with st.expander("🔍 详细处理日志"):
                                for l in logs:
                                    if "❌" in l or "Unknown" in l or "不一致" in l:
                                        st.error(l)
                                    elif "⚠️" in l:
                                        st.warning(l)
                                    else:
                                        st.text(l)

                            error_logs = [l for l in logs if "❌" in l or "Unknown" in l or "不一致" in l]
                            warning_logs = [l for l in logs if "⚠️" in l]

                            if error_logs:
                                st.error(f"发现 {len(error_logs)} 条异常日志：")
                                for l in error_logs:
                                    st.error(l)

                            if warning_logs:
                                st.warning(f"发现 {len(warning_logs)} 条警告日志：")
                                for l in warning_logs:
                                    st.warning(l)

                        callback(u_info['username'])
                    else:
                        st.warning("未能在压缩包内找到有效的 OBC 文件夹或匹配数据。")
                except Exception as e:
                    st.error(f"处理失败: {str(e)}")
        else:
            st.error("请上传所有必需的文件。")
