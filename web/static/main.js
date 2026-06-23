// 闲鱼运营助手 - 主逻辑

// 全局状态
const state = {
    currentModule: 'messages'
};

// API 请求封装
async function api(path, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    const response = await fetch(path, {
        ...options,
        headers
    });

    return response.json();
}

// 导航切换
function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            const module = item.dataset.module;
            switchModule(module);
        });
    });
}

function switchModule(moduleName) {
    // 更新导航激活状态
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.module === moduleName);
    });

    // 更新模块显示状态
    document.querySelectorAll('.module').forEach(module => {
        module.classList.toggle('active', module.id === `module-${moduleName}`);
    });

    state.currentModule = moduleName;

    // 加载模块数据
    loadModuleData(moduleName);
}

function loadModuleData(moduleName) {
    switch (moduleName) {
        case 'listings':
            loadListings();
            break;
        case 'overview':
            loadOverview();
            break;
        case 'settings':
            loadSettings();
            break;
    }
}

// ==================== 我的商品模块 ====================

async function loadListings() {
    const container = document.getElementById('listings-container');
    container.innerHTML = '<div class="loading">加载中...</div>';

    try {
        const data = await api('/api/xianyu/listings?limit=50');

        if (!data.listings || data.listings.length === 0) {
            container.innerHTML = '<div class="placeholder-card"><h3>暂无商品</h3><p class="placeholder-desc">请先连接闲鱼账号</p></div>';
            return;
        }

        container.innerHTML = data.listings.map(item => `
            <div class="listing-card">
                <div class="listing-info">
                    <div class="listing-title">${escapeHtml(item.title || '无标题')}</div>
                    <div class="listing-price">¥${item.price || '0.00'}</div>
                    <span class="listing-status active">${item.status || '在售'}</span>
                </div>
            </div>
        `).join('');
    } catch (error) {
        container.innerHTML = `<div class="placeholder-card"><h3>加载失败</h3><p class="placeholder-desc">${error.message}</p></div>`;
    }
}

// ==================== 上架模块 ====================

async function generateListingPlan() {
    const keyword = document.getElementById('publish-keyword').value.trim();
    const productInfo = document.getElementById('publish-desc').value.trim();
    const cost = parseFloat(document.getElementById('publish-cost').value) || 0;
    const margin = parseFloat(document.getElementById('publish-margin').value) || 20;

    if (!keyword) {
        alert('请输入商品关键词');
        return;
    }

    const resultArea = document.getElementById('listing-plan-result');
    resultArea.innerHTML = '<div class="loading">AI 正在生成上架方案...</div>';

    try {
        const data = await api('/api/xianyu/listing-plan', {
            method: 'POST',
            body: JSON.stringify({
                keyword,
                product_info: productInfo,
                cost,
                target_margin: margin,
                competitor_limit: 10
            })
        });

        resultArea.innerHTML = `
            <div class="publish-result-content">
                <h3>AI 推荐上架方案</h3>
                <div style="margin-top: 16px; line-height: 1.8; color: var(--text-primary);">
                    <pre style="white-space: pre-wrap; font-family: inherit; margin: 0;">${escapeHtml(data.plan_text || '生成失败')}</pre>
                </div>
            </div>
        `;
    } catch (error) {
        resultArea.innerHTML = `
            <div class="publish-result-empty">
                <div class="publish-result-empty-icon">⚠️</div>
                <div class="publish-result-empty-text">生成失败: ${escapeHtml(error.message)}</div>
            </div>
        `;
    }
}

function formatListingPlan(data) {
    if (data.plan_text) {
        return `<pre style="white-space: pre-wrap; font-family: inherit;">${escapeHtml(data.plan_text)}</pre>`;
    }
    return '<p>生成失败，请稍后重试</p>';
}

// ==================== 竞品选品模块 ====================

function initResearchTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(`tab-${tab}`).classList.add('active');
        });
    });
}

