# OpenClaw Stock Deep Analysis UI

Standalone Taiwan-stock deep-analysis tool extracted from a legacy gateway workflow.

## What It Does

- Builds a full markdown stock report from a stock code
- Pulls price / trend context from Yahoo Finance and TWSE
- Pulls valuation and monthly revenue from TWSE OpenAPI
- Pulls three-institution flow from TWSE T86 when available
- Adds broker/news signal scan via Google News RSS
- Adds same-industry 10MA relative-strength comparison
- Adds mapped US leader comparison
- Adds supply-chain / demand bias summary
- Adds convertible-bond note from local `cb_tool.db`
- Can save the final report to Desktop `報告專夾`

## Files

- `tools/stock_deep_analysis_core.py`
  Core analysis pipeline and markdown report generation.
- `tools/stock_deep_analysis_ui.py`
  Tkinter GUI plus CLI entry point.
- `tools/build_stock_deep_analysis_exe.py`
  Optional PyInstaller build script for packaging into a Windows EXE.

## Run

GUI:

```powershell
python tools\stock_deep_analysis_ui.py
```

CLI:

```powershell
python tools\stock_deep_analysis_ui.py --code 2324 --no-gui
python tools\stock_deep_analysis_ui.py --code 2324 --no-gui --save
```

## Output

When `--save` is used, or when GUI auto-save is enabled, reports are written to:

```text
C:\Users\<user>\Desktop\報告專夾\<stock_code>.MD
```

## EXE Build

```powershell
python tools\build_stock_deep_analysis_exe.py
```

Default output:

```text
tools\dist\StockDeepAnalysisUI.exe
```

## Notes

- This tool is optimized for Taiwan listed stocks first.
- Some sections degrade gracefully when official data is unavailable.
- TPEx support is partial in this extracted version; Yahoo fallback is used where needed.
