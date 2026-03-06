/**
 * Zipper Dashboard Frontend
 */

let currentConversationId = null;
let currentMessageElement = null;   // streaming text node
let currentAssistantContent = null; // .content div of the active assistant bubble
let ws = null;
let USER_NAME = 'You';

// Token streaming smooth animation
let tokenBuffer = '';
let tokenAnimationFrame = null;
const TOKEN_BATCH_DELAY = 16; // ~60fps

// Configure marked.js
if (typeof marked !== 'undefined') {
    marked.setOptions({ breaks: true, gfm: true });
}

// Render all unrendered .markdown-body elements
function renderMarkdown() {
    document.querySelectorAll('.markdown-body:not([data-rendered])').forEach(el => {
        const raw = el.textContent;
        if (!raw.trim()) return;
        try {
            el.innerHTML = marked.parse(raw);
            el.dataset.rendered = '1';
            el.querySelectorAll('pre code').forEach(block => {
                try { hljs.highlightElement(block); } catch (e) {}
            });
        } catch (e) {
            console.error('markdown render error', e);
        }
    });
}

// Escape HTML for safe insertion
function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

// Scroll chat to bottom
function scrollToBottom() {
    const el = document.getElementById('chat-messages');
    if (el) setTimeout(() => { el.scrollTop = el.scrollHeight; }, 0);
}

// Build an assistant message wrapper (avatar + label)
function createAssistantWrapper(time) {
    const wrapper = document.createElement('div');
    wrapper.className = 'msg-assistant';
    wrapper.innerHTML = `
        <div class="avatar-sm">Z</div>
        <div class="msg-assistant-body">
            <div class="msg-label">Zipper${time ? ' · ' + escapeHtml(time) : ''}</div>
            <div class="content msg-content"></div>
        </div>`;
    return wrapper;
}

// Ensure an assistant wrapper exists for streaming, return its .content div
function ensureAssistantContent() {
    if (!currentAssistantContent) {
        hideTyping();
        const messages = document.getElementById('chat-messages');
        // Clear empty-state placeholder
        const placeholder = messages?.querySelector('[style*="color:var(--tx-3)"]');
        if (placeholder && messages.children.length === 1) placeholder.remove();
        const wrapper = createAssistantWrapper('');
        wrapper.id = 'streaming-message-wrapper';
        messages?.appendChild(wrapper);
        currentAssistantContent = wrapper.querySelector('.content');
    }
    return currentAssistantContent;
}

// Append a streaming text token (buffered for smooth animation)
function appendToken(token) {
    const content = ensureAssistantContent();
    if (!currentMessageElement) {
        const textNode = document.createElement('div');
        textNode.className = 'streaming-text';
        content.appendChild(textNode);
        currentMessageElement = textNode;
    }
    
    // Buffer tokens and flush on animation frame
    tokenBuffer += token;
    
    if (tokenAnimationFrame === null) {
        tokenAnimationFrame = requestAnimationFrame(() => {
            if (tokenBuffer && currentMessageElement) {
                currentMessageElement.textContent += tokenBuffer;
                tokenBuffer = '';
            }
            scrollToBottom();
            tokenAnimationFrame = null;
        });
    }
}

// Inject a tool block HTML into the current assistant message
function injectToolHtml(html) {
    const content = ensureAssistantContent();
    // Seal the current text node — next text tokens start a new one
    currentMessageElement = null;
    const div = document.createElement('div');
    div.innerHTML = html;
    content.appendChild(div.firstElementChild ?? div);
    scrollToBottom();
}

// Show a small popup near a rating button
let _ratingPopupTimer = null;
function showRatingPopup(btn) {
    const popup = document.getElementById('rating-popup');
    if (!popup) return;
    clearTimeout(_ratingPopupTimer);
    popup.textContent = `${btn.dataset.label}: ${btn.dataset.value}/5`;
    const rect = btn.getBoundingClientRect();
    popup.style.display = 'block';
    // Position above the button, centered
    const pw = popup.offsetWidth;
    popup.style.left = Math.max(4, rect.left + rect.width / 2 - pw / 2) + 'px';
    popup.style.top = (rect.top - popup.offsetHeight - 6) + 'px';
    _ratingPopupTimer = setTimeout(() => { popup.style.display = 'none'; }, 1800);
}

