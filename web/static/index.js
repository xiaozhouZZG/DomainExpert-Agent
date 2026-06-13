// 全局状态
let currentSessionId = null;
let currentUserId = 'default_user';
let isLoading = false;
let hasStartedChat = false; // 是否已开始对话（用于控制快捷选项显隐）

// 快捷选项配置
const QUICK_OPTIONS = [
    '退款/退货政策',
    '订单查询',
    '配送/物流时效',
    '商品咨询',
    '转人工客服'
];

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    setupInputHandlers();
    loadGreeting(); // 页面加载时自动获取问候语
});

// 加载问候语
async function loadGreeting() {
    const container = document.getElementById('messagesContainer');

    // 显示加载态
    container.innerHTML = `
        <div class="message assistant loading-message">
            <div class="message-avatar">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83"/>
                </svg>
            </div>
            <div class="message-content">
                <div class="typing-indicator">
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                </div>
            </div>
        </div>
    `;

    try {
        const res = await fetch('/api/greeting');
        const data = await res.json();

        // 移除加载态，显示问候语 + 快捷选项
        container.innerHTML = '';
        appendAssistantMessage(data.greeting, null, true); // true 表示显示快捷选项

    } catch (e) {
        console.error('加载问候语失败:', e);
        // 兜底：直接显示固定问候
        container.innerHTML = '';
        appendAssistantMessage('您好，我是智能客服，很高兴为您服务~ 请问有什么可以帮您？', null, true);
    }
}

// 发送消息
async function sendMessage(messageText) {
    const input = document.getElementById('messageInput');
    const message = messageText || input.value.trim();

    if (!message || isLoading) return;

    isLoading = true;
    if (!messageText) {
        input.value = '';
        input.style.height = 'auto';
    }
    document.getElementById('sendBtn').disabled = true;

    // 标记已开始对话（隐藏快捷选项）
    if (!hasStartedChat) {
        hasStartedChat = true;
        hideQuickOptions();
    }

    // 显示用户消息
    appendUserMessage(message);

    // 显示加载状态
    appendLoadingMessage();

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                session_id: currentSessionId,
                user_id: currentUserId
            })
        });

        const data = await res.json();

        // 更新当前会话ID
        currentSessionId = data.session_id;

        // 移除加载状态
        removeLoadingMessage();

        // 显示AI回复
        appendAssistantMessage(data.content, data.agent, false);

    } catch (e) {
        console.error('发送消息失败:', e);
        removeLoadingMessage();
        appendAssistantMessage('抱歉，服务暂时不可用，请稍后再试。', null, false);
    } finally {
        isLoading = false;
        document.getElementById('sendBtn').disabled = false;
        if (!messageText) input.focus();
    }
}

// 快捷选项点击
function onQuickOptionClick(optionText) {
    sendMessage(optionText);
}

// 隐藏快捷选项
function hideQuickOptions() {
    const quickOptions = document.querySelector('.quick-options');
    if (quickOptions) {
        quickOptions.style.display = 'none';
    }
}

// 添加用户消息到界面
function appendUserMessage(content) {
    const container = document.getElementById('messagesContainer');

    const div = document.createElement('div');
    div.className = 'message user';
    div.innerHTML = `<div class="message-content">${escapeHtml(content)}</div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// 添加AI消息到界面
function appendAssistantMessage(content, agent, showQuickOptions) {
    const container = document.getElementById('messagesContainer');

    const div = document.createElement('div');
    div.className = 'message assistant';

    let html = `
        <div class="message-avatar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z"/>
                <path d="M12 6v6l4 2"/>
            </svg>
        </div>
        <div class="message-content">
            ${agent ? `<div class="agent-tag">
                <svg class="icon-sm" viewBox="0 0 24 24">
                    <circle cx="12" cy="12" r="10" fill="currentColor" opacity="0.2"/>
                    <circle cx="12" cy="12" r="3" fill="currentColor"/>
                </svg>
                ${getAgentName(agent)}
            </div>` : ''}
            <div class="message-text">${formatMarkdown(content)}</div>
    `;

    // 如果需要显示快捷选项（仅问候语时）
    if (showQuickOptions && !hasStartedChat) {
        html += '<div class="quick-options">';
        QUICK_OPTIONS.forEach(option => {
            html += `<button class="quick-option-chip" onclick="onQuickOptionClick('${escapeHtml(option)}')">${escapeHtml(option)}</button>`;
        });
        html += '</div>';
    }

    html += '</div>'; // 关闭 message-content

    div.innerHTML = html;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// 添加加载状态
function appendLoadingMessage() {
    const container = document.getElementById('messagesContainer');

    const div = document.createElement('div');
    div.className = 'message assistant loading-message';
    div.innerHTML = `
        <div class="message-avatar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z"/>
                <path d="M12 6v6l4 2"/>
            </svg>
        </div>
        <div class="message-content">
            <div class="typing-indicator">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
        </div>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// 移除加载状态
function removeLoadingMessage() {
    const loading = document.querySelector('.loading-message');
    if (loading) loading.remove();
}

// 输入框自适应高度
function setupInputHandlers() {
    const input = document.getElementById('messageInput');

    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 200) + 'px';
    });

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
}

// 获取Agent中文名
function getAgentName(agent) {
    const names = {
        'customer_service': '客服专员',
        'order': '订单专员',
        'data_analyst': '数据分析师',
        'report': '报表专员',
        'system': '系统'
    };
    return names[agent] || agent;
}

// HTML转义
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 简单的Markdown渲染
function formatMarkdown(text) {
    return escapeHtml(text)
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        .replace(/`(.*?)`/g, '<code style="background:#f3f4f6;padding:2px 6px;border-radius:4px;font-size:13px;">$1</code>')
        .replace(/\n/g, '<br>');
}
