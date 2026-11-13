import json
import os
import re
import shutil
import time
import traceback
from copy import copy
from typing import Any

import openpyxl
from openpyxl.drawing.image import Image
from openpyxl.styles.alignment import Alignment
from openpyxl.styles.borders import Border, Side
from openpyxl.utils import column_index_from_string, get_column_letter, range_boundaries
from openpyxl.worksheet.worksheet import Worksheet


def open_excel(file_path: str):
    """
    Opens an Excel file for read/write operations, supporting .xlsm and .xlsx formats.

    Args:
        file_path (str): The path to the Excel file.

    Returns:
        Workbook: An openpyxl Workbook object.

    Raises:
        Exception: If the file type is not supported or the file cannot be opened.
    """
    try:
        if file_path.endswith(".xlsm"):
            wb = openpyxl.load_workbook(file_path, keep_vba=True, keep_links=True, data_only=True)
        elif file_path.endswith(".xlsx"):
            wb = openpyxl.load_workbook(file_path, keep_vba=False, keep_links=True, data_only=True)
        else:
            raise Exception("Error: File type not supported! only support .xlsm or .xlsx.")
    except FileNotFoundError:
        wb = openpyxl.Workbook()
    return wb


def insert_img_to_excel(
    file_path: str,
    sheet_name: str,
    cell: str,
    img_path: str,
    width: int = None,
    height: int = None,
    save_path: str = None
):
    """
    Inserts an image into a specified cell in an Excel worksheet with optional resizing.

    Args:
        file_path (str): The path to the target Excel file.
        sheet_name (str): The name of the worksheet to insert the image into.
        cell (str): The coordinate of the cell (e.g., 'A1').
        img_path (str): The path to the image file.
        width (int, optional): The target width of the image.
        height (int, optional): The target height of the image.
        save_path (str, optional): The path to save the modified Excel file. Defaults to file_path.

    Returns:
        str: The path to the saved Excel file.

    Raises:
        Exception: If an error occurs during image insertion or file saving.
    """
    try:
        wb = open_excel(file_path)

        if sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
        else:
            ws = wb.create_sheet(sheet_name)

        img = Image(img_path)

        if width and not height:
            scale = width / img.width
            img.width = width
            img.height = int(img.height * scale)

        elif height and not width:
            scale = height / img.height
            img.height = height
            img.width = int(img.width * scale)

        else:
            img.width = width
            img.height = height

        # 插入到指定单元格
        ws.add_image(img, cell)

        save_path = save_path or file_path
        wb.save(save_path)
        return save_path
    except:
        raise Exception(traceback.format_exc())


def copy_sheet_to_index(file_path: str, orign_sheet_name: str, target_sheet_name: str, target_index: int = 1):
    """
    Copies a worksheet to a specified index position in the workbook.

    Args:
        file_path (str): The path to the Excel file.
        orign_sheet_name (str): The name of the sheet to be copied.
        target_sheet_name (str): The name for the new copied sheet.
        target_index (int, optional): The target position index for the new sheet (1-based). Defaults to 1.

    Raises:
        Exception: If the source sheet is not found, the target sheet already exists, or an error occurs.
    """
    try:
        wb = open_excel(file_path)

        if orign_sheet_name not in wb.sheetnames:
            raise Exception(f"Error: The orign sheet '{orign_sheet_name}' was not found!")
        if target_sheet_name in wb.sheetnames:
            raise Exception(f"Error: The target sheet '{orign_sheet_name}' was already exists!")

        source_sheet = wb[orign_sheet_name]
        new_sheet = wb.copy_worksheet(source_sheet)
        new_sheet.title = target_sheet_name

        new_sheet.sheet_view.view = "pageBreakPreview"

        if new_sheet.sheet_properties.tabColor:
            new_sheet.sheet_properties.tabColor = None

        current_index = len(wb.sheetnames) - 1
        offset = target_index - current_index
        wb.move_sheet(new_sheet, offset=offset)
        wb.save(file_path)

    except:
        raise Exception(traceback.format_exc())