async function searchCompetitors() {
    const keyword = document.getElementById('competitor-keyword').value.trim();
    if (!keyword) {
        alert('请输入搜索关键词');
        return;
    }

    const resultArea = document.getElementById('competitors-result');
    resultArea.innerHTML = '<div class="loading">实时抓取中，请稍候...</div>';

    try {
        const data = await api(`/api/xianyu/competitors?keyword=${encodeURIComponent(keyword)}&limit=20`);

        // ✅ 根据真实状态显示不同提示
        if (data.status === 'not_logged_in') {
            resultArea.innerHTML = `
                <div class="placeholder-card">
                    <h3>未登录</h3>
                    <p class="placeholder-desc">请先扫码登录闲鱼</p>
                    <p style="margin-top: 12px;">
                        <a href="#" onclick="navigateTo('settings'); return false;" style="color: var(--primary-color);">前往设置页登录 →</a>
                    </p>
                </div>
            `;
            return;
        }

        if (data.status === 'not_ready') {
            resultArea.innerHTML = `
                <div class="placeholder-card">
                    <h3>未登录或被拦截</h3>
                    <p class="placeholder-desc">请先登录，或检查是否被反爬拦截</p>
                    <p style="margin-top: 12px;">
                        <a href="#" onclick="navigateTo('settings'); return false;" style="color: var(--primary-color);">前往设置页登录 →</a>
                    </p>
                </div>
            `;
            return;
        }

        if (data.status === 'error' || data.status === 'failed') {
            resultArea.innerHTML = `
                <div class="placeholder-card">
                    <h3>抓取失败</h3>
                    <p class="placeholder-desc">${escapeHtml(data.detail || data.error || '未知错误')}</p>
                </div>
            `;
            return;
        }

        // ✅ 改为读取data.results（后端返回的字段）
        const results = data.results || [];

        if (data.status === 'success' && results.length === 0) {
            resultArea.innerHTML = `
                <div class="placeholder-card">
                    <h3>该关键词无在售商品</h3>
                    <p class="placeholder-desc">"${escapeHtml(keyword)}"暂无在售商品，请尝试其他关键词</p>
                </div>
            `;
            return;
        }

        // ✅ 计算平均价格（price可能是"¥19.99"或"19.99"）
        const prices = results.map(item => {
            const priceStr = (item.price || '0').replace(/[¥￥,，]/g, '').trim();
            return parseFloat(priceStr) || 0;
        }).filter(p => p > 0);
        const avgPrice = prices.length > 0 ? prices.reduce((a, b) => a + b, 0) / prices.length : 0;

        resultArea.innerHTML = `
            <h3>竞品分析结果</h3>
            <div style="margin: 16px 0; padding: 16px; background: var(--primary-light); border-radius: 6px;">
                <strong>平均价格:</strong> ¥${avgPrice.toFixed(2)} |
                <strong>样本数量:</strong> ${results.length}
            </div>
            <div class="listings-grid">
                ${results.map((item, idx) => {
                    // ✅ 标题截断到60字（中文约30字）
                    const title = item.title || '无标题';
                    const titleShort = title.length > 60 ? title.substring(0, 60) + '…' : title;

                    // ✅ 价格（保持原格式或添加¥）
                    const price = item.price || '价格未知';
                    const priceDisplay = price.startsWith('¥') || price.startsWith('￥') ? price : `¥${price}`;

                    // ✅ 想要数（可能为null）
                    const wantCount = item.want_count ? `<span style="color: #999; font-size: 12px;">${escapeHtml(item.want_count)}</span>` : '';

                    // ✅ 卖点/地区（可能为空）
                    const sellingPoints = item.selling_points || item.summary || '';
                    const sellingPointsDisplay = sellingPoints ? `<div style="color: #666; font-size: 12px; margin-top: 4px;">${escapeHtml(sellingPoints.length > 50 ? sellingPoints.substring(0, 50) + '…' : sellingPoints)}</div>` : '';

                    // ✅ 链接（新标签打开）
                    const itemUrl = item.item_url || '#';

                    return `
                        <div class="listing-card">
                            <div class="listing-info">
                                <div class="listing-title" title="${escapeHtml(title)}">
                                    <a href="${escapeHtml(itemUrl)}" target="_blank" style="color: inherit; text-decoration: none;">
                                        ${escapeHtml(titleShort)}
                                    </a>
                                </div>
                                <div class="listing-price" style="display: flex; align-items: center; gap: 8px;">
                                    ${priceDisplay}
                                    ${wantCount}
                                </div>
                                ${sellingPointsDisplay}
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    } catch (error) {
        resultArea.innerHTML = `<div style="color: var(--error);">搜索失败: ${error.message}</div>`;
    }
}

