# GPU 節點診斷

[English](../en/gpu-diagnostics.md) · **繁體中文** · [← README](../../README.zh-TW.md)

BMC stage 問的是「管理韌體對不對」，這個 stage 問的是「被它管理的機器健不健康」。三個工具從不同深度回答：

| 工具 | 深度 | 成本 |
|---|---|---|
| `gpu-health`（C++/NVML） | 當下的健康狀態：幾秒鐘，在正式節點上跑也安全 | 約 1 秒 |
| `bmcval xid` | 驅動程式已經回報過的問題 | 解析 log |
| `dcgmi diag -r 2/3` | 主動壓力測試：記憶體、PCIe 頻寬、功耗 | 數分鐘，節點必須閒置 |

## `gpu-health`

C++17，用 CMake 連結 `CUDA::nvml`。CI 在 `nvidia/cuda:*-devel` 裡編譯並連結 NVML stub，所以編譯時不需要 GPU。

| 檢查項目 | Fail | Warn | 為什麼重要 |
|---|---|---|---|
| `temperature` | > 85 °C | | 長時間高溫會縮短壽命，也會觸發降頻 |
| `pcie_width` | 目前 < 最大 | | 應該是 x16 卻只跑到 x8 的 GPU，代表沒插好或 riser/線材有問題，主機頻寬直接少一半 |
| `pcie_gen` | | 目前 < 最大 | **只發警告**：閒置時為了省電降速（ASPM）是正常的，要在負載下用 DCGM 確認 |
| `ecc_uncorrected` | volatile > 0 | | 資料毀損已經發生了 |
| `ecc_corrected` | | volatile > 1000 | 記憶體開始劣化的早期徵兆 |
| `row_remap` | remap **失敗** | 有 remap 待處理 | Ampere 之後：失敗代表這個 bank 已經沒有備用 row，應該把板子拔下來；待處理則需要重置 GPU |
| `retired_pages_pending` | | 有待處理 | Ampere 之前的對應機制 |
| `clock_events` | HW slowdown、HW thermal、power brake | SW thermal、SW power cap | 硬體降頻代表平台有問題（散熱、電源）；軟體上限則是政策設定 |
| `power_limit` | | 實際上限 < 預設的 95% | 被限制在規格以下的板子會過不了效能驗收 |
| `xid_event`（`--watch-xid N`） | 監看期間出現任何 critical Xid | | 跟 burn-in 一起跑時，可以抓到只在*負載下*才出現的錯誤 |

裝置或驅動不支援的檢查，會**以 skip 回報並附上 NVML 的原因**，絕不會直接省略。Exit code：`0` 健康、`1` 有檢查失敗、`2` NVML 無法使用，因此驅動壞掉不會被誤判成 GPU 壞掉。

**用真實 header 編譯時發現的一個相容性細節：** CUDA 12.2 以後只把*軟體*降頻原因改名成 `nvmlClocksEvent*`，硬體 slowdown 的常數仍然叫 `nvmlClocksThrottleReason*`。程式碼統一使用 `Throttle` 的寫法，因為它在每個版本的 header 裡都存在。

## Xid 分診

驅動程式會記錄像 `NVRM: Xid (PCI:0000:3b:00): 79, pid=..., GPU has fallen off the bus.` 這樣的訊息。光看數字，值班工程師不知道該做什麼，所以 `bmcval xid` 會把每個 Xid 對應到可能的來源和處置方式：

| 處置 | Xid 例子 | 意義 |
|---|---|---|
| `NONE` | 45、63 | 連帶產生的訊息或單純通知 |
| `CHECK_APP` | 13、31、43 | 通常是工作負載的問題（kernel 寫錯、非法位址） |
| `RESET_GPU` | 48、92、94、119、120 | ECC 事件、GSP 錯誤：重置 GPU 就能恢復 |
| `DRAIN_NODE` | 64、74、79、95 | row remap 失敗、NVLink、從 bus 上掉了、無法隔離的 ECC：把節點下線 |

解析器支援舊格式（沒有 `pid=`）和新格式（有 `pid=`、`name=`），會正規化 PCI 位址，並以**每張 GPU** 為單位取最嚴重的處置。Suite 的 `node_action` 屬性就是排程器唯一需要讀的欄位。

## DCGM

`dcgmi diag -j` 的輸出格式在不同 DCGM 大版本之間改過。解析器不綁定特定 schema，而是在 JSON 裡找任何同時有測試名稱和狀態的物件，並用巢狀結構的單元測試固定這個行為。

## 限制

- **WSL2 不支援 DCGM**，也沒有真正的 kernel log，所以 Xid 這條路在 WSL2 上測不到。這就是為什麼這個 stage 只跑在有 `gpu` label 的 agent 上，而且預設關閉。
