import json
import os
import re
import shutil
import time
import traceback

import xlwings as xw
from PIL import ImageGrab
from xlwings import Sheet


def _validate_sheet_name(sheet_name: str):
    if not sheet_name:
        raise ValueError("Error: Sheet name must not be empty.")
    if len(sheet_name) > 31:
        raise ValueError("Error: Sheet name must be 31 characters or fewer.")
    if any(char in "\\/*?:[]" for char in sheet_name):
        raise ValueError("Error: Sheet name contains an invalid character: \\ / * ? : [ ]")


def _sheet_name_exists(wb, sheet_name: str) -> bool:
    target_name = sheet_name.casefold()
    return any(sheet.name.casefold() == target_name for sheet in wb.sheets)


def get_open_workbooks() -> list[dict]:
    """
    Get all currently open workbooks in running Excel instances,
    along with the active sheet name of each workbook.

    Returns:
        list[dict]: e.g. [{"workbook": "Book1.xlsx", "full_path": "C:\\...", "active_sheet": "Sheet1"}, ...]
                    Returns an empty list if no Excel instance is running.
    """
    result = []
    for app in xw.apps:
        for wb in app.books:
            try:
                active_sheet = wb.sheets.active.name
            except Exception:
                active_sheet = None
            try:
                full_path = wb.api.FullName
            except Exception:
                full_path = wb.name
            result.append({
                "workbook": wb.name,
                "full_path": full_path,
                "active_sheet": active_sheet,
            })
    return result


def find_rows_by_keyword_in_open_workbook(
    workbook_name: str,
    sheet_name: str,
    keyword: str,
    header_row: int = 1,
    search_range: str = None,
) -> list[list]:
    """
    Search for a keyword in an already-open workbook and return matched rows together with the header row.

    Args:
        workbook_name: Name of the workbook (e.g. "Book1.xlsx"). Must already be open in Excel.
        sheet_name:    Sheet to search in.
        keyword:       Text to find (case-insensitive, partial match).
        header_row:    1-based row number of the header row. Default 1.
        search_range:  Optional cell range to restrict the search (e.g. "A1:Z100").
                       If omitted the sheet's used range is used.

    Returns:
        2-D list where the first row is the header and subsequent rows are matched data rows.
        e.g. [["ID", "Name", ...], ["1", "Alice", ...], ...]

    Raises:
        ValueError: If the workbook is not open or the sheet does not exist.
    """
    # 1. Locate the workbook among all open Excel instances
    target_wb = None
    for app in xw.apps:
        for wb in app.books:
            if wb.name.casefold() == workbook_name.casefold():
                target_wb = wb
                break
        if target_wb is not None:
            break

    if target_wb is None:
        raise ValueError(f"Workbook '{workbook_name}' is not currently open in Excel.")

    sheet_names = [s.name for s in target_wb.sheets]
    if sheet_name not in sheet_names:
        raise ValueError(f"Sheet '{sheet_name}' was not found in workbook '{workbook_name}'.")

    ws = target_wb.sheets[sheet_name]

    # 2. Determine the search range
    if search_range:
        rng = ws.range(search_range)
    else:
        rng = ws.used_range

    used_start_col = rng.column
    used_end_col = rng.column + rng.columns.count - 1
    used_start_row = rng.row
    used_end_row = rng.row + rng.rows.count - 1

    # 3. Find all cells matching the keyword (Excel Find, case-insensitive, partial)
    matched_rows = set()
    try:
        found = rng.api.Find(
            What=keyword,
            LookIn=-4163,   # xlValues
            LookAt=2,       # xlPart
            MatchCase=False,
        )
        if found is not None:
            first_address = found.Address
            matched_rows.add(found.Row)
            while True:
                try:
                    found = rng.api.FindNext(found)
                except Exception:
                    break
                if found is None or found.Address == first_address:
                    break
                matched_rows.add(found.Row)
    except Exception as e:
        raise RuntimeError(f"Error during keyword search: {e}")

    if not matched_rows:
        return []

    # 4. Read header row (full width of used range)
    header_rng = ws.range((header_row, used_start_col), (header_row, used_end_col))
    raw_header = header_rng.options(ndim=2).value[0]
    header_data = [
        str(int(v)) if isinstance(v, float) and v.is_integer() else (re.sub(r'\s+', ' ', str(v)).strip() if v is not None else "")
        for v in raw_header
    ]

    # 5. Read each matched row; skip if it is the header row itself
    result = [header_data]
    for row_num in sorted(matched_rows):
        if row_num == header_row:
            continue
        if row_num < used_start_row or row_num > used_end_row:
            continue
        row_rng = ws.range((row_num, used_start_col), (row_num, used_end_col))
        raw_row = row_rng.options(ndim=2).value[0]
        row_data = [
            str(int(v)) if isinstance(v, float) and v.is_integer() else (re.sub(r'\s+', ' ', str(v)).strip() if v is not None else "")
            for v in raw_row
        ]
        result.append(row_data)

    return result