async function generateMarketingPlan() {
    const keyword = document.getElementById('marketing-keyword').value.trim();
    if (!keyword) {
        alert('请输入产品关键词');
        return;
    }

    const resultArea = document.getElementById('marketing-result');
    resultArea.innerHTML = '<div class="loading">生成中...</div>';

    try {
        const data = await api('/api/xianyu/marketing-plan', {
            method: 'POST',
            body: JSON.stringify({
                keyword,
                competitor_limit: 10
            })
        });

        resultArea.innerHTML = `
            <h3>引流方案</h3>
            <div style="margin-top: 16px; line-height: 1.8;">
                <pre style="white-space: pre-wrap; font-family: inherit;">${escapeHtml(data.plan_text || '生成失败')}</pre>
            </div>
        `;
    } catch (error) {
        resultArea.innerHTML = `<div style="color: var(--error);">生成失败: ${error.message}</div>`;
    }
}

async function analyzeProfitability() {
    const keyword = document.getElementById('profit-keyword').value.trim();
    if (!keyword) {
        alert('请输入分析关键词');
        return;
    }

    const resultArea = document.getElementById('profit-result');
    resultArea.innerHTML = '<div class="loading">分析中...</div>';

    try {
        const data = await api('/api/xianyu/profit-analysis', {
            method: 'POST',
            body: JSON.stringify({
                keyword,
                competitor_limit: 20
            })
        });

        resultArea.innerHTML = `
            <h3>利润分析</h3>
            <div style="margin-top: 16px; line-height: 1.8;">
                <pre style="white-space: pre-wrap; font-family: inherit;">${escapeHtml(data.analysis_text || '分析失败')}</pre>
            </div>
        `;
    } catch (error) {
        resultArea.innerHTML = `<div style="color: var(--error);">分析失败: ${error.message}</div>`;
    }
}

// ==================== 数据概览模块 ====================

async function loadOverview() {
    const container = document.getElementById('overview-container');
    container.innerHTML = '<div class="loading">加载中...</div>';

    try {
        const data = await api('/api/dashboard/overview');
        const metrics = data.metrics || {};

        container.innerHTML = `
            <div class="stat-card">
                <div class="stat-label">在售商品</div>
                <div class="stat-value">${metrics.on_sale_items || 0}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">今日消息</div>
                <div class="stat-value">${metrics.today_messages || 0}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">竞品记录</div>
                <div class="stat-value">${metrics.competitor_records || 0}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">生成方案</div>
                <div class="stat-value">${metrics.generated_artifacts || 0}</div>
            </div>
        `;
    } catch (error) {
        container.innerHTML = `<div class="placeholder-card"><h3>加载失败</h3><p class="placeholder-desc">${error.message}</p></div>`;
    }
}

// ==================== 设置模块 ====================

async function loadSettings() {
    // 加载登录状态
    try {
        const data = await api('/api/xianyu/status');
        const statusEl = document.getElementById('login-status');

        if (data.logged_in) {  // ← 使用单一真相源的logged_in
            statusEl.textContent = '已登录';
            statusEl.className = 'status-badge success';
        } else {
            statusEl.textContent = '未登录';
            statusEl.className = 'status-badge error';
        }
    } catch (error) {
        console.error('加载状态失败:', error);
    }

    // 加载LLM配置（只读显示）
    try {
        const config = await api('/api/admin/llm-config');
        if (config) {
            document.getElementById('llm-model-display').textContent = config.model || '未配置';
            document.getElementById('llm-base-url-display').textContent = config.base_url || '未配置';
            document.getElementById('llm-protocol-display').textContent = config.protocol || 'openai';
        }
    } catch (error) {
        console.error('加载LLM配置失败:', error);
        document.getElementById('llm-model-display').textContent = '加载失败';
        document.getElementById('llm-base-url-display').textContent = '加载失败';
        document.getElementById('llm-protocol-display').textContent = '加载失败';
    }
}

