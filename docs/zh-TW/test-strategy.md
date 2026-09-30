# 測試策略

[English](../en/test-strategy.md) · **繁體中文** · [← README](../../README.zh-TW.md)

## 測試層級

| 層級 | 在哪裡跑 | 把關的對象 |
|---|---|---|
| **單元測試**（35） | 每次 push，GitHub Actions，不需硬體 | 合併 |
| **Smoke**（8，`-m smoke`） | 開機後立刻跑 | smoke 通過才會跑完整功能測試 |
| **功能測試**（共 71） | 每晚在 QEMU 上跑；實體機透過 profile | 失敗時 build 標為 *unstable* |
| **破壞性測試**（11，`--run-destructive`） | QEMU 預設開啟（有 golden image 複本，所以安全）；實體機需手動啟用 | 同上 |
| **資安 gate** | 每個 build | 有達到政策門檻的新漏洞，build 就 *fail* |

## 覆蓋矩陣

| 區塊 | 檔案 | 測試數 | 驗證內容 |
|---|---|---|---|
| Service root | `test_service_root.py` | 9 | 依 DSP0266 公開 root、版本格式、必要連結可解析、`$metadata`、往下一層沒有斷掉的 `@odata.id`、manager 回報韌體版本 |
| 認證與 session | `test_auth.py` | 12 | 沒帶認證或帶偽造 token 時受保護資源回 401、密碼錯誤、**登出後 token 立刻失效**、錯誤帳密不會建立 session、session timeout 有上限 |
| 異常輸入 | `test_negative_input.py` | 11 | 格式錯誤的 JSON、未知屬性、型別錯誤、超出範圍的值**不會被部分套用**、無效的 ResetType、405、路徑穿越（`../`、`%2e%2e`）、10 MiB 的 body、錯誤的 Content-Type |
| 感測器 | `test_sensors.py` | 6 | 動態探索、符合 profile 的最少數量、ID 不重複、數值讀數與合法單位、門檻值順序、閒置時沒有超過 critical、沒有 Critical 健康狀態 |
| 電源 | `test_power.py` | 5 | 回報電源狀態、透過 inline 值或 ActionInfo 探索 ResetType；*（host_power）* ForceOff→On、重複 On 的冪等性、**狀態切換會留下 event log** |
| 帳號與 RBAC | `test_accounts.py` | 8 | 密碼政策、重複帳號、ReadOnly 可讀、**ReadOnly 不能寫入也不能建立 Administrator**、**不能自行提升角色**、**變更密碼後新密碼生效、舊密碼立刻失效**、ReadOnly 不能改別人的密碼、已刪除帳號無法登入 |
| 韌體 | `test_firmware.py` | 11 | 啟用中的映像在 inventory 裡、Manager 版本與它一致、可指定預期版本；壞掉的映像（隨機、空白、截斷）分兩方面測：**不會被套用、BMC 仍在運作**（安全性），以及**回 4xx 或 Task 失敗**（協定正確性）；用真實簽章更新包做正向更新 |
| IPMI | `test_ipmi.py` | 9 | `mc info`、selftest、chassis status、SDR、SEL、raw Get Device ID、無效指令回傳 completion code 且 **ipmid 沒掛掉**、密碼錯誤、**cipher suite 0 已停用** |

## 負向測試怎麼判定

只檢查 `status == 400` 的負向測試，會漏掉真正要緊的 bug。`test_negative_input.py` 的每個案例都檢查三件事：

1. **狀態碼正確**（400 / 404 / 405 / 413）。
2. **錯誤內容符合規格**：要有 Redfish 的 `error` 物件和 `@Message.ExtendedInfo`；規格有定義的話，還要比對確切的 `MessageId`（`MalformedJSON`、`PropertyUnknown`、`PropertyValueTypeError`）。
3. **測完之後 BMC 還是健康的。** 有一個 autouse fixture 會在每個測試後請求 service root。回了 400 但 bmcweb 接著不斷 crash 重啟，一樣算失敗。

韌體更新測試用的是同一個想法：映像被拒絕，只有在**執行中的韌體版本沒變**、而且 BMC 還能回應時才算通過。如果推送是非同步接受的（202 + Task），那個 Task 必須以 `Exception`、`Killed` 或 `Cancelled` 結束。

## 實際執行的結果

請看 [findings.md](findings.md)：romulus #1752 到 #1754，以及 gb200nvl-obmc #1754，每個失敗都追到三個根因之一。

## 隔離

- 帳號透過 `temp_account` factory fixture 建立，測完一定會刪除。使用者名稱是隨機的，平行執行也不會衝突。
- 沒有加 `--run-destructive` 時，破壞性測試會被跳過。在 QEMU 上執行是安全的，因為有 golden image 複本（見[架構 D2](architecture.md#d2-每次開機都從-golden-image-的複本開始)）。

## 已知缺口（下一步）

- **DMTF Redfish Service Validator**：驗證每個資源是否符合 schema，是最自然的下一個 Jenkins stage。
- **事件訂閱 / SSE**：電源和感測器事件發生後，`EventService` 有沒有送出通知。
- **Fuzzing**：用 grammar-based fuzzing 測 Redfish PATCH body 和 IPMI raw 指令，沿用同樣的「還活著」判定。
- **In-band IPMI（KCS）** 與**主機電源**需要實體硬體的 profile。
- **效能**：多個 session 同時存取時的 Redfish 延遲百分位數，放進趨勢 dashboard 追蹤。