def set_outer_border(
        ws: Worksheet,
        cell_range: str,
        border_style: str = "thin",
        color: str = "000000",
        inner_set: bool = False,
        mode: str = "append"
):
    """
    Sets an outer border for a specified range of cells in an Excel worksheet.

    Args:
        ws (Worksheet): The openpyxl Worksheet object.
        cell_range (str): The range of cells to set the border for (e.g., "T10:BH15").
        border_style (str, optional): The style of the border line. Defaults to "thin".
        color (str, optional): The hex color code of the border. Defaults to "000000".
        inner_set (bool, optional): Whether to apply borders to inner cells. Defaults to False.
        mode (str, optional): The mode for applying borders ("append" or "overwrite"). Defaults to "append".
    """
    thin = Side(border_style=border_style, color=color)
    min_col, min_row, max_col, max_row = range_boundaries(cell_range)
    for r in range(min_row, max_row + 1):
        for c in range(min_col, max_col + 1):
            cell = ws.cell(row=r, column=c)

            is_top = r == min_row or inner_set
            is_bottom = r == max_row or inner_set
            is_left = c == min_col or inner_set
            is_right = c == max_col or inner_set

            new_border = Border()
            if is_top:
                new_border.top = thin
            if is_bottom:
                new_border.bottom = thin
            if is_left:
                new_border.left = thin
            if is_right:
                new_border.right = thin

            if mode == "append":
                cell.border += new_border
            else:
                cell.border = new_border


def set_merged_alignment_and_wrap(ws, cell_range: str):
    """
    Merges cells in a range and sets top-left alignment and text wrapping.

    Args:
        ws (Worksheet): The openpyxl Worksheet object.
        cell_range (str): The range of cells to merge and format (e.g., "T10:BH15").
    """
    ws.merge_cells(cell_range)
    min_col, min_row, max_col, max_row = range_boundaries(cell_range)

    # 定义左上角对齐 + 自动换行的样式
    align_style = Alignment(horizontal='left', vertical='top', wrap_text=True)

    # 💡 注意：在 Excel 中，合并单元格的实质是【左上角第一个单元格】承载数据和样式
    # 为了防止部分 Excel 软件显示异常，我们把整个范围内的单元格都设上这个样式
    for row in range(min_row, max_row + 1):
        for col in range(min_col, max_col + 1):
            ws.cell(row=row, column=col).alignment = align_style


def copy_range_row_ref_template(
        file_path: str,
        orign_sheet_name: str,
        orign_row_start_index: int,
        orign_row_end_index: int,
        target_sheet_name: str,
        target_row_index: int
):
    """
    Copies a range of rows from a source sheet to a target sheet, preserving merged cell structures.

    Args:
        file_path (str): The path to the Excel file.
        orign_sheet_name (str): The name of the source worksheet.
        orign_row_start_index (int): The starting index of the source row range.
        orign_row_end_index (int): The ending index of the source row range.
        target_sheet_name (str): The name of the target worksheet.
        target_row_index (int): The target index to insert the copied rows.

    Returns:
        Workbook: The openpyxl Workbook object.

    Raises:
        Exception: If the specified sheets are not found or an error occurs.
    """
    try:
        wb = open_excel(file_path)

        if orign_sheet_name not in wb.sheetnames:
            raise Exception(f"Error: The orign sheet '{orign_sheet_name}' was not found!")
        if target_sheet_name not in wb.sheetnames:
            raise Exception(f"Error: The target sheet '{target_sheet_name}' was not found!")
        target_sheet = wb[target_sheet_name]
        source_sheet = wb[orign_sheet_name]

        # source_sheet.insert_rows(target_row_index, row_count)
        insert_rows_preserve_merges(source_sheet, target_row_index, orign_row_end_index - orign_row_start_index)

        for index in range(orign_row_end_index - orign_row_start_index):
            copy_row_with_merged_cells(source_sheet, orign_row_start_index + index, target_sheet, target_row_index + index)
        wb.save(file_path)
        return wb
    except:
        raise Exception(traceback.format_exc())