def get_sheet_names(file_path: str):
    """Get all sheet names by opening the workbook with the native Excel application."""
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    wb = None
    try:
        wb = app.books.open(file_path, read_only=True)
        return [sheet.name for sheet in wb.sheets]
    finally:
        if wb is not None:
            wb.close()
        app.quit()


def copy_sheet(file_path: str, source_sheet_name: str, target_sheet_name: str, position_after: str = None):
    """Copy a sheet in the same workbook using the native Excel application."""
    _validate_sheet_name(target_sheet_name)
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    wb = None
    try:
        wb = app.books.open(file_path)
        sheet_names = [sheet.name for sheet in wb.sheets]
        if source_sheet_name not in sheet_names:
            raise ValueError(f"Error: Source sheet '{source_sheet_name}' was not found.")
        if _sheet_name_exists(wb, target_sheet_name):
            raise ValueError(f"Error: Target sheet '{target_sheet_name}' already exists.")

        anchor_name = position_after or source_sheet_name
        if anchor_name not in sheet_names:
            raise ValueError(f"Error: Position sheet '{anchor_name}' was not found.")

        wb.sheets[source_sheet_name].api.Copy(After=wb.sheets[anchor_name].api)
        copied_sheets = [sheet for sheet in wb.sheets if sheet.name not in sheet_names]
        if len(copied_sheets) != 1:
            raise RuntimeError("Error: Could not uniquely identify the copied sheet.")
        copied_sheet = copied_sheets[0]
        copied_sheet.name = target_sheet_name
        wb.save()
        return [sheet.name for sheet in wb.sheets]
    finally:
        if wb is not None:
            wb.close()
        app.quit()


def rename_sheet(file_path: str, source_sheet_name: str, target_sheet_name: str):
    """Rename a sheet using the native Excel application."""
    _validate_sheet_name(target_sheet_name)
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    wb = None
    try:
        wb = app.books.open(file_path)
        sheet_names = [sheet.name for sheet in wb.sheets]
        if source_sheet_name not in sheet_names:
            raise ValueError(f"Error: Source sheet '{source_sheet_name}' was not found.")
        if _sheet_name_exists(wb, target_sheet_name):
            raise ValueError(f"Error: Target sheet '{target_sheet_name}' already exists.")

        wb.sheets[source_sheet_name].name = target_sheet_name
        wb.save()
        return [sheet.name for sheet in wb.sheets]
    finally:
        if wb is not None:
            wb.close()
        app.quit()


def delete_sheet(file_path: str, sheet_name: str):
    """Delete a sheet using the native Excel application."""
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    wb = None
    try:
        wb = app.books.open(file_path)
        sheet_names = [sheet.name for sheet in wb.sheets]
        if sheet_name not in sheet_names:
            raise ValueError(f"Error: Sheet '{sheet_name}' was not found.")
        if len(sheet_names) == 1:
            raise ValueError("Error: The only sheet in a workbook cannot be deleted.")

        wb.sheets[sheet_name].delete()
        wb.save()
        return [sheet.name for sheet in wb.sheets]
    finally:
        if wb is not None:
            wb.close()
        app.quit()


def col_to_num(col) -> int:
    """
    Helper function: Convert a column name (e.g., 'A', 'U', 'AI') or a 
    numeric string to a 1-based integer column index.
    """
    if isinstance(col, int):
        return col
    if isinstance(col, str):
        if col.isdigit():
            return int(col)
        # Convert letters to numbers
        exp = 0
        col_num = 0
        for char in reversed(col.upper()):
            col_num += (ord(char) - 64) * (26 ** exp)
            exp += 1
        return col_num
    raise ValueError(f"Invalid column format: {col}")


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


