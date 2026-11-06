# lettia: 生態擴充、中介軟體與後續版本規劃規範 (Ecosystem Extensions & Roadmap)

日期：2026-11-06  
狀態：v0.5.0 已釋出；本文件記錄架構原則、已完成項目與尚未排程的 Backlog  
關聯文件：[`_Design.md`](_Design.md)

---

## 1. 核心定位與微核心架構原則 (Microkernel Architecture)

lettia 堅持**微核心（Microkernel）**的架構哲學：

1. **核心純粹（Pristine Core）**：
   - `lettia.core` 與 `lettia.contract` 專注於提供極致效能的 ASGI 抽象、Radix Tree 路由、宣告式 Protocol 合約、msgspec 編解碼與 OpenAPI 3.1 規範生成。
   - 核心保持**單一執行期依賴（僅 `msgspec`）**，零外部額外依賴，嚴格維持「掛載期編譯、請求期零反射」的低附加損耗（設計文件的目標：S3 合約路徑相對手寫 Core 處理器的附加損耗 ≤ 25%）。
2. **周邊場景外掛化（Decoupled Extensions）**：
   - 凡非所有微服務/API 必需、或各團隊存在不同實作偏好的功能（靜態檔案託管、背景非同步任務、宣告式依賴注入），均以**選配擴充套件（Optional Extensions / Extras）**形式提供，不污染微核心。
3. **原生協定收斂於核心（Native Protocols in Core）**：
   - WebSocket 屬於 ASGI 3.0 的基石通訊協定（與 HTTP 同級），共享相同的路由尋路、連接埠監聽與前置安全守衛，已於 v0.3.1 由核心原生支援。

---

## 2. 選配擴充套件規劃 (Optional Extensions)

建議以 Monorepo Extras 模式（例如 `pip install lettia[static]` 或獨立模組）維護，使用者依需安裝：

### 2.1 靜態檔案目錄託管 (Static Files Serving)
- **模組名稱**：`lettia.extensions.staticfiles`（無額外依賴）
- **職責邊界**：
  - 封裝目錄映射、檔案串流、MIME 類型推導（`mimetypes`）。
  - 實作 HTTP 快取協商機制（`Last-Modified`、`ETag` 與 `304 Not Modified`）。
  - 實作 `Range` 請求支援（音訊/視訊串流斷點續傳）。
  - **安全性保證**：路徑正規化與目錄穿越（Directory Traversal / Path Traversal）防護。
- **架構整合模式**：
  作為 Core Route Handler 掛載，利用萬用字元前綴路由：
  ```python
  from lettia import App
  from lettia.extensions.staticfiles import StaticFiles

  app = App()
  StaticFiles.mount(app, "/static", "./assets")
  ```
- **解耦理由**：現代雲原生或容器化架構多由 CDN、Nginx 或 S3 託管靜態前端資源，核心不應承擔檔案系統快取與權限安全檢查之負擔。

---

### 2.2 輕量背景非同步任務 (Background Tasks)
- **模組名稱**：`lettia.extensions.tasks`（無額外依賴）
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
- **模組名稱**：`lettia.extensions.di`，選配 `lettia[di]`（安裝 `rodi`）
- **職責邊界**：
  - 提供針對依賴關係容器（IoC Container）的宣告式綁定。
  - 支援類別實例、設定物件或資料庫連線池的階層式解析與生命週期綁定（Singleton / Request-scoped）。
- **架構整合模式**：
  在合約掛載期解析依賴圖，並在請求期自動提供注入物件，不影響現有 `(self, c, req)` 標準合約。
- **解耦理由**：
  Lettia 核心倡導「顯式優於隱式」（推薦 Service 建構子注入與型別安全的 `Context.state[Key[T]]`）。將宣告式 DI 隔離於外掛，既能保護核心維持零反射與超低延遲，又能滿足偏好容器化 DI 模式的開發者。

---

## 3. 原生協定：WebSocket 支援 (v0.3.1 已實現)

