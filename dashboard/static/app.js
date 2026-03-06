/**
 * Zipper Dashboard Frontend
 * WebSocket streaming for real-time message rendering
 */

let currentConversationId = null;
let ws = null;
let currentMessageElement = null;

// Syntax highlighting for code blocks
function highlightCode() {
    document.querySelectorAll('pre code').forEach(block => {
        try {
            hljs.highlightElement(block);
        } catch (e) {
            console.error('Syntax highlighting error:', e);
        }
    });
}

// Scroll chat to bottom
function scrollToBottom() {
    const messagesContainer = document.getElementById('chat-messages');
    if (messagesContainer) {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
}

// Append a token to the current streaming message (typewriter effect)
function appendToken(token) {
    if (!currentMessageElement) {
        // Create new assistant message
        currentMessageElement = document.createElement('div');
        currentMessageElement.className = 'mb-4 p-4 rounded bg-slate-700 text-white whitespace-pre-wrap break-words text-sm';
        currentMessageElement.id = 'streaming-message';
        document.getElementById('chat-messages').appendChild(currentMessageElement);
    }
    
    currentMessageElement.textContent += token;
    scrollToBottom();
}

// Add a complete message to the chat
function addMessage(role, content) {
    const msgDiv = document.createElement('div');
    msgDiv.className = `mb-4 p-4 rounded ${role === 'user' ? 'bg-blue-900' : 'bg-slate-700'} text-white whitespace-pre-wrap break-words text-sm`;
    
    // Format content based on type
    if (typeof content === 'object') {
        msgDiv.innerHTML = `<pre>${JSON.stringify(content, null, 2)}</pre>`;
    } else {
        msgDiv.textContent = content;
    }
    
    const roleDiv = document.createElement('div');
    roleDiv.className = 'text-xs text-slate-300 mb-2';
    roleDiv.textContent = role + ' • ' + new Date().toLocaleTimeString();
    
    msgDiv.insertBefore(roleDiv, msgDiv.firstChild);
    
    document.getElementById('chat-messages').appendChild(msgDiv);
    scrollToBottom();
}

// Render a tool call result
function addToolResult(toolName, result) {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'mb-4 p-4 rounded bg-amber-900/30 border border-amber-700 text-white';
    
    const titleDiv = document.createElement('div');
    titleDiv.className = 'text-sm font-bold text-amber-200 mb-2';
    titleDiv.textContent = `🔧 ${toolName}`;
    
    const codeDiv = document.createElement('pre');
    codeDiv.className = 'bg-slate-900 p-3 rounded overflow-x-auto text-xs whitespace-pre-wrap';
    const codeContent = document.createElement('code');
    codeContent.className = 'language-bash';
    
    // Truncate very long outputs
    const displayResult = result.length > 5000 ? result.substring(0, 5000) + '\n... (truncated)' : result;
    codeContent.textContent = displayResult;
    
    codeDiv.appendChild(codeContent);
    
    msgDiv.appendChild(titleDiv);
    msgDiv.appendChild(codeDiv);
    
    document.getElementById('chat-messages').appendChild(msgDiv);
    highlightCode();
    scrollToBottom();
}

// Show typing indicator
function showTyping() {
    const typingDiv = document.createElement('div');
    typingDiv.id = 'typing-indicator';
    typingDiv.className = 'mb-4 p-4 rounded bg-slate-700 text-slate-400 text-sm';
    typingDiv.innerHTML = '<span class="animate-pulse">Zipper is thinking...</span>';
    document.getElementById('chat-messages').appendChild(typingDiv);
    scrollToBottom();
}

// Remove typing indicator
function hideTyping() {
    const typingDiv = document.getElementById('typing-indicator');
    if (typingDiv) {
        typingDiv.remove();
    }
}

// Handle WebSocket messages
function handleWebSocketMessage(event) {
    const msg = JSON.parse(event.data);
    
    switch (msg.type) {
        case 'token':
            // Streaming text token
            appendToken(msg.data || '');
            break;
        
        case 'tool_call':
            // Tool invocation
            console.log('Tool called:', msg.tool, msg.args);
            addMessage('system', `🔧 Calling ${msg.tool}...`);
            break;
        
        case 'tool_result':
            // Tool output
            if (msg.tool && msg.result) {
                addToolResult(msg.tool, msg.result);
            }
            break;
        
        case 'message':
            // Complete message (when not streaming tokens)
            appendToken(msg.content || '');
            break;
        
        case 'done':
            // Finalize — move streaming to permanent
            hideTyping();
            if (currentMessageElement) {
                // Message is already in DOM, just remove streaming marker
                currentMessageElement.id = '';
                currentMessageElement = null;
            }
            enableInput();
            break;
        
        case 'error':
            hideTyping();
            const errDiv = document.createElement('div');
            errDiv.className = 'mb-4 p-4 rounded bg-red-900/30 border border-red-700 text-red-200 text-sm';
            errDiv.textContent = '❌ ' + (msg.message || 'An error occurred');
            document.getElementById('chat-messages').appendChild(errDiv);
            enableInput();
            break;
    }
}

// Enable/disable the input form
function disableInput() {
    const form = document.getElementById('chat-form');
    if (form) {
        const input = form.querySelector('input');
        const btn = form.querySelector('button');
        if (input) input.disabled = true;
        if (btn) btn.disabled = true;
    }
}

function enableInput() {
    const form = document.getElementById('chat-form');
    if (form) {
        const input = form.querySelector('input');
        const btn = form.querySelector('button');
        if (input) {
            input.disabled = false;
            input.focus();
        }
        if (btn) btn.disabled = false;
    }
}

// Set up conversation view when loaded
async function loadConversation(conversationId) {
    const response = await fetch(`/api/conversations/${conversationId}/view`);
    const html = await response.text();
    const chatContainer = document.getElementById('chat-container');
    chatContainer.innerHTML = html;
    
    currentConversationId = conversationId;
    setupWebSocket(conversationId);
}

// Set up WebSocket connection
function setupWebSocket(conversationId) {
    // Close existing connection
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close();
    }
    
    const wsUrl = `ws://${window.location.host}/ws/conversations/${conversationId}`;
    ws = new WebSocket(wsUrl);
    
    ws.addEventListener('open', () => {
        console.log('WebSocket connected');
        
        // Attach form submit handler
        const form = document.getElementById('chat-form');
        if (form) {
            form.addEventListener('submit', handleFormSubmit);
        }
    });
    
    ws.addEventListener('message', handleWebSocketMessage);
    
    ws.addEventListener('close', () => {
        console.log('WebSocket disconnected');
    });
    
    ws.addEventListener('error', (err) => {
        console.error('WebSocket error:', err);
        hideTyping();
        const errDiv = document.createElement('div');
        errDiv.className = 'mb-4 p-4 rounded bg-red-900/30 border border-red-700 text-red-200 text-sm';
        errDiv.textContent = '❌ Connection lost. Please refresh.';
        document.getElementById('chat-messages')?.appendChild(errDiv);
    });
}

// Handle form submission
function handleFormSubmit(e) {
    e.preventDefault();
    const input = e.target.querySelector('input[name="text"]');
    const text = input.value.trim();
    
    if (text && ws && ws.readyState === WebSocket.OPEN) {
        // Add user message immediately
        addMessage('user', text);
        
        // Show typing indicator
        showTyping();
        
        // Send via WebSocket
        ws.send(JSON.stringify({ text: text }));
        
        input.value = '';
        disableInput();
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
                    const convoId = link.href.match(/conversation=([a-f0-9]+)/)?.[1];
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
});
