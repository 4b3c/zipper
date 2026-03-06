/**
 * Zipper Dashboard Frontend
 */

let currentConversationId = null;
let currentMessageElement = null;
let currentMessageWrapper = null;
let pollInterval = null;
let USER_NAME = 'You';

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
    wrapper.className = 'flex gap-3';
    wrapper.innerHTML = `
        <div class="w-7 h-7 rounded-full bg-violet-700 flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5 select-none">Z</div>
        <div class="flex-1 min-w-0">
            <div class="text-[11px] text-slate-400 mb-2">Zipper${time ? ' <span class="text-slate-500">· ' + escapeHtml(time) + '</span>' : ''}</div>
            <div class="content space-y-1 min-w-0 text-sm text-slate-100"></div>
        </div>`;
    return wrapper;
}

// Append a streaming token to the current message
function appendToken(token) {
    if (!currentMessageElement) {
        currentMessageWrapper = createAssistantWrapper('');
        currentMessageWrapper.id = 'streaming-message-wrapper';
        document.getElementById('chat-messages').appendChild(currentMessageWrapper);
        currentMessageElement = currentMessageWrapper.querySelector('.content');
        // Use a pre-like div for streaming (markdown rendered on completion)
        const textNode = document.createElement('div');
        textNode.id = 'streaming-text';
        textNode.className = 'whitespace-pre-wrap text-slate-100 text-sm';
        currentMessageElement.appendChild(textNode);
        currentMessageElement = textNode;
    }
    currentMessageElement.textContent += token;
    scrollToBottom();
}

