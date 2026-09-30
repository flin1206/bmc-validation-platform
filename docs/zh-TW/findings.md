# 實際執行時找到的問題

[English](../en/findings.md) · **繁體中文** · [← README](../../README.zh-TW.md)

以下每一項，都是把本 repo 的測試跑在 **OpenBMC 上游參考版映像**（以 QEMU 11.1.2 開機）上找到的。每一項都照回報給上游的格式撰寫：發生什麼事、怎麼重現、證據，以及還不確定的部分。

> **範圍說明**：這些是 OpenBMC 專案為各機型每晚建置的參考版映像（`jenkins.openbmc.org/job/latest-master`），**不是**任何廠商實際出貨的韌體。特別是 gb200nvl-obmc 的發現，與 NVIDIA 實際出貨的 BMC 韌體無關。

## 總覽

| # | 機型 | Build | 什麼壞了 | 嚴重度 | 抓到它的測試數 |
|---|---|---|---|---|---|
| F1 | romulus | #1752、#1753、#1754 | 無法透過 Redfish 更新 BMC 韌體；Manager 不會回報目前執行的版本 | 高 | 6 |
| F2 | gb200nvl-obmc | #1754 | 任何密碼都設不進去：無法建立帳號，**root 的預設密碼也改不掉** | 高（資安） | 7 |
| F3 | gb200nvl-obmc | #1754 | 上傳格式錯誤的韌體時回 500，而不是用戶端錯誤 | 低 | 2 |

## 每次執行的結果

| 機型 | Build | 通過 | 失敗 | 跳過 | 失敗原因 |
|---|---|---|---|---|---|
| romulus | #1752 | 50 | 6 | 11 | F1 |
| romulus | #1753 | 50 | 6 | 11 | F1 |
| romulus | #1754 | 54 | 6 | 11 | F1 |
| gb200nvl-obmc | #1754 | 40 | 9 | 22 | F2（7）、F3（2） |

romulus #1754 那一輪多了 4 個之後才加入的測試（安全性與錯誤碼拆開的測試，以及密碼變更測試）。每一個失敗都能對應到這三個根因之一。跳過的是模擬機型本來就沒有的功能（沒有主機 CPU、沒有感測器硬體、沒有 FRU EEPROM、gb200nvl 沒有 IPMI），每一項都寫在 [profile](../../src/bmcval/data) 裡，並附上支持這個判斷的觀察。

---

## F1 · romulus：韌體更新流程壞掉

**發生什麼事**

- `GET /redfish/v1/Managers/bmc` 沒有 `FirmwareVersion`，也沒有 `Links.ActiveSoftwareImage`。
- 韌體清單裡找得到這個映像（`FirmwareInventory/07d40fbb`，`Version: 3.1.0-dev-1341-g16d23c4743`），但標記是 `Updateable: false`。
- 所有韌體推送都回 **500 InternalError**，包括同一個 build 附的正式簽章更新包 `.static.mtd.tar`，而且 `HttpPushUri` 和 `MultipartHttpPushUri` 兩個端點都一樣。
- 開機 5 分鐘後狀態仍然一樣，所以不是開機時的時間差。

**重現方式**

```bash
bmcval fetch --machine romulus --build 1754
bmcval boot --profile qemu-romulus --image-dir build/images/romulus/1754
curl -sk -u root:0penBmc https://127.0.0.1:2443/redfish/v1/Managers/bmc | jq .FirmwareVersion   # null
```

**證據（透過 Redfish `LogServices/Journal` 讀取 BMC 的 journal）**

```
mapperx: Found invalid association on path /xyz/openbmc_project/software/07d40fbb
phosphor-image-updater: Error in mapper GetSubTreePath: ... ResourceNotFound
openpower-update-manager: Error version is empty
bmcweb: [update_service.hpp:971] Found 0 MultipartUpdate objects, expected exactly 1
bmcweb: [update_service.hpp:860] error_code = Invalid request descriptor
```

**為什麼重要：** 無法透過管理 API 更新的 BMC，就只能靠人工重刷。