async function startLogin() {
    try {
        const data = await api('/api/xianyu/login/start', {
            method: 'POST',
            body: JSON.stringify({ max_wait_seconds: 300 })
        });

        alert('请在弹出的浏览器中扫码登录闲鱼');

        // 轮询登录状态
        const checkInterval = setInterval(async () => {
            try {
                const status = await api('/api/xianyu/login/status');
                // ✅ 修复：正确判断登录状态
                if (!status.running && status.status === 'success') {
                    clearInterval(checkInterval);
                    alert('登录成功！');
                    loadSettings();  // 刷新设置页，读取真相源的logged_in
                } else if (!status.running && status.status === 'failed') {
                    clearInterval(checkInterval);
                    alert('登录失败：' + (status.error || '未知错误'));
                }
            } catch (error) {
                clearInterval(checkInterval);
                alert('检查登录状态失败: ' + error.message);
            }
        }, 2000);
    } catch (error) {
        alert('启动登录失败: ' + error.message);
    }
}

// ==================== 工具函数 ====================

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ==================== 会话列表 ====================

async function loadConversations() {
    const container = document.getElementById('conversations-container');
    container.innerHTML = '<div class="loading">加载中...</div>';

    try {
        const data = await api('/api/xianyu/conversations');
        renderConversations(data);
    } catch (error) {
        container.innerHTML = `<div style="color: var(--error); padding: 20px;">加载失败: ${error.message}</div>`;
    }
}

function renderConversations(data) {
    const container = document.getElementById('conversations-container');
    const { status, results = [], detail } = data;

    if (status === 'not_logged_in') {
        container.innerHTML = `
            <div class="placeholder-card">
                <h3>未登录</h3>
                <p class="placeholder-desc">请先前往"设置"页面扫码登录闲鱼</p>
            </div>
        `;
        return;
    }

    if (status === 'error') {
        container.innerHTML = `
            <div class="placeholder-card">
                <h3>读取失败</h3>
                <p class="placeholder-desc">${escapeHtml(detail || '未知错误')}</p>
            </div>
        `;
        return;
    }

    if (results.length === 0) {
        container.innerHTML = `
            <div class="placeholder-card">
                <h3>暂无会话</h3>
                <p class="placeholder-desc">当前没有买家消息</p>
            </div>
        `;
        return;
    }

    // 按状态排序：pending_handoff > human_taking > open > resolved
    const statusPriority = {
        'pending_handoff': 0,
        'human_taking': 1,
        'open': 2,
        'bot': 2,
        'resolved': 3
    };

    const sortedResults = [...results].sort((a, b) => {
        const priorityA = statusPriority[a.status] ?? 2;
        const priorityB = statusPriority[b.status] ?? 2;
        if (priorityA !== priorityB) return priorityA - priorityB;
        // 同优先级按时间排序
        return new Date(b.updated_at || 0) - new Date(a.updated_at || 0);
    });

    // 渲染会话列表
    container.innerHTML = '';
    container.className = 'conversations-list';

    sortedResults.forEach(conv => {
        const item = document.createElement('div');
        item.className = conv.is_system ? 'conversation-item system-conversation' : 'conversation-item';

        // 买家昵称
        const buyerNick = conv.buyer_nick || conv.buyer_name || '未知买家';
        const systemTag = conv.is_system ? '<span class="system-tag">(系统)</span>' : '';

        // 会话状态徽章（新增）
        let conversationStatusBadge = '';
        const convStatus = conv.status || 'open';
        if (convStatus === 'pending_handoff') {
            conversationStatusBadge = '<span class="status-badge badge-orange">🔴 待人工</span>';
        } else if (convStatus === 'human_taking') {
            conversationStatusBadge = '<span class="status-badge badge-blue">👤 人工中</span>';
        } else if (convStatus === 'resolved') {
            conversationStatusBadge = '<span class="status-badge badge-green">✓ 已解决</span>';
        }

        // 订单状态标签（保留原有逻辑）
        let orderStatusBadge = '';
        if (conv.order_status) {
            const isWait = conv.order_status.includes('等待');
            const isSuccess = conv.order_status.includes('成功');
            const badgeClass = isWait ? 'badge-orange' : (isSuccess ? 'badge-green' : 'badge-gray');
            orderStatusBadge = `<span class="status-badge ${badgeClass}">${escapeHtml(conv.order_status)}</span>`;
        }

        // 最后消息
        const lastMessage = conv.last_message || '暂无消息';

        // 时间
        const timeStr = conv.time || '';

        item.innerHTML = `
            <div class="conversation-header">
                <div class="conversation-info">
                    ${conv.avatar_url ? `<img src="${escapeHtml(conv.avatar_url)}" class="buyer-avatar" alt="头像">` : '<div class="buyer-avatar-placeholder">👤</div>'}
                    <div class="buyer-details">
                        <div class="buyer-name">${escapeHtml(buyerNick)} ${systemTag}</div>
                        <div class="badge-group">
                            ${conversationStatusBadge}
                            ${orderStatusBadge}
                        </div>
                    </div>
                </div>
                ${conv.product_image ? `<img src="${escapeHtml(conv.product_image)}" class="product-thumb" alt="商品">` : ''}
            </div>
            <div class="conversation-message">${escapeHtml(lastMessage)}</div>
            <div class="conversation-footer">
                <span class="conversation-time">${escapeHtml(timeStr)}</span>
                <div class="conversation-actions">
                    ${!conv.is_system ? '<button class="btn-view-chat btn-sm">查看对话</button>' : ''}
                    ${convStatus === 'pending_handoff' ? '<button class="btn-handoff btn-sm btn-primary">接手</button>' : ''}
                    ${convStatus === 'human_taking' ? '<button class="btn-resolve btn-sm btn-success">已解决</button>' : ''}
                    ${(convStatus === 'human_taking' || convStatus === 'resolved') ? '<button class="btn-return-bot btn-sm btn-secondary">交回机器人</button>' : ''}
                </div>
            </div>
        `;

        // 查看对话
        if (!conv.is_system) {
            const btnView = item.querySelector('.btn-view-chat');
            if (btnView) {
                btnView.addEventListener('click', (e) => {
                    e.stopPropagation();
                    viewConversationMessages(conv.conversation_id, buyerNick);
                });
            }
        }

        // 接手按钮
        const btnHandoff = item.querySelector('.btn-handoff');
        if (btnHandoff) {
            btnHandoff.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm(`确认接手会话 ${buyerNick} ？`)) return;

                try {
                    await api(`/api/xianyu/conversations/${conv.conversation_id}/handoff`, {
                        method: 'POST'
                    });
                    alert('接手成功');
                    loadConversations(); // 刷新列表
                } catch (error) {
                    alert(`接手失败: ${error.message}`);
                }
            });
        }

        // 已解决按钮
        const btnResolve = item.querySelector('.btn-resolve');
        if (btnResolve) {
            btnResolve.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm(`确认标记会话 ${buyerNick} 为已解决？`)) return;

                try {
                    await api(`/api/xianyu/conversations/${conv.conversation_id}/resolve`, {
                        method: 'POST'
                    });
                    alert('标记成功');
                    loadConversations(); // 刷新列表
                } catch (error) {
                    alert(`标记失败: ${error.message}`);
                }
            });
        }

        // 交回机器人按钮
        const btnReturnBot = item.querySelector('.btn-return-bot');
        if (btnReturnBot) {
            btnReturnBot.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm(`确认将会话 ${buyerNick} 交回机器人？`)) return;

                try {
                    await api(`/api/xianyu/conversations/${conv.conversation_id}/return-to-bot`, {
                        method: 'POST'
                    });
                    alert('交回成功，机器人将处理后续消息');
                    loadConversations(); // 刷新列表
                } catch (error) {
                    alert(`交回失败: ${error.message}`);
                }
            });
        }

        container.appendChild(item);
    });
}