def col_num_to_letter(col_num):
    """
    Convert a 1-based column index to its Excel column letter.

    Args:
        col_num (int):
            Column index starting from 1 (e.g., 1 -> A, 26 -> Z, 27 -> AA).

    Returns:
        str: Corresponding Excel column letter.
    """
    letter = ""
    while col_num > 0:
        col_num -= 1
        letter = chr(col_num % 26 + 65) + letter
        col_num = col_num // 26
    return letter


def extract_column_letters(cell_ref: str) -> str:
    """
    Extracts the column letters from a cell reference string using a regular expression.

    Args:
        cell_ref (str): The cell reference string (e.g., "A5", "AB10", "$C$7").

    Returns:
        str: The extracted column letters (e.g., "A", "AB", "C").
             Returns an empty string if no match is found.
    """
    match = re.match(r'^\$*([A-Z]+)\d+$', cell_ref, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return ""


def create_col_data(start_col_index: int, end_col_index: int):
    """
    Creates a list of column letters for a given range of indices.

    Args:
        start_col_index (int): The starting column index (1-based).
        end_col_index (int): The ending column index (1-based).

    Returns:
        list: A list containing 'NA' followed by the column letters for the specified range.
    """
    col_data = ['NA']
    for col_index in range(start_col_index, end_col_index + 1):
        col_data.append(col_num_to_letter(col_index))
    return col_data


def get_merged_data(ws: Worksheet):
    """
    Retrieves a list of coordinates for all merged cells in the worksheet.

    Args:
        ws (Worksheet): The openpyxl Worksheet object.

    Returns:
        list: A list of coordinate strings for all merged regions.
    """
    merged_ranges = ws.merged_cells.ranges
    merged_data = []
    for merged_range in merged_ranges:
        merged_data.append(merged_range.coord)
    return merged_data


def read_sheet_data(ws: Worksheet, sheet_range: str = None):
    """
    Reads data from a worksheet within an optional specified range.

    Args:
        ws (Worksheet): The openpyxl Worksheet object.
        sheet_range (str, optional): The range to read (e.g., "A1:C10"). Defaults to None (reads all data).

    Returns:
        dict: A dictionary containing the sheet name, data, and merged ranges.
    """
    if sheet_range is None:
        max_row = ws.max_row
        max_col_str = col_num_to_letter(ws.max_column)
        sheet_range = f"A1:{max_col_str}{max_row}"

    start_col_index = column_index_from_string(extract_column_letters(sheet_range.split(":")[0]))
    end_col_index = column_index_from_string(extract_column_letters(sheet_range.split(":")[1]))

    sheet_obj = {
        "sheet_name": ws.title,
        "sheet_data": [create_col_data(start_col_index, end_col_index)],
        "merged_ranges": get_merged_data(ws)
    }

    start_row_index = int(re.search(r'\d+', sheet_range.split(":")[0]).group())

    index = 0
    for row in ws[sheet_range]:
        row_data = [f"{start_row_index + index}"]
        for cell in row:
            if cell.value:
                cell_value = re.sub(r'\s+', ' ', f"{cell.value}").strip()
                row_data.append(cell_value)
            else:
                row_data.append("")

        # filter the blank data
        filter_data = [ data for data in row_data if len(data.strip()) > 0 ]
        if len(filter_data) >= 2 or end_col_index - start_col_index < 2:
            sheet_obj["sheet_data"].append(row_data)
        index += 1
    return sheet_obj


def read_sheet(file_path: str, sheet_name: str, sheet_range: str = None):
    """
    Reads data from a specific sheet in an Excel file.

    Args:
        file_path (str): The path to the Excel file.
        sheet_name (str): The name of the worksheet to read.
        sheet_range (str, optional): The range to read. Defaults to None.

    Returns:
        dict: The data extracted from the sheet.

    Raises:
        Exception: If the sheet cannot be read.
    """
    try:
        wb = open_excel(file_path)
        ws = wb[sheet_name]
        return read_sheet_data(ws, sheet_range)
    except:
        raise Exception(traceback.format_exc())


def read_excel(file_path: str, sheet_names: list = None):
    """
    Reads data from multiple sheets (or all sheets) in an Excel file.

    Args:
        file_path (str): The path to the Excel file.
        sheet_names (list, optional): A list of Worksheet objects or sheet names to read. Defaults to None (reads all).

    Returns:
        list: A list of dicts containing data for each read sheet.

    Raises:
        Exception: If the Excel file cannot be read.
    """
    try:
        wb = open_excel(file_path)
        excel_data = []
        if sheet_names is None:
            sheet_names = wb.worksheets
        for s in sheet_names:
            ws = wb[s] if isinstance(s, str) else s
            sheet_obj = read_sheet_data(ws)
            excel_data.append(sheet_obj)
        return excel_data
    except:
        raise Exception(traceback.format_exc())


def find_rows_in_sheet_by_value(file_path: str, sheet_name: str, search_value: str, sheet_range: str = None, title_row_index: int = None):
    """
    Searches for rows in a worksheet that contain a specific value within a given range.

    Args:
        file_path (str): The path to the Excel file.
        sheet_name (str): The name of the worksheet.
        sheet_range (str, optional): The range to search within, formatted as "A1:C10". Defaults to None.
        search_value (str): The value to search for.
        title_row_index (int, optional): The index of the title row.

    Returns:
        list: A list of row numbers where the search value is found.

    Raises:
        Exception: If an error occurs during the search.
    """
    try:
        result = read_sheet(file_path, sheet_name, sheet_range)
        rows = []
        for index, row in enumerate(result["sheet_data"]):
            if index == 0:
                rows.append(row)
                continue
            if title_row_index is not None and int(row[0]) == int(title_row_index):
                rows.append(row)
                continue
            if search_value in row:
                rows.append(row)
                break
        return rows
    except:
        raise Exception(traceback.format_exc())



def extract_vba_from_xlsm(xlsm_filepath: str):
    """
    Extracts VBA macros from an .xlsm Excel file.

    Args:
        xlsm_filepath (str): The path to the .xlsm file.

    Returns:
        tuple: A tuple containing:
            - list: A list of dicts with 'module_name' and 'vba_code'.
            - str: A concatenated string of all VBA code.
        Returns None if the file does not exist or has no macros.

    Raises:
        Exception: If an error occurs during extraction.
    """
    if not os.path.exists(xlsm_filepath):
        return

    try:
        from oletools.olevba import VBA_Parser

        with open(xlsm_filepath, 'rb') as f:
            vba_parser = VBA_Parser(xlsm_filepath, data=f.read())

            result_list = []
            all_code_str = ""
            if vba_parser.detect_vba_macros():
                for (filename, module_name, stream_path, vba_code) in vba_parser.extract_macros():
                    module_name = os.path.basename(module_name)
                    all_code_str += f"{module_name}\n---\n{vba_code}\n---\n\n"
                    result_list.append({
                        "module_name": module_name,
                        "vba_code": vba_code
                    })
            else:
                pass
            return result_list, all_code_str

    except:
        raise


def filter_data_by_condition(file_path: str, sheet_name: str, search_value: str, col_str: str):
    """
    Searches for a specific value in an Excel file and returns the cell coordinates where it is found.

    Args:
        file_path (str): The path to the Excel file.
        sheet_name (str): The name of the worksheet to search in.
        col_str (str): The column letter to search in.
        search_value (str): The value to search for.

    Returns:
        list: A list of tuples containing (sheet_name, cell_coordinate) where the value is found.
              Returns an empty list if the value is not found in any sheet.

    Raises:
        Exception: If an error occurs during the search.
    """
    try:
        result = read_sheet(file_path=file_path, sheet_name=sheet_name, sheet_range=None)
        data_list = result.get("sheet_data", [])

        col_index_in_data = data_list[0].index(col_str) if col_str in data_list[0] else None
        if col_index_in_data is None:
            raise Exception(f"Error: Column '{col_str}' not found in the sheet '{sheet_name}'.")

        filter_rows = [data_list[0]]  # Include the header row
        for row in data_list[1:]:
            if search_value in row[col_index_in_data]:
                filter_rows.append(row)
        return filter_rows

    except:
        raise Exception(traceback.format_exc())


def insert_rows_preserve_merges(ws: Worksheet, insert_at: int=1, num_rows: int = 1):
    """
    Inserts empty rows into an Excel worksheet while preserving merged cell structures.

    Args:
        ws (Worksheet): The worksheet to modify.
        insert_at (int, optional): The starting row index for insertion. Defaults to 1.
        num_rows (int, optional): The number of rows to insert. Defaults to 1.
    """
    merged_ranges = list(ws.merged_cells.ranges)

    for merged_range in merged_ranges:
        ws.unmerge_cells(str(merged_range))

    ws.insert_rows(insert_at, num_rows)

    for merged_range in merged_ranges:
        min_col, min_row, max_col, max_row = range_boundaries(str(merged_range))

        if min_row >= insert_at:
            min_row += num_rows
            max_row += num_rows
        elif max_row >= insert_at:
            max_row += num_rows

        new_range = f"{ws.cell(row=min_row, column=min_col).coordinate}:{ws.cell(row=max_row, column=max_col).coordinate}"
        ws.merge_cells(new_range)

def copy_row_with_merged_cells(src_ws, src_row, dst_ws, dst_row):
    """
    Copies row formatting (including merged cells and cell styles) and values from a source row
    to a destination row in an Excel worksheet.

    Args:
        src_ws (Worksheet): The source worksheet.
        src_row (int): The source row index.
        dst_ws (Worksheet): The destination worksheet.
        dst_row (int): The destination row index.

    Returns:
        int: The index of the next row after the destination row.
    """
    merges_to_add = []
    for merged_range in list(src_ws.merged_cells.ranges):
        min_col, min_row, max_col, max_row = range_boundaries(str(merged_range))
        # src_row の行全体にわたる結合領域のみを処理
        if min_row == src_row and max_row == src_row:
            # 新しい行での結合範囲を計算
            new_range = f"{get_column_letter(min_col)}{dst_row}:{get_column_letter(max_col)}{dst_row}"
            merges_to_add.append(new_range)

    max_col = src_ws.max_column
    for col in range(1, max_col + 1):
        src_cell = src_ws.cell(row=src_row, column=col)
        dst_cell = dst_ws.cell(row=dst_row, column=col)

        if src_cell.has_style:
            dst_cell._style = copy(src_cell._style)
            dst_cell.value = src_cell.value
    if src_row in src_ws.row_dimensions:
        src_rd = src_ws.row_dimensions[src_row]
        dst_rd = dst_ws.row_dimensions[dst_row]

        dst_rd.height = src_rd.height
        dst_rd.hidden = src_rd.hidden
        dst_rd.outlineLevel = src_rd.outlineLevel
        dst_rd.collapsed = src_rd.collapsed

    for mr in merges_to_add:
        try:
            dst_ws.merge_cells(mr)
        except ValueError:
            pass
    return dst_row + 1

def copy_rows_ref_template(
        source_sheet: Worksheet,
        orign_row_index: int,
        target_sheet: Worksheet,
        target_row_index: int,
        row_count: int = 1
):
    """
    Creates new rows based on a template row from another sheet, preserving merged cell structures.

    Args:
        source_sheet (Worksheet): The source worksheet containing the template row.
        orign_row_index (int): The index of the template row to copy.
        target_sheet (Worksheet): The target worksheet to insert rows into.
        target_row_index (int): The index where the new rows should be inserted.
        row_count (int, optional): The number of rows to create. Defaults to 1.

    Raises:
        Exception: If an error occurs.
    """
    # 同一シート内で挿入すると、テンプレート行の位置が後ろにずれるため補正する
    source_row_index = orign_row_index
    if source_sheet is target_sheet and orign_row_index >= target_row_index:
        source_row_index = orign_row_index + row_count

    insert_rows_preserve_merges(target_sheet, target_row_index, row_count)
    for index in range(row_count):
        copy_row_with_merged_cells(
            src_ws=source_sheet, 
            src_row=source_row_index,
            dst_ws=target_sheet,
            dst_row=target_row_index + index
        )


def copy_rows_with_style(ws: Worksheet, start_row: int, row_count: int):
    """
    Copies a specified number of rows in a worksheet, preserving their styles.

    Args:
        ws (Worksheet): The openpyxl Worksheet object.
        start_row (int): The starting row index to copy from.
        row_count (int): The number of rows to copy.

    Raises:
        Exception: If an error occurs during the copy operation.
    """
    try:
        for i in range(row_count):
            source_row = start_row + i
            target_row = source_row + 1

            ws.insert_rows(target_row)

            # for col in range(1, ws.max_column + 1):
            #     source_cell = ws.cell(row=source_row, column=col)
            #     target_cell = ws.cell(row=target_row, column=col)

            #     # Copy value and style
            #     target_cell.value = source_cell.value
            #     if source_cell.has_style:
            #         target_cell.font = copy(source_cell.font)
            #         target_cell.border = copy(source_cell.border)
            #         target_cell.fill = copy(source_cell.fill)
            #         target_cell.number_format = copy(source_cell.number_format)
            #         target_cell.protection = copy(source_cell.protection)
            #         target_cell.comment = copy(source_cell.comment) if source_cell.comment else None
            #     else:
            #         target_cell.font = None
            #         target_cell.border = None
            #         target_cell.fill = None
            #         target_cell.number_format = None
            #         target_cell.protection = None
            #         target_cell.comment = None
    except:
        raise Exception(traceback.format_exc())


def write_data_to_excel(file_path: str, sheet_name: str, data_config: dict | str):
    """
    Writes data to a specified Excel worksheet based on the provided data configuration.

    Args:
        file_path (str): The path to the Excel file.
        sheet_name (str): The name of the target worksheet.
        data_config (dict | str): A dictionary containing the data to write or a JSON string/file path.
            Each key should be the cell coordinate (str) and the corresponding value should be the cell value (str).

    Raises:
        Exception: If an error occurs during the write operation.
    """
    try:
        wb = open_excel(file_path)  # Ensure the file exists and is opened
        ws = wb[sheet_name] if sheet_name in wb.sheetnames else None
        if ws is None:
            raise Exception(f"Error: Sheet '{sheet_name}' not found in the workbook.")
        if isinstance(data_config, str):
            if os.path.exists(data_config):
                data_config = json.load(open(data_config, 'r', encoding='utf-8'))
            else:
                try:
                    data_config = json.loads(data_config)
                except:
                    raise Exception(f"Error: {data_config} is not a valid JSON string or file path.")

        offset_rows = 0

        for cell, value in data_config.items():
            try:
                if cell and value is not None:
                    start_row = int(re.sub(r'[^0-9]+', '', cell)) + offset_rows
                    start_col_index = column_index_from_string(re.sub(r'[^A-Z]+', '', cell))

                    if type(value) == str:
                        ws.cell(row=start_row, column=start_col_index, value=_normalize_excel_text(value))
                    elif type(value) in (int, float):
                        ws.cell(row=start_row, column=start_col_index, value=value)
                    elif type(value) == dict:
                        col_refs = value.get("col_refs", None)
                        data_list = value.get("data_list", None)

                        if data_list is None or len(data_list) == 0:
                            print(f"Warning: when set the value is dict, must set 'data_list' and 'data_list' is not empty, but got {value}.")
                            continue
                        
                        op_mode = value.get("op_mode", "write")
                        if op_mode == "insert":
                            copy_rows_ref_template(ws, start_row, ws, start_row, len(data_list))
                            offset_rows += len(data_list)

                        for r_idx, row_data in enumerate(data_list):
                            target_row = start_row + r_idx
                            for c_idx, cell_value in enumerate(row_data):
                                target_col = start_col_index + c_idx
                                if col_refs is not None and c_idx < len(col_refs):
                                    target_col = column_index_from_string(col_refs[c_idx])
                                ws.cell(row=target_row, column=target_col, value=_normalize_excel_text(f"{cell_value}"))
            except:
                print(f"Error: when set the value of cell '{cell}' to '{value}', got error: {traceback.format_exc()}")
                wb.save(file_path)
                raise

        wb.save(file_path)

    except:
        raise Exception(traceback.format_exc())
    finally:
        if wb:
            wb.close()


def _normalize_excel_text(value):
    if not isinstance(value, str):
        return value

    return (
        value
        .replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

def get_sheet_names(file_path: str):
    """
    Retrieves the names of all sheets in an Excel workbook.

    Args:
        file_path (str): The path to the Excel file.

    Returns:
        list: A list of sheet names in the workbook.
    
    Raises:
        Exception: If the workbook cannot be opened or read.
    """
    try:
        wb = open_excel(file_path)
        return wb.sheetnames
    except:
        raise Exception(traceback.format_exc())

def test_04():
    template_file = r"D:\Downloads\format\04_response_plan.xlsx"
    result_file = template_file.replace(".xlsx", f"_{int(time.time()) * 1000}.xlsx")
    shutil.copyfile(template_file, result_file)
    sheet_name = "入力フォーム"
    data_config = {
        "C4": "c4",
        "C5": "c5",
        "C6": "c6",
        "G4": "g4",
        "G5": "g5",
        "A10": {
            "col_refs": ["A", "B", "C", "D", "E", "F", "G"],
            "data_list": [
                ["Data1", "Data2", "Data3", "Data4", "Data5", "Data6", "Data7"],
                ["Data4", "Data5", "Data6", "Data7", "Data8", "Data9", "Data10"]
            ],
            "op_mode": "write"
        },
        "C15": "c15",
        "C16": "c16",
        "A20": {
            "col_refs": ["A", "B", "C", "D", "E", "F"],
            "data_list": [
                ["Row1Col1", "Row1Col2", "Row1Col3", "Row1Col4", "Row1Col5", "Row1Col6"],
                ["Row2Col1", "Row2Col2", "Row2Col3", "Row2Col4", "Row2Col5", "Row2Col6"]
            ],
            "op_mode": "insert"
        },
        "A24": {
            "col_refs": ["A", "B", "C", "D", "E", "F", "G"],
            "data_list": [
                ["Row1Col1", "Row1Col2", "Row1Col3", "Row1Col4", "Row1Col5", "Row1Col6", "Row1Col7"],
                ["Row2Col1", "Row2Col2", "Row2Col3", "Row2Col4", "Row2Col5", "Row2Col6", "Row2Col7"]
            ],
            "op_mode": "insert"
        },
        "C27": "c27",
        "C28": "c28",
        "C29": "c29",
        "C30": "c30",
        "C31": "c31"
    }
    write_data_to_excel(file_path=result_file, sheet_name=sheet_name, data_config=data_config)

def test_03():

    template_file = r"D:\Downloads\format\03_investigation_results.xlsx"
    result_file = template_file.replace(".xlsx", f"_{int(time.time()) * 1000}.xlsx")
    shutil.copyfile(template_file, result_file)
    sheet_name = "入力フォーム"
    data_config = r"D:\MyProjects\DP\AMSエージェント\outputs\M-0007569_202607291354\03_investigation_results_data_config_M-0007569.json"
    write_data_to_excel(file_path=result_file, sheet_name=sheet_name, data_config=data_config)

def test_04():
    result = find_rows_in_sheet_by_value(
        file_path=r"D:\MyProjects\DP\AMSエージェント\docs\data\課題管理表.xlsm",
        sheet_name="課題管理表",
        sheet_range="B6:V100",
        search_value="M-0007568",
        title_row_index=6
    )
    print(result)


if __name__ == '__main__':
    # test_03()
    test_04()