// Add a complete message bubble
function addMessage(role, content) {
    const messages = document.getElementById('chat-messages');
    if (!messages) return;

    const wrapper = document.createElement('div');
    const text = typeof content === 'object' ? JSON.stringify(content, null, 2) : String(content);

    if (role === 'user') {
        wrapper.className = 'flex justify-end';
        wrapper.innerHTML = `
            <div class="flex flex-col items-end max-w-[80%] min-w-0">
                <div class="user-bubble px-4 py-3 bg-blue-600 rounded-2xl rounded-tr-sm text-white text-sm min-w-0">
                    <div class="markdown-body">${escapeHtml(text)}</div>
                </div>
                <div class="text-[11px] text-slate-500 mt-1 mr-1">${escapeHtml(USER_NAME)}</div>
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
    el.className = 'flex gap-3';
    el.innerHTML = `
        <div class="w-7 h-7 rounded-full bg-violet-700 flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5 select-none">Z</div>
        <div class="flex-1 min-w-0">
            <div class="text-[11px] text-slate-400 mb-2">Zipper</div>
            <div class="flex gap-1 items-center h-5">
                <div class="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce bounce-1"></div>
                <div class="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce bounce-2"></div>
                <div class="w-1.5 h-1.5 rounded-full bg-slate-500 animate-bounce bounce-3"></div>
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
}

function enableInput() {
    const ta = document.getElementById('chat-input');
    const btn = document.querySelector('#chat-form button[type="submit"]');
    if (ta) { ta.disabled = false; ta.focus(); }
    if (btn) btn.disabled = false;
}

// Display conversation title/status in the top bar
function displayConversationMetadata(metadata) {
    const titleEl = document.getElementById('conv-title');
    const statusEl = document.getElementById('conv-status');
    if (titleEl) titleEl.textContent = metadata.title || 'Untitled';
    if (statusEl) {
        if (metadata.status === 'active') {
            statusEl.innerHTML = '<span class="inline-block w-1.5 h-1.5 rounded-full bg-green-500 mr-1"></span>active';
            statusEl.className = 'ml-3 text-xs text-green-400';
        } else {
            statusEl.textContent = metadata.status || '';
            statusEl.className = 'ml-3 text-xs text-slate-500';
        }
    }
}

// Set up conversation view when loaded
async function loadConversation(conversationId) {
    const response = await fetch(`/api/conversations/${conversationId}/view`);
    const html = await response.text();
    const chatContainer = document.getElementById('chat-container');
    chatContainer.innerHTML = html;

    currentConversationId = conversationId;
    currentMessageElement = null;
    currentMessageWrapper = null;

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

    // Attach form handlers
    const form = document.getElementById('chat-form');
    if (form) form.addEventListener('submit', handleFormSubmit);

    // Auto-grow textarea
    const ta = document.getElementById('chat-input');
    if (ta) {
        ta.addEventListener('input', () => {
            ta.style.height = 'auto';
            ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
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

// Poll for updated conversation view
async function pollForUpdates() {
    if (!currentConversationId) return;

    try {
        const response = await fetch(`/api/conversations/${currentConversationId}/view`);
        if (!response.ok) return;

        const html = await response.text();
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = html;
        const newMessages = tempDiv.querySelector('#chat-messages')?.innerHTML;

        if (newMessages) {
            const messagesContainer = document.getElementById('chat-messages');
            if (messagesContainer && messagesContainer.innerHTML !== newMessages) {
                messagesContainer.innerHTML = newMessages;
                renderMarkdown();
                scrollToBottom();
                // Stop polling once the response is in and typing is gone
                if (!document.getElementById('typing-indicator')) {
                    stopPolling();
                    enableInput();
                }
            }
        }
    } catch (err) {
        console.error('Polling error:', err);
    }
}

function startPolling() {
    stopPolling(); // Clear any existing interval
    pollInterval = setInterval(pollForUpdates, 2000); // Poll every 2 seconds
}

function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

// Handle form submission
async function handleFormSubmit(e) {
    e.preventDefault();
    const ta = document.getElementById('chat-input');
    const text = ta?.value.trim();

    if (!text || !currentConversationId) return;

    try {
        disableInput();
        showTyping();

        const response = await fetch(`/api/conversations/${currentConversationId}/message`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const html = await response.text();
        const messagesContainer = document.getElementById('chat-messages');
        if (messagesContainer) {
            messagesContainer.innerHTML = html;
            renderMarkdown();
            scrollToBottom();
            // Re-show typing since LLM response hasn't arrived yet
            showTyping();
        }

        if (ta) {
            ta.value = '';
            ta.style.height = 'auto';
        }

        startPolling();

    } catch (err) {
        console.error('Error sending message:', err);
        hideTyping();
        const errDiv = document.createElement('div');
        errDiv.className = 'flex gap-3';
        errDiv.innerHTML = `<div class="flex-1 px-4 py-2 rounded-xl bg-red-900/30 border border-red-800 text-red-300 text-sm">Failed to send: ${escapeHtml(err.message)}</div>`;
        document.getElementById('chat-messages')?.appendChild(errDiv);
        enableInput();
        stopPolling();
    }
}

// Handle sidebar conversation selection
async function selectConversation(conversationId, event) {
    if (event) {
        event.preventDefault();
    }
    
    // Update URL
    window.history.pushState({}, '', `/?conversation=${conversationId}`);
    
    // Load conversation
    await loadConversation(conversationId);
}

// Handle new conversation creation
async function createNewConversation() {
    try {
        const response = await fetch('/api/conversations', { method: 'POST' });
        const html = await response.text();
        
        // Extract conversation ID from script
        const match = html.match(/conversation=([a-f0-9]+)/);
        if (match) {
            await selectConversation(match[1]);
        }
    } catch (err) {
        console.error('Error creating conversation:', err);
    }
}

// Load more conversations
async function loadMoreConversations(offset) {
    try {
        const response = await fetch(`/api/conversations?offset=${offset}`);
        const html = await response.text();
        
        // Replace the conversation list items and load more button
        const listContainer = document.getElementById('conversation-list');
        if (listContainer) {
            listContainer.innerHTML = html;
            
            // Add click handlers to new conversation links
            listContainer.querySelectorAll('a').forEach(link => {
                link.addEventListener('click', (e) => {
                    const convoId = link.href.match(/conversation=([^&"]+)/)?.[1];
                    if (convoId) {
                        selectConversation(convoId, e);
                    }
                });
            });
        }
    } catch (err) {
        console.error('Error loading more conversations:', err);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    // Load conversation list
    try {
        const response = await fetch('/api/conversations');
        const html = await response.text();
        const listContainer = document.getElementById('conversation-list');
        if (listContainer) {
            listContainer.innerHTML = html;
            
            // Add click handlers to conversation links
            listContainer.querySelectorAll('a').forEach(link => {
                link.addEventListener('click', (e) => {
                    const convoId = link.href.match(/conversation=([^&"]+)/)?.[1];
                    if (convoId) {
                        selectConversation(convoId, e);
                    }
                });
            });
        }
    } catch (err) {
        console.error('Error loading conversations:', err);
    }
    
    // Add new chat button handler
    const newChatBtn = document.getElementById('new-chat-btn');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', createNewConversation);
    }
    
    // Load conversation from URL if specified
    const conversationId = new URLSearchParams(window.location.search).get('conversation');
    if (conversationId) {
        await loadConversation(conversationId);
    }
    
    // Add handlers for resource links (Tasks, Memory, Status)
    const resourceLinks = document.querySelectorAll('.border-t.border-slate-700.p-4 a');
    resourceLinks.forEach((link, index) => {
        link.href = '#';
        link.addEventListener('click', async (e) => {
            e.preventDefault();
            const text = link.textContent.trim();
            if (text.includes('Tasks')) {
                showTasks();
            } else if (text.includes('Memory')) {
                showMemory();
            } else if (text.includes('Status')) {
                showStatus();
            }
        });
    });
});

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