// ==================== 初始化 ====================

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initResearchTabs();

    // 默认加载消息模块（占位）
    switchModule('messages');
});

// ==================== 查看对话 ====================

async function viewConversationMessages(conversationId, buyerNick) {
    const container = document.getElementById('conversations-container');
    container.innerHTML = '<div class="loading">加载对话中...</div>';

    try {
        const data = await api(`/api/xianyu/conversations/${conversationId}/messages`);
        renderConversationMessages(data, buyerNick);
    } catch (error) {
        container.innerHTML = `
            <div style="color: var(--error); padding: 20px;">
                加载对话失败: ${error.message}
                <br><br>
                <button onclick="loadConversations()" class="btn-primary">返回列表</button>
            </div>
        `;
    }
}

function renderConversationMessages(data, buyerNick) {
    const container = document.getElementById('conversations-container');
    const { status, messages = [], conversation_id } = data;

    if (status !== 'success') {
        container.innerHTML = `
            <div style="color: var(--error); padding: 20px;">
                加载失败
                <br><br>
                <button onclick="loadConversations()" class="btn-primary">返回列表</button>
            </div>
        `;
        return;
    }

    // 标题栏
    let html = `
        <div style="padding: 20px; border-bottom: 1px solid var(--border);">
            <button onclick="loadConversations()" class="btn-sm" style="margin-bottom: 10px;">← 返回列表</button>
            <h3 style="margin: 10px 0;">与 ${escapeHtml(buyerNick)} 的对话</h3>
            <p style="color: var(--text-muted); font-size: 14px;">会话ID: ${escapeHtml(conversation_id)}</p>
        </div>
        <div style="padding: 20px; max-height: 600px; overflow-y: auto;">
    `;

    if (messages.length === 0) {
        html += `<div style="text-align: center; color: var(--text-muted); padding: 40px;">暂无已存消息</div>`;
    } else {
        messages.forEach(msg => {
            const isBuyer = msg.direction === 'buyer';
            const alignClass = isBuyer ? 'message-left' : 'message-right';
            const bgColor = isBuyer ? '#f0f0f0' : '#e3f2fd';
            const label = isBuyer ? '买家' : '卖家';

            // 解析 draft_reply（可能包含 holding 话术）
            let draftReply = '';
            if (msg.draft_reply) {
                try {
                    const draftData = JSON.parse(msg.draft_reply);
                    if (draftData.type === 'handoff' && draftData.suggested_reply) {
                        draftReply = `
                            <div style="margin-top: 10px; padding: 10px; background: #fff3cd; border-left: 3px solid #ff9800; font-size: 13px;">
                                <strong>💬 建议 Holding 话术：</strong><br>
                                "${escapeHtml(draftData.suggested_reply)}"<br>
                                <span style="color: #666; font-size: 12px;">⚠️ 请手动复制发送</span><br>
                                <span style="color: #999; font-size: 11px;">原因: ${draftData.reason} (分数: ${draftData.confidence_score?.toFixed(4) || 'N/A'})</span>
                            </div>
                        `;
                    }
                } catch (e) {
                    // 非 JSON 格式，当作普通 draft
                    draftReply = `
                        <div style="margin-top: 10px; padding: 8px; background: #f9f9f9; border-left: 2px solid #ccc; font-size: 13px;">
                            <strong>草稿:</strong> ${escapeHtml(msg.draft_reply.substring(0, 100))}...
                        </div>
                    `;
                }
            }

            html += `
                <div class="${alignClass}" style="margin-bottom: 15px;">
                    <div style="display: inline-block; max-width: 70%; padding: 10px 15px; background: ${bgColor}; border-radius: 10px;">
                        <div style="font-size: 12px; color: #666; margin-bottom: 5px;">
                            ${label} · ${escapeHtml(msg.created_at || '')}
                        </div>
                        <div>${escapeHtml(msg.content)}</div>
                        ${draftReply}
                    </div>
                </div>
            `;
        });
    }

    html += '</div>';

    container.innerHTML = html;
}