def create_col_data(start_col_index: int, end_col_index: int):
    col_data = ['NA']
    for col_index in range(start_col_index, end_col_index + 1):
        col_data.append(col_num_to_letter(col_index))
    return col_data


def read_sheet_data(file_path: str, sheet_name: str, sheet_range: str = None):
    """
    Read data from a specified sheet in an Excel file using xlwings.
    """
    # 1. Start Excel silently in the background (invisible, faster)
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False 

    try:
        if hasattr(app, 'api'):
            app.api.EnableEvents = False  # 禁用所有 Excel 事件 🔑
    except Exception as e:
        print(f"Failed to disable events (may be restricted on non-Windows systems): {e}")

    try:
        # Open the workbook (read-only mode)
        wb = app.books.open(file_path, read_only=True)
        sheet = wb.sheets[sheet_name]

        # 2. Determine the read range
        if sheet_range is None:
            # sheet.used_range can directly get the range containing data
            # The address property returns e.g., "$A$1:$D$10", we remove the "$" symbols
            rng = sheet.used_range
            sheet_range = rng.address.replace('$', '')
        else:
            rng = sheet.range(sheet_range)

        # 3. Get the start and end column indices (1-based)
        start_col_index = rng.column
        end_col_index = rng.column + rng.columns.count - 1

        # 4. Get merged cell information (xlwings adapted version)
        merged_ranges = []
        try:
            # Iterate through cells in the range to find the addresses of merged cells
            for cell in rng:
                if cell.api.MergeCells:
                    # Get the normalized address of the merged area (e.g., "A1:B2")
                    addr = cell.api.MergeArea.Address(False, False)
                    if addr not in merged_ranges:
                        merged_ranges.append(addr)
        except Exception as e:
            print(f"Failed to get merged cells (may be restricted on non-Windows systems): {e}")

        # 5. Initialize the return object
        # Note: This assumes the external 'create_col_data' function still exists
        sheet_obj = {
            "sheet_name": sheet.name,
            "sheet_data": [create_col_data(start_col_index, end_col_index)],
            "merged_ranges": merged_ranges
        }

        # 6. [Core Optimization] Read the entire range data at once (ndim=2 ensures a 2D list is returned)
        values = rng.options(ndim=2).value

        # 7. Process data
        for index, row in enumerate(values, start=1):
            row_data = [f"{index}"]  # Row index

            for val in row:
                if val is not None:
                    # 💡 Detail handling: xlwings reads integers as floats by default (e.g., 10 becomes 10.0)
                    # We restore it here to an integer-styled string to maintain consistency with openpyxl
                    if isinstance(val, float) and val.is_integer():
                        val = int(val)

                    # Clean whitespace using regex
                    cell_value = re.sub(r'\s+', ' ', f"{val}").strip()
                    row_data.append(cell_value)
                else:
                    row_data.append("")

            # Filter out completely empty rows (rows containing only the index)
            filter_data = [data for data in row_data if len(data.strip()) > 0]
            if len(filter_data) >= 1:
                sheet_obj["sheet_data"].append(row_data)

        return sheet_obj

    except Exception as e:
        print(f"Error reading Excel: {e}")
        raise e

    finally:
        # 8. In any case, make sure to close the workbook and quit the Excel process at the end!
        if 'wb' in locals():
            wb.close()
        app.quit()


def write_data_to_excel(file_path: str, sheet_name: str, data_config: dict | str):
    """
    Main entry function: Write data to Excel.
    """
    check_results = check_config(data_config)
    if len(check_results) > 0:
        raise Exception(check_results)

    app = xw.App(visible=False, add_book=False)
    wb = None
    try:
        wb = app.books.open(file_path)
        if sheet_name not in [s.name for s in wb.sheets]:
            raise Exception(f"Error: Sheet '{sheet_name}' not found.")
        ws = wb.sheets[sheet_name]

        # Parse JSON
        if isinstance(data_config, str):
            if os.path.exists(data_config):
                with open(data_config, 'r', encoding='utf-8') as f:
                    data_config = json.load(f)
            else:
                data_config = json.loads(data_config)

        # Call the recursive write engine with an initial offset of 0
        _write_node_recursive(ws, data_config, base_offset=0)

        wb.save()
        return "Write successful."

    except Exception as e:
        print(f"Failed to write: {traceback.format_exc()}")
        raise e
    finally:
        if wb:
            wb.close()
        app.quit()


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