### 3.1 定位與核心優勢
WebSocket 屬於 ASGI 規範核心協定，已直接在 `lettia.core` 與 `lettia.contract` 中原生實作（v0.3.1）。

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
1. **ASGI-to-Lettia 適配輔助函式**：提供統一的適配器，使標準 ASGI 函式亦可直接置入 `app.use(...)`。
2. **串流回應體轉換抽象**：為即時串流壓縮或日誌記錄提供更高階的串流迭代器包裝 helper。

## 5. 已完成的擴充套件（v0.4.1 ~ v0.4.3，隨 v0.5.0 釋出）

以下 4 項擴充套件原規劃為三個 0.4.x 實驗版本，全部完成並驗收後合併為 **v0.5.0** 釋出，實驗性標示已解除。第 4.3 節的 Backlog 未排入任何版本，也不是 0.5.0 的條件。

### 5.1 `lettia.extensions.docs`（已完成）：開發者視覺化與互動除錯體驗
- **核心目標**：將現有的 OpenAPI 3.1 導出能力轉化為瀏覽器隨開即用的互動式 API 文件 UI，支援 Swagger UI、Scalar 與 Redoc。
- **主要職責**：
  - 提供 `mount_docs(app, prefix="/docs", provider="scalar")` 一鍵掛載（`provider` 為 `scalar`、`swagger` 或 `redoc`）；既有 `App(docs_url=..., docs_ui=...)` 保留，並與 `mount_docs` 共用同一實作。
  - 沿用 `App` 已提供的 `openapi_url` 端點，不另行註冊第二個文件端點。「熱重載」指開發伺服器（如 `uvicorn --reload`）重啟 worker 後重新建立 App 並推導文件；路由於 build 後不可變，不在請求期重建 OpenAPI。
  - 零外部依賴：預設透過 CDN 載入前端腳本，亦可搭配 `staticfiles` 支援完全離線部署。
  - 支援客製化標題、Favicon、深色模式主題配置。

### 5.2 `lettia.extensions.sse`（已完成）：現代即時串流與生成式 AI 整合
- **核心目標**：提供專為 Server-Sent Events 與 LLM Token 串流打造的高效能事件產生器。
- **主要職責**：
  - 實作符合 RFC 規範的 `ServerSentEvent(data, event, id, retry)`。
  - 封裝 `EventStream[T]` 回應類別（`StreamReply[ServerSentEvent[T]]`），可用於合約回傳註記；ASGI 傳送階段由 `Response(watch_disconnect=True)` 監看用戶端斷線並釋放串流來源。
  - 自動心跳保活機制（定期發送 `: keep-alive\n\n` 註解），防止反向代理（Nginx / Cloudflare）逾時斷線。
  - 自動附加 `Cache-Control: no-cache`、`X-Accel-Buffering: no` 最佳實踐標頭。

### 5.3 `lettia.extensions.sessions` 與 `lettia.extensions.ratelimit`（已完成）：生產環境狀態管理與防護
- **子任務 A：`lettia.extensions.sessions`（安全 Cookie 狀態管理）**
  - 提供基於 HMAC-SHA256 **簽署**（不加密）的輕量無狀態 Cookie Session：內容用戶端可讀、不可竄改，不得存放機密；不提供伺服端撤銷與重放防護。
  - 透過 `SessionMiddleware` 與 `Session.of(c)` 取得 dict-like 介面（不在 Core `Context` 新增 `c.session`）。
  - 支援單次快閃訊息（Flash Messages）、`SameSite=Lax`、`HttpOnly`、`Secure` 安全預設值。
- **子任務 B：`lettia.extensions.ratelimit`（宣告式 API 流量限流）**
  - 僅以 middleware 形式提供：`app.use(rate_limit("5/minute"))` 或 `app.group("/login", rate_limit("5/minute"))`；不提供 guard 形式（guard 僅取得 `RequestHead`，無法替成功回應附加標頭）。
  - 僅提供滑動窗口（Sliding Window）記憶體演算法，計數限於單一進程；跨進程或跨主機共享限流需要 Redis 等外部儲存，不在規劃範圍內。
  - 自動注入 `X-RateLimit-Limit`、`X-RateLimit-Remaining`、`Retry-After` 標頭，超額請求自動生成 RFC 9457 Problem 429 回應。

---

