---
name: excel-skill
description: Excel（.xlsx/.xlsm）の読込・検索・書込、シートの複製/改名/削除、画像出力、VBA抽出を行う。ユーザーが Excel・表格・xlsx・sheet・セル・单元格 に言及した場合や、Excelファイルを扱う必要がある場合は必ずこのスキルを使用する。
---

# Excel Skill

Excelファイルの読込・検索・書込・シート操作・VBA抽出・画像出力をCLIで実行するスキルです。

## 1. 実行方法

スキルディレクトリ: `{{SKILL_DIR}}`（以下 `<SKILL_DIR>`。プレースホルダーが置換されていない場合は、この SKILL.md と同じディレクトリ）

すべてのコマンドは `run.ps1` 経由で実行する（PowerShell 上でそのまま実行し、`Powershell -Command` で包まない）:

```powershell
& "<SKILL_DIR>/run.ps1" <command> [options]
```

- `.venv` の作成・パッケージインストール・UTF-8 設定は `run.ps1` が自動で行うため、事前準備は不要。
- 出力は JSON。失敗時は `{"status": "error", "message": ...}` を返す。
- JSON 配列などの引数は、クォートのエスケープを避けるため `.tmp/` に JSON ファイルを作成し、そのパスを渡すことを推奨。

## 2. 推奨フロー

- **読込**: `get_sheet_names` → `read_sheet`（`--sheet-range A1:Z20` などで表頭を確認）→ 必要な範囲だけ `read_sheet` / `find_rows_in_sheet_by_value`
  - `read_excel` の全シート読込は出力が大きくなるため、小さなファイル以外では使わない。
- **書込**: 対象範囲を読込 → `.tmp/` に data_config.json を作成 → **書込内容をユーザーに確認** → `write_data_to_excel`
- **破壊的操作**（`write_data_to_excel` / `delete_sheet` / `rename_sheet`）: 実行前にユーザーへ確認する。必要に応じて元ファイルを `.tmp/` にバックアップする。
- 書込・シート操作は Excel COM（xlwings）で行う。対象ファイルを Excel で開いていると失敗することがあるため、事前に閉じてもらう。

## 3. コマンド一覧

| コマンド | 必須 | 任意 | 説明 |
|---|---|---|---|
| `get_sheet_names` | `--file-path` | | シート名一覧 |
| `read_sheet` | `--file-path` `--sheet-name` | `--sheet-range`（例 `A1:C10`） | 1シート読込 |
| `read_excel` | `--file-path` | `--sheet-names`（スペース区切りで複数） | 複数シート読込（未指定時は全シート） |
| `find_rows_in_sheet_by_value` | `--file-path` `--sheet-name` `--search-value` | `--sheet-range` `--title-row-index` | 値を含む行を検索（タイトル行番号を指定するとその行も返す） |
| `filter_data_by_condition` | `--file-path` `--sheet-name` `--col-str`（例 `B`） `--search-value` | | 指定列の部分一致で行を抽出 |
| `get_open_workbooks` | | | Excelで開いているブックとアクティブシート一覧 |
| `find_rows_by_keyword_in_open_workbook` | `--workbook-name` `--sheet-name` `--keyword` | `--header-row`（既定 1） `--search-range` | 開いているブックでキーワード検索（ヘッダー行＋一致行） |
| `write_data_to_excel` | `--file-path` `--sheet-name` `--data-config` | | セル/表データ書込（data-config は JSON ファイルパス または JSON 文字列） |
| `copy_sheet` | `--file-path` `--source-sheet-name` `--target-sheet-name` | `--position-after` | シート複製（未指定時はコピー元の直後） |
| `rename_sheet` | `--file-path` `--source-sheet-name` `--target-sheet-name` | | シート名変更 |
| `delete_sheet` | `--file-path` `--sheet-name` | | シート削除 |
| `extract_vba_from_xlsm` | `--file-path` | `--code-only` | `.xlsm` の VBA 抽出 |
| `excel2images` | `--file-path` | `--sheet-names` `--sheet-ranges`（JSON配列 or JSONファイルパス） `--output-path` `--scale`（既定 2.0） | シート/範囲を PNG 出力（未指定時は使用範囲を自動判定） |

シート名は31文字以内、`\ / * ? : [ ]` を含めないこと。

### 実行例

```powershell
& "<SKILL_DIR>/run.ps1" read_sheet --file-path data/課題管理表.xlsm --sheet-name 課題管理表 --sheet-range A6:V10
& "<SKILL_DIR>/run.ps1" find_rows_in_sheet_by_value --file-path data/課題管理表.xlsm --sheet-name 課題管理表 --sheet-range A6:V300 --search-value M-0007568 --title-row-index 6
& "<SKILL_DIR>/run.ps1" write_data_to_excel --file-path outputs/form.xlsx --sheet-name 入力フォーム --data-config .tmp/data_config.json
& "<SKILL_DIR>/run.ps1" excel2images --file-path outputs/form.xlsx --sheet-names .tmp/sheets.json --sheet-ranges .tmp/ranges.json --scale 1.5
```

## 4. 読込結果の形式

`read_sheet` / `read_excel` は以下のJSONを返す（`read_excel` はその配列）:

```json
{
  "sheet_name": "課題管理表",
  "sheet_data": [
    ["NA", "A", "B", "C"],
    ["1", "A1の値", "B1の値", "C1の値"],
    ["2", "A2の値", "B2の値", "C2の値"]
  ],
  "merged_ranges": ["A1:B1", "C2:D2"]
}
```

- `sheet_data` の1行目は列名、各行の1列目はExcelの行番号。
- `merged_ranges` は結合セル範囲。

## 5. data_config の書き方

キーは書込開始セル（例 `A1`）。値が単一値ならそのまま書き込み、表データはオブジェクトで指定する。

```json
{
  "A1": "A1の値",
  "B2": 123,
  "D3": {
    "data_type": "table",
    "col_refs": ["D", "E", "F"],
    "data_list": [
      ["D3の値", "E3の値", "F3の値"],
      ["D4の値", "E4の値", "F4の値"]
    ],
    "op_mode": "write"
  }
}
```

| フィールド | 説明 |
|---|---|
| `data_type` | `table`: 通常の表 / `table_multi_lines`: セル内の `\n` を複数の物理行へ展開 |
| `col_refs` | 書込先の列リスト |
| `data_list` | 書込データの2次元リスト（ヘッダーを除いた実データのみ） |
| `op_mode` | `write`（既定）: 開始行から上書き / `insert`: 開始行をコピーして挿入してから書込 |

`table_multi_lines` の例:

```json
{
  "A7": {
    "data_type": "table_multi_lines",
    "col_refs": ["A", "P", "AR", "AZ"],
    "data_list": [
      ["1.保存ボタン押下", "①入力チェック\n②データ更新\n③完了メッセージ表示", "権限に注意\n\nトランザクション内", "IT-001\nIT-002\nIT-003"]
    ],
    "op_mode": "insert"
  }
}
```

- 各論理行の物理行数は、`\n` が最も多い要素に合わせる。不足する要素の物理行は空欄。
- `insert` は必要な物理行を挿入してから書込、`write` は挿入せず既存の物理行へ展開して書込。