// ==================== 自动客服 ====================

let autoReplyRefreshTimer = null;

async function toggleAutoReply(enabled) {
    const toggle = document.getElementById('auto-reply-toggle');
    try {
        const result = await api(enabled ? '/api/admin/auto-reply/start' : '/api/admin/auto-reply/stop', { method: 'POST' });
        if (result.status === 'ok') {
            updateAutoReplyUI(enabled);
            if (enabled) startAutoReplyRefresh();
            else stopAutoReplyRefresh();
        } else {
            alert('操作失败: ' + (result.detail || JSON.stringify(result)));
            toggle.checked = !enabled;
        }
    } catch (e) {
        alert('请求失败: ' + e.message);
        toggle.checked = !enabled;
    }
}

function updateAutoReplyUI(enabled) {
    const toggle = document.getElementById('auto-reply-toggle');
    const track = document.getElementById('toggle-track');
    const thumb = document.getElementById('toggle-thumb');
    const statusEl = document.getElementById('auto-reply-status');

    toggle.checked = enabled;
    if (enabled) {
        track.style.background = '#4ade80';
        thumb.style.left = '25px';
        statusEl.textContent = '运行中';
        statusEl.style.color = '#4ade80';
    } else {
        track.style.background = '#555';
        thumb.style.left = '3px';
    }
}