// Fill in the output section of a pending tool block
function fillToolResult(toolUseId, resultText) {
    if (!currentAssistantContent) return;
    const pre = currentAssistantContent.querySelector(`[data-result-for="${CSS.escape(toolUseId)}"]`);
    if (pre) {
        pre.textContent = resultText;
        pre.classList.remove('tool-code-pending');
    }
    scrollToBottom();
}

// Add a complete message bubble
function addMessage(role, content) {
    const messages = document.getElementById('chat-messages');
    if (!messages) return;

    // Clear empty-state placeholder if present
    const placeholder = messages.querySelector('[style*="color:var(--tx-3)"]');
    if (placeholder && messages.children.length === 1) placeholder.remove();

    const wrapper = document.createElement('div');
    const text = typeof content === 'object' ? JSON.stringify(content, null, 2) : String(content);

    if (role === 'user') {
        wrapper.className = 'msg-user';
        wrapper.innerHTML = `
            <div class="msg-user-inner">
                <div class="bubble-user">
                    <div class="markdown-body">${escapeHtml(text)}</div>
                </div>
                <div class="msg-meta">${escapeHtml(USER_NAME)}</div>
            </div>`;
    } else {
        const asst = createAssistantWrapper('');
        const contentEl = asst.querySelector('.content');
        const mdDiv = document.createElement('div');
        mdDiv.className = 'markdown-body text-sm';
        mdDiv.textContent = text;
        contentEl.appendChild(mdDiv);
        messages.appendChild(asst);
        renderMarkdown();
        scrollToBottom();
        return;
    }

    messages.appendChild(wrapper);
    renderMarkdown();
    scrollToBottom();
}

// Show typing indicator
function showTyping() {
    if (document.getElementById('typing-indicator')) return;
    const el = document.createElement('div');
    el.id = 'typing-indicator';
    el.className = 'msg-assistant';
    el.innerHTML = `
        <div class="avatar-sm">Z</div>
        <div class="msg-assistant-body">
            <div class="msg-label">Zipper</div>
            <div style="display:flex;gap:4px;align-items:center;height:18px;">
                <div style="width:5px;height:5px;border-radius:50%;background:var(--tx-3);" class="animate-bounce bounce-1"></div>
                <div style="width:5px;height:5px;border-radius:50%;background:var(--tx-3);" class="animate-bounce bounce-2"></div>
                <div style="width:5px;height:5px;border-radius:50%;background:var(--tx-3);" class="animate-bounce bounce-3"></div>
            </div>
        </div>`;
    document.getElementById('chat-messages')?.appendChild(el);
    scrollToBottom();
}

function hideTyping() {
    document.getElementById('typing-indicator')?.remove();
}

// Enable/disable the input
function disableInput() {
    const ta = document.getElementById('chat-input');
    const btn = document.querySelector('#chat-form button[type="submit"]');
    if (ta) ta.disabled = true;
    if (btn) btn.disabled = true;
    document.getElementById('conv-status')?.classList.add('visible');
}

function enableInput() {
    const ta = document.getElementById('chat-input');
    const btn = document.querySelector('#chat-form button[type="submit"]');
    if (ta) { ta.disabled = false; ta.focus(); }
    if (btn) btn.disabled = false;
    document.getElementById('conv-status')?.classList.remove('visible');
}

