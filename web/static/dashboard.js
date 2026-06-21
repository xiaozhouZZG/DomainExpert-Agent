const state = {
    token: localStorage.getItem("xianyuAdminToken") || "",
    socket: null,
    overviewTimer: null,
    eventIds: new Set(),
    autoScroll: true,
    lastOverview: null,
};

const $ = (id) => document.getElementById(id);

function headers() {
    return {
        Authorization: `Bearer ${state.token}`,
        "Content-Type": "application/json",
    };
}

async function api(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: {
            ...headers(),
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

function showToast(message, isError = false) {
    const toast = $("toast");
    if (!toast) return;
    toast.textContent = message;
    toast.style.background = isError ? "#7f1d1d" : "#172033";
    toast.hidden = false;
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => {
        toast.hidden = true;
    }, 3200);
}

function requireToken() {
    if (!state.token) {
        throw new Error("请先输入管理 Token");
    }
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function sourceLabel(source) {
    const normalized = String(source || "").toLowerCase();
    if (normalized.includes("goofish") || normalized.includes("real")) {
        return { text: "真实", cls: "source-real" };
    }
    if (normalized.includes("system") || normalized.includes("system")) {
        return { text: "系统", cls: "source-system" };
    }
    if (normalized === "not_connected" || normalized === "unknown" || !normalized) {
        return { text: "未接通", cls: "source-not-connected" };
    }
    return { text: source, cls: "source-idle" };
}

function statusLabel(status) {
    const normalized = String(status || "").toLowerCase();
    if (["start", "running", "in_progress"].includes(normalized)) {
        return { text: "进行中", cls: "status-running" };
    }
    if (["success", "completed", "done"].includes(normalized)) {
        return { text: "成功", cls: "status-success" };
    }
    if (["failure", "failed", "error"].includes(normalized)) {
        return { text: "失败", cls: "status-failure" };
    }
    if (["pending", "queued"].includes(normalized)) {
        return { text: "等待", cls: "status-pending" };
    }
    return { text: status || "未定", cls: "status-idle" };
}

function formatTimestamp(value) {
    if (!value) return "--";
    return String(value).replace("T", " ").replace(/\.\d+$/, "");
}

function setLastUpdated(text) {
    const node = $("lastUpdated");
    if (node) {
        node.textContent = `最后更新：${text}`;
    }
}

    if (!node) return;
    node.textContent = `数据源：${text}`;
    node.className = `status-pill status-pill-source ${cls || "source-idle"}`;
}

function buildBadge(text, cls) {
    const span = document.createElement("span");
    span.className = `table-pill ${cls}`;
    span.textContent = text;
    return span;
}

function buildCellMain(title, sub) {
    const wrap = document.createElement("div");
    wrap.className = "cell-main";
    const strong = document.createElement("div");
    strong.className = "cell-title";
    strong.textContent = title || "--";
    wrap.appendChild(strong);
    if (sub) {
        const small = document.createElement("div");
        small.className = "cell-sub";
        small.textContent = sub;
        wrap.appendChild(small);
    }
    return wrap;
}

function renderMetrics(metrics) {
    $("todayMessages").textContent = metrics.today_messages ?? 0;
    $("unrepliedMessages").textContent = metrics.unreplied_messages ?? 0;
    $("pendingApprovals").textContent = metrics.pending_approvals ?? 0;
    $("competitorKeywords").textContent = metrics.competitor_keywords ?? 0;
    $("onSaleItems").textContent = metrics.on_sale_items ?? 0;
}

function timelineItem(event) {
    const article = document.createElement("article");
    const stat = statusLabel(event.status);
    article.className = `timeline-item ${event.status || "idle"}`;
    const source = sourceLabel(event.data_source);
    const time = formatTimestamp(event.timestamp);
    const detail = event.detail || event.error || "";
    article.innerHTML = `
        <div class="timeline-top">
            <div class="timeline-title">
                <strong>${escapeHtml(event.action_name || "-")}</strong>
                <span class="status-chip ${stat.cls}">${stat.text}</span>
                <span class="source-tag ${source.cls}">${source.text}</span>
            </div>
            <div class="timeline-duration">${escapeHtml(time)}</div>
        </div>
        <div class="timeline-meta">
            ${escapeHtml(event.component || "browser")}
            ${event.duration_ms != null ? ` / ${escapeHtml(String(event.duration_ms))}ms` : ""}
            ${detail ? `<br>${escapeHtml(detail)}` : ""}
        </div>
    `;
    if (stat.cls === "status-failure") {
        article.classList.add("failure");
    } else if (stat.cls === "status-success") {
        article.classList.add("success");
    } else if (stat.cls === "status-running") {
        article.classList.add("running");
    } else if (stat.cls === "status-pending") {
        article.classList.add("pending");
    }
    return article;
}

function renderTimeline(events) {
    const root = $("timeline");
    if (!root) return;
    root.innerHTML = "";
    root.classList.remove("empty");

    if (!events || !events.length) {
        root.classList.add("empty");
        root.textContent = "暂无执行事件";
        return;
    }

    const ordered = [...events].slice().reverse();
    for (const event of ordered) {
        root.appendChild(timelineItem(event));
    }

    if (state.autoScroll) {
        root.scrollTop = 0;
    }
}

function renderTable(bodyId, rows, columns, emptyText = "暂无数据") {
    const body = $(bodyId);
    if (!body) return;
    body.innerHTML = "";

    if (!rows || !rows.length) {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = columns.length;
        td.innerHTML = `<div class="empty-cell">${emptyText}</div>`;
        tr.appendChild(td);
        body.appendChild(tr);
        return;
    }

    rows.forEach((row) => {
        const tr = document.createElement("tr");
        tr.classList.add("flash");
        columns.forEach((column) => {
            const td = document.createElement("td");
            const value = column.render ? column.render(row, td) : row[column.key];
            if (value instanceof Node) {
                td.appendChild(value);
            } else if (value !== undefined && value !== null && value !== "") {
                td.innerHTML = value;
            } else {
                td.innerHTML = "<span class='empty-cell'>--</span>";
            }
            tr.appendChild(td);
        });
        body.appendChild(tr);
    });
}

function renderOverview(data) {
    state.lastOverview = data;
    renderMetrics(data.metrics || {});

    const logs = data.execution_logs || [];
    renderTimeline(logs);

    const sourceSummary = [
        ...new Set([
            ...(data.conversations || []).map((row) => row.data_source),
            ...(data.messages || []).map((row) => row.data_source),
            ...(data.competitors || []).map((row) => row.data_source),
            ...(data.listings || []).map((row) => row.data_source),
        ].filter(Boolean)),
    ];
    if (sourceSummary.length) {
        const first = sourceLabel(sourceSummary[0]);
        setGlobalDataSource(first.text, first.cls);
    } else {
        setGlobalDataSource("未接通", "source-not-connected");
    }

    const latest = logs[0] || null;
    setLastUpdated(latest ? formatTimestamp(latest.timestamp) : "--");

    renderTable("conversationsBody", data.conversations || [], [
        {
            key: "conversation_id",
            render: (row) => buildCellMain(row.conversation_id, row.last_intent || row.platform || ""),
        },
        {
            key: "buyer_name",
            render: (row) => buildCellMain(row.buyer_name, row.updated_at || row.last_message_at || ""),
        },
        {
            key: "data_source",
            render: (row) => {
                const tag = sourceLabel(row.data_source);
                return buildBadge(tag.text, tag.cls);
            },
        },
        {
            key: "status",
            render: (row) => {
                const tag = statusLabel(row.status);
                return buildBadge(tag.text, tag.cls);
            },
        },
        {
            key: "last_message_at",
            render: (row) => buildCellMain(formatTimestamp(row.last_message_at), formatTimestamp(row.updated_at)),
        },
    ]);

    renderTable("messagesBody", data.messages || [], [
        {
            key: "conversation_id",
            render: (row) => buildCellMain(row.conversation_id, row.message_id || ""),
        },
        {
            key: "direction",
            render: (row) => buildBadge(row.direction || "--", row.direction === "buyer" ? "status-running" : "status-idle"),
        },
        {
            key: "content",
            render: (row) => buildCellMain(row.content || "--", row.intent || ""),
        },
        {
            key: "draft_reply",
            render: (row) => buildCellMain(row.draft_reply || "--", row.sent_status || ""),
        },
        {
            key: "data_source",
            render: (row) => {
                const tag = sourceLabel(row.data_source);
                return buildBadge(tag.text, tag.cls);
            },
        },
    ]);

    renderTable("approvalsBody", data.approvals || [], [
        {
            key: "approval_id",
            render: (row) => buildCellMain(row.approval_id, row.title || ""),
        },
        {
            key: "workflow_type",
            render: (row) => buildCellMain(row.workflow_type, row.content || ""),
        },
        {
            key: "status",
            render: (row) => {
                const tag = statusLabel(row.status);
                return buildBadge(tag.text, tag.cls);
            },
        },
        {
            key: "order_id",
            render: (row) => buildCellMain(row.order_id || "--", row.customer_id || ""),
        },
        {
            key: "created_at",
            render: (row) => buildCellMain(formatTimestamp(row.created_at), ""),
        },
    ]);

    renderTable("competitorsBody", data.competitors || [], [
        {
            key: "keyword",
            render: (row) => buildCellMain(row.keyword || "--", row.observed_at || ""),
        },
        {
            key: "title",
            render: (row) => buildCellMain(row.title || "--", row.platform || ""),
        },
        {
            key: "price",
            render: (row) => buildCellMain(row.price ?? "--", "价格"),
        },
        {
            key: "sold_count",
            render: (row) => buildCellMain(row.sold_count ?? "--", "销量"),
        },
        {
            key: "data_source",
            render: (row) => {
                const tag = sourceLabel(row.data_source);
                return buildBadge(tag.text, tag.cls);
            },
        },
    ]);

    renderTable("listingsBody", data.listings || [], [
        {
            key: "title",
            render: (row) => buildCellMain(row.title || row.item_id || "--", row.item_id || ""),
        },
        {
            key: "status",
            render: (row) => {
                const tag = statusLabel(row.status);
                return buildBadge(tag.text, tag.cls);
            },
        },
        {
            key: "price",
            render: (row) => buildCellMain(row.price ?? "--", ""),
        },
        {
            key: "data_source",
            render: (row) => {
                const tag = sourceLabel(row.data_source);
                return buildBadge(tag.text, tag.cls);
            },
        },
        {
            key: "last_seen_at",
            render: (row) => buildCellMain(formatTimestamp(row.last_seen_at), ""),
        },
    ]);
}

function mergeEvent(event) {
    if (!event || state.eventIds.has(event.event_id)) return;
    state.eventIds.add(event.event_id);

    const root = $("timeline");
    if (root && root.classList.contains("empty")) {
        root.classList.remove("empty");
        root.innerHTML = "";
    }
    if (root) {
        root.prepend(timelineItem(event));
        if (state.autoScroll) {
            root.scrollTop = 0;
        }
    }

    setLastUpdated(formatTimestamp(event.timestamp));
}

async function loadOverview() {
    requireToken();
    const data = await api("/api/dashboard/overview");
    renderOverview(data);
    (data.execution_logs || []).forEach((event) => state.eventIds.add(event.event_id));
    return data;
}


function startPolling() {
    window.clearInterval(state.overviewTimer);
    state.overviewTimer = window.setInterval(async () => {
        try {
            await loadOverview();
        } catch (error) {
        }
    }, 5000);
}

function bindTabs() {
    document.querySelectorAll(".tab-button[data-tab]").forEach((button) => {
        button.addEventListener("click", () => {
            const tab = button.getAttribute("data-tab");
            document.querySelectorAll(".tab-button[data-tab]").forEach((item) => item.classList.remove("active"));
            document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));
            button.classList.add("active");
            const activePanel = $(`tab-${tab}`);
            if (activePanel) activePanel.classList.add("active");
        });
    });
}