async function refreshAutoReplyStatus() {
    try {
        const result = await api('/api/admin/auto-reply/status');
        const statusEl = document.getElementById('auto-reply-status');
        const lastScanEl = document.getElementById('auto-reply-last-scan');
        const banner = document.getElementById('need-login-banner');
        const alertText = document.getElementById('alert-text');
        const releaseBtn = document.getElementById('release-lock-btn');
        const toggle = document.getElementById('auto-reply-toggle');

        const status = result.status || 'stopped';
        const enabled = result.enabled || false;

        // 更新状态文字
        if (status === 'need_login') {
            statusEl.textContent = '需扫码登录';
            statusEl.style.color = '#ff6666';
            alertText.textContent = '闲鱼登录已失效，请在浏览器窗口扫码';
            releaseBtn.style.display = 'none';
            banner.style.display = 'flex';
            updateAutoReplyUI(false);
            stopAutoReplyRefresh();
        } else if (status === 'profile_locked') {
            statusEl.textContent = '浏览器被占用';
            statusEl.style.color = '#ff6666';
            alertText.textContent = '浏览器 profile 被旧进程占用，请点击"释放浏览器锁"一键恢复';
            releaseBtn.style.display = 'inline-block';
            banner.style.display = 'flex';
            updateAutoReplyUI(false);
            stopAutoReplyRefresh();
        } else if (status === 'running') {
            statusEl.textContent = '运行中';
            statusEl.style.color = '#4ade80';
            banner.style.display = 'none';
            updateAutoReplyUI(true);
        } else {
            statusEl.textContent = '已停止';
            statusEl.style.color = 'var(--text-secondary, #aaa)';
            banner.style.display = 'none';
            updateAutoReplyUI(false);
        }

        // 更新上次扫描时间
        if (result.last_scan) {
            lastScanEl.textContent = '上次扫描: ' + result.last_scan.substring(11, 19);
        }

        // 同步 toggle 状态
        toggle.checked = enabled && status === 'running';

    } catch (e) {
        console.error('refreshAutoReplyStatus error:', e);
    }
}

async function refreshAutoReplyFeed() {
    try {
        const result = await api('/api/admin/auto-reply/feed?limit=20');
        const feedEl = document.getElementById('auto-reply-feed');
        const messages = result.messages || [];

        if (messages.length === 0) {
            feedEl.innerHTML = '<div style="color:var(--text-muted, #666); text-align:center; padding:12px;">开启自动客服后，处理记录会出现在这里</div>';
            return;
        }

        let html = '';
        messages.forEach(m => {
            const actionLabel = m.action === 'reply' ? '自动回复' : (m.action === 'handoff' ? '转人工' : m.action);
            const actionColor = m.action === 'reply' ? '#4ade80' : '#f59e0b';
            const time = m.timestamp ? m.timestamp.substring(11, 19) : '';

            html += `
                <div style="padding:10px; border-bottom:1px solid var(--border-color, #333);">
                    <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
                        <span style="font-weight:600;">${escapeHtml(m.buyer_name || '买家')}</span>
                        <span style="font-size:12px; color:var(--text-muted, #666);">${time}</span>
                    </div>
                    <div style="font-size:13px; margin-bottom:6px;">
                        <span style="color:var(--text-secondary, #aaa);">收到:</span>
                        ${escapeHtml(m.buyer_msg || '')}
                    </div>
                    <div style="display:flex; gap:8px; align-items:center;">
                        <span style="padding:2px 8px; background:${actionColor}20; color:${actionColor}; border-radius:4px; font-size:12px;">
                            ${actionLabel}
                        </span>
                        ${m.score ? `<span style="font-size:11px; color:var(--text-muted, #666);">score: ${m.score}</span>` : ''}
                        ${m.reason ? `<span style="font-size:11px; color:var(--text-muted, #666);">${escapeHtml(m.reason)}</span>` : ''}
                    </div>
                    ${m.reply ? `<div style="margin-top:6px; padding:8px; background:var(--bg-primary, #252536); border-radius:6px; font-size:13px;">${escapeHtml(m.reply)}</div>` : ''}
                </div>
            `;
        });

        feedEl.innerHTML = html;
    } catch (e) {
        console.error('refreshAutoReplyFeed error:', e);
    }
}