// WebSocket setup
function setupWebSocket(conversationId) {
    if (ws) {
        ws.onmessage = null;
        ws.onclose = null;
        ws.close();
        ws = null;
    }
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/conversations/${conversationId}`);
    ws.addEventListener('message', handleWebSocketMessage);
    ws.addEventListener('close', () => { ws = null; });
    ws.addEventListener('error', () => { ws = null; });
}

// Handle incoming WebSocket messages
function handleWebSocketMessage(event) {
    const msg = JSON.parse(event.data);

    switch (msg.type) {
        case 'token':
            appendToken(msg.text || '');
            break;

        case 'tool_call_html':
            injectToolHtml(msg.html || '');
            break;

        case 'tool_result_data':
            fillToolResult(msg.tool_use_id || '', msg.result || '');
            break;

        case 'done':
            // Flush any remaining buffered tokens
            if (tokenAnimationFrame !== null) {
                cancelAnimationFrame(tokenAnimationFrame);
                if (tokenBuffer && currentMessageElement) {
                    currentMessageElement.textContent += tokenBuffer;
                    tokenBuffer = '';
                }
                tokenAnimationFrame = null;
            }
            hideTyping();
            // Render markdown on streamed text nodes, stripping any ratings tag
            if (currentAssistantContent) {
                currentAssistantContent.querySelectorAll('.streaming-text').forEach(el => {
                    let raw = el.textContent;
                    if (!raw.trim()) { el.remove(); return; }
                    // Extract and replace ratings tag
                    const ratingMatch = raw.match(/\{\{c:(\d),\s*d:(\d),\s*a:(\d)\}\}/);
                    let ratingHtml = '';
                    if (ratingMatch) {
                        raw = raw.replace(ratingMatch[0], '').trim();
                        const [, c, d, a] = ratingMatch;
                        ratingHtml = `<div class="ratings-bar">`
                            + `<button class="rating-btn" data-label="Complexity" data-value="${c}" onclick="showRatingPopup(this)">C</button>`
                            + `<button class="rating-btn" data-label="Difficulty" data-value="${d}" onclick="showRatingPopup(this)">D</button>`
                            + `<button class="rating-btn" data-label="Ambiguity" data-value="${a}" onclick="showRatingPopup(this)">A</button>`
                            + `</div>`;
                    }
                    el.innerHTML = marked.parse(raw) + ratingHtml;
                    el.classList.add('markdown-body');
                    el.classList.remove('streaming-text', 'whitespace-pre-wrap');
                    el.dataset.rendered = '1';
                    el.querySelectorAll('pre code').forEach(b => {
                        try { hljs.highlightElement(b); } catch(e) {}
                    });
                });
            }
            currentAssistantContent = null;
            currentMessageElement = null;
            // Update top bar title/status from server
            if (msg.title) {
                const titleEl = document.getElementById('conv-title');
                if (titleEl) titleEl.textContent = msg.title;
                // Update sidebar item too
                markActiveSidebarItem(currentConversationId);
            }
            document.getElementById('conv-status')?.classList.remove('visible');
            enableInput();
            scrollToBottom();
            if (currentConversationId) updateContextMeter(currentConversationId);
            break;

        case 'error':
            hideTyping();
            document.getElementById('conv-status')?.classList.remove('visible');
            currentAssistantContent = null;
            currentMessageElement = null;
            const errDiv = document.createElement('div');
            errDiv.className = 'msg-error';
            errDiv.innerHTML = `
                <div class="msg-error-icon">!</div>
                <div class="msg-assistant-body" style="color:#fca5a5;font-size:0.9rem;padding-top:2px;">${escapeHtml(msg.message || 'An error occurred')}</div>`;
            document.getElementById('chat-messages')?.appendChild(errDiv);
            enableInput();
            break;
    }
}

// Update the context length meter in the topbar
async function updateContextMeter(conversationId) {
    try {
        const r = await fetch(`/api/conversations/${conversationId}/context-length`);
        if (!r.ok) return;
        const d = await r.json();

        const meter = document.getElementById('ctx-meter');
        const fill  = document.getElementById('ctx-bar-fill');
        const label = document.getElementById('ctx-label');
        if (!meter || !fill || !label) return;

        const pct = Math.min(d.percent, 100);
        fill.style.width = pct + '%';
        fill.style.backgroundColor = pct > 80 ? '#f87171' : pct > 50 ? '#fbbf24' : '#4ade80';

        const tokK = d.token_count >= 1000
            ? (d.token_count / 1000).toFixed(1) + 'k'
            : String(d.token_count);
        label.textContent = `${tokK} / 200k`;
        meter.title = `${d.token_count.toLocaleString()} / ${d.token_limit.toLocaleString()} tokens · ${d.percent}% of context · ${d.message_count} messages${d.estimated ? ' (estimated)' : ''}`;
        meter.classList.add('visible');
    } catch (e) { /* silently ignore */ }
}

// Display conversation title/status in the top bar
function displayConversationMetadata(metadata) {
    const titleEl = document.getElementById('conv-title');
    if (titleEl) titleEl.textContent = metadata.title || 'Untitled';
    // dot is driven by live WS state, not stored status
}

// Set up conversation view when loaded
async function loadConversation(conversationId) {
    stopStatsRefresh();
    const response = await fetch(`/api/conversations/${conversationId}/view`);
    const html = await response.text();
    const chatContainer = document.getElementById('chat-container');
    chatContainer.innerHTML = html;

    currentConversationId = conversationId;
    currentMessageElement = null;
    currentAssistantContent = null;

    // Connect WebSocket for this conversation
    setupWebSocket(conversationId);

    // Render markdown and highlight code in loaded messages
    renderMarkdown();

    // Scroll to bottom
    const messagesContainer = document.getElementById('chat-messages');
    if (messagesContainer) {
        setTimeout(() => {
            void messagesContainer.offsetHeight;
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }, 60);
    }

    // Load metadata for top bar
    try {
        const metaResponse = await fetch(`/api/conversations/${conversationId}/metadata`);
        const metadata = await metaResponse.json();
        displayConversationMetadata(metadata);
    } catch (err) {
        console.error('Error loading conversation metadata:', err);
    }

    // Update context length meter
    updateContextMeter(conversationId);

    // Attach form handlers
    const form = document.getElementById('chat-form');
    if (form) form.addEventListener('submit', handleFormSubmit);

    // Auto-grow textarea
    const ta = document.getElementById('chat-input');
    if (ta) {
        ta.addEventListener('input', () => {
            ta.style.height = 'auto';
            ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
            const btn = document.querySelector('.btn-send');
            if (btn) btn.classList.toggle('ready', ta.value.trim().length > 0);
        });
        ta.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
            }
        });
        ta.focus();
    }
}

// Set up WebSocket connection
// TODO: Re-enable when main zipper service supports streaming callbacks
// function setupWebSocket(conversationId) {
//     // Only create new connection if it's a different conversation or connection is closed
//     if (ws && ws.readyState === WebSocket.OPEN && currentConversationId === conversationId) {
//         console.log('WebSocket already connected to this conversation');
//         return;
//     }
//     
//     // Close existing connection to different conversation
//     if (ws && ws.readyState === WebSocket.OPEN) {
//         ws.close();
//     }
//     
//     const wsUrl = `ws://${window.location.host}/ws/conversations/${conversationId}`;
//     ws = new WebSocket(wsUrl);
//     
//     ws.addEventListener('open', () => {
//         console.log('WebSocket connected to conversation', conversationId);
//         
//         // Attach form submit handler
//         const form = document.getElementById('chat-form');
//         if (form) {
//             // Remove any existing listeners first
//             const oldForm = form.cloneNode(true);
//             form.parentNode.replaceChild(oldForm, form);
//             
//             const newForm = document.getElementById('chat-form');
//             if (newForm) {
//                 newForm.addEventListener('submit', handleFormSubmit);
//             }
//         }
//     });
//     
//     ws.addEventListener('message', handleWebSocketMessage);
//     
//     ws.addEventListener('close', () => {
//         console.log('WebSocket disconnected');
//     });
//     
//     ws.addEventListener('error', (err) => {
//         console.error('WebSocket error:', err);
//         hideTyping();
//         const errDiv = document.createElement('div');
//         errDiv.className = 'mb-4 p-4 rounded bg-red-900/30 border border-red-700 text-red-200 text-sm';
//         errDiv.textContent = '❌ Connection lost. Please refresh.';
//         document.getElementById('chat-messages')?.appendChild(errDiv);
//     });
// }


// Handle form submission — send over WebSocket
async function handleFormSubmit(e) {
    e.preventDefault();
    const ta = document.getElementById('chat-input');
    const text = ta?.value.trim();
    if (!text) return;

    // Lazy creation: no conversation yet — create one first
    if (!currentConversationId) {
        if (ta) { ta.value = ''; ta.style.height = 'auto'; }
        document.querySelector('.btn-send')?.classList.remove('ready');
        stopStatsRefresh();

        let newId;
        try {
            const r = await fetch('/api/conversations', { method: 'POST' });
            const html = await r.text();
            const match = html.match(/conversation=([a-f0-9-]+)/);
            if (!match) return;
            newId = match[1];
        } catch (err) { return; }

        window.history.pushState({}, '', `/?conversation=${newId}`);
        await loadConversation(newId);
        refreshConversationList(); // fire and forget
        markActiveSidebarItem(newId);

        // Send the message into the freshly loaded conversation
        addMessage('user', text);
        disableInput();
        showTyping();
        const send = () => ws?.send(JSON.stringify({ text }));
        if (ws?.readyState === WebSocket.OPEN) send();
        else ws?.addEventListener('open', send, { once: true });
        // Poll until the server generates a real title
        pollForTitle(newId);
        return;
    }

    if (!ws || ws.readyState !== WebSocket.OPEN) {
        setupWebSocket(currentConversationId);
        ws.addEventListener('open', () => ws.send(JSON.stringify({ text })), { once: true });
    } else {
        ws.send(JSON.stringify({ text }));
    }

    addMessage('user', text);
    if (ta) { ta.value = ''; ta.style.height = 'auto'; }
    document.querySelector('.btn-send')?.classList.remove('ready');
    disableInput();
    showTyping();
}

// Poll until the server generates a real title for a new conversation
async function pollForTitle(conversationId) {
    const defaultTitles = new Set(['New Conversation', 'Untitled', '']);
    for (let i = 0; i < 20; i++) {
        await new Promise(r => setTimeout(r, 1500));
        if (currentConversationId !== conversationId) return;
        try {
            const r = await fetch(`/api/conversations/${conversationId}/metadata`);
            if (!r.ok) continue;
            const meta = await r.json();
            const title = meta.title || '';
            if (!defaultTitles.has(title)) {
                const titleEl = document.getElementById('conv-title');
                if (titleEl) titleEl.textContent = title;
                refreshConversationList();
                return;
            }
        } catch (e) { /* ignore */ }
    }
}

// Handle sidebar conversation selection
function markActiveSidebarItem(conversationId) {
    document.querySelectorAll('#conversation-list a.conv-item').forEach(a => {
        const id = a.href.match(/conversation=([^&"]+)/)?.[1];
        a.classList.toggle('active', id === conversationId);
    });
}

async function deleteConversation(event, conversationId) {
    event.preventDefault();
    event.stopPropagation();
    if (!confirm('Delete this conversation? This cannot be undone.')) return;

    try {
        const r = await fetch(`/api/conversations/${conversationId}`, { method: 'DELETE' });
        if (!r.ok) { alert('Failed to delete conversation.'); return; }

        // Remove from sidebar
        const wrap = event.target.closest('.conv-item-wrap');
        wrap?.remove();

        // If it was the active conversation, clear the main area
        if (currentConversationId === conversationId) {
            currentConversationId = null;
            hideContextMeter();
            document.getElementById('conv-title').textContent = '';
            document.getElementById('conv-status')?.classList.remove('visible');
            document.getElementById('chat-container').innerHTML = `
                <div class="empty-state">
                    <div class="text-center">
                        <div class="empty-logo">Z</div>
                        <div style="font-size:1rem;font-weight:600;color:var(--tx-2);margin-bottom:4px;">Zipper</div>
                        <div style="font-size:0.875rem;">Select a conversation or start a new one</div>
                    </div>
                </div>`;
            if (ws) { ws.close(); ws = null; }
        }
    } catch (e) {
        alert('Error deleting conversation.');
    }
}

async function selectConversation(conversationId, event) {
    if (event) event.preventDefault();
    window.history.pushState({}, '', `/?conversation=${conversationId}`);
    markActiveSidebarItem(conversationId);
    await loadConversation(conversationId);
}

// Handle new conversation creation
async function refreshConversationList() {
    try {
        const response = await fetch('/api/conversations');
        const html = await response.text();
        const listContainer = document.getElementById('conversation-list');
        if (listContainer) {
            listContainer.innerHTML = html;
            listContainer.querySelectorAll('a').forEach(link => {
                link.addEventListener('click', (e) => {
                    const convoId = link.href.match(/conversation=([^&"]+)/)?.[1];
                    if (convoId) selectConversation(convoId, e);
                });
            });
        }
        if (currentConversationId) markActiveSidebarItem(currentConversationId);
    } catch (err) {
        console.error('Error refreshing conversation list:', err);
    }
}

// Stats refresh handle
let _statsInterval = null;

function stopStatsRefresh() {
    if (_statsInterval) { clearInterval(_statsInterval); _statsInterval = null; }
}

async function fetchAndRenderStats() {
    const grid = document.getElementById('stats-grid');
    if (!grid) { stopStatsRefresh(); return; }
    try {
        const d = await fetch('/api/stats').then(r => r.json());
        const cards = [
            {
                icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/></svg>`,
                value: d.cpu_load ?? '—', label: 'CPU load', sub: '1 min avg',
            },
            {
                icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/></svg>`,
                value: d.memory ?? '—', label: 'Memory', sub: d.memory_pct != null ? `${d.memory_pct}% used` : '',
            },
            {
                icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="18" rx="2"/><path d="M8 21v-4M16 21v-4M2 13h20M2 9h20"/></svg>`,
                value: d.disk ?? '—', label: 'Disk', sub: d.disk_pct != null ? `${d.disk_pct}% used` : '',
            },
            {
                icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
                value: d.uptime ?? '—', label: 'Uptime', sub: '',
            },
            {
                icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
                value: d.conversations ?? '—', label: 'Conversations', sub: '',
            },
            {
                icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>`,
                value: d.pending_tasks ?? '—', label: 'Pending tasks', sub: '',
            },
        ];
        grid.innerHTML = cards.map(c => `
            <div class="stat-card">
                <div class="stat-card-icon">${c.icon}</div>
                <div class="stat-card-value">${escapeHtml(String(c.value))}</div>
                <div class="stat-card-label">${c.label}</div>
                ${c.sub ? `<div class="stat-card-sub">${escapeHtml(c.sub)}</div>` : ''}
            </div>`).join('');
    } catch (e) { /* silently ignore */ }
}

async function showNewConversationView() {
    stopStatsRefresh();
    hideContextMeter();
    if (ws) { ws.close(); ws = null; }
    currentConversationId = null;
    currentMessageElement = null;
    currentAssistantContent = null;

    window.history.pushState({}, '', '/');
    document.querySelectorAll('.conv-item').forEach(a => a.classList.remove('active'));
    document.getElementById('conv-title').textContent = '';
    document.getElementById('conv-status')?.classList.remove('visible');


    const container = document.getElementById('chat-container');
    container.innerHTML = `
        <div style="flex:1;display:flex;flex-direction:column;overflow:hidden;">
            <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 24px 24px;gap:20px;">
                <div style="font-size:0.7rem;color:var(--tx-4);letter-spacing:0.1em;text-transform:uppercase;">System Status</div>
                <div class="stats-grid" id="stats-grid">
                    ${Array(6).fill(`<div class="stat-card"><div class="stat-card-value" style="color:var(--tx-4);font-size:1.1rem;">—</div><div class="stat-card-label" style="color:var(--tx-4);">···</div></div>`).join('')}
                </div>
            </div>
            <div class="input-bar">
                <form id="chat-form" style="display:flex;gap:8px;align-items:flex-end;">
                    <textarea id="chat-input" name="text" rows="1" placeholder="Start a new conversation…" required autocomplete="off"></textarea>
                    <button type="submit" class="btn-send">
                        <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 12h14M12 5l7 7-7 7"/></svg>
                    </button>
                </form>
            </div>
        </div>`;

    const form = document.getElementById('chat-form');
    if (form) form.addEventListener('submit', handleFormSubmit);
    const ta = document.getElementById('chat-input');
    if (ta) {
        ta.addEventListener('input', () => {
            ta.style.height = 'auto';
            ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
            document.querySelector('.btn-send')?.classList.toggle('ready', ta.value.trim().length > 0);
        });
        ta.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
            }
        });
        ta.focus();
    }

    fetchAndRenderStats();
    _statsInterval = setInterval(fetchAndRenderStats, 15000);
}

async function createNewConversation() {
    await showNewConversationView();
}

// Load more conversations
async function loadMoreConversations(offset) {
    try {
        const response = await fetch(`/api/conversations?offset=${offset}`);
        const html = await response.text();
        const listContainer = document.getElementById('conversation-list');
        if (listContainer) {
            listContainer.innerHTML = html;
            listContainer.querySelectorAll('a').forEach(link => {
                link.addEventListener('click', (e) => {
                    const convoId = link.href.match(/conversation=([^&"]+)/)?.[1];
                    if (convoId) selectConversation(convoId, e);
                });
            });
        }
        if (currentConversationId) markActiveSidebarItem(currentConversationId);
    } catch (err) {
        console.error('Error loading more conversations:', err);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    // Load config (user name, etc.)
    try {
        const cfg = await fetch('/api/config').then(r => r.json());
        if (cfg.user) USER_NAME = cfg.user;
    } catch (e) {}

    // Load conversation list
    await refreshConversationList();
    
    // Add new chat button handler
    const newChatBtn = document.getElementById('new-chat-btn');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', createNewConversation);
    }
    
    // Load conversation from URL, or show new conversation view
    const conversationId = new URLSearchParams(window.location.search).get('conversation');
    if (conversationId) {
        await loadConversation(conversationId);
        markActiveSidebarItem(conversationId);
    } else {
        await showNewConversationView();
    }
    
    // Add handlers for resource links (Tasks, Memory, Status)
    const resourceLinks = document.querySelectorAll('.sidebar-footer .sidebar-link');
    resourceLinks.forEach((link) => {
        link.addEventListener('click', async (e) => {
            e.preventDefault();
            const text = link.textContent.trim();
            if (text.includes('Tasks')) {
                stopStatsRefresh(); hideContextMeter(); showTasks();
            } else if (text.includes('Memory')) {
                stopStatsRefresh(); hideContextMeter(); showMemory();
            } else if (text.includes('Status')) {
                stopStatsRefresh(); hideContextMeter(); showStatus();
            }
        });
    });
});

function hideContextMeter() {
    document.getElementById('ctx-meter')?.classList.remove('visible');
}

// Show Tasks view
async function showTasks() {
    try {
        const response = await fetch('/api/tasks');
        const data = await response.json();
        const container = document.getElementById('chat-container');
        
        let html = '<div class="flex-1 flex flex-col overflow-hidden">';
        html += '<div class="flex-1 overflow-y-auto p-6">';
        html += '<h2 class="text-2xl font-bold mb-4">📋 Tasks</h2>';
        
        if (data.tasks && data.tasks.length > 0) {
            html += '<div class="space-y-3">';
            data.tasks.forEach(task => {
                const statusColor = task.status === 'done' ? 'bg-green-900' : task.status === 'failed' ? 'bg-red-900' : 'bg-slate-700';
                html += `<div class="${statusColor} p-3 rounded">
                    <div class="font-semibold">${task.title}</div>
                    <div class="text-sm text-slate-300">${task.description}</div>
                    <div class="text-xs text-slate-400 mt-2">Status: <span class="font-mono">${task.status}</span> | Due: ${new Date(task.due_at).toLocaleDateString()}</div>
                </div>`;
            });
            html += '</div>';
        } else {
            html += '<div class="text-slate-400">No tasks</div>';
        }
        
        html += '</div></div>';
        container.innerHTML = html;
    } catch (err) {
        console.error('Error loading tasks:', err);
    }
}

// Show Memory view
async function showMemory() {
    try {
        const response = await fetch('/api/memory');
        const data = await response.json();
        const container = document.getElementById('chat-container');
        
        let html = '<div class="flex-1 flex flex-col overflow-hidden">';
        html += '<div class="flex-1 overflow-y-auto p-6">';
        html += '<h2 class="text-2xl font-bold mb-4">💾 Memory</h2>';
        
        if (data.memory && Object.keys(data.memory).length > 0) {
            html += '<div class="space-y-3">';
            Object.entries(data.memory).forEach(([key, entry]) => {
                const value = entry.value || entry;
                html += `<div class="bg-slate-700 p-3 rounded">
                    <div class="font-semibold text-blue-400">${key}</div>
                    <div class="text-sm text-slate-300 mt-1 max-h-20 overflow-y-auto"><code>${JSON.stringify(value, null, 2)}</code></div>
                </div>`;
            });
            html += '</div>';
        } else {
            html += '<div class="text-slate-400">No memory entries</div>';
        }
        
        html += '</div></div>';
        container.innerHTML = html;
    } catch (err) {
        console.error('Error loading memory:', err);
    }
}

// Show Status view
async function showStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        const container = document.getElementById('chat-container');
        
        let html = '<div class="flex-1 flex flex-col overflow-hidden">';
        html += '<div class="flex-1 overflow-y-auto p-6">';
        html += '<h2 class="text-2xl font-bold mb-4">📊 System Status</h2>';
        
        html += '<div class="bg-slate-700 p-4 rounded"><pre>' + JSON.stringify(data, null, 2) + '</pre></div>';
        
        html += '</div></div>';
        container.innerHTML = html;
    } catch (err) {
        console.error('Error loading status:', err);
    }
}