def _write_node_recursive(ws, config: dict, base_offset: int) -> int:
    """
    Recursive write engine.
    :return: The total number of extra rows inserted into Excel by this operation (and its sub-operations).
    """
    inserted_rows_summary = 0  # Record the total number of rows inserted in the current level and sub-levels

    for cell_key, value in config.items():
        try:
            # 1. Calculate the actual row number of the current cell under the current state
            temp_rng = ws.range(cell_key)
            start_row = temp_rng.row + base_offset + inserted_rows_summary
            start_col = temp_rng.column

            # Case A: Normal string/numeric write
            if not isinstance(value, dict):
                ws.range((start_row, start_col)).value = _normalize_excel_text(value)

            # Case B: Complex dictionary configuration (Table or Object)
            else:
                data_type = value.get("data_type", "value")
                op_mode = value.get("op_mode", "write")
                data_list = value.get("data_list", None)
                cell_value = value.get("value", None)

                # ==================== Core Logic 1: Process Object Block ====================
                if data_type == "object":
                    object_range = value.get("object_range")  # e.g., "17-47"
                    t_start, t_end = map(int, object_range.split("-"))
                    t_height = t_end - t_start + 1  # Height of the template block (e.g., 31 rows)
                    num_items = len(data_list)
                    gap = 1  # ⭐ Number of gap rows when copying and inserting

                    # 1.1 If there are multiple data items and the mode is "insert", copy and insert them one by one, leaving gap rows in between
                    actual_t_start = t_start + base_offset + inserted_rows_summary
                    actual_t_end = t_end + base_offset + inserted_rows_summary

                    if num_items > 1 and op_mode == "insert":
                        current_end = actual_t_end
                        for i in range(1, num_items):
                            # Copy the current template block
                            ws.range(f"{actual_t_start}:{actual_t_end}").copy()

                            # Calculate the insertion position (insert at current end row + 1 + gap)
                            insert_start = current_end + 1 + gap
                            insert_end = insert_start + t_height - 1

                            # Execute insertion
                            ws.range(f"{insert_start}:{insert_end}").insert(shift='down')
                            current_end = insert_end

                        # Clear clipboard
                        try:
                            ws.book.app.api.CutCopyMode = False
                        except:
                            pass

                        # Accumulate the number of new rows added due to copying blocks (including gap rows)
                        inserted_rows_summary += (num_items - 1) * (t_height + gap)

                    # 1.2 Loop to write each Object data item
                    child_inserted_rows = 0  # Record the number of rows dynamically inserted by child nodes during the writing process
                    for i, item_data in enumerate(data_list):
                        # Calculate the starting row of the i-th Block (initial start + block offset + child node dynamic insertion offset)
                        block_i_start = actual_t_start + i * (t_height + gap) + child_inserted_rows

                        # Sub-offset = actual start of current Block - original template start
                        sub_offset = block_i_start - t_start

                        # Recursive call: Write child data
                        inserted_by_child = _write_node_recursive(ws, item_data, base_offset=sub_offset)
                        child_inserted_rows += inserted_by_child

                    # Merge the rows inserted by child nodes into the total inserted rows
                    inserted_rows_summary += child_inserted_rows
                elif data_type == "multi_lines":
                    if cell_value and type(cell_value) == str and len(cell_value) > 0:
                        lines = _normalize_excel_text(cell_value).split("\n")
                        for line in lines:

                            if op_mode == "insert":
                                ws.range(f"{start_row}:{start_row}").copy()
                                insert_range = ws.range(f"{start_row}:{start_row + 1 - 1}")
                                insert_range.insert(shift='down')
                                try:
                                    ws.book.app.api.CutCopyMode = False
                                except:
                                    pass
                                inserted_rows_summary += 1

                            ws.range((start_row, start_col)).value = line
                            start_row += 1
                    else:
                        print("No data need be to deal.")

                # ==================== Core Logic 2: Process Ordinary Table ====================
                elif data_type == "table":
                    col_refs = value.get("col_refs", None)
                    num_rows = len(data_list)

                    # 2.1 If it is insert mode, insert rows first
                    if op_mode == "insert":
                        ws.range(f"{start_row}:{start_row}").copy()
                        insert_range = ws.range(f"{start_row}:{start_row + num_rows - 1}")
                        insert_range.insert(shift='down')
                        try:
                            ws.book.app.api.CutCopyMode = False
                        except:
                            pass
                        inserted_rows_summary += num_rows

                    # 2.2 Write Table data
                    if col_refs is not None:
                        for r_idx, row_data in enumerate(data_list):
                            target_row = start_row + r_idx
                            for c_idx, cell_value in enumerate(row_data):
                                target_col = start_col
                                if c_idx < len(col_refs):
                                    # ⭐ Key Fix: Use col_to_num to ensure the column number is an integer
                                    target_col = col_to_num(col_refs[c_idx])
                                ws.range((target_row, target_col)).value = _normalize_excel_text(f"{cell_value}")
                    else:
                        ws.range((start_row, start_col)).value = data_list

                elif data_type == "table_multi_lines":
                    col_refs = value.get("col_refs", None)
                    expanded_rows = []

                    for row_data in data_list:
                        split_values = [
                            _normalize_excel_text(f"{item}").split("\n")
                            for item in row_data
                        ]
                        physical_row_count = max(len(lines) for lines in split_values)
                        for line_index in range(physical_row_count):
                            expanded_rows.append([
                                lines[line_index] if line_index < len(lines) else ""
                                for lines in split_values
                            ])

                    if op_mode == "insert" and expanded_rows:
                        ws.range(f"{start_row}:{start_row}").copy()
                        insert_range = ws.range(f"{start_row}:{start_row + len(expanded_rows) - 1}")
                        insert_range.insert(shift='down')
                        try:
                            ws.book.app.api.CutCopyMode = False
                        except:
                            pass
                        inserted_rows_summary += len(expanded_rows)

                    for row_index, row_data in enumerate(expanded_rows):
                        target_row = start_row + row_index
                        for col_index, cell_value in enumerate(row_data):
                            target_col = start_col
                            if col_index < len(col_refs):
                                target_col = col_to_num(col_refs[col_index])
                            ws.range((target_row, target_col)).value = _normalize_excel_text(cell_value)

                else:
                    print(f"Warning: '{data_type}' is not supported.'")

        except Exception as e:
            print(f"Error processing cell '{cell_key}': {e}")
            raise e

    return inserted_rows_summary