function bindControls() {
    $("tokenInput").value = state.token;
    $("saveTokenBtn").addEventListener("click", async () => {
        try {
            state.token = $("tokenInput").value.trim().replace(/^Bearer\s+/i, "");
            localStorage.setItem("xianyuAdminToken", state.token);
            await loadOverview();
            startPolling();
            showToast("驾驶舱已连接");
        } catch (error) {
            showToast(error.message, true);
        }
    });
    $("refreshBtn").addEventListener("click", async () => {
        try {
            await loadOverview();
            showToast("数据已刷新");
        } catch (error) {
            showToast(error.message, true);
        }
    });
    $("browserRefreshBtn").addEventListener("click", async () => {
        try {
            await loadOverview();
            showToast("画面已刷新");
        } catch (error) {
            showToast(error.message, true);
        }
    });
    $("toggleAutoplayBtn").addEventListener("click", () => {
        state.autoScroll = !state.autoScroll;
        $("autoScrollToggle").checked = state.autoScroll;
        showToast(state.autoScroll ? "自动滚动已开启" : "自动滚动已关闭");
    });
    $("autoScrollToggle").addEventListener("change", (event) => {
        state.autoScroll = event.target.checked;
    });
    $("clearTimelineBtn").addEventListener("click", () => {
        state.eventIds.clear();
        const root = $("timeline");
        if (root) {
            root.classList.add("empty");
            root.textContent = "暂无执行事件";
        }
        showToast("时间线已清空");
    });
}

function init() {
    bindTabs();
    bindControls();
    if (state.token) {
        loadOverview()
            .then(() => {
                startPolling();
            })
            .catch((error) => {
                showToast(error.message, true);
            });
    } else {
    }
}

document.addEventListener("DOMContentLoaded", init);
