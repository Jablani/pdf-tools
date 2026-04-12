import streamlit as st
import pandas as pd
from pathlib import Path
from typing import List, Dict, Tuple
from collections import defaultdict
import io
import sys
import zipfile
import tempfile
import os
import re
import fitz  # PyMuPDF


class PDFMergeAnalyzer:
    """分析 Excel 数据并生成 PDF 合并方案"""

    def __init__(self, excel_file):
        """
        初始化分析器

        Args:
            excel_file: Excel 文件对象或路径
        """
        # 读取 Excel 文件
        if hasattr(excel_file, 'read'):  # Streamlit 上传的文件对象
            self.df = pd.read_excel(excel_file, header=None)
        else:  # 文件路径
            self.df = pd.read_excel(excel_file, header=None)

        self.sku_names = self.df.iloc[1]  # 获取 SKU 名称行（第2行，0-based索引为1）

    def parse_range(self, range_str: str) -> Tuple[int, int, int, int]:
        """
        解析范围字符串，如 'D56:Q60'

        Args:
            range_str: 范围字符串 (如 'D56:Q60')

        Returns:
            (start_row, end_row, start_col, end_col) - 都是 0-based
        """
        parts = range_str.split(':')
        if len(parts) != 2:
            raise ValueError(f"Invalid range format: {range_str}")

        start_cell, end_cell = parts[0].strip(), parts[1].strip()

        # 解析起始单元格
        start_col = ord(start_cell[0].upper()) - ord('A')
        start_row = int(start_cell[1:]) - 1  # 转为 0-based

        # 解析结束单元格
        end_col = ord(end_cell[0].upper()) - ord('A')
        end_row = int(end_cell[1:]) - 1  # 转为 0-based

        return start_row, end_row, start_col, end_col

    def analyze_range(self, range_str: str, debug: bool = False) -> Dict:
        """
        分析给定范围内的单 SKU 和混合 SKU 行

        数据结构：
        - D~P 列：件数（数量）
        - Q 列：页数（最后一列）
        - 分类：相同 SKU 且件数相同的可以合并

        Args:
            range_str: 范围字符串 (如 'D56:Q60')
            debug: 是否输出调试信息

        Returns:
            包含分析结果的字典
        """
        start_row, end_row, start_col, end_col = self.parse_range(range_str)

        # 提取范围内的数据
        range_data = self.df.iloc[start_row:end_row+1, start_col:end_col+1]

        if debug:
            st.write(f"**调试信息 - 范围: {range_str}**")
            st.write(f"行索引: {start_row}-{end_row}, 列索引: {start_col}-{end_col}")
            st.write(f"SKU 名称: {list(self.sku_names.iloc[start_col:end_col+1])}")
            st.dataframe(range_data)

        # 分析每一行
        single_sku_rows = []  # 单 SKU 行：[{sku, qty, excel_row, col_idx, rel_col_idx, pages}, ...]
        mixed_sku_rows = []   # 混合 SKU 行：[{excel_row, sku_qty_pairs, pages, sku_count}, ...]

        for row_idx, row in enumerate(range_data.itertuples(index=False)):
            excel_row = start_row + row_idx + 1  # 1-based 行号

            # 最后一列是页数（Q 列）
            pages = row[-1] if pd.notna(row[-1]) else 0
            try:
                pages = int(float(pages))
            except:
                pages = 0

            # 前面的列是件数（D 到 P，即除了最后一列）
            sku_qty_pairs = []  # [{sku, qty, col_idx, rel_col_idx}, ...]

            for col_idx, value in enumerate(row[:-1]):  # 排除最后一列（Q 列）
                if pd.notna(value) and value != '' and value != 0:
                    try:
                        qty = int(float(value))
                        actual_col = start_col + col_idx
                        sku = self.sku_names.iloc[actual_col] if actual_col < len(self.sku_names) else None

                        # 验证 SKU 是否有效
                        if pd.notna(sku) and str(sku).strip() != 'nan' and str(sku).strip() != '':
                            sku_qty_pairs.append({
                                'sku': str(sku).strip(),
                                'qty': qty,
                                'col_idx': actual_col,
                                'rel_col_idx': col_idx
                            })
                    except:
                        pass

            if debug and sku_qty_pairs:
                st.write(f"行 {excel_row}: {[(p['sku'], p['qty']) for p in sku_qty_pairs]} | 页数: {pages}")

            # 根据 SKU 数量判断
            if len(sku_qty_pairs) == 1:
                # 单 SKU 行
                pair = sku_qty_pairs[0]
                single_sku_rows.append({
                    'sku': pair['sku'],
                    'qty': pair['qty'],
                    'excel_row': excel_row,
                    'col_idx': pair['col_idx'],
                    'rel_col_idx': pair['rel_col_idx'],
                    'pages': pages
                })
            elif len(sku_qty_pairs) > 1:
                # 混合 SKU 行
                mixed_sku_rows.append({
                    'excel_row': excel_row,
                    'sku_qty_pairs': sku_qty_pairs,
                    'pages': pages,
                    'sku_count': len(sku_qty_pairs)
                })

        return {
            'range': range_str,
            'single_sku_rows': single_sku_rows,
            'mixed_sku_rows': mixed_sku_rows,
            'start_row': start_row + 1,
            'end_row': end_row + 1
        }

    def get_platform_from_row(self, row_num: int) -> str:
        """
        从指定行获取平台信息（A列）

        Args:
            row_num: 1-based行号

        Returns:
            平台信息（大写）
        """
        try:
            platform = str(self.df.iloc[row_num - 1, 0]).strip().upper()
            return platform if platform and platform != 'NAN' else None
        except:
            return None

    def generate_filename_with_platform(self, sequence_num: int, sku_info, rows_list, qty=None, total_pages=None) -> str:
        """
        根据平台信息生成文件名

        Args:
            sequence_num: 序号
            sku_info: SKU信息（字符串或列表）
            rows_list: 行号列表
            qty: 件数（可选）
            total_pages: 总页数（可选）

        Returns:
            文件名（不含.pdf扩展名）
        """
        # 检查所有行的平台信息
        platforms = set()
        if rows_list:
            for row_num in rows_list:
                platform = self.get_platform_from_row(row_num)
                if platform:
                    platforms.add(platform)

        # 构建SKU字符串
        if isinstance(sku_info, list):
            sku_str = '+'.join(sku_info)
        else:
            sku_str = str(sku_info)

        # 获取平台字符串（取第一个平台，如果有多个）
        platform_str = ""
        if platforms:
            first_platform = list(platforms)[0]  # 取第一个平台
            # 将EBAY/xxx
            if first_platform in ['EBAY', 'xxx']:
                platform_str = 'TEMU'
            elif first_platform == 'TIKTOK':
                platform_str = 'TK'
            else:
                platform_str = first_platform

        # 命名规则：{序号}，{平台}-{sku} X{件数}（共{页数}单）
        if qty is not None:
            # 单 SKU 的情况，输出 X{qty}
            if platform_str:
                return f"{platform_str}{sequence_num},-{sku_str} X{qty}pcs（total {total_pages} order）"
            else:
                return f"{sequence_num},{sku_str} X{qty}pcs（total {total_pages} order）"
        else:
            # 混合 SKU 的情况，sku_str 已包含 X{qty}，不再额外添加
            if platform_str:
                return f"{platform_str}{sequence_num},-{sku_str}（total {total_pages} order）"
            else:
                return f"{sequence_num},{sku_str}（total {total_pages} order）"

    def generate_pdf_merge_plan(self, analysis_result: Dict) -> List[Dict]:
        """
        根据分析结果生成 PDF 合并方案

        排序逻辑：
        1. 单 SKU 行：按件数升序 → 按列位置升序
        2. 混合 SKU 行：最后（不合并）

        合并条件：相同 SKU 且件数相同的可以合并

        Args:
            analysis_result: analyze_range 返回的结果

        Returns:
            PDF 合并方案列表
        """
        pdf_plan = []
        sequence_num = 1

        # 1. 处理单 SKU 行
        single_rows = analysis_result['single_sku_rows']

        # 按件数升序、按列位置升序排序
        single_rows_sorted = sorted(single_rows, key=lambda x: (x['qty'], x['rel_col_idx']))

        # 按 (SKU, 件数) 分组合并
        sku_qty_groups = defaultdict(list)
        for row_info in single_rows_sorted:
            key = (row_info['sku'], row_info['qty'])
            sku_qty_groups[key].append(row_info)

        # 为每个分组生成 PDF
        for (sku, qty), rows in sku_qty_groups.items():
            if rows and sku and sku != 'nan':
                total_pages = sum(r['pages'] for r in rows)
                rows_list = [r['excel_row'] for r in rows]

                pdf_plan.append({
                    'sequence': sequence_num,
                    'name': self.generate_filename_with_platform(sequence_num, sku, rows_list, qty, total_pages),
                    'type': 'single_sku',
                    'sku': sku,
                    'qty': qty,
                    'rows': rows_list,
                    'total_pages': total_pages
                })
                sequence_num += 1

        # 2. 处理混合 SKU 行（不合并，每行一个 PDF）
        mixed_rows = analysis_result['mixed_sku_rows']

        for row_info in mixed_rows:
            clean_skus = [p['sku'] for p in row_info['sku_qty_pairs']]
            clean_qtys = [p['qty'] for p in row_info['sku_qty_pairs']]

            if clean_skus:
                rows_list = [row_info['excel_row']]
                # 构造 SKU1X{qty1}+SKU2X{qty2} 格式
                sku_with_qty_str = '+'.join([f"{sku}X{qty}pcs" for sku, qty in zip(clean_skus, clean_qtys)])

                pdf_plan.append({
                    'sequence': sequence_num,
                    'name': self.generate_filename_with_platform(sequence_num, sku_with_qty_str, rows_list, None, row_info['pages']),
                    'type': 'mixed_sku',
                    'skus': clean_skus,
                    'qtys': clean_qtys,
                    'rows': rows_list,
                    'total_pages': row_info['pages']
                })
                sequence_num += 1

        return pdf_plan

    def print_merge_plan(self, pdf_plan: List[Dict]) -> str:
        """
        格式化输出 PDF 合并方案

        Args:
            pdf_plan: generate_pdf_merge_plan 返回的结果

        Returns:
            格式化的字符串
        """
        output = []
        output.append("="*80)
        output.append("PDF 合并方案")
        output.append("="*80)

        for pdf_info in pdf_plan:
            rows_str = ', '.join(str(r) for r in pdf_info['rows'])

            if pdf_info['type'] == 'single_sku':
                output.append(f"\n序号: {pdf_info['sequence']}")
                output.append(f"文件名: {pdf_info['name']}")
                output.append(f"类型: 单 SKU 合并")
                output.append(f"SKU: {pdf_info['sku']}")
                output.append(f"件数: {pdf_info['qty']}")
                output.append(f"来自行: {rows_str}")
                output.append(f"总页数: {pdf_info['total_pages']}")
            else:
                output.append(f"\n序号: {pdf_info['sequence']}")
                output.append(f"文件名: {pdf_info['name']}")
                output.append(f"类型: 混合 SKU")
                output.append(f"SKUs: {', '.join(pdf_info['skus'])}")
                output.append(f"件数: {', '.join(str(q) for q in pdf_info['qtys'])}")
                output.append(f"来自行: {rows_str}")
                output.append(f"总页数: {pdf_info['total_pages']}")

        output.append("\n" + "="*80)
        output.append(f"总计: {len(pdf_plan)} 个 PDF")
        output.append("="*80 + "\n")

        return "\n".join(output)