> 範圍界線：凡需要資料庫或外部資料儲存（SQL、Redis 等）才能實現的功能，不列入本路線圖；上述各項均不依賴任何資料儲存後端。

## 6. 結論與歷史成就

- **v0.3.1 (已釋出)**：Core WebSocket 原生協定、`lettia.extensions.di`、`lettia.extensions.tasks`、`lettia.extensions.staticfiles`。
- **v0.4.0 (已釋出)**：背景任務改為回應成功傳送後才啟動（`Response(after_send=...)`），並補齊 WebSocket Origin 檢查、Problem 回應與 group middleware 限制等文件。
- **v0.5.0 (已釋出)**：`lettia.extensions.docs`（Swagger UI / Scalar / Redoc 互動文件，含主題、favicon 與離線資產）、`lettia.extensions.sse`（Server-Sent Events 與 `EventStream[T]`、`Response(watch_disconnect=True)`）、`lettia.extensions.sessions`（簽署 Cookie session）與 `lettia.extensions.ratelimit`（單進程記憶體限流）。
- **v0.5.1 (已釋出)**：單一路由與單一合約操作的 middleware：Core 的 `route/get/post/…(…, middleware=[...])`，以及 `mount(impl, middleware={Api.login: [...]})`（以合約方法為鍵，於 Guard 之前執行，不進入合約本身）。
- **v0.5.2 (已釋出)**：合約回應類別 `Accepted`、`BadRequest`、`Conflict` 與五種轉址；Core 的 `Context.redirect()`、`Response.set_cookie()`、`Response.delete_cookie()`；壓縮、主機檢查與 HTTPS 轉址的文件配方；`Container.wire` 轉傳 `middleware=`。
- **v0.5.3 (已釋出)**：`SessionMiddleware.read(head)`（供合約 Guard 使用的唯讀 session 讀取函式）與 Cookie 型安全方案 `ApiKeyCookie`（OpenAPI `apiKey`、`in: cookie`）。
- **尚未排程**：僅第 4.3 節的兩項 Backlog（ASGI-to-Lettia 適配輔助函式、串流回應體轉換 helper）。

---

## 7. 補強排程（v0.5.2 ~ v0.5.4）

依對開發體驗的影響排序。三個版本都只新增 API，不改既有行為；每版各自完成驗證後才釋出。

### 7.1 v0.5.2（已釋出）：常用回應與輔助函式
- **回應類別（`lettia.contract`）**：`Accepted[T]`（202）、`BadRequest[T]`（400）、`Conflict[T]`（409），以及五種轉址 `MovedPermanently`（301）、`Found`（302）、`SeeOther`（303）、`TemporaryRedirect`（307）、`PermanentRedirect`（308）。轉址類別沒有 body，必填 `Location` 標頭欄位。只收這一組；其他狀態碼仍以 `Reply` 子類別自訂。
- **Core 輔助函式**：`Context.redirect(location, status=302)`；`Response.set_cookie(...)` 與 `Response.delete_cookie(...)`，預設 `HttpOnly`、`SameSite=Lax`，名稱、值與屬性驗證以防標頭注入。
- **文件配方**：外層 ASGI 壓縮（SSE 須排除）、主機檢查與 HTTPS 轉址的做法。
- **`Container.wire`**：轉傳 `middleware=`，讓 DI 掛載的服務也能使用單一操作 middleware。

### 7.2 v0.5.3（已釋出）：Session 用於合約 Guard
- `SessionMiddleware.read(head)`：從 `RequestHead` 解 Cookie 的唯讀讀取函式，不寫入、不提交，供 Guard 驗證身分。
- 新增 Cookie 型安全方案 `ApiKeyCookie`，OpenAPI 表達為 `apiKey`、`in: cookie`。

### 7.3 v0.5.4：Request 網址資訊
- `scheme`、`host`、`url`、`base_url`，搭配明確的信任代理設定；預設不採用轉發標頭。設定方式於該版開始前另行設計。

### 7.4 尚未排程
- 依例外類型註冊 handler、結尾斜線的可選轉址、掛載子 ASGI 應用（`Mount`）、`TestClient` 接受外層包過 middleware 的 app。
