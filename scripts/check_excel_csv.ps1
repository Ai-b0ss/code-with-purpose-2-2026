param(
    [Parameter(Mandatory=$true)][string]$CsvPath,
    [int]$ExpectedRecords = 1,
    [string]$ExpectedExperience = ""
)
# Read-only compatibility check in a new, invisible, owned Excel instance.
$ErrorActionPreference = "Stop"
$excel = $null
$workbook = $null
try {
    $path = (Resolve-Path -LiteralPath $CsvPath).Path
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AutomationSecurity = 3
    # Normal Open handles multiline CSV. Legacy OpenText is not supported here.
    $workbook = $excel.Workbooks.Open($path)
    $sheet = $workbook.Worksheets.Item(1)
    if ($sheet.UsedRange.Rows.Count -ne ($ExpectedRecords + 1)) { throw "CSV_ROWS_MISMATCH" }
    if ($sheet.UsedRange.Columns.Count -ne 43) { throw "CSV_COLUMNS_MISMATCH" }
    for ($row = 2; $row -le ($ExpectedRecords + 1); $row++) {
        for ($col = 1; $col -le 43; $col++) {
            if ($sheet.Cells.Item($row, $col).HasFormula) { throw "CSV_FORMULA_EXECUTED" }
        }
    }
    if ($ExpectedExperience) {
        $actual = [string]$sheet.Cells.Item(2, 16).Value2
        if ($actual.Replace("`r`n", "`n") -cne $ExpectedExperience.Replace("`r`n", "`n")) {
            throw "UNICODE_MULTILINE_MISMATCH"
        }
    }
    Write-Output ("EXCEL_CSV_PASS excel=" + $excel.Version + " records=" + $ExpectedRecords + " columns=43 formula_executed=false")
} finally {
    if ($workbook) { $workbook.Close($false) }
    if ($excel) { $excel.Quit(); [Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel) | Out-Null }
}