def get_used_range_including_shapes(ws: Sheet = None, file_path: str = None, sheet_name: str = None) -> str:
    """
    Get the actual used range address of the specified Sheet (including the complete area occupied by cell data, shapes, images, and charts).

    Args:
        ws: The instance of xlwings.
        file_path (str): Excel file path.
        sheet_name (str): Worksheet name.

    Returns:
        str: Used range address, e.g., "B5:F12"
    """
    app = None
    if ws is None:
        app = xw.App(visible=False, add_book=False)
    wb = None

    min_row, min_col = None, None
    max_row, max_col = None, None

    try:
        if ws is None:
            wb = app.books.open(file_path)
            ws = wb.sheets[sheet_name]

        # 1. Get the boundaries of cell data - improved judgment logic
        used_range = ws.used_range

        # Check if used_range actually contains data
        # Method 1: Check if it contains actual values
        has_data = False
        if used_range.address != '$A$1':
            # If the range is not A1, it means there is data
            has_data = True
        else:
            # If it is A1, check if there is an actual value
            try:
                if used_range.value is not None:
                    # For a single cell, check if it is None or an empty string
                    if isinstance(used_range.value, (list, tuple)):
                        # Multi-dimensional array
                        if any(any(cell is not None and cell != '' for cell in row) for row in used_range.value):
                            has_data = True
                    else:
                        # Single value
                        if used_range.value is not None and used_range.value != '':
                            has_data = True
            except:
                pass

        if has_data:
            min_row = used_range.row
            min_col = used_range.column
            max_row = used_range.last_cell.row
            max_col = used_range.last_cell.column

        # 2. Get the boundaries of shapes, images, and charts - improved method
        if ws.shapes:
            for shape in ws.shapes:
                try:
                    # Use API to get more accurate coordinates
                    # Get the cell positions of the top-left and bottom-right corners of the shape
                    top_left = shape.api.TopLeftCell
                    bottom_right = shape.api.BottomRightCell

                    if top_left and bottom_right:
                        tl_row = top_left.Row
                        tl_col = top_left.Column
                        br_row = bottom_right.Row
                        br_col = bottom_right.Column

                        # If there was no previous data, initialize directly with the boundary of the first shape
                        if min_row is None:
                            min_row = tl_row
                            min_col = tl_col
                            max_row = br_row
                            max_col = br_col
                        else:
                            # If a boundary already exists, take the union (expand the range)
                            min_row = min(min_row, tl_row)
                            min_col = min(min_col, tl_col)
                            max_row = max(max_row, br_row)
                            max_col = max(max_col, br_col)

                except Exception as shape_err:
                    # If the API method fails, try using the original method
                    try:
                        tl_cell = shape.top_left_cell
                        br_cell = shape.bottom_right_cell

                        if tl_cell and br_cell:
                            if min_row is None:
                                min_row = tl_cell.row
                                min_col = tl_cell.column
                                max_row = br_cell.row
                                max_col = br_cell.column
                            else:
                                min_row = min(min_row, tl_cell.row)
                                min_col = min(min_col, tl_cell.column)
                                max_row = max(max_row, br_cell.row)
                                max_col = max(max_col, br_cell.column)
                    except:
                        continue

        # 3. If the entire Sheet has neither data nor shapes, default to A1
        if min_row is None:
            min_row, min_col, max_row, max_col = 1, 1, 1, 1

        # 4. Generate a standard address without $
        full_range = ws.range((min_row, min_col), (max_row, max_col))
        range_address = full_range.address.replace('$', '')
        return range_address

    except:
        raise
    finally:
        if wb:
            wb.close()
        if app:
            app.quit()


