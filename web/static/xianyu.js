const state = {
    token: localStorage.getItem("xianyuAdminToken") || "",
    loginPollTimer: null,
    overviewTimer: null,
    eventIds: new Set(),
    autoRefreshMs: 5000,
    latestBrowserFrame: null,
    latestMessagesPayload: null,
    latestListingsPayload: null,
    latestCompetitorsPayload: null,
    latestListingPlanPayload: null,
    latestMarketingPlanPayload: null,
    latestProfitAnalysisPayload: null,
    latestReplyDraftPayload: null,
};

const $ = (id) => document.getElementById(id);

function authHeaders() {
    return {
        Authorization: `Bearer ${state.token}`,
        "Content-Type": "application/json",
    };
}

async function api(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: {
            ...authHeaders(),
            ...(options.headers || {}),
        },
    });

    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json")
        ? await response.json()
        : { detail: await response.text() };

    if (!response.ok) {
        throw new Error(data.detail || `HTTP ${response.status}`);
    }
    return data;
}

function requireToken() {
    if (!state.token) {
        throw new Error("请先保存管理 Token");
    }
}

function showToast(message, isError = false) {
    const toast = $("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast ${isError ? "error" : "success"}`;
    toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => {
        toast.hidden = true;
    }, 3600);
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function formatTimestamp(value) {
    if (!value) return "--";
    return String(value).replace("T", " ").replace(/\.\d+$/, "");
}

function sourceLabel(source) {
    const normalized = String(source || "").toLowerCase();
    if (normalized.includes("goofish") || normalized.includes("real")) {
        return { text: "真实", cls: "source-real" };
    }
    if (normalized.includes("system")) {
        return { text: "系统", cls: "source-system" };
    }
    if (normalized.includes("mock") || normalized.includes("fake") || normalized.includes("dummy")) {
        return { text: "异常数据源", cls: "source-error" };
    }
    if (normalized === "unknown" || normalized === "not_connected" || !normalized) {
        return { text: "未接入", cls: "source-not-connected" };
    }
    return { text: source, cls: "source-idle" };
}

function statusLabel(status) {
    const normalized = String(status || "").toLowerCase();
    const map = {
        idle: { text: "空闲", cls: "status-idle" },
        running: { text: "进行中", cls: "status-running" },
        start: { text: "开始", cls: "status-running" },
        success: { text: "成功", cls: "status-success" },
        completed: { text: "成功", cls: "status-success" },
        pending: { text: "待处理", cls: "status-pending" },
        queued: { text: "待处理", cls: "status-pending" },
        failure: { text: "失败", cls: "status-failure" },
        failed: { text: "失败", cls: "status-failure" },
        error: { text: "错误", cls: "status-failure" },
        logged_in: { text: "已登录", cls: "status-success" },
        not_logged_in: { text: "未登录", cls: "status-pending" },
        not_ready: { text: "未就绪", cls: "status-pending" },
    };
    return map[normalized] || { text: status || "未知", cls: "status-idle" };
}

function setStatusPill(text, cls) {
    const pill = $("statusPill");
    pill.textContent = text;
    pill.className = `status-pill ${cls || "source-idle"}`.trim();
}

function setInlineStatus(nodeId, status, detail) {
    const node = $(nodeId);
    if (!node) return;
    const info = statusLabel(status);
    node.className = `inline-status ${info.cls}`;
    node.innerHTML = `<span>${info.text}</span>${detail ? `<small>${escapeHtml(detail)}</small>` : ""}`;
}

function setText(id, value) {
    const node = $(id);
    if (node) node.textContent = value;
}

function metricText(value) {
    return value == null || value === "" ? "暂无" : String(value);
}

function renderStatus(data) {
    setText("platformMode", data.platform_mode || "goofish");
    const loginState = data.login_state || {};
    setText("storageState", loginState.logged_in ? "已登录" : "未登录");

    const loginTask = data.login_task || {};
    const loginInfo = statusLabel(loginState.logged_in ? "logged_in" : (loginTask.status || "idle"));
    setText("loginTaskState", loginInfo.text);

    setStatusPill(
        loginState.logged_in ? "已登录" : (state.token ? "已连接" : "未连接"),
        loginState.logged_in || state.token ? "status-success" : "source-not-connected"
    );
}

function updateLoginTask(task) {
    const label = statusLabel(task?.login_state?.logged_in ? "logged_in" : (task?.status || "idle"));
    setText("loginTaskState", label.text);
    if (task?.login_state) {
        setText("storageState", task.login_state.logged_in ? "已登录" : "未登录");
        setStatusPill(
            task.login_state.logged_in ? "已登录" : label.text,
            task.login_state.logged_in ? "status-success" : label.cls
        );
    }
}

function clearList(nodeId, emptyText) {
    const root = $(nodeId);
    root.innerHTML = "";
    root.className = "card-list empty-state";
    root.textContent = emptyText;
}

function renderMessages(payload) {
    state.latestMessagesPayload = payload;
    const { status = "success", detail = "", results = [] } = payload || {};
    const list = $("messagesList");
    list.innerHTML = "";

    setInlineStatus("messagesStatus", status, detail);

    if (status === "not_logged_in") {
        clearList("messagesList", "请先扫码登录闲鱼，再读取真实买家消息。");
        return;
    }

    if (status === "not_ready") {
        clearList("messagesList", "消息页真实 selector 尚未确认，当前不会伪造消息。");
        return;
    }

    if (status === "error") {
        clearList("messagesList", `读取失败: ${detail}`);
        return;
    }

    if (!results.length) {
        clearList("messagesList", "当前没有读取到真实买家消息。");
        return;
    }

    list.className = "card-list";
    results.forEach((conv) => {
        const item = document.createElement("article");
        item.className = "data-card";

        // ✅ 使用新字段: buyer_nick, is_system, last_message, time, order_status
        const title = conv.buyer_nick || "未知买家";
        const messageText = conv.last_message || "暂无消息";
        const timeStr = conv.time || "";
        const orderStatus = conv.order_status || "";
        const isSystem = conv.is_system || false;

        // ✅ 系统消息用不同样式
        const cardClass = isSystem ? "data-card system-message" : "data-card";
        item.className = cardClass;

        // ✅ 订单状态标签
        let statusBadge = "";
        if (orderStatus) {
            const isWait = orderStatus.includes("等待");
            const isSuccess = orderStatus.includes("成功");
            const badgeColor = isWait ? "orange" : (isSuccess ? "green" : "gray");
            statusBadge = `<span class="status-badge badge-${badgeColor}">${escapeHtml(orderStatus)}</span>`;
        }

        item.innerHTML = `
            <div class="data-card-top">
                <div style="display: flex; align-items: center; gap: 8px;">
                    ${conv.avatar_url ? `<img src="${escapeHtml(conv.avatar_url)}" style="width: 32px; height: 32px; border-radius: 50%; object-fit: cover;">` : ''}
                    <div>
                        <div class="data-card-title">${escapeHtml(title)} ${isSystem ? '<span style="font-size: 12px; color: #999;">(系统)</span>' : ''}</div>
                        ${statusBadge}
                    </div>
                </div>
                ${conv.product_image ? `<img src="${escapeHtml(conv.product_image)}" style="width: 40px; height: 40px; border-radius: 4px; object-fit: cover;">` : ''}
            </div>
            <div class="data-card-body">${escapeHtml(messageText)}</div>
            <div class="data-card-foot">
                <span style="font-size: 12px; color: #999;">${escapeHtml(timeStr)}</span>
                ${!isSystem ? '<button class="button button-ghost action-button" type="button">查看对话</button>' : ''}
            </div>
        `;

        if (!isSystem) {
            const btn = item.querySelector("button");
            if (btn) {
                btn.addEventListener("click", () => {
                    // TODO: 点击后加载该会话的具体消息
                    showToast(`会话 ${title} 的消息读取功能待实现`);
                });
            }
        }

        list.appendChild(item);
    });
}

function renderListings(payload) {
    state.latestListingsPayload = payload;
    const { status = "success", detail = "", listings = [] } = payload || {};
    const list = $("listingsList");
    if (!list) return;
    list.innerHTML = "";

    setInlineStatus("listingsStatus", status, detail);

    if (status === "not_logged_in") {
        clearList("listingsList", "请先扫码登录闲鱼，再读取真实在售商品。");
        return;
    }

    if (status === "not_ready") {
        clearList("listingsList", detail || "在售商品页真实 selector 尚未确认，当前不会伪造商品。");
        return;
    }

    if (!listings.length) {
        clearList("listingsList", "当前没有读取到真实在售商品。");
        return;
    }

    list.className = "card-list";
    listings.forEach((item) => {
        const row = document.createElement("article");
        row.className = "data-card";
        const source = sourceLabel(item.data_source || "goofish");
        const title = item.title || item.item_id || "未命名商品";
        row.innerHTML = `
            <div class="data-card-top">
                <div>
                    <div class="data-card-title">${escapeHtml(title)}</div>
                    <div class="data-card-meta mono">${escapeHtml(item.item_id || "")}</div>
                </div>
                <span class="price-tag">${escapeHtml(item.price == null ? "--" : String(item.price))}</span>
            </div>
            <div class="data-card-body">
                浏览 ${escapeHtml(item.view_count ?? "--")} / 想要 ${escapeHtml(item.want_count ?? "--")}
                ${item.published_at ? ` / 上架 ${escapeHtml(item.published_at)}` : ""}
            </div>
            <div class="data-card-foot">
                ${item.item_url ? `<a href="${escapeHtml(item.item_url)}" target="_blank" rel="noreferrer">打开商品</a>` : "<span>无商品链接</span>"}
                <span class="table-pill ${source.cls}">${source.text}</span>
            </div>
        `;
        list.appendChild(row);
    });
}

function intentText(intent) {
    const map = {
        price_negotiation: "议价 / 询价",
        shipping: "发货 / 物流",
        after_sales: "售后",
        availability: "库存 / 在售",
        general: "一般咨询",
    };
    return map[intent] || intent || "";
}

async function selectConversation(conversationId) {
    try {
        if (!conversationId) throw new Error("会话 ID 为空");
        const data = await api(`/api/xianyu/conversations/${encodeURIComponent(conversationId)}/messages`);
        const buyerMessages = (data.messages || []).filter((item) => item.direction === "buyer");
        const latest = buyerMessages[buyerMessages.length - 1];
        $("conversationIdInput").value = conversationId;
        $("buyerMessageInput").value = latest?.content || "";
        $("replyDraftInput").value = latest?.draft_reply || "";
    } catch (error) {
        showToast(error.message, true);
    }
}

function renderPricingAdvice(results) {
    const panel = $("pricingAdvice");
    if (!results.length) {
        panel.hidden = true;
        panel.textContent = "";
        return;
    }

    const prices = results
        .map((item) => Number(String(item.price ?? "").replace(/[^\d.]/g, "")))
        .filter((value) => Number.isFinite(value) && value > 0);

    if (!prices.length) {
        panel.hidden = false;
        panel.textContent = "当前结果没有可计算的真实价格字段，需要人工回到闲鱼页面确认。";
        return;
    }

    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const avg = prices.reduce((sum, value) => sum + value, 0) / prices.length;
    panel.hidden = false;
    panel.innerHTML = `
        <div class="advice-title">真实抓取价格区间</div>
        <div class="advice-metrics">
            <span>最低 ${min.toFixed(2)}</span>
            <span>均价 ${avg.toFixed(2)}</span>
            <span>最高 ${max.toFixed(2)}</span>
        </div>
        <p>这只是对本次真实抓取结果的数值汇总，不替代人工核对成色、配件和发布时间。</p>
    `;
}

function renderCompetitors(payload) {
    state.latestCompetitorsPayload = payload;
    const { status = "success", detail = "", results = [] } = payload || {};
    const list = $("competitorList");
    list.innerHTML = "";

    setInlineStatus("competitorsStatus", status, detail);

    if (status === "not_logged_in") {
        clearList("competitorList", "请先扫码登录闲鱼，再抓取真实竞品数据。");
        renderPricingAdvice([]);
        return;
    }

    if (status === "not_ready") {
        clearList("competitorList", "竞品搜索页真实 selector 尚未确认，当前不会伪造竞品数据。");
        renderPricingAdvice([]);
        return;
    }

    if (!results.length) {
        clearList("competitorList", "当前没有抓到真实竞品结果。");
        renderPricingAdvice([]);
        return;
    }

    list.className = "card-list";
    results.forEach((item) => {
        const row = document.createElement("article");
        row.className = "data-card";
        const source = sourceLabel(item.source_label || item.source || item.platform || "goofish");
        const activity = item.want_count || item.sold_count || "未读到销量/想要字段";
        const summary = item.selling_points || item.summary || "未读到额外卖点摘要";
        row.innerHTML = `
            <div class="data-card-top">
                <div>
                    <div class="data-card-title">${escapeHtml(item.title || "未命名商品")}</div>
                    <div class="data-card-meta mono">${escapeHtml(item.item_url || "")}</div>
                </div>
                <span class="price-tag">${escapeHtml(String(item.price ?? "--"))}</span>
            </div>
            <div class="data-card-body">
                ${escapeHtml(summary)}
                <br>
                <span>${escapeHtml(activity)}</span>
            </div>
            <div class="data-card-foot">
                ${item.item_url ? `<a href="${escapeHtml(item.item_url)}" target="_blank" rel="noreferrer">打开竞品</a>` : `<span>${escapeHtml(item.keyword || "")}</span>`}
                <span class="table-pill ${source.cls}">${source.text}</span>
            </div>
        `;
        list.appendChild(row);
    });
    renderPricingAdvice(results);
}

function renderPlanList(items) {
    if (!Array.isArray(items) || !items.length) return "";
    return `<ul class="plan-list">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function toPlanArray(value) {
    if (Array.isArray(value)) return value;
    if (value == null || value === "") return [];
    return [value];
}

function renderListingPlan(payload) {
    state.latestListingPlanPayload = payload;
    const { status = "success", detail = "", plan = {}, competitor_stats: stats = {}, llm_call: llmCall = {} } = payload || {};
    const root = $("listingPlanPreview");
    if (!root) return;
    root.innerHTML = "";

    setInlineStatus("listingPlanStatus", status, detail);

    if (status === "idle") {
        clearList("listingPlanPreview", "先抓取真实竞品，再生成只读上架方案。");
        return;
    }

    if (status !== "success") {
        clearList("listingPlanPreview", detail || "真实 LLM 上架方案生成失败。");
        return;
    }

    root.className = "card-list";
    const pricing = plan.pricing || {};
    const title = plan.suggested_title || "LLM 未返回结构化标题";
    const price = pricing.suggested_price ?? "--";
    const reason = pricing.reason || "LLM 未返回结构化定价理由";
    const description = plan.description || plan.raw_text || "LLM 未返回结构化描述";
    const category = plan.category || "--";
    const competitorSummary = `真实竞品 ${stats.competitor_count ?? 0} 条 / 有价格 ${stats.priced_count ?? 0} 条 / 区间 ¥${stats.price_min ?? "--"}~¥${stats.price_max ?? "--"} / 中位 ¥${stats.price_median ?? "--"}`;

    const card = document.createElement("article");
    card.className = "data-card listing-plan-card";
    card.innerHTML = `
        <div class="data-card-top">
            <div>
                <div class="data-card-title">${escapeHtml(title)}</div>
                <div class="data-card-meta">${escapeHtml(competitorSummary)}</div>
            </div>
            <span class="price-tag">${escapeHtml(String(price))}</span>
        </div>
        <div class="plan-section">
            <strong>定价理由</strong>
            <p>${escapeHtml(reason)}</p>
        </div>
        <div class="plan-section">
            <strong>商品描述</strong>
            <p>${escapeHtml(description)}</p>
        </div>
        <div class="plan-section">
            <strong>卖点 / 广告词</strong>
            ${renderPlanList([...toPlanArray(plan.selling_points), ...toPlanArray(plan.ad_copy)]) || "<p>LLM 未返回结构化卖点。</p>"}
        </div>
        <div class="data-card-foot">
            <span>建议分类：${escapeHtml(category)}</span>
            <span class="table-pill source-real">${escapeHtml(llmCall.model || "真实 LLM")}</span>
        </div>
    `;
    root.appendChild(card);
}

function renderMarketingPlan(payload) {
    state.latestMarketingPlanPayload = payload;
    const { status = "success", detail = "", marketing_plan: plan = {}, competitor_stats: stats = {}, llm_call: llmCall = {}, product_info: productInfo = {} } = payload || {};
    const root = $("marketingPlanPreview");
    if (!root) return;
    root.innerHTML = "";

    setInlineStatus("marketingPlanStatus", status, detail);

    if (status === "idle") {
        clearList("marketingPlanPreview", "基于真实商品方案和真实竞品，生成只读引流建议与文案。");
        return;
    }

    if (status !== "success") {
        clearList("marketingPlanPreview", detail || "真实 LLM 引流方案生成失败。");
        return;
    }

    root.className = "card-list";
    const competitorSummary = `真实竞品 ${stats.competitor_count ?? 0} 条 / 有价格 ${stats.priced_count ?? 0} 条 / 区间 ¥${stats.price_min ?? "--"}~¥${stats.price_max ?? "--"} / 中位 ¥${stats.price_median ?? "--"}`;
    const productSummary = `${productInfo.title || "--"} / 定价 ${productInfo.price || "--"} / ${productInfo.category || "--"}`;
    const channels = Array.isArray(plan.channels) ? plan.channels : [];
    const differentiation = toPlanArray(plan.differentiation_points);
    const copywriting = Array.isArray(plan.copywriting) ? plan.copywriting : [];

    const card = document.createElement("article");
    card.className = "data-card listing-plan-card";
    card.innerHTML = `
        <div class="data-card-top">
            <div>
                <div class="data-card-title">真实引流方案</div>
                <div class="data-card-meta">${escapeHtml(competitorSummary)}</div>
            </div>
            <span class="table-pill source-real">${escapeHtml(llmCall.model || "真实 LLM")}</span>
        </div>
        <div class="plan-section">
            <strong>商品基础</strong>
            <p>${escapeHtml(productSummary)}</p>
        </div>
        <div class="plan-section">
            <strong>渠道建议</strong>
            ${channels.length ? `<ul class="plan-list">${channels.map((item) => `<li><strong>${escapeHtml(item.channel || "--")}</strong>${item.type ? `（${escapeHtml(item.type)}）` : ""}：${escapeHtml((item.actions || []).join("；"))}</li>`).join("")}</ul>` : "<p>LLM 未返回结构化渠道建议。</p>"}
        </div>
        <div class="plan-section">
            <strong>差异化引流点</strong>
            ${renderPlanList(differentiation) || "<p>LLM 未返回差异化卖点。</p>"}
        </div>
        <div class="plan-section">
            <strong>可直接用的文案</strong>
            ${copywriting.length ? `<ul class="plan-list">${copywriting.map((item) => `<li><strong>${escapeHtml(item.title || "--")}</strong>${item.channel ? `（${escapeHtml(item.channel)}）` : ""}<br>${escapeHtml(item.body || "")}</li>`).join("")}</ul>` : "<p>LLM 未返回结构化文案。</p>"}
        </div>
        <div class="data-card-foot">
            <span>${escapeHtml(productInfo.pricing_reason || "基于真实商品方案定价理由")}</span>
            <span class="table-pill source-real">prompt ${escapeHtml(String(llmCall.prompt_chars ?? 0))} / response ${escapeHtml(String(llmCall.response_chars ?? 0))}</span>
        </div>
    `;
    root.appendChild(card);
}

function renderProfitAnalysis(payload) {
    state.latestProfitAnalysisPayload = payload;
    const { status = "success", detail = "", analysis = {}, competitor_stats: stats = {}, llm_call: llmCall = {}, hot_competitors: hot = [], price_band_distribution: bands = [], profit_scenarios: scenarios = {}, assumption = {} } = payload || {};
    const root = $("profitAnalysisPreview");
    if (!root) return;
    root.innerHTML = "";

    setInlineStatus("profitAnalysisStatus", status, detail);

    if (status === "idle") {
        clearList("profitAnalysisPreview", "基于真实竞品价格与想要数，生成只读选品和利润分析。");
        return;
    }

    if (status !== "success") {
        clearList("profitAnalysisPreview", detail || "真实 LLM 选品分析生成失败。");
        return;
    }

    root.className = "card-list";
    const competitorSummary = `真实竞品 ${stats.competitor_count ?? 0} 条 / 有价格 ${stats.priced_count ?? 0} 条 / 区间 ¥${stats.price_min ?? "--"}~¥${stats.price_max ?? "--"} / 中位 ¥${stats.price_median ?? "--"} / 均价 ¥${stats.price_avg ?? "--"}`;
    const hotSummary = Array.isArray(hot) && hot.length
        ? hot.slice(0, 3).map((item) => `${item.title || "--"}（${item.want_count || item.sold_count || "热度未读到"}）`).join("；")
        : "未读到高热度竞品。";
    const bandSummary = Array.isArray(bands) && bands.length
        ? bands.filter((item) => (item.count ?? 0) > 0).map((item) => `${item.band} ${item.count}条 / 想要合计 ${item.total_want_count}`).join("；")
        : "未生成真实价格带分布。";
    const recommended = Array.isArray(analysis.recommended_products) ? analysis.recommended_products : [];
    const scenariosList = Array.isArray(scenarios.scenarios) ? scenarios.scenarios : [];

    const card = document.createElement("article");
    card.className = "data-card listing-plan-card";
    card.innerHTML = `
        <div class="data-card-top">
            <div>
                <div class="data-card-title">真实选品 / 利润分析</div>
                <div class="data-card-meta">${escapeHtml(competitorSummary)}</div>
            </div>
            <span class="table-pill source-real">${escapeHtml(llmCall.model || "真实 LLM")}</span>
        </div>
        <div class="plan-section">
            <strong>需求热度</strong>
            <p>${escapeHtml(analysis?.demand_analysis?.summary || "LLM 未返回结构化需求热度结论。")}</p>
            ${renderPlanList(toPlanArray(analysis?.demand_analysis?.hottest_segments)) || "<p>未返回热度细分结论。</p>"}
        </div>
        <div class="plan-section">
            <strong>热门真实竞品</strong>
            <p>${escapeHtml(hotSummary)}</p>
        </div>
        <div class="plan-section">
            <strong>价格带分布</strong>
            <p>${escapeHtml(analysis?.price_band_analysis?.summary || "LLM 未返回结构化价格带结论。")}</p>
            <p>真实分布：${escapeHtml(bandSummary)}</p>
            <p>竞争最密集：${escapeHtml(analysis?.price_band_analysis?.competition_band || "--")} / 机会价位：${escapeHtml(analysis?.price_band_analysis?.opportunity_band || "--")}</p>
        </div>
        <div class="plan-section">
            <strong>值得做的款式 / 价位</strong>
            ${recommended.length ? `<ul class="plan-list">${recommended.map((item) => `<li><strong>${escapeHtml(item.segment || "--")}</strong>：${escapeHtml(item.reason || "")}</li>`).join("")}</ul>` : "<p>LLM 未返回结构化选品建议。</p>"}
        </div>
        <div class="plan-section">
            <strong>利润分析</strong>
            <p>${escapeHtml(assumption.note || scenarios.detail || "需要进价才能算真实利润；当前仅提供真实竞品分析。")}</p>
            <p>${escapeHtml(analysis?.profit_analysis?.summary || "LLM 未返回结构化利润结论。")}</p>
            <p>建议售价：${escapeHtml(analysis?.profit_analysis?.recommended_price || "--")} / 毛利润：${escapeHtml(analysis?.profit_analysis?.gross_profit || "--")} / 利润率：${escapeHtml(analysis?.profit_analysis?.profit_margin || "--")}</p>
            ${scenariosList.length ? `<ul class="plan-list">${scenariosList.map((item) => `<li><strong>${escapeHtml(item.label || "--")}</strong>：售价 ¥${escapeHtml(String(item.suggested_price ?? "--"))} / 毛利润 ¥${escapeHtml(String(item.gross_profit ?? "--"))} / 毛利率 ${escapeHtml(String(item.gross_margin_rate ?? "--"))}%</li>`).join("")}</ul>` : "<p>未提供假设进价，未计算利润场景。</p>"}
        </div>
        <div class="plan-section">
            <strong>风险提示</strong>
            ${renderPlanList(toPlanArray(analysis?.risk_notes)) || "<p>LLM 未返回风险提示。</p>"}
        </div>
        <div class="data-card-foot">
            <span>${escapeHtml(analysis?.profit_analysis?.assumption_note || "")}</span>
            <span class="table-pill source-real">prompt ${escapeHtml(String(llmCall.prompt_chars ?? 0))} / response ${escapeHtml(String(llmCall.response_chars ?? 0))}</span>
        </div>
    `;
    root.appendChild(card);
}

function renderReplyRag(payload) {
    state.latestReplyDraftPayload = payload;
    const meta = $("replyRagMeta");
    const resultsRoot = $("replyRagResults");
    if (!meta || !resultsRoot) return;

    const rag = payload?.rag;
    const llmCall = payload?.llm_call || {};
    const seed = payload?.knowledge_seed || {};

    if (!rag) {
        meta.innerHTML = `<strong>RAG 检索证据</strong><p>尚未执行检索。</p>`;
        clearList("replyRagResults", "执行回复生成后，这里展示真实命中的知识片段和分数。");
        return;
    }

    const retrieval = rag.retrieval_path || {};
    meta.innerHTML = `
        <strong>RAG 检索证据</strong>
        <div class="rag-meta-grid">
            <div>Embedding: ${escapeHtml(rag.embedding_model || "--")} / ${escapeHtml(String(rag.embedding_dimension ?? "--"))} 维</div>
            <div>Reranker: ${escapeHtml(rag.reranker_model || "--")}</div>
            <div class="rag-metrics">
                <span class="rag-chip">top score ${escapeHtml(String(rag.top_score ?? "--"))}</span>
                <span class="rag-chip">threshold ${escapeHtml(String(rag.threshold ?? "--"))}</span>
                <span class="rag-chip">vector ${escapeHtml(String(retrieval.vector_hits ?? 0))}</span>
                <span class="rag-chip">bm25 ${escapeHtml(String(retrieval.bm25_hits ?? 0))}</span>
                <span class="rag-chip">${retrieval.rrf_used ? "RRF 融合" : "仅向量命中"}</span>
                <span class="rag-chip">seed ${escapeHtml(String(seed.entries?.length ?? 0))} 条 / ${escapeHtml(String(seed.chunks_count ?? 0))} chunks</span>
            </div>
            ${llmCall.model ? `<div>LLM: ${escapeHtml(llmCall.model)} / prompt ${escapeHtml(String(llmCall.prompt_chars ?? 0))} chars / response ${escapeHtml(String(llmCall.response_chars ?? 0))} chars / ${escapeHtml(String(llmCall.duration_ms ?? 0))} ms</div>` : ""}
        </div>
    `;

    const results = rag.results || [];
    if (!results.length) {
        clearList("replyRagResults", payload?.detail || "无相关知识。");
        return;
    }

    resultsRoot.className = "card-list";
    resultsRoot.innerHTML = "";
    results.forEach((item, index) => {
        const card = document.createElement("article");
        card.className = "data-card rag-result-card";
        card.innerHTML = `
            <div class="data-card-top">
                <div>
                    <div class="data-card-title">命中片段 #${index + 1}</div>
                    <div class="data-card-meta mono">${escapeHtml(item.id || "")}</div>
                </div>
                <span class="price-tag">${escapeHtml(String(item.score ?? "--"))}</span>
            </div>
            <div class="data-card-body">${escapeHtml(item.content || "")}</div>
            <div class="data-card-foot">
                <span>${escapeHtml(item.category || "--")}</span>
                <span class="table-pill source-real">${escapeHtml(item.source || "real")}</span>
            </div>
        `;
        resultsRoot.appendChild(card);
    });
}

function renderOverview(data) {
    const metrics = data.metrics || {};
    renderStatus({
        platform_mode: data.platform_mode,
        login_state: data.login_state,
        login_task: data.login_task,
    });
    setText("metricMessages", metricText(metrics.today_messages ?? 0));
    setText("metricPendingReply", metricText(metrics.unreplied_messages ?? 0));
    setText("metricApprovals", metricText(metrics.pending_approvals ?? 0));
    setText("metricConversations", metricText(metrics.conversation_count ?? 0));
    setText("metricCompetitors", metricText(metrics.competitor_records ?? 0));
    setText("metricListings", metricText(metrics.on_sale_items ?? 0));
    setText("metricArtifacts", metricText(metrics.generated_artifacts ?? 0));

    const latest = data.latest_results || {};
    if (latest.listing_plan) {
        renderListingPlan(latest.listing_plan);
    } else if (!state.latestListingPlanPayload) {
        renderListingPlan({ status: "idle" });
    }
    if (latest.marketing_plan) {
        renderMarketingPlan(latest.marketing_plan);
    } else if (!state.latestMarketingPlanPayload) {
        renderMarketingPlan({ status: "idle" });
    }
    if (latest.profit_analysis) {
        renderProfitAnalysis(latest.profit_analysis);
    } else if (!state.latestProfitAnalysisPayload) {
        renderProfitAnalysis({ status: "idle" });
    }
    if (latest.reply_draft) {
        renderReplyRag(latest.reply_draft);
        if (!($("replyDraftInput").value || "").trim()) {
            $("replyDraftInput").value = latest.reply_draft.draft || "";
        }
        if (!($("conversationIdInput").value || "").trim()) {
            $("conversationIdInput").value = latest.reply_draft.conversation_id || "";
        }
    } else if (!state.latestReplyDraftPayload) {
        renderReplyRag(null);
    }

    if (!state.latestMessagesPayload || state.latestMessagesPayload.status === "idle") {
        renderMessages({
            status: data.login_state?.logged_in ? "success" : "not_logged_in",
            detail: data.login_state?.logged_in ? "" : "请先扫码登录闲鱼，登录后有真实活动才会显示消息。",
            conversations: data.conversations || [],
            messages: data.messages || [],
        });
    }
    if (!state.latestListingsPayload || state.latestListingsPayload.status === "idle") {
        renderListings({
            status: data.login_state?.logged_in ? "success" : "not_logged_in",
            detail: data.login_state?.logged_in ? "" : "请先扫码登录闲鱼，登录后有真实在售商品才会显示。",
            listings: data.listings || [],
        });
    }
    if (
        !state.latestCompetitorsPayload ||
        state.latestCompetitorsPayload.status === "idle" ||
        !((state.latestCompetitorsPayload.results || []).length)
    ) {
        renderCompetitors({
            status: (data.competitors || []).length ? "success" : "idle",
            detail: (data.competitors || []).length ? "" : "暂无真实竞品记录，先执行竞品调研后这里才会有数据。",
            results: data.competitors || [],
        });
    }

    renderTimeline(data.execution_logs || []);
    setStatusPill("轮询中", "status-success");
}

async function loadListings() {
    try {
        requireToken();
        const data = await api("/api/xianyu/listings?limit=50");
        renderListings(data);
        await loadOverview();
        if (data.status === "success") {
            showToast(`在售商品读取完成，真实结果 ${data.listings?.length ?? 0} 条。`);
        } else {
            showToast(data.detail || "在售商品未读取完成", data.status !== "success");
        }
    } catch (error) {
        showToast(error.message, true);
    }
}

function timelineItem(event) {
    const article = document.createElement("article");
    const stat = statusLabel(event.status);
    const source = sourceLabel(event.data_source);
    article.className = `timeline-item ${stat.cls}`;
    article.innerHTML = `
        <div class="timeline-top">
            <div class="timeline-title">
                <strong>${escapeHtml(event.action_name || "-")}</strong>
                <span class="status-chip ${stat.cls}">${stat.text}</span>
                <span class="source-tag ${source.cls}">${source.text}</span>
            </div>
            <span class="timeline-duration mono">${escapeHtml(formatTimestamp(event.timestamp))}</span>
        </div>
        <div class="timeline-meta">
            ${escapeHtml(event.component || "browser")}
            ${event.duration_ms != null ? ` / ${escapeHtml(String(event.duration_ms))}ms` : ""}
            ${event.detail ? `<br>${escapeHtml(event.detail)}` : ""}
            ${event.error ? `<br>${escapeHtml(event.error)}` : ""}
        </div>
    `;
    return article;
}

function renderTimeline(events) {
    const root = $("executionTimeline");
    root.innerHTML = "";
    if (!events.length) {
        root.className = "timeline empty";
        root.textContent = "暂无真实执行事件。";
        return;
    }
    root.className = "timeline";
    events.forEach((event) => {
        root.appendChild(timelineItem(event));
    });
}

function openImageModal() {
    showToast("实时截图功能已移除", true);
    return;
    $("imageModal").hidden = false;
}

function closeImageModal() {
    $("imageModal").hidden = true;
}

async function refreshStatus() {
    requireToken();
    const data = await api("/api/xianyu/status");
    renderStatus(data);
    return data;
}

async function loadOverview() {
    requireToken();
    const data = await api("/api/dashboard/overview");
    renderOverview(data);
    return data;
}

async function startLogin() {
    try {
        requireToken();
        const data = await api("/api/xianyu/login/start", {
            method: "POST",
            body: JSON.stringify({ max_wait_seconds: 300 }),
        });
        updateLoginTask(data);
        showToast("已打开本地真实浏览器，请在窗口中完成闲鱼登录。");
        startLoginPolling();
    } catch (error) {
        showToast(error.message, true);
    }
}

function startLoginPolling() {
    window.clearInterval(state.loginPollTimer);
    state.loginPollTimer = window.setInterval(async () => {
        try {
            const data = await api("/api/xianyu/login/status");
            updateLoginTask(data);
            if (!data.running) {
                window.clearInterval(state.loginPollTimer);
                await refreshStatus();
                showToast(data.status === "success" ? "闲鱼登录态已保存" : (data.error || "闲鱼登录流程失败"), data.status !== "success");
            }
        } catch (error) {
            window.clearInterval(state.loginPollTimer);
            showToast(error.message, true);
        }
    }, 2500);
}

async function loadMessages() {
    try {
        requireToken();
        // ✅ 调用新的conversations API
        const data = await api("/api/xianyu/conversations");
        renderMessages(data);
        await loadOverview();
        if (data.status === "success") {
            showToast(`消息读取完成，真实会话 ${data.results?.length ?? 0} 条。`);
        }
    } catch (error) {
        showToast(error.message, true);
    }
}

async function loadCompetitors() {
    try {
        requireToken();
        const keyword = $("keywordInput").value.trim();
        if (!keyword) {
            throw new Error("请输入真实商品关键词");
        }
        const data = await api(`/api/xianyu/competitors?keyword=${encodeURIComponent(keyword)}&limit=20`);
        renderCompetitors(data);
        await loadOverview();
        if (data.status === "success") {
            showToast(`竞品抓取完成，真实结果 ${data.results?.length ?? 0} 条。`);
        }
    } catch (error) {
        showToast(error.message, true);
    }
}

async function generateListingPlan() {
    try {
        requireToken();
        const keyword = $("keywordInput").value.trim();
        if (!keyword) {
            throw new Error("请输入已有真实竞品数据的关键词");
        }
        setInlineStatus("listingPlanStatus", "running", "正在调用真实 LLM 生成上架方案");
        const data = await api("/api/xianyu/listing-plan", {
            method: "POST",
            body: JSON.stringify({
                keyword,
                product_info: $("listingProductInfoInput").value.trim(),
                competitor_limit: 25,
            }),
        });
        renderListingPlan(data);
        showToast(`真实 LLM 上架方案已生成，使用竞品 ${data.competitors_used?.length ?? 0} 条。`);
    } catch (error) {
        setInlineStatus("listingPlanStatus", "failure", error.message);
        clearList("listingPlanPreview", error.message);
        showToast(error.message, true);
    }
}

async function generateMarketingPlan() {
    try {
        requireToken();
        const keyword = $("keywordInput").value.trim();
        if (!keyword) {
            throw new Error("请输入已有真实竞品数据的关键词");
        }
        setInlineStatus("marketingPlanStatus", "running", "正在调用真实 LLM 生成引流方案");
        const data = await api("/api/xianyu/marketing-plan", {
            method: "POST",
            body: JSON.stringify({
                keyword,
                product_info: $("listingProductInfoInput").value.trim(),
                competitor_limit: 25,
            }),
        });
        renderMarketingPlan(data);
        showToast(`真实 LLM 引流方案已生成，使用竞品 ${data.competitors_used?.length ?? 0} 条。`);
    } catch (error) {
        setInlineStatus("marketingPlanStatus", "failure", error.message);
        clearList("marketingPlanPreview", error.message);
        showToast(error.message, true);
    }
}

async function generateProfitAnalysis() {
    try {
        requireToken();
        const keyword = $("keywordInput").value.trim();
        if (!keyword) {
            throw new Error("请输入已有真实竞品数据的关键词");
        }
        const assumedCostRaw = $("assumedCostInput").value.trim();
        const payload = {
            keyword,
            competitor_limit: 25,
        };
        if (assumedCostRaw !== "") {
            payload.assumed_cost = Number(assumedCostRaw);
        }
        setInlineStatus("profitAnalysisStatus", "running", "正在调用真实 LLM 生成选品 / 利润分析");
        const data = await api("/api/xianyu/profit-analysis", {
            method: "POST",
            body: JSON.stringify(payload),
        });
        renderProfitAnalysis(data);
        showToast(`真实 LLM 选品分析已生成，使用竞品 ${data.competitors_used?.length ?? 0} 条。`);
    } catch (error) {
        setInlineStatus("profitAnalysisStatus", "failure", error.message);
        clearList("profitAnalysisPreview", error.message);
        showToast(error.message, true);
    }
}

async function createDraft() {
    try {
        requireToken();
        const conversationId = $("conversationIdInput").value.trim();
        const buyerMessage = $("buyerMessageInput").value.trim();
        if (!conversationId || !buyerMessage) {
            throw new Error("请先从真实消息中选择会话并填入买家消息");
        }
        const data = await api("/api/xianyu/reply-draft", {
            method: "POST",
            body: JSON.stringify({
                conversation_id: conversationId,
                buyer_message: buyerMessage,
            }),
        });
        $("replyDraftInput").value = data.draft || "";
        renderReplyRag(data);
        if (data.status === "no_knowledge") {
            showToast(data.detail || "无相关知识", true);
            return;
        }
        showToast("真实 RAG + LLM 草稿已生成");
    } catch (error) {
        showToast(error.message, true);
    }
}

function showApprovalResult(data) {
    if (data.status === "approval_required") {
        showToast(`已创建审批单 ${data.approval_id}`);
        return;
    }
    showToast(data.message || data.status || "操作已提交");
}

async function submitReplyApproval() {
    try {
        requireToken();
        const conversationId = $("conversationIdInput").value.trim();
        const content = $("replyDraftInput").value.trim();
        if (!conversationId || !content) {
            throw new Error("请先准备真实会话和草稿内容");
        }
        const data = await api("/api/xianyu/send-reply", {
            method: "POST",
            body: JSON.stringify({
                conversation_id: conversationId,
                content,
            }),
        });
        showApprovalResult(data);
        await loadOverview();
    } catch (error) {
        showToast(error.message, true);
    }
}

async function submitListingApproval() {
    try {
        requireToken();
        const raw = $("listingJsonInput").value.trim();
        if (!raw) {
            throw new Error("请填写真实上架参数 JSON");
        }
        const item = JSON.parse(raw);
        const data = await api("/api/xianyu/list-item", {
            method: "POST",
            body: JSON.stringify({ item }),
        });
        showApprovalResult(data);
        await loadOverview();
    } catch (error) {
        showToast(error.message, true);
    }
}

async function submitShippingApproval() {
    try {
        requireToken();
        const orderId = $("shipOrderIdInput").value.trim();
        const shipmentRaw = $("shipmentJsonInput").value.trim();
        if (!orderId) {
            throw new Error("请填写真实订单 ID");
        }
        if (!shipmentRaw) {
            throw new Error("请填写真实发货参数 JSON");
        }
        const shipment = JSON.parse(shipmentRaw);
        const data = await api("/api/xianyu/ship-order", {
            method: "POST",
            body: JSON.stringify({
                order_id: orderId,
                shipment,
            }),
        });
        showApprovalResult(data);
        await loadOverview();
    } catch (error) {
        showToast(error.message, true);
    }
}

function mergeEvent(event) {
    if (!event || state.eventIds.has(event.event_id)) return;
    state.eventIds.add(event.event_id);

    const root = $("executionTimeline");
    if (root.classList.contains("empty")) {
        root.className = "timeline";
        root.innerHTML = "";
    }
    root.prepend(timelineItem(event));
}

function startOverviewPolling() {
    window.clearInterval(state.overviewTimer);
    state.overviewTimer = window.setInterval(async () => {
        try {
            await loadOverview();
        } catch (error) {
            setStatusPill("轮询失败", "status-failure");
        }
    }, state.autoRefreshMs);
}

function setupTabs() {
    document.querySelectorAll(".tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
            document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));
            tab.classList.add("active");
            $(`${tab.dataset.tab}Tab`).classList.add("active");
        });
    });
}

function bindModal() {
    $("browserFullscreenBtn").addEventListener("click", openImageModal);
    $("browserFrame").addEventListener("click", openImageModal);
    $("closeImageModalBtn").addEventListener("click", closeImageModal);
    $("imageModal").addEventListener("click", (event) => {
        if (event.target.dataset.closeModal === "true") {
            closeImageModal();
        }
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !$("imageModal").hidden) {
            closeImageModal();
        }
    });
}

async function connectAll() {
    await refreshStatus();
    await loadOverview();
    startOverviewPolling();
}

function init() {
    $("tokenInput").value = state.token;
    $("startLoginBtn").textContent = "登录闲鱼";

    $("saveTokenBtn").addEventListener("click", async () => {
        try {
            state.token = $("tokenInput").value.trim().replace(/^Bearer\s+/i, "");
            localStorage.setItem("xianyuAdminToken", state.token);
            await connectAll();
            showToast("闲鱼面板已连接");
        } catch (error) {
            showToast(error.message, true);
        }
    });

    $("refreshStatusBtn").addEventListener("click", async () => {
        try {
            await connectAll();
            showToast("面板状态已刷新");
        } catch (error) {
            showToast(error.message, true);
        }
    });

    $("startLoginBtn").addEventListener("click", startLogin);
    $("loadMessagesBtn").addEventListener("click", loadMessages);
    $("loadListingsBtn").addEventListener("click", loadListings);
    $("loadCompetitorsBtn").addEventListener("click", loadCompetitors);
    $("generateListingPlanBtn").addEventListener("click", generateListingPlan);
    $("generateMarketingPlanBtn").addEventListener("click", generateMarketingPlan);
    $("generateProfitAnalysisBtn").addEventListener("click", generateProfitAnalysis);
    $("draftBtn").addEventListener("click", createDraft);
    $("approvalReplyBtn").addEventListener("click", submitReplyApproval);
    $("submitListingBtn").addEventListener("click", submitListingApproval);
    $("submitShippingBtn").addEventListener("click", submitShippingApproval);

    setupTabs();
    bindModal();
    renderMessages({ status: "idle", conversations: [] });
    renderListings({ status: "idle", listings: [] });
    renderCompetitors({ status: "idle", results: [] });
    renderListingPlan({ status: "idle" });
    renderMarketingPlan({ status: "idle" });
    renderProfitAnalysis({ status: "idle" });
    renderReplyRag(null);
    renderTimeline([]);

    if (state.token) {
        connectAll().catch((error) => {
            showToast(error.message, true);
        });
    }
}

document.addEventListener("DOMContentLoaded", init);
