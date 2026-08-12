# Python Version — Supported / Tested

本文件記錄「實際確認過」的 Python 版本，不做未經測試的推測。

## 正式支援版本

**Python 3.11**（建議）或 **Python 3.10**。

## 確認依據（實測，非猜測）

| 環境 | Python 版本 | 依據 |
|---|---|---|
| 使用者實際 Windows 開發／執行環境 | **3.11.9** | `venv/pyvenv.cfg` 記錄：`home = C:\Users\mxxxx\AppData\Local\Programs\Python\Python311`，`version = 3.11.9` |
| Phase 8 驗證/測試環境（本次 Finalization 審計、pytest 全套件、`health_check.py`、`final_acceptance_test.py` 皆在此環境執行） | **3.10.12** | `python3 --version` 實測輸出 |

兩個環境下，175 項既有測試（Phase 1-7）與 Phase 8 新增測試皆通過，代表本專案在 **Python 3.10 與 3.11 兩個版本上皆已被驗證可正常運作**。

## 已驗證可用的套件版本組合

以下版本組合已在上述環境中實際安裝並通過完整測試套件（非理論相容性，是實測結果）：

```
fastapi        0.141.1
starlette      1.6.0
jinja2         3.1.6
uvicorn        0.52.1
httpx          0.28.1
python-multipart 0.0.32
feedparser     6.0.14
beautifulsoup4 4.15.0
lxml           6.1.1
requests       2.34.2
python-dotenv  1.2.2
pytest         9.1.1
```

## 不建議使用

- **Python 3.9 以下**：未經測試，且部分套件（`fastapi`/`starlette` 較新版本）可能要求更新的 Python 版本，不保證相容。
- **Python 3.13+**：未在本專案任何環境中實測過，安裝前建議先在測試環境跑過 `run_tests.bat`（或 `pytest -q`）確認。

## 給 IT 部署人員

安裝 Python 時請選擇 **3.11.x**（與目前開發環境一致，風險最低）。若企業標準映像檔僅提供 3.10.x，本專案在該版本下同樣測試通過，可以使用。

安裝完成後的驗證方式：

```
python --version
pip install -r requirements.txt
pip install -r requirements-dev.txt
python -m pytest -q
```

若上述指令全數成功，代表當前 Python 版本與本專案相容。
