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

    // 加载LLM配置
    try {
        const config = await api('/api/admin/llm-config');
        if (config) {
            document.getElementById('llm-base-url').value = config.base_url || '';
            document.getElementById('llm-api-key').value = config.api_key ? '••••••••' : '';
            document.getElementById('llm-model').value = config.model || '';
        }
    } catch (error) {
        console.error('加载LLM配置失败:', error);
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

async function saveLLMConfig() {
    const baseUrl = document.getElementById('llm-base-url').value.trim();
    const apiKey = document.getElementById('llm-api-key').value.trim();
    const model = document.getElementById('llm-model').value.trim();

    if (!baseUrl || !apiKey || !model) {
        alert('请填写完整的LLM配置');
        return;
    }

    try {
        await api('/api/admin/llm-config', {
            method: 'POST',
            body: JSON.stringify({
                protocol: 'openai',
                base_url: baseUrl,
                api_key: apiKey,
                model: model
            })
        });
        alert('配置已保存');
    } catch (error) {
        alert('保存失败: ' + error.message);
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

    // 渲染会话列表
    container.innerHTML = '';
    container.className = 'conversations-list';

    results.forEach(conv => {
        const item = document.createElement('div');
        item.className = conv.is_system ? 'conversation-item system-conversation' : 'conversation-item';

        // 买家昵称
        const buyerNick = conv.buyer_nick || '未知买家';
        const systemTag = conv.is_system ? '<span class="system-tag">(系统)</span>' : '';

        // 订单状态标签
        let statusBadge = '';
        if (conv.order_status) {
            const isWait = conv.order_status.includes('等待');
            const isSuccess = conv.order_status.includes('成功');
            const badgeClass = isWait ? 'badge-orange' : (isSuccess ? 'badge-green' : 'badge-gray');
            statusBadge = `<span class="status-badge ${badgeClass}">${escapeHtml(conv.order_status)}</span>`;
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
                        ${statusBadge}
                    </div>
                </div>
                ${conv.product_image ? `<img src="${escapeHtml(conv.product_image)}" class="product-thumb" alt="商品">` : ''}
            </div>
            <div class="conversation-message">${escapeHtml(lastMessage)}</div>
            <div class="conversation-footer">
                <span class="conversation-time">${escapeHtml(timeStr)}</span>
                ${!conv.is_system ? '<button class="btn-view-chat">查看对话</button>' : ''}
            </div>
        `;

        // 点击查看对话（待实现）
        if (!conv.is_system) {
            const btn = item.querySelector('.btn-view-chat');
            if (btn) {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    alert(`会话 ${buyerNick} 的消息读取功能待实现（需要先dump消息气泡结构）`);
                });
            }
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
