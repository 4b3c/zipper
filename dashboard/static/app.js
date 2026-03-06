/**
 * Zipper Dashboard Frontend
 * WebSocket streaming for real-time message rendering
 */

let currentConversationId = null;
let ws = null;
let currentMessageElement = null;
let pollInterval = null;

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
        // Use a small timeout to ensure DOM has been painted
        setTimeout(() => {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }, 0);
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
    
    // Attach form submit handler
    const form = document.getElementById('chat-form');
    if (form) {
        form.addEventListener('submit', handleFormSubmit);
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
        
        // Extract just the messages section
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = html;
        const newMessages = tempDiv.querySelector('#chat-messages')?.innerHTML;
        
        if (newMessages) {
            const messagesContainer = document.getElementById('chat-messages');
            if (messagesContainer) {
                const currentContent = messagesContainer.innerHTML;
                // Only update if content has changed
                if (currentContent !== newMessages) {
                    messagesContainer.innerHTML = newMessages;
                    scrollToBottom();
                    highlightCode();
                    
                    // Check if we're still waiting (has typing indicator)
                    if (!document.getElementById('typing-indicator')) {
                        // No more typing, stop polling
                        stopPolling();
                    }
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
    const input = e.target.querySelector('input[name="text"]');
    const text = input.value.trim();
    
    if (!text || !currentConversationId) return;
    
    try {
        // Disable input and show typing indicator
        disableInput();
        showTyping();
        
        // Send message via HTTP POST
        const response = await fetch(`/api/conversations/${currentConversationId}/message`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const html = await response.text();
        
        // Replace messages with updated list
        const messagesContainer = document.getElementById('chat-messages');
        if (messagesContainer) {
            messagesContainer.innerHTML = html;
            scrollToBottom();
            highlightCode();
        }
        
        input.value = '';
        
        // Start polling for LLM response
        startPolling();
        
    } catch (err) {
        console.error('Error sending message:', err);
        hideTyping();
        const errDiv = document.createElement('div');
        errDiv.className = 'mb-4 p-4 rounded bg-red-900/30 border border-red-700 text-red-200 text-sm';
        errDiv.textContent = '❌ Failed to send message: ' + err.message;
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
                    const convoId = link.href.match(/conversation=([a-f0-9]+)/)?.[1];
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