def extract_label_from_pdf_name(pdf_name: str) -> Tuple[str, str]:
    """
    修正版：完美支持 42 这种单号和 30-33 这种范围号
    """
    # 1. 预处理
    clean_name = pdf_name.upper().rsplit('.', 1)[0].split('（')[0].split('(')[0]
    
    # 2. 提取平台
    platform_match = re.match(r'^([A-Z]+)', clean_name)
    platform = platform_match.group(1) if platform_match else None
    
    # 3. 提取标签号的核心逻辑
    # 匹配规则：找到 6 位日期数字后，提取紧跟其后的部分
    # 这个部分可以是 '30-33' 这种格式，也可以是 '42' 这种格式
    # 我们匹配：日期- (数字-数字 或者 纯数字)
    match = re.search(r'\d{6}-((?:\d+-\d+)|\d+)', clean_name)
    
    if match:
        label = match.group(1)
        return (platform, label)

    # 兜底：如果日期匹配失败，按横杠切分取第三段
    parts = clean_name.split('-')
    if len(parts) >= 3:
        return (platform, parts[2])

    return (platform, None)





def parse_zip_file(zip_file) -> Dict[str, bytes]:
    """
    解析 ZIP 文件，提取其中的 PDF 文件

    Args:
        zip_file: ZIP 文件对象

    Returns:
        字典 {文件名: 文件内容}
    """
    pdf_files = {}

    with zipfile.ZipFile(zip_file, 'r') as zip_ref:
        for file_name in zip_ref.namelist():
            if file_name.lower().endswith('.pdf'):
                with zip_ref.open(file_name) as file:
                    pdf_files[file_name] = file.read()

    return pdf_files


