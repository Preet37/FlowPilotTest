document.addEventListener("DOMContentLoaded", () => {
    // --- CONFIG ---
    const API_BASE_URL = "/api"; 
    
    // !!! IMPORTANT: Get your Canvas/GCal ICS link and paste it here
    const DEMO_CALENDAR_ICS_URL = "https://calendar.google.com/calendar/ical/preetkaria37%40gmail.com/public/basic.ics";

    // --- STATE ---
    let state = { tasks: [] };
    let isFetching = false;
    let hasSynced = false; 

    // --- ELEMENTS ---
    const textInput = document.getElementById("text-input");
    const sendButton = document.getElementById("send-button");
    const clarifyList = document.getElementById("clarifyList");
    const todayList = document.getElementById("todayList");
    const tomorrowList = document.getElementById("tomorrowList");
    const inboxList = document.getElementById("inboxList"); // <-- NEW
    const syncAllButton = document.getElementById("syncAllButton");
    const clarifySection = document.getElementById("clarifySection");

    // --- Initial UI State (Disabled until synced) ---
    textInput.disabled = true;
    sendButton.disabled = true;

    // --- SEND LOGIC ---
    function handleSend() {
        if (!hasSynced) {
            alert("Please tap 'Sync All' first!");
            return;
        }
        const text = textInput.value.trim();
        if (text) {
            textInput.value = "";
            textInput.placeholder = "Processing...";
            textInput.disabled = true;
            sendButton.disabled = true;
            sendToAgent(text);
        }
    }
    sendButton.addEventListener("click", handleSend);
    textInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") handleSend();
    });

    // --- API CALLS ---
    async function sendToAgent(text) {
        console.log("Sending to agent:", text);
        const AGENT_ENDPOINT = `${API_BASE_URL}/parse_and_plan`;
        try {
            const res = await fetch(AGENT_ENDPOINT, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: text })
            });
            if (!res.ok) throw new Error(`Agent returned status ${res.status}`);
            
            console.log("Agent flow triggered. Refreshing state...");
            textInput.placeholder = "Done! Refreshing...";
            await new Promise(r => setTimeout(r, 1500)); // Wait for agent to process
            await fetchState();
            textInput.placeholder = "Type or use your keyboard mic...";
        } catch (err) {
            console.error("Error calling agent:", err);
            textInput.placeholder = "Error: Agent failed. Try again.";
        }
        // Re-enable input
        textInput.disabled = false;
        sendButton.disabled = false;
    }

    async function fetchState() {
        if (isFetching) return;
        isFetching = true;
        console.log("Fetching state...");
        try {
            const res = await fetch(`${API_BASE_URL}/demo_state`);
            if (!res.ok) throw new Error(`Failed to fetch state: ${res.status}`);
            state = await res.json();
            console.log("State received:", state);
            render();
        } catch (err) {
            console.error("Error fetching state:", err);
            textInput.placeholder = "Error: Could not load tasks.";
        }
        isFetching = false;
    }

    async function deleteTask(taskId, isExternal) {
        console.log(`Deleting task: ${taskId}, isExternal: ${isExternal}`);
        state.tasks = state.tasks.filter(t => t.id !== taskId);
        render(); // Optimistic update

        // --- NEW: Safe Delete ---
        // Only send delete request to backend if it's *not* an external event
        if (isExternal) {
            console.log("External event, only removing from UI.");
            return;
        }
        // --- END NEW ---

        try {
            await fetch(`${API_BASE_URL}/delete_task`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ taskId: taskId })
            });
        } catch (err) {
            console.error("Error deleting task:", err);
            fetchState(); // Re-sync if delete fails
        }
    }

    function isValidEmail(email) {
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    }

    async function sendClarification(taskID, question, answer) {
        if (question.includes("email") && !isValidEmail(answer)) {
            alert("Please enter a valid email address.");
            return;
        }
        console.log(`Clarifying task ${taskID} with answer: ${answer}`);
        try {
            await fetch(`${API_BASE_URL}/clarify`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ taskID, question, answer })
            });
            // After clarifying, the task will be auto-scheduled in the backend
            // We'll poll for the state change
            await new Promise(r => setTimeout(r, 1500)); 
            await fetchState();
        } catch (err) {
            console.error("Error sending clarification:", err);
        }
    }
    
    // --- SYNC ALL BUTTON ---
    async function runFullSync() {
        if (DEMO_CALENDAR_ICS_URL === "YOUR_CALENDAR_ICS_URL_HERE") {
            alert("Please paste your GCal/Canvas ICS URL into frontend/app.js");
            return;
        }
        console.log("Running full sync...");
        textInput.placeholder = "Syncing all sources...";
        syncAllButton.disabled = true; 
        syncAllButton.innerText = "Syncing...";
        textInput.disabled = true;
        sendButton.disabled = true;

        try {
            await fetch(`${API_BASE_URL}/sync_all`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: DEMO_CALENDAR_ICS_URL }) 
            });
            
            // Poll for state to update
            // Sync is slow, wait 6-8s
            await new Promise(r => setTimeout(r, 8000)); 
            await fetchState();
            
            textInput.placeholder = "Sync complete! You can now add tasks.";
            hasSynced = true; 
        } catch (err) {
            textInput.placeholder = "Error: Full sync failed.";
        }
        
        // Re-enable everything
        syncAllButton.disabled = false;
        syncAllButton.innerText = "Sync All";
        textInput.disabled = false;
        sendButton.disabled = false;
    }
    
    syncAllButton.addEventListener("click", (e) => {
        e.preventDefault();
        runFullSync();
    });
    // --- END NEW ---

    // --- RENDER FUNCTIONS ---
    function render() {
        todayList.innerHTML = "";
        tomorrowList.innerHTML = "";
        inboxList.innerHTML = "";
        clarifyList.innerHTML = "";

        const tasksToClarify = state.tasks.filter(t => t.needsClarification);
        if (tasksToClarify.length > 0) {
            clarifySection.style.display = "block";
            tasksToClarify.forEach(renderClarifyItem);
        } else {
            clarifySection.style.display = "none";
        }

        const otherTasks = state.tasks.filter(t => !t.needsClarification);
        let tasksRendered = 0;

        otherTasks.sort((a, b) => {
            if (a.dueDate && b.dueDate) {
                return new Date(a.dueDate) - new Date(b.dueDate);
            }
            if (a.dueDate) return -1;
            if (b.dueDate) return 1;
            return 0;
        });

        otherTasks.forEach(task => {
            const el = renderTaskItem(task);
            if (task.planDay === "today") {
                todayList.appendChild(el);
                tasksRendered++;
            } else if (task.planDay === "tomorrow") {
                tomorrowList.appendChild(el);
                tasksRendered++;
            } else {
                inboxList.appendChild(el);
                tasksRendered++;
            }
        });

        if (tasksRendered === 0 && tasksToClarify.length === 0) {
            if (hasSynced) {
                inboxList.innerHTML = "<li>All clear! Use the text bar to add tasks.</li>";
            } else {
                inboxList.innerHTML = "<li>Tap 'Sync All' to load your life.</li>";
            }
        }
    }

    function renderTaskItem(task) {
        const li = document.createElement("li");
        li.className = "task-item";
        
        let dueHTML = "";
        if (task.dueDate) {
            const startTime = new Date(task.dueDate);
            const duration = task.duration || 60; 
            const endTime = new Date(startTime.getTime() + duration * 60000);

            const timeFormat = { hour: 'numeric', minute: '2-digit' };
            const dateFormat = { month: 'short', day: 'numeric' };

            const startDateStr = startTime.toLocaleDateString(undefined, dateFormat);
            const startTimeStr = startTime.toLocaleTimeString(undefined, timeFormat);
            const endTimeStr = endTime.toLocaleTimeString(undefined, timeFormat);

            dueHTML = `<div class="due">${startDateStr}, ${startTimeStr} - ${endTimeStr}</div>`;
        }
        
        // --- "Done" button on ALL tasks ---
        li.innerHTML = `
            <div class="task-content">
                <div class="title">${task.title}</div>
                ${dueHTML}
            </div>
            <button class="done-button">✓</button>
        `;
        
        li.querySelector(".done-button").addEventListener("click", () => {
            deleteTask(task.id, task.isExternal);
        });
        
        return li;
    }

    function renderClarifyItem(task) {
        const itemDiv = document.createElement("div");
        itemDiv.className = "clarify-item";
        
        const question = task.pendingQuestions[0] || "Needs information";
        const inputId = `clarify-input-${task.id}`;

        itemDiv.innerHTML = `
            <p><strong>${task.title}</strong></p>
            <p>${question}</p>
            <div>
                <input type="text" id="${inputId}" class="clarify-input" placeholder="Type an email...">
                <button class="clarify-save">Save</button>
            </div>
        `;

        itemDiv.querySelector(".clarify-save").addEventListener("click", () => {
            const answer = document.getElementById(inputId).value.trim();
            if (answer) {
                sendClarification(task.id, question, answer);
            }
        });
        clarifyList.appendChild(itemDiv);
    }

    // --- INIT ---
    // We now wait for the user to tap "Sync All"
    // We'll do a quick fetch just to render the empty state
    fetchState();
});