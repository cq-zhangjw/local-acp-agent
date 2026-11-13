import argparse
import json
import os
import sys
from pathlib import Path


# Ensure imports work when running this script directly from the workspace.
SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
	sys.path.insert(0, str(SKILL_DIR))

from scripts.excel_util import (  # noqa: E402
	extract_vba_from_xlsm,
	filter_data_by_condition,
	find_rows_in_sheet_by_value,
	read_excel,
	read_sheet,
	# write_data_to_excel,
)

from scripts.xlwings_util import (
	copy_sheet,
	delete_sheet,
	excel2images,
	find_rows_by_keyword_in_open_workbook,
	get_open_workbooks,
	get_sheet_names,
	rename_sheet,
	write_data_to_excel,
)


def _print_json(data):
	print(json.dumps(data, ensure_ascii=False, indent=2))


def _parse_json_input(value: str):
	if os.path.exists(value):
		with open(value, "r", encoding="utf-8") as f:
			return json.load(f)
	return json.loads(value)


def cmd_read_excel(args):
	result = read_excel(file_path=args.file_path, sheet_names=args.sheet_names)
	_print_json(result)


def cmd_read_sheet(args):
	result = read_sheet(
		file_path=args.file_path,
		sheet_name=args.sheet_name,
		sheet_range=args.sheet_range,
	)
	_print_json(result)


def cmd_filter_data_by_condition(args):
	result = filter_data_by_condition(
		file_path=args.file_path,
		sheet_name=args.sheet_name,
		search_value=args.search_value,
		col_str=args.col_str,
	)
	_print_json(result)


def cmd_find_rows_in_sheet_by_value(args):
	result = find_rows_in_sheet_by_value(
		file_path=args.file_path,
		sheet_name=args.sheet_name,
		search_value=args.search_value,
		sheet_range=args.sheet_range,
		title_row_index=args.title_row_index,
	)
	_print_json(result)


def cmd_get_open_workbooks(_args):
	result = get_open_workbooks()
	_print_json(result)


def cmd_find_rows_by_keyword_in_open_workbook(args):
	result = find_rows_by_keyword_in_open_workbook(
		workbook_name=args.workbook_name,
		sheet_name=args.sheet_name,
		keyword=args.keyword,
		header_row=args.header_row,
		search_range=args.search_range,
	)
	_print_json(result)


def cmd_get_sheet_names(args):
	result = get_sheet_names(file_path=args.file_path)
	_print_json(result)


def cmd_copy_sheet(args):
	result = copy_sheet(
		file_path=args.file_path,
		source_sheet_name=args.source_sheet_name,
		target_sheet_name=args.target_sheet_name,
		position_after=args.position_after,
	)
	_print_json({"status": "ok", "sheet_names": result})


def cmd_rename_sheet(args):
	result = rename_sheet(
		file_path=args.file_path,
		source_sheet_name=args.source_sheet_name,
		target_sheet_name=args.target_sheet_name,
	)
	_print_json({"status": "ok", "sheet_names": result})


def cmd_delete_sheet(args):
	result = delete_sheet(file_path=args.file_path, sheet_name=args.sheet_name)
	_print_json({"status": "ok", "sheet_names": result})


def cmd_write_data_to_excel(args):
	data_config = _parse_json_input(args.data_config)
	write_data_to_excel(
		file_path=args.file_path,
		sheet_name=args.sheet_name,
		data_config=data_config,
	)
	_print_json(
		{
			"status": "ok",
			"file_path": args.file_path,
			"sheet_name": args.sheet_name,
			"updated_cells": len(data_config),
		}
	)


def cmd_extract_vba_from_xlsm(args):
	result = extract_vba_from_xlsm(args.file_path)
	if not result:
		_print_json({"modules": [], "all_code": ""})
		return

	modules, all_code = result
	if args.code_only:
		print(all_code)
		return

	_print_json({"modules": modules, "all_code": all_code})