def merge_pdfs(pdf_contents: List[bytes]) -> bytes:
    """
    合并多个 PDF 文件

    Args:
        pdf_contents: PDF 文件内容的列表

    Returns:
        合并后的 PDF 内容
    """
    merged_doc = fitz.open()

    for pdf_content in pdf_contents:
        src_doc = fitz.open(stream=pdf_content, filetype="pdf")
        merged_doc.insert_pdf(src_doc)
        src_doc.close()

    # 创建输出缓冲区
    output_buffer = io.BytesIO()
    merged_doc.save(output_buffer)
    merged_doc.close()

    return output_buffer.getvalue()


def create_zip_from_pdfs(pdf_files: Dict[str, bytes], output_name: str = "merged_pdfs.zip") -> bytes:
    """
    将多个 PDF 文件打包成 ZIP

    Args:
        pdf_files: {文件名: 内容} 的字典
        output_name: 输出 ZIP 文件名

    Returns:
        ZIP 文件的 bytes 内容
    """
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for filename, content in pdf_files.items():
            zip_file.writestr(filename, content)

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


def show_ui(user_info, update_usage_callback):
    """PDF 合并分析与处理工具的 Streamlit 界面"""
    st.title("📊 PDF 合并分析与处理工具 v1.0")

    st.markdown("""
    **功能说明：**
    1. 上传 Excel 文件，分析指定范围内的 SKU 数据。
    2. 自动识别单 SKU 行和混合 SKU 行，按件数排序。
    3. 生成 PDF 合并方案，相同 SKU 且件数相同的行可合并。
    4. 上传包含 PDF 文件的压缩包，根据合并方案处理 PDF。
    """)

    st.info("提示：Excel 第2行应为 SKU 名称，D~P 列为件数，Q 列为页数。")

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    if today > user_info['expiry_date']:
        st.error("❌ 账号已过期")
        return
    if user_info['used_count'] >= user_info['total_limit']:
        st.error("❌ 使用次数已耗尽")
        return

    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("📁 参数配置")

        # Excel 文件上传
        excel_file = st.file_uploader(
            "上传 Excel 文件",
            type=['xlsx', 'xls'],
            help="选择包含 SKU 数据的 Excel 文件"
        )

        # 范围输入
        range_input = st.text_input(
            "分析范围",
            value="D5:Q31",
            help="Excel 范围格式，如 D5:Q31 或 D5:Q31,D16:Q31"
        )

        # 调试模式
        debug_mode = st.checkbox("启用调试模式", help="显示详细的分析过程")

        # 执行按钮
        analyze_button = st.button("🔍 开始分析", type="primary", use_container_width=True)

        # PDF 处理部分
        st.markdown("---")
        st.subheader("📄 PDF 处理")

        # 压缩包上传
        zip_file = st.file_uploader(
            "上传 PDF 压缩包",
            type=['zip'],
            help="选择包含需要合并的 PDF 文件的压缩包"
        )

        # 处理按钮
        process_button = st.button("⚙️ 开始处理 PDF", type="primary", use_container_width=True)

    with col2:
        st.subheader("📋 分析结果")

        # 存储分析结果在 session_state 中
        if 'analysis_results' not in st.session_state:
            st.session_state.analysis_results = None
        if 'pdf_plan' not in st.session_state:
            st.session_state.pdf_plan = None
        if 'analyzer' not in st.session_state:
            st.session_state.analyzer = None

        if excel_file and analyze_button:
            with st.spinner("正在分析 Excel 数据..."):
                try:
                    # 初始化分析器
                    analyzer = PDFMergeAnalyzer(excel_file)
                    st.session_state.analyzer = analyzer

                    # 解析多个范围
                    ranges = [r.strip() for r in range_input.split(',') if r.strip()]

                    all_results = []
                    total_pdfs = 0

                    for range_str in ranges:
                        st.markdown(f"### 📊 分析范围: {range_str}")

                        # 分析范围
                        analysis = analyzer.analyze_range(range_str, debug=debug_mode)

                        # 生成合并方案
                        pdf_plan = analyzer.generate_pdf_merge_plan(analysis)

                        # 显示结果
                        result_text = analyzer.print_merge_plan(pdf_plan)
                        st.code(result_text, language="text")

                        # 统计信息
                        single_count = len([p for p in pdf_plan if p['type'] == 'single_sku'])
                        mixed_count = len([p for p in pdf_plan if p['type'] == 'mixed_sku'])

                        st.info(f"📈 本范围统计: {len(pdf_plan)} 个 PDF ({single_count} 个单 SKU, {mixed_count} 个混合 SKU)")

                        all_results.extend(pdf_plan)
                        total_pdfs += len(pdf_plan)

                    # 全局统计
                    if len(ranges) > 1:
                        st.markdown("---")
                        st.markdown("### 📈 全局统计")
                        total_single = len([p for p in all_results if p['type'] == 'single_sku'])
                        total_mixed = len([p for p in all_results if p['type'] == 'mixed_sku'])
                        total_pages = sum(p['total_pages'] for p in all_results)

                        col_a, col_b, col_c = st.columns(3)
                        with col_a:
                            st.metric("总 PDF 数量", total_pdfs)
                        with col_b:
                            st.metric("单 SKU PDF", total_single)
                        with col_c:
                            st.metric("混合 SKU PDF", total_mixed)

                        st.metric("预计总页数", total_pages)

                    # 存储结果
                    st.session_state.analysis_results = all_results
                    st.session_state.pdf_plan = all_results

                    # 扣除使用次数
                    update_usage_callback(user_info['username'])
                    st.success("✅ 分析完成！")

                except Exception as e:
                    st.error(f"❌ 分析出错: {str(e)}")
                    st.exception(e)

        elif analyze_button and not excel_file:
            st.warning("⚠️ 请先上传 Excel 文件")

        # PDF 处理部分
        if process_button:
            if not st.session_state.pdf_plan:
                st.error("❌ 请先进行 Excel 分析")
            elif not zip_file:
                st.error("❌ 请上传包含 PDF 文件的压缩包")
            else:
                with st.spinner("正在处理 PDF 文件..."):
                    try:
                        # 获取保存的 analyzer
                        analyzer = st.session_state.analyzer
                        if analyzer is None:
                            st.error("❌ 分析器丢失，请重新分析 Excel")
                            return

                        # 解析压缩包
                        pdf_files = parse_zip_file(zip_file)
                        pdf_count = len(pdf_files)

                        # 获取 Excel 行数（从合并方案中）
                        excel_rows = set()
                        for pdf_info in st.session_state.pdf_plan:
                            excel_rows.update(pdf_info['rows'])
                        excel_row_count = len(excel_rows)

                        # 验证数量一致性
                        if pdf_count != excel_row_count:
                            st.error(f"❌ 快递明细中有 {excel_row_count} 行，但压缩包中有 {pdf_count} 个 PDF，两者不一致，请检查后再试。")
                            return

                        st.success(f"✅ 数量验证通过：{excel_row_count} 个 PDF 文件")

                        # 创建 PDF 到标签号的映射（支持平台+标签组合）
                        pdf_label_map = {}  # {platform_label: (pdf_name, pdf_content)}
                        for pdf_name, pdf_content in pdf_files.items():
                            platform, label = extract_label_from_pdf_name(pdf_name)
                            
                            if platform and label:
                                # 使用平台+标签的组合作为key
                                platform_label_key = f"{platform}-{label}"
                                pdf_label_map[platform_label_key] = (pdf_name, pdf_content)
                                # 也存储简化标签（向后兼容）
                                pdf_label_map[label] = (pdf_name, pdf_content)
                            elif label:
                                # 如果没有平台信息，只用标签
                                pdf_label_map[label] = (pdf_name, pdf_content)
                            
                            if not platform and not label:
                                st.warning(f"⚠️ 无法从文件名提取平台和标签号: {pdf_name}")

                        # 根据合并方案处理 PDF
                        merged_pdfs = {}
                        processed_count = 0

                        for pdf_info in st.session_state.pdf_plan:
                            pdf_name = f"{pdf_info['name']}.pdf"
                            pdf_contents = []

                            # 收集需要合并的 PDF
                            for row_num in pdf_info['rows']:
                                # 从 Excel 数据中获取标签号
                                # 假设标签号在 A 列（平台）、B 列（日期）、C 列（标签号）
                                try:
                                    # 获取对应行的数据 (row_num 是 1-based)
                                    row_data = analyzer.df.iloc[row_num - 1]  # 转为 0-based

                                    # A 列: 平台, B 列: 日期, C 列: 标签号
                                    platform = str(row_data.iloc[0]).strip().upper() if pd.notna(row_data.iloc[0]) else ""
                                    date = str(row_data.iloc[1]) if pd.notna(row_data.iloc[1]) else ""
                                    label = str(row_data.iloc[2]).strip() if pd.notna(row_data.iloc[2]) else ""

                                    # 尝试多种匹配方式
                                    found_pdf = None
                                    search_keys = []
                                    
                                    # 1. 平台+标签组合匹配
                                    if platform and label:
                                        platform_label_key = f"{platform}-{label}"
                                        search_keys.append(platform_label_key)
                                        if platform_label_key in pdf_label_map:
                                            found_pdf = pdf_label_map[platform_label_key]
                                    
                                    # 2. 如果没找到，只用标签匹配（向后兼容）
                                    if not found_pdf and label:
                                        search_keys.append(label)
                                        if label in pdf_label_map:
                                            found_pdf = pdf_label_map[label]

                                    if found_pdf:
                                        pdf_contents.append(found_pdf[1])
                                        st.write(f"✓ 找到 PDF: {found_pdf[0]} -> {platform}-{label}")
                                    else:
                                        st.error(f"❌ 未找到标签号 {platform}-{label} 对应的 PDF 文件")
                                        st.write(f"📌 尝试的匹配键: {', '.join(search_keys)}")
                                        st.write(f"📌 可用的标签号: {', '.join(pdf_label_map.keys())}")
                                        return

                                except Exception as e:
                                    st.error(f"❌ 处理行 {row_num} 时出错: {str(e)}")
                                    return

                            # 合并 PDF
                            if pdf_contents:
                                merged_content = merge_pdfs(pdf_contents)
                                merged_pdfs[pdf_name] = merged_content
                                processed_count += 1
                                st.write(f"✓ 合并完成: {pdf_name} ({len(pdf_contents)} 个文件)")

                        # 创建下载用的 ZIP 文件
                        if merged_pdfs:
                            zip_content = create_zip_from_pdfs(merged_pdfs, "merged_pdfs.zip")

                            # 显示处理结果
                            st.markdown("---")
                            st.markdown("### 📄 处理结果")
                            st.success(f"✅ 成功处理了 {processed_count} 个合并 PDF")

                            # 下载按钮
                            st.download_button(
                                label="📥 下载合并后的 PDF 文件",
                                data=zip_content,
                                file_name="merged_pdfs.zip",
                                mime="application/zip",
                                use_container_width=True
                            )

                            # 显示合并详情
                            with st.expander("查看合并详情"):
                                for pdf_name, content in merged_pdfs.items():
                                    file_size = len(content) / 1024  # KB
                                    st.write(f"• {pdf_name}: {file_size:.1f} KB")

                        else:
                            st.error("❌ 没有成功合并任何 PDF 文件")

                    except Exception as e:
                        st.error(f"❌ PDF 处理出错: {str(e)}")
                        st.exception(e)

        if not (excel_file and analyze_button) and not process_button:
            st.info("💡 请上传 Excel 文件并设置参数，然后点击开始分析。")


if __name__ == "__main__":
    # 这部分代码在实际的 Streamlit 应用中不会执行
    # 它会被主应用调用 show_ui 函数
    pass