**對照組：** 用**同一個 OpenBMC 版本**建出來的 gb200nvl-obmc 映像，`FirmwareVersion` 和 `ActiveSoftwareImage` 都正常。所以這是 romulus 機型設定特有的問題，不是 bmcweb 共通的 bug。

**推測（尚未證實）：** romulus 屬於 OpenPOWER 機型，會另外執行 `openpower-update-manager` 來管理主機韌體。它在開機時出錯，加上 BMC 軟體物件的 association 無效，看起來像是兩個更新服務之間起了衝突。QEMU 也可能有影響，例如模擬出來的 flash 配置。這需要在實機上或向上游確認。

---

## F2 · gb200nvl-obmc：PAM 引用了映像裡沒有的模組

**發生什麼事**

- 用符合 BMC 公告政策的密碼 `POST /AccountService/Accounts`，都回 **400 PropertyValueFormatError**。測過 5 組 12 到 14 字元的密碼（政策是 `MinPasswordLength` 8、`MaxPasswordLength` 20），結果都一樣。帳號其實有先建立，隨後又被 rollback（journal 裡可以看到 `userdel`）。
- `PATCH /AccountService/Accounts/root {"Password": ...}` 回 **500**。之後預設密碼 `0penBmc` **仍然有效**，新密碼則無效。

**根因（靜態分析與實際執行兩邊都確認過）**

```
# rootfs /etc/pam.d/common-password
17: password [success=ok default=die]  pam_ipmicheck.so spec_grp_name=ipmi use_authtok
20: password [success=1  default=die]  pam_ipmisave.so  spec_grp_name=ipmi ...
```

- `/usr/lib/security/` 裡**沒有** `pam_ipmicheck.so` 和 `pam_ipmisave.so`，映像 manifest 裡也**沒有** `pam-ipmi`。
- 因為這兩行設的是 `default=die`，模組載入失敗就會讓每一次密碼變更都中止。
- journal 也印證了這點：先出現 `PAM unable to dlopen(/usr/lib/security/pam_ipmicheck.so)`，接著是 `pamUpdatePassword Failed`。

**對照組：** romulus 有打包 `pam-ipmi`，兩個模組都在，`test_password_change_takes_effect` 也通過。

**為什麼重要：** 在這個 build 上，眾所周知的預設密碼無法透過 Redfish 更換，也無法建立任何其他使用者。

**可能的修法：** 把 `pam-ipmi` 放進映像；或者在映像不包含 IPMI-over-LAN 時，就不要加入 IPMI 的 PAM 設定。這個映像確實沒有 `phosphor-ipmi-net`，這也解釋了為什麼 RMCP+ 連不上。

---

## F3 · gb200nvl-obmc：上傳格式錯誤的韌體時回 500

推送隨機位元組或截斷的 tar 檔會回 **500 InternalError**。journal 裡是 `pldmd: No devices discovered, cannot process the PLDM fw update package`，以及 `bmcweb: error_code = Input/output error`。

**安全性有守住：** 獨立的 `test_corrupt_image_never_applied` 測試都通過，執行中的韌體沒有被改動，BMC 也持續回應。只有錯誤分類不對：上傳的內容有問題是用戶端的錯，應該回 4xx，或者讓 Task 以失敗結束。

---

## 測試集怎麼讓這些發現保持清楚

- **安全性和協定正確性是分開的測試。**「壞映像沒被套用」和「回了正確的錯誤碼」各自獨立判定，所以 F3 會被看成低嚴重度，而不會被誤會成有變磚的風險。
- **每個缺陷只會讓一小組相關的測試失敗。** 韌體清單是透過 `RelatedItem` 找出來的，不依賴 Manager 的連結，所以 F1 不會連帶讓不相關的測試失敗。
- **測試寫錯就修測試，而不是加豁免。** 第一次實際執行時 `test_readonly_user_cannot_escalate_own_role` 失敗了，原因是 bmcweb 用 400 拒絕、測試卻預期 403。後來把測試改成驗證真正的安全性質（角色沒被改），而權限提升仍然是被拒絕的。
- **環境限制寫成 profile 資料**，每一項都附上觀察依據，不會變成永遠亮紅燈的測試。
