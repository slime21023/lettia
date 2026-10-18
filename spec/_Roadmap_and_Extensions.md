# lettia: 生態擴充、中介軟體與後續版本規劃規範 (Ecosystem Extensions & Roadmap)

日期：2026-10-18  
狀態：Discussion Summary & Architecture Blueprint  
關聯文件：[`spec/_Design.md`](file:///E:/Projects/python/lettia/spec/_Design.md)

---

## 1. 核心定位與微核心架構原則 (Microkernel Architecture)

lettia 堅持**微核心（Microkernel）**的架構哲學：

1. **核心純粹（Pristine Core）**：
   - `lettia.core` 與 `lettia.contract` 專注於提供極致效能的 ASGI 抽象、Radix Tree 路由、宣告式 Protocol 合約、msgspec 編解碼與 OpenAPI 3.1 規範生成。
   - 核心保持**單一執行期依賴（僅 `msgspec`）**，零外部額外依賴，嚴格維持「掛載期編譯、請求期零反射」的 18% 以下超低附加損耗。
2. **周邊場景外掛化（Decoupled Extensions）**：
   - 凡非所有微服務/API 必需、或各團隊存在不同實作偏好的功能（靜態檔案託管、背景非同步任務、宣告式依賴注入），均以**選配擴充套件（Optional Extensions / Extras）**形式提供，不污染微核心。
3. **原生協定收斂於核心（Native Protocols in Core）**：
   - WebSocket 屬於 ASGI 3.0 的基石通訊協定（與 HTTP 同級），共享相同的路由尋路、連接埠監聽與前置安全守衛，因此規劃於後續版本（0.3+）由核心原生支援。

---

## 2. 選配擴充套件規劃 (Optional Extensions)

建議以 Monorepo Extras 模式（例如 `pip install lettia[static]` 或獨立模組）維護，使用者依需安裝：

### 2.1 靜態檔案目錄託管 (Static Files Serving)
- **套件名稱**：`lettia-static` 或選配 `lettia[static]`
- **職責邊界**：
  - 封裝目錄映射、檔案串流、MIME 類型推導（`mimetypes`）。
  - 實作 HTTP 快取協商機制（`Last-Modified`、`ETag` 與 `304 Not Modified`）。
  - 實作 `Range` 請求支援（音訊/視訊串流斷點續傳）。
  - **安全性保證**：路徑正規化與目錄穿越（Directory Traversal / Path Traversal）防護。
- **架構整合模式**：
  作為 Core Route Handler 掛載，利用萬用字元前綴路由：
  ```python
  from lettia import App
  from lettia.extensions.static import StaticFiles

  app = App()
  app.route("GET", "/static/{file_path*}", StaticFiles(directory="./assets"))
  ```
- **解耦理由**：現代雲原生或容器化架構多由 CDN、Nginx 或 S3 託管靜態前端資源，核心不應承擔檔案系統快取與權限安全檢查之負擔。

---

### 2.2 輕量背景非同步任務 (Background Tasks)
- **套件名稱**：`lettia-tasks` 或選配 `lettia[tasks]`
- **職責邊界**：
  - 提供輕量的進程內非同步背景作業排程器（如郵件發送、審計日誌非同步寫入）。
  - 確保在 HTTP Response 串流發送完成後，非同步喚起任務協程。
- **架構整合模式**：
  基於 `Context.state` 與中介層實現：
  ```python
  from lettia import Context, Ok, op
  from lettia.extensions.tasks import BackgroundTasks

  class OrderApi:
      @op.post("/orders")
      async def create_order(self, c: Context, req: OrderReq) -> Ok[OrderOut]:
          tasks = BackgroundTasks.of(c)
          tasks.add_task(send_confirmation_email, req.email)
          return Ok(OrderOut(id=123))
  ```
- **解耦理由**：
  大型系統通常需要分散式可靠性保證（Redis Streams、ARQ、Celery、Kafka），內建進程內任務無法滿足高可靠場景；外掛化能提供簡潔的進階選項，而不強迫所有服務承擔生命週期管理開銷。

---

### 2.3 宣告式依賴注入 (Dependency Injection, DI)
- **套件名稱**：`lettia-di` 或選配 `lettia[di]`
- **職責邊界**：
  - 提供針對依賴關係容器（IoC Container）的宣告式綁定。
  - 支援類別實例、設定物件或資料庫連線池的階層式解析與生命週期綁定（Singleton / Request-scoped）。
- **架構整合模式**：
  在合約掛載期解析依賴圖，並在請求期自動提供注入物件，不影響現有 `(self, c, req)` 標準合約。
- **解耦理由**：
  Lettia 核心倡導「顯式優於隱式」（推薦 Service 建構子注入與型別安全的 `Context.state[Key[T]]`）。將宣告式 DI 隔離於外掛，既能保護核心維持零反射與超低延遲，又能滿足偏好容器化 DI 模式的開發者。

---

## 3. 原生協定演進規劃：WebSocket 支援 (0.3+)

### 3.1 定位與核心優勢
WebSocket 屬於 ASGI 規範核心協定，後續版本將直接在 `lettia.core` 與 `lettia.contract` 中原生實作。

### 3.2 關鍵架構設計
1. **L0/L1 Core 層：原生 ASGI WebSocket 支援**：
   - 擴充 `src/lettia/core/asgi.py` 處理 `scope["type"] == "websocket"`。
   - 實作極輕量、具備 `__slots__` 的 `WebSocket` 物件，提供直觀非同步 API：
     - `await ws.accept(subprotocols=None)`
     - `await ws.receive_text()` / `await ws.receive_bytes()`
     - `await ws.send_text(data)` / `await ws.send_bytes(data)`
     - `await ws.close(code=1000)`
2. **L2 Contract 層：合約化宣告與握手前前置守衛（Pre-Handshake Guard）**：
   - 提供 `@op.websocket("/ws/chat", guard=auth_guard)` 宣告。
   - **重大安全優勢**：Guard 在 HTTP 握手升級（`Upgrade: websocket`）**之前**即完成驗證。若未授權，直接返回 HTTP 401/403 阻斷連線，完全不進入 WebSocket 連線建立流程，節省伺服器連線與握手資源。
3. **msgspec 編解碼深度整合**：
   - 支援 `await ws.receive_json(Model)` 與 `await ws.send_json(payload)`，直接走 `MsgspecCodec` 的極速二進位編解碼管線。

---

## 4. 中介軟體（Middleware）系統架構現況評定

### 4.1 設計特徵
- **純函式閉包（Higher-Order Function Pipeline）**：
  `type Middleware = Callable[[Handler], Handler]`
  擺脫傳統繼承式中介軟體的串流阻塞與例外截斷問題，具備微秒級的轉發效能。
- **錯誤隔離防護網（Error Conversion by Design）**：
  在每個中介層邊界自動包覆 `_convert_errors`，確保 Handler 或內部中介軟體拋出任何例外時，外層中介軟體（CORS、RequestId、AccessLog）**永遠保證能取得合法的 `Response`**，徹底終結 CORS 標頭因內部未捕捉例外而丟失的問題。
- **型別化狀態系統（`Key[T]`）**：
  透過泛型 Key 提供編譯期型別安全的資料傳遞，取代傳統易拼錯的弱型別 `request.state`。

### 4.2 擴充性與生態相容
- **內層極速**：原生使用 `Context -> Response`，無多餘打包開銷。
- **外層相容**：`App` 本身即為標準 ASGI 3.0 應用，可直接在最外層串接既有 ASGI 生態中介軟體（SentryAsgiMiddleware, GZipMiddleware, PrometheusAsgi 等）。

### 4.3 後續增強演進方向 (Backlog)
1. **單一端點合約級中介層**：支援在 `@op` 裝飾器上宣告選配中介層（如 `@op.post("/login", middleware=[rate_limit(5)])`）。
2. **ASGI-to-Lettia 適配輔助函式**：提供統一的適配器，使標準 ASGI 函式亦可直接置入 `app.use(...)`。
3. **串流回應體轉換抽象**：為即時串流壓縮或日誌記錄提供更高階的串流迭代器包裝 helper。

---

## 5. 結論

本規範確立了 Lettia 邁向 0.3+ 的發展藍圖：以**微核心 + 選配擴充套件**的方式滿足日常後端多樣化需求，並將關鍵的**原生 WebSocket** 與**進階中介軟體特性**規劃於後續版本有序推進。