def excel2images(
    file_path: str,
    sheet_names: list[str] = None,
    sheet_ranges: list[str] = None,
    output_path: str = None,
    scale: float = 2.0
):
    app = xw.App(visible=False, add_book=False)  # Must be visible (Note: xlwings App configuration)
    wb = None

    result_imgs = []
    try:
        wb = app.books.open(file_path, read_only=True)
        for index, ws in enumerate(wb.sheets):

            if sheet_names is not None and ws.name not in sheet_names:
                continue
            if sheet_names is not None and sheet_ranges is not None and len(sheet_names) == len(sheet_ranges):
                sheet_range = sheet_ranges[index]
            else:
                sheet_range = get_used_range_including_shapes(ws=ws)
            target_range = ws.range(sheet_range)
            ws.activate()
            target_range.select()
            time.sleep(0.2)

            target_range.api.CopyPicture(1, 2)
            time.sleep(0.3)

            img = ImageGrab.grabclipboard()

            if img is None:
                raise Exception("Error: Can't not copy the range.")

            base_name = os.path.splitext(os.path.basename(file_path))[0]
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            file_name = f"{base_name}_{ws.name}_{sheet_range.replace(':', '_')}_{timestamp}.png"
            if output_path is None:
                output_path = os.path.dirname(file_path) or "."

            os.makedirs(output_path, exist_ok=True)
            output_file = os.path.join(output_path, file_name)

            if scale != 1.0:
                from PIL import Image
                new_size = (int(img.width * scale), int(img.height * scale))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            img.save(output_file, 'PNG')
            result_imgs.append(output_file)

        return result_imgs

    except:
        raise
    finally:
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.CloseClipboard()
        except:
            pass

        if wb:
            wb.close()
        app.quit()