function startAutoReplyRefresh() {
    refreshAutoReplyStatus();
    refreshAutoReplyFeed();
    loadPendingHandoffs();
    if (autoReplyRefreshTimer) clearInterval(autoReplyRefreshTimer);
    autoReplyRefreshTimer = setInterval(() => {
        refreshAutoReplyStatus();
        refreshAutoReplyFeed();
        loadPendingHandoffs();
    }, 3000);
}

function stopAutoReplyRefresh() {
    if (autoReplyRefreshTimer) {
        clearInterval(autoReplyRefreshTimer);
        autoReplyRefreshTimer = null;
    }
}

// ==================== 转人工接管区 ====================

async function loadPendingHandoffs() {
    try {
        const result = await api('/api/admin/auto-reply/handoffs');
        const listEl = document.getElementById('handoff-list');
        const handoffs = result.handoffs || [];

        if (handoffs.length === 0) {
            listEl.innerHTML = '<div style="color:var(--text-muted,#666); text-align:center; padding:20px;">机器人搞不定的会话排在这里</div>';
            return;
        }

        let html = '';
        handoffs.forEach(h => {
            const time = h.updated_at ? h.updated_at.substring(11, 19) : '';
            const reasonLabel = {
                'sensitive_intent': '碰钱/敏感',
                'retrieval_gray': '答不准',
                'retrieval_not_found': '没命中',
                'llm_generation_failed': '生成失败',
                'send_failed:uncertain': '发送不确定',
                'send_failed:failed': '发送失败',
            }[h.reason] || h.reason || '其他';

            html += `
                <div style="padding:10px; border-bottom:1px solid var(--border-color,#333);">
                    <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                        <span style="font-weight:600; font-size:13px;">${escapeHtml(h.buyer_name)}</span>
                        <span style="font-size:11px; color:var(--text-muted,#666);">${time}</span>
                    </div>
                    <div style="font-size:12px; color:#f59e0b; margin-bottom:4px;">
                        原因: ${escapeHtml(reasonLabel)}
                    </div>
                    <div style="font-size:13px; color:var(--text-secondary,#aaa); margin-bottom:8px; padding:6px 8px; background:var(--bg-primary,#252536); border-radius:4px;">
                        ${escapeHtml(h.last_msg)}
                    </div>
                    <button onclick="markHandoffResolved('${escapeHtml(h.conversation_id)}')" style="background:#4ade80; color:#000; border:none; border-radius:4px; padding:4px 12px; font-size:12px; cursor:pointer; width:100%;">
                        已处理（标记解决）
                    </button>
                </div>
            `;
        });

        listEl.innerHTML = html;
    } catch (e) {
        console.error('loadPendingHandoffs error:', e);
    }
}

async function markHandoffResolved(conversationId) {
    try {
        const result = await api('/api/admin/auto-reply/handoff/resolve?conversation_id=' + encodeURIComponent(conversationId), { method: 'POST' });
        if (result.status === 'ok') {
            loadPendingHandoffs();
        } else {
            alert('操作失败: ' + (result.detail || JSON.stringify(result)));
        }
    } catch (e) {
        alert('请求失败: ' + e.message);
    }
}

// ==================== 浏览器会话管理 ====================

async function releaseBrowserLock() {
    const btn = document.getElementById('release-lock-btn');
    btn.disabled = true;
    btn.textContent = '释放中...';

    try {
        const result = await api('/api/admin/browser/release-lock', { method: 'POST' });
        if (result.status === 'released' || result.status === 'ok') {
            const killed = result.killed_pids || [];
            const removed = result.removed_locks || [];
            alert(`浏览器锁已释放！\n已终止进程: ${killed.join(', ') || '无'}\n已清理锁文件: ${removed.length} 个\n\n请刷新页面后重新开启自动客服。`);
            setTimeout(() => {
                refreshAutoReplyStatus();
                refreshAutoReplyFeed();
            }, 1000);
        } else if (result.status === 'still_locked') {
            const remaining = result.remaining_pids || [];
            alert(`仍有进程占用 profile: PIDs ${remaining.join(', ')}\n请手动关闭这些 Chrome 进程后重试。`);
        } else {
            alert('释放失败: ' + (result.detail || JSON.stringify(result)));
        }
    } catch (e) {
        alert('请求失败: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '释放浏览器锁';
    }
}

// 页面加载时初始化自动客服状态
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        refreshAutoReplyStatus();
        refreshAutoReplyFeed();
        loadPendingHandoffs();
    }, 500);
});