def cmd_excel2images(args):
	sheet_names = _parse_json_input(args.sheet_names) if args.sheet_names else None
	sheet_ranges = _parse_json_input(args.sheet_ranges) if args.sheet_ranges else None

	result = excel2images(
		file_path=args.file_path,
		sheet_names=sheet_names,
		sheet_ranges=sheet_ranges,
		output_path=args.output_path,
		scale=args.scale,
	)
	_print_json({"images": result})


def build_parser():
	parser = argparse.ArgumentParser(
		description="CLI wrapper for excel_util helpers.",
	)
	subparsers = parser.add_subparsers(dest="command", required=True)

	p_read_excel = subparsers.add_parser("read_excel", help="Read multiple sheets from an Excel file.")
	p_read_excel.add_argument("--file-path", required=True, help="Path to .xlsx/.xlsm file")
	p_read_excel.add_argument(
		"--sheet-names",
		nargs="+",
		default=None,
		help="Optional sheet names list. If omitted, all sheets are read.",
	)
	p_read_excel.set_defaults(func=cmd_read_excel)

	p_read_sheet = subparsers.add_parser("read_sheet", help="Read one sheet from an Excel file.")
	p_read_sheet.add_argument("--file-path", required=True, help="Path to .xlsx/.xlsm file")
	p_read_sheet.add_argument("--sheet-name", required=True, help="Sheet name")
	p_read_sheet.add_argument("--sheet-range", default=None, help="Optional Excel range, e.g. A1:C10")
	p_read_sheet.set_defaults(func=cmd_read_sheet)

	p_filter = subparsers.add_parser(
		"filter_data_by_condition",
		help="Filter rows by substring match in a target column.",
	)
	p_filter.add_argument("--file-path", required=True, help="Path to .xlsx/.xlsm file")
	p_filter.add_argument("--sheet-name", required=True, help="Sheet name")
	p_filter.add_argument("--col-str", required=True, help="Target column letter, e.g. B")
	p_filter.add_argument("--search-value", required=True, help="Substring to search for")
	p_filter.set_defaults(func=cmd_filter_data_by_condition)

	p_find_rows = subparsers.add_parser(
		"find_rows_in_sheet_by_value",
		help="Find the first row containing a specific value (plus header/title rows).",
	)
	p_find_rows.add_argument("--file-path", required=True, help="Path to .xlsx/.xlsm file")
	p_find_rows.add_argument("--sheet-name", required=True, help="Sheet name")
	p_find_rows.add_argument("--search-value", required=True, help="Value to search for")
	p_find_rows.add_argument("--sheet-range", default=None, help="Optional Excel range, e.g. A1:C10")
	p_find_rows.add_argument(
		"--title-row-index",
		type=int,
		default=None,
		help="Optional row index to always include in output.",
	)
	p_find_rows.set_defaults(func=cmd_find_rows_in_sheet_by_value)

	p_open_books = subparsers.add_parser(
		"get_open_workbooks",
		help="List all workbooks currently open in Excel and their active sheet.",
	)
	p_open_books.set_defaults(func=cmd_get_open_workbooks)

	p_find_open = subparsers.add_parser(
		"find_rows_by_keyword_in_open_workbook",
		help="Search a keyword in an open workbook and return matched rows with the header row.",
	)
	p_find_open.add_argument("--workbook-name", required=True, help="Name of the open workbook, e.g. Book1.xlsx")
	p_find_open.add_argument("--sheet-name", required=True, help="Sheet name")
	p_find_open.add_argument("--keyword", required=True, help="Keyword to search (partial, case-insensitive)")
	p_find_open.add_argument("--header-row", type=int, default=1, help="1-based header row number (default: 1)")
	p_find_open.add_argument("--search-range", default=None, help="Optional Excel range to restrict search, e.g. A1:Z100")
	p_find_open.set_defaults(func=cmd_find_rows_by_keyword_in_open_workbook)

	p_get_sheet_names = subparsers.add_parser(
		"get_sheet_names",
		help="Get all sheet names from an Excel file.",
	)
	p_get_sheet_names.add_argument("--file-path", required=True, help="Path to .xlsx/.xlsm file")
	p_get_sheet_names.set_defaults(func=cmd_get_sheet_names)

	p_copy_sheet = subparsers.add_parser("copy_sheet", help="Copy a sheet using Excel COM.")
	p_copy_sheet.add_argument("--file-path", required=True, help="Path to .xlsx/.xlsm file")
	p_copy_sheet.add_argument("--source-sheet-name", required=True, help="Source sheet name")
	p_copy_sheet.add_argument("--target-sheet-name", required=True, help="Target sheet name")
	p_copy_sheet.add_argument("--position-after", default=None, help="Place the copied sheet after this sheet")
	p_copy_sheet.set_defaults(func=cmd_copy_sheet)

	p_rename_sheet = subparsers.add_parser("rename_sheet", help="Rename a sheet using Excel COM.")
	p_rename_sheet.add_argument("--file-path", required=True, help="Path to .xlsx/.xlsm file")
	p_rename_sheet.add_argument("--source-sheet-name", required=True, help="Current sheet name")
	p_rename_sheet.add_argument("--target-sheet-name", required=True, help="New sheet name")
	p_rename_sheet.set_defaults(func=cmd_rename_sheet)

	p_delete_sheet = subparsers.add_parser("delete_sheet", help="Delete a sheet using Excel COM.")
	p_delete_sheet.add_argument("--file-path", required=True, help="Path to .xlsx/.xlsm file")
	p_delete_sheet.add_argument("--sheet-name", required=True, help="Sheet name")
	p_delete_sheet.set_defaults(func=cmd_delete_sheet)

	p_write = subparsers.add_parser("write_data_to_excel", help="Write values to target cells.")
	p_write.add_argument("--file-path", required=True, help="Path to .xlsx/.xlsm file")
	p_write.add_argument("--sheet-name", required=True, help="Sheet name")
	p_write.add_argument(
		"--data-config",
		required=True,
		help="JSON string or path to JSON file, e.g. '{\"A1\":\"hello\"}'",
	)
	p_write.set_defaults(func=cmd_write_data_to_excel)

	p_vba = subparsers.add_parser("extract_vba_from_xlsm", help="Extract VBA modules from an .xlsm file.")
	p_vba.add_argument("--file-path", required=True, help="Path to .xlsm file")
	p_vba.add_argument(
		"--code-only",
		action="store_true",
		help="Print only merged VBA code text instead of JSON.",
	)
	p_vba.set_defaults(func=cmd_extract_vba_from_xlsm)

	p_excel2images = subparsers.add_parser(
		"excel2images",
		help="Export worksheet ranges to image files.",
	)
	p_excel2images.add_argument("--file-path", required=True, help="Path to .xlsx/.xlsm file")
	p_excel2images.add_argument(
		"--sheet-names",
		default=None,
		help="Optional JSON array string or JSON file path, e.g. '[\"Sheet1\",\"Sheet2\"]'.",
	)
	p_excel2images.add_argument(
		"--sheet-ranges",
		default=None,
		help="Optional JSON array string or JSON file path, e.g. '[\"A1:C10\",\"A1:D20\"]'.",
	)
	p_excel2images.add_argument(
		"--output-path",
		default=None,
		help="Optional output image path. If omitted, files are generated next to input workbook.",
	)
	p_excel2images.add_argument(
		"--scale",
		type=float,
		default=2.0,
		help="Image scale factor. Default is 2.0.",
	)
	p_excel2images.set_defaults(func=cmd_excel2images)

	return parser


def main():
	parser = build_parser()
	args = parser.parse_args()
	try:
		args.func(args)
	except Exception as ex:
		error = {"status": "error", "message": str(ex)}
		_print_json(error)
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