def check_config(config: str | dict):
    error_infos = []
    available_data_types = ['table', 'table_multi_lines', 'multi_lines', 'object']
    table_keys = ["col_refs", "data_list", "op_mode"]
    if isinstance(config, dict):
        for key, value in config.items():
            if value is None:
                error_infos.append(f"'{key}': value field cannot be None.")
            elif isinstance(value, dict):
                if "data_type" not in value:
                    error_infos.append(f"{key}': 'data_type' field must be set. Available values: {available_data_types}")
                if value["data_type"] not in available_data_types:
                    error_infos.append(f"{key}': 'data_type' field must be in available values: {available_data_types}")
                else:
                    data_type = value["data_type"]
                    if data_type in ("table", "table_multi_lines"):
                        col_refs = value.get("col_refs", None)
                        data_list = value.get("data_list", None)
                        op_mode = value.get("op_mode", None)

                        if ("col_refs" not in value or "data_list" not in value or "op_mode" not in value
                                or col_refs is None or data_list is None or op_mode is None):
                            error_infos.append(
                                f"{key}': {table_keys} fields must be set and cannot be None.")
                        else:
                            if type(col_refs) is not list or type(data_list) is not list:
                                error_infos.append(f"{key}: 'col_refs' and 'data_list' fields should be a list.")
                            else:
                                for index, data in enumerate(data_list):
                                    if len(data) != len(col_refs):
                                        error_infos.append(
                                            f"{key}: The length of 'col_refs' and 'data_list' fields should be the same. error data: data_list[{index}]")
                    elif data_type == "object":
                        if "object_range" not in value or "data_list" not in value:
                            error_infos.append(
                                f"{key}': 'object_range' and 'data_list' fields must be set for 'object' data_type.")
                        else:
                            object_range = value.get("object_range")
                            data_list = value.get("data_list")
                            if type(data_list) is not list:
                                error_infos.append(f"{key}: 'data_list' field should be a list.")
                            else:
                                for item in data_list:
                                    if type(item) is not dict:
                                        error_infos.append(f"{key}: Each item in 'data_list' should be a dict. error item: {item}")
                                    else:
                                        # Recursive check for nested objects
                                        nested_errors = check_config(item)
                                        if nested_errors:
                                            error_infos.extend([f"{key}.{nested_key}" for nested_key in nested_errors])
                    elif data_type == "multi_lines":
                        if "value" not in value:
                            error_infos.append(f"{key}': 'value' field must be set for 'multi_lines' data_type.")
                        else:
                            multi_lines_value = value.get("value")
                            if type(multi_lines_value) is not str:
                                error_infos.append(f"{key}: 'value' field should be a string for 'multi_lines' data_type.")
                            
    elif isinstance(config, str):
        try:
            if os.path.exists(config):
                config_dict = json.load(open(config, encoding="utf-8"))
                return check_config(config_dict)
            else:
                error_infos.append(f"File does not exist: {config}")
        except json.JSONDecodeError as e:
            error_infos.append(f"Invalid JSON string: {e}")
    return error_infos


def test_01():
    result = read_sheet_data(
        file_path=r"D:\MyProjects\DP\AMSエージェント_githubCopilot\docs\data\課題管理表.xlsm",
        sheet_name="課題管理表",
        sheet_range="A1:V9"
    )
    print(result)

def test_02():
    result = get_used_range_including_shapes(
        file_path=r"C:\Users\cq.zhangjw\Desktop\新規 Microsoft Excel Worksheet.xlsx",
        sheet_name="Sheet1"
    )
    print(result)

def test_03():
    result = excel2images(
        file_path=r"C:\Users\cq.zhangjw\Desktop\新規 Microsoft Excel Worksheet.xlsx",
    )
    print(result)


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


def test_05():
    template_file = r"D:\MyProjects\MTCode\agent-base\data\mcp_servers\stdio\project\qclms\qclms_document_generate_server\templates\XXX_アプリケーション詳細仕様／Application Detailed Specification.xlsx"
    result_file = template_file.replace(".xlsx", f"_{int(time.time()) * 1000}.xlsx")
    shutil.copyfile(template_file, result_file)
    sheet_name = "アプリケーション詳細仕様"
    data_config = {
        "T17": {
            "op_mode": "insert",
            "data_type": "object",
            "object_range": "17-47",
            "data_list": [
                {
                    "T17": "str1",
                    "T18": "str1",
                    "T19": "str1",
                    "U22": {
                        "col_refs": ["U", "W", "AI", "AU"],
                        "data_list": [
                            ["Data1", "Data2", "Data3", "Data4"],
                            ["Data4", "Data5", "Data6", "Data7"]
                        ],
                        "op_mode": "insert"
                    },
                    "T26": "T26",
                    "T33": "T33"
                },
                {
                    "T17": "T17",
                    "T18": "T18",
                    "T19": "T19",
                    "U22": {
                        "col_refs": ["U", "W", "AI", "AU"],
                        "data_list": [
                            ["Data1", "Data2", "Data3", "Data4"],
                            ["Data4", "Data5", "Data6", "Data7"]
                        ],
                        "op_mode": "insert"
                    },
                    "T33": "T33"
                }
            ]
        }
    }
    write_data_to_excel(file_path=result_file, sheet_name=sheet_name, data_config=data_config)

def test_06():
    template_file = r"D:\gitproj\aks_dev_agent\testfile\プログラム設計書_プログラムID_プログラム名_Rev0.00.xlsx"
    result_file = template_file.replace(".xlsx", f"_{int(time.time()) * 1000}.xlsx")
    shutil.copyfile(template_file, result_file)
    sheet_name = "要求定義書"
    data_config = {
    "B8": "Ｉ／Ｏ関連図",
    "AE9": "要求事項",
    "AE17": "機能",
    "AE20": "運用",
    "AE26": "主要部門",
    "AE28": "使用想定時期",
    "AE32": "実行方法",
    "B36": {
      "label": "テーブル　I/O SEQ1-10",
      "col_refs": ["B", "D", "O", "Q"],
      "data_list": [
          ["SEQ", "テーブル名（システム）", "I/O", "主"]
        ],
      "op_mode": "write"
    },
    "S36": {
      "label": "テーブル　I/O SEQ11-20",
      "col_refs": ["S", "U", "AF", "AH"],
      "data_list": [
          ["SEQ", "テーブル名（システム）", "I/O", "主"]
        ],
      "op_mode": "write"
    },
    "AJ36": {
      "label": "共通モジュール名称 SEQ1-10",
      "col_refs": ["AJ", "AL", "AV"],
      "data_list": [
          ["SEQ", "共通モジュール名称", "共通モジュールID"]
        ],
      "op_mode": "write"
    }
  }
    write_data_to_excel(file_path=result_file, sheet_name=sheet_name, data_config=data_config)

def test_07():
    template_file = r"D:\MyProjects\DP\aks_dev_agent\docs\templates\01_base_design.xlsx"
    result_file = template_file.replace(".xlsx", f"_{int(time.time()) * 1000}.xlsx")
    shutil.copyfile(template_file, result_file)
    # data_config = {
    #     "A9": {
    #         "data_type": "table",
    #         "label": "テーブル　SEQ1-10",
    #         "col_refs": ["A", "C", "O", "V", "AB"],
    #         "data_list": [
    #             ["SEQ", "テーブル名（システム）", "I/O", "主", "備考"],
    #             ["SEQ", "テーブル名（システム）", "I/O", "主", "備考"],
    #             ["SEQ", "テーブル名（システム）", "I/O", "主", "備考"]
    #         ],
    #         "op_mode": "insert"
    #     }
    # }
    data_config = r"D:\Downloads\01_base_design_画面項目_data_config.json"
    check_result = check_config(data_config)
    print(f"check_result: {check_result}")
    sheet_name = "画面項目"

    write_data_to_excel(file_path=result_file, sheet_name=sheet_name, data_config=data_config)


def test_08():
    template_file = r"D:\MyProjects\DP\aks_dev_agent\docs\templates\01_base_design.xlsx"
    result_file = template_file.replace(".xlsx", f"_{int(time.time()) * 1000}.xlsx")
    shutil.copyfile(template_file, result_file)
    data_config = {
        "C13": {
            "label": "テーブル　SEQ1-10",
            "data_type": "multi_lines",
            "value": "line1\n    line2\n    line3  line4",
            "op_mode": "insert"
        }
    }

    sheet_name = "要求定義書"

    write_data_to_excel(file_path=result_file, sheet_name=sheet_name, data_config=data_config)

def test_09():
    result = get_open_workbooks()
    if result:
        for item in result:
            print(item)
    else:
        print("No Excel workbooks are currently open.")


def test_10():
    open_books = get_open_workbooks()
    if not open_books:
        print("No Excel workbooks are currently open.")
        return
    # Use the first open workbook / sheet for demonstration
    wb_name = open_books[0]["workbook"]
    sheet = open_books[0]["active_sheet"]
    print(f"Searching in: {wb_name} / {sheet}")
    keyword = input("Enter keyword to search: ").strip()
    header_row = int(input("Header row number (default 1): ").strip() or "1")
    result = find_rows_by_keyword_in_open_workbook(
        workbook_name=wb_name,
        sheet_name=sheet,
        keyword=keyword,
        header_row=header_row,
    )
    if not result:
        print("No matching rows found.")
    else:
        for row in result:
            print(row)


if __name__ == '__main__':
    # test_04()
    # test_05()
    # test_02()
    # test_03()
    # test_01()
    # test_07()
    # test_08()
    # test_09()
    test_10()