// SPV Quantum AI Operating System - Dashboard Controller v2
document.addEventListener("DOMContentLoaded", () => {
    const apiBase = window.location.origin;
    const wsUri = `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;

    // Active state caches
    let activePrices = {};
    let allLogs = [];
    let activeLogFilter = "all";
    let activeEmployeeCode = null;

    // REST Interval loops
    let metricsInterval;
    let portfolioInterval;
    let telemetryInterval;

    // Initialize clock
    setInterval(() => {
        const now = new Date();
        document.getElementById("top-time").textContent = now.toLocaleTimeString();
    }, 1000);

    // Initial setup
    fetchInitialData();
    connectWs();
    setupEventListeners();

    // Setup WebSockets
    let ws;
    function connectWs() {
        console.log("Connecting WebSocket...");
        ws = new WebSocket(wsUri);

        ws.onopen = () => {
            document.getElementById("connection-status").textContent = "CONNECTED";
            document.getElementById("connection-status").className = "value text-green";
            document.getElementById("status-indicator").classList.remove("disconnected");
            appendTerminalLog("system", "websocket", "WebSocket stream established successfully.");
        };

        ws.onmessage = (event) => {
            const payload = JSON.parse(event.data);
            handleBusEvent(payload);
        };

        ws.onclose = () => {
            document.getElementById("connection-status").textContent = "DISCONNECTED";
            document.getElementById("connection-status").className = "value text-red";
            document.getElementById("status-indicator").classList.add("disconnected");
            appendTerminalLog("system", "websocket", "WebSocket disconnected. Reconnecting in 3s...", "color: var(--accent-red)");
            setTimeout(connectWs, 3000);
        };
    }

    // Event router
    function handleBusEvent(event) {
        const { topic, sender, timestamp, data } = event;
        const formattedTime = new Date(timestamp).toLocaleTimeString();

        // Save log in memory
        allLogs.push({ sender, topic, dataStr: JSON.stringify(data), timestamp });
        if (allLogs.length > 200) allLogs.shift();
        
        // Re-render logs if current filter matches
        if (activeLogFilter === "all" || matchesFilter(sender, topic, activeLogFilter)) {
            appendTerminalLog(sender, topic, JSON.stringify(data), getTopicStyle(topic));
        }

        // Live prices
        if (topic === "market_data" || topic === "tick") {
            updatePriceWidget(data);
        } else if (topic === "order_filled" || topic === "paper_order_filled") {
            refreshTables();
        }
    }

    // Setup event handlers
    function setupEventListeners() {
        // Log filters
        const filterContainer = document.getElementById("log-filters");
        filterContainer.addEventListener("click", (e) => {
            if (e.target.tagName === "BUTTON") {
                filterContainer.querySelectorAll("button").forEach(b => b.classList.remove("active"));
                e.target.classList.add("active");
                activeLogFilter = e.target.getAttribute("data-filter");
                renderLogsFromMemory();
            }
        });

        // AI Employee switches
        const empSelect = document.getElementById("emp-select");
        empSelect.addEventListener("change", async () => {
            const code = empSelect.value;
            if (code) {
                try {
                    const res = await fetch(`${apiBase}/api/employees/activate?code=${code}`, { method: "POST" });
                    const r = await res.json();
                    if (r.status === "success") {
                        appendTerminalLog("employee_engine", "activate", `AI Employee ${code} activated successfully.`);
                        await fetchEmployeeData();
                        await fetchSafetyData();
                    }
                } catch (e) {
                    console.error("Failed to activate employee", e);
                }
            }
        });

        // Emergency controls
        document.getElementById("btn-emerg-kill").addEventListener("click", () => triggerEmergencyAction("kill"));
        document.getElementById("btn-emerg-reset").addEventListener("click", () => triggerEmergencyAction("reset"));
        document.getElementById("btn-emerg-pause").addEventListener("click", () => triggerEmergencyAction("pause"));
        document.getElementById("btn-emerg-resume").addEventListener("click", () => triggerEmergencyAction("resume"));

        // CSV export
        document.getElementById("btn-export-csv").addEventListener("click", exportTradesCSV);

        // Watchlist searches
        document.getElementById("market-search").addEventListener("input", (e) => {
            const query = e.target.value.toLowerCase();
            document.querySelectorAll(".price-card").forEach(card => {
                const sym = card.id.replace("price-card-", "").toLowerCase();
                if (sym.includes(query)) {
                    card.style.display = "flex";
                } else {
                    card.style.display = "none";
                }
            });
        });
    }

    async function triggerEmergencyAction(action) {
        try {
            const res = await fetch(`${apiBase}/api/safety/emergency/${action}`, { method: "POST" });
            const r = await res.json();
            appendTerminalLog("dashboard_client", `emerg_${action}`, r.message || `Action ${action} triggered successfully.`);
            await fetchSafetyData();
        } catch (e) {
            console.error(`Emergency action ${action} failed`, e);
        }
    }

    // Refresh core tables
    async function refreshTables() {
        await Promise.all([
            fetchPortfolioSummary(),
            fetchClosedTrades(),
            fetchActivePositions()
        ]);
    }

    // Initial load
    async function fetchInitialData() {
        await Promise.all([
            fetchEmployeeData(),
            fetchBrokerStatus(),
            fetchSafetyData(),
            fetchTelemetryData(),
            refreshTables()
        ]);

        // Start loops
        portfolioInterval = setInterval(refreshTables, 3000);
        telemetryInterval = setInterval(fetchTelemetryData, 3000);
    }

    // Fetch employee registry
    async function fetchEmployeeData() {
        try {
            const res = await fetch(`${apiBase}/api/employees`);
            const data = await res.json();
            
            activeEmployeeCode = data.active_employee_code;
            document.getElementById("top-employee").textContent = data.active_employee_name || "None";

            const empSelect = document.getElementById("emp-select");
            empSelect.innerHTML = "<option value=''>-- Select Employee --</option>";
            
            data.employees.forEach(e => {
                const opt = document.createElement("option");
                opt.value = e.employee_code;
                opt.textContent = `${e.name} (${e.employee_code})`;
                if (e.employee_code === activeEmployeeCode) {
                    opt.selected = true;
                    renderActiveEmployeeDetails(e);
                }
                empSelect.appendChild(opt);
            });
        } catch (e) {
            console.error("Failed to fetch employee registry", e);
        }
    }

    function renderActiveEmployeeDetails(emp) {
        document.getElementById("emp-name").textContent = emp.name;
        document.getElementById("emp-type").textContent = emp.employee_type;
        document.getElementById("emp-desc").textContent = emp.description;
        document.getElementById("emp-status-val").textContent = emp.state;
        document.getElementById("emp-loss-limit").textContent = `$${emp.risk_stats.max_daily_loss}`;
        document.getElementById("emp-exposure-limit").textContent = `$${emp.risk_stats.max_exposure}`;
        document.getElementById("emp-trade-count").textContent = emp.trade_count;
        document.getElementById("emp-win-rate").textContent = `${emp.win_rate}%`;
        
        const pnlEl = document.getElementById("emp-pnl-val");
        pnlEl.textContent = `$${emp.pnl.toFixed(2)}`;
        pnlEl.className = emp.pnl >= 0 ? "text-green" : "text-red";
        
        document.getElementById("emp-details").style.display = "block";
    }

    // Fetch broker settings
    async function fetchBrokerStatus() {
        try {
            const res = await fetch(`${apiBase}/api/broker/status`);
            const status = await res.json();
            
            document.getElementById("top-broker").textContent = status.connected_broker.toUpperCase();
            
            const isLive = status.connected_broker !== "paper_broker";
            document.getElementById("top-mode").textContent = isLive ? "LIVE_MODE" : "PAPER_MODE";
            document.getElementById("top-mode").className = isLive ? "value text-red" : "value text-green";
        } catch (e) {
            console.error("Failed to fetch broker status", e);
        }
    }

    // Fetch safety status
    async function fetchSafetyData() {
        try {
            const res = await fetch(`${apiBase}/api/safety/status`);
            const data = await res.json();

            const safetyEl = document.getElementById("risk-safety-status");
            safetyEl.textContent = data.safety_status;
            safetyEl.className = `badge ${data.safety_status === "OPERATIONAL" ? "text-green" : "text-red"}`;

            const activeKill = data.emergency_status.kill_switch_active;
            const paused = data.emergency_status.trading_paused;
            let emergVal = "NORMAL";
            if (activeKill) emergVal = "KILL SWITCH ACTIVE";
            else if (paused) emergVal = "TRADING PAUSED";
            
            document.getElementById("risk-emergency-switch").textContent = emergVal;
            document.getElementById("risk-emergency-switch").className = emergVal === "NORMAL" ? "text-green" : "text-red";

            document.getElementById("risk-current-exposure").textContent = `$${data.current_exposure.toLocaleString()}`;
            document.getElementById("risk-profit-lock").textContent = `$${data.daily_limits.daily_profit_lock_usd}`;
        } catch (e) {
            console.error("Failed to fetch safety status", e);
        }
    }

    // Fetch system telemetry
    async function fetchTelemetryData() {
        try {
            const res = await fetch(`${apiBase}/api/health/status`);
            const health = await res.json();

            // Status bar health
            const healthEl = document.getElementById("top-health");
            healthEl.textContent = health.overall_system_health;
            if (health.overall_system_health === "HEALTHY") {
                healthEl.className = "value text-green";
            } else if (health.overall_system_health === "DEGRADED") {
                healthEl.className = "value text-warning";
            } else {
                healthEl.className = "value text-red";
            }

            // CPU
            document.getElementById("sys-cpu-val").textContent = `${health.cpu_usage_pct}%`;
            document.getElementById("sys-cpu-fill").style.width = `${health.cpu_usage_pct}%`;

            // Memory
            document.getElementById("sys-mem-val").textContent = `${health.memory_usage_pct}%`;
            document.getElementById("sys-mem-fill").style.width = `${health.memory_usage_pct}%`;

            // Latencies
            document.getElementById("sys-net-latency").textContent = `${health.latency.internet_latency_ms}ms`;
            document.getElementById("sys-broker-latency").textContent = `${health.latency.broker_latency_ms}ms`;
            document.getElementById("sys-db-latency").textContent = `${health.latency.database_latency_ms}ms`;
            
            // Queue size
            document.getElementById("sys-queue-size").textContent = health.event_queue_health.event_bus_queue_size;
        } catch (e) {
            console.error("Failed to fetch telemetry data", e);
        }
    }

    // Fetch portfolio summary
    async function fetchPortfolioSummary() {
        try {
            const res = await fetch(`${apiBase}/api/portfolio/summary`);
            const sum = await res.json();

            document.getElementById("card-capital").textContent = `$${sum.available_capital.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
            
            const portfolioVal = sum.available_capital + sum.mtm;
            document.getElementById("card-portfolio-val").textContent = `$${portfolioVal.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
            
            const todayPnlEl = document.getElementById("card-today-pnl");
            todayPnlEl.textContent = `${sum.realized_pnl >= 0 ? '+' : ''}$${sum.realized_pnl.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
            todayPnlEl.className = sum.realized_pnl >= 0 ? "value text-green" : "value text-red";

            const totalPnlEl = document.getElementById("card-total-pnl");
            totalPnlEl.textContent = `${sum.mtm >= 0 ? '+' : ''}$${sum.mtm.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
            totalPnlEl.className = sum.mtm >= 0 ? "value text-green" : "value text-red";

            document.getElementById("card-margin").textContent = `$${sum.utilized_margin.toLocaleString(undefined, {minimumFractionDigits: 2})}`;
        } catch (e) {
            console.error("Failed to fetch portfolio summary", e);
        }
    }

    // Fetch active positions
    async function fetchActivePositions() {
        try {
            const res = await fetch(`${apiBase}/api/portfolio/positions`);
            const data = await res.json();
            
            document.getElementById("card-open-pos").textContent = data.open_positions.length;
            document.getElementById("card-closed-trades").textContent = data.closed_positions.length;

            const posBody = document.getElementById("positions-body");
            posBody.innerHTML = "";
            
            if (data.open_positions.length === 0) {
                posBody.innerHTML = `<tr><td colspan="8" class="text-center text-muted">No open positions.</td></tr>`;
                return;
            }

            data.open_positions.forEach(p => {
                const tr = document.createElement("tr");
                const pnlClass = p.unrealized_pnl >= 0 ? "text-green" : "text-red";
                tr.innerHTML = `
                    <td><b>${p.symbol}</b></td>
                    <td><span class="${p.side === 'BUY' ? 'text-green' : 'text-red'}">${p.side}</span></td>
                    <td>${p.quantity}</td>
                    <td>$${p.avg_price.toFixed(2)}</td>
                    <td>$${p.ltp.toFixed(2)}</td>
                    <td class="${pnlClass}">$${p.unrealized_pnl.toFixed(2)}</td>
                    <td>-</td>
                    <td><button class="btn-danger btn-xs" onclick="exitPosition('${p.symbol}', '${p.side}', ${p.quantity})">Exit</button></td>
                `;
                posBody.appendChild(tr);
            });
        } catch (e) {
            console.error("Failed to fetch open positions", e);
        }
    }

    // Exit Position handler
    window.exitPosition = async function(symbol, side, qty) {
        const exitSide = side === "BUY" ? "SELL" : "BUY";
        const payload = {
            symbol: symbol,
            side: exitSide,
            quantity: qty,
            type: "MARKET"
        };
        try {
            const res = await fetch(`${apiBase}/api/execution/submit`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const r = await res.json();
            appendTerminalLog("execution_engine", "exit_position", `Closed position in ${symbol}.`);
            await refreshTables();
        } catch (e) {
            console.error("Failed to exit position", e);
        }
    };

    // Fetch closed trades
    async function fetchClosedTrades() {
        try {
            const res = await fetch(`${apiBase}/api/trades?limit=15`);
            const trades = await res.json();
            
            const tradesBody = document.getElementById("trades-body");
            tradesBody.innerHTML = "";

            if (trades.length === 0) {
                tradesBody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">No closed trades.</td></tr>`;
                return;
            }

            trades.forEach(t => {
                const tr = document.createElement("tr");
                const pnlClass = t.realized_pnl >= 0 ? "text-green" : "text-red";
                tr.innerHTML = `
                    <td><b>${t.symbol}</b></td>
                    <td><span class="${t.side === 'BUY' ? 'text-green' : 'text-red'}">${t.side}</span></td>
                    <td>${t.quantity}</td>
                    <td>$${t.price.toFixed(2)}</td>
                    <td>$${t.commission.toFixed(4)}</td>
                    <td class="${pnlClass}">$${t.realized_pnl.toFixed(2)}</td>
                    <td>${new Date(t.executed_at).toLocaleTimeString()}</td>
                `;
                tradesBody.appendChild(tr);
            });
        } catch (e) {
            console.error("Failed to fetch closed trades", e);
        }
    }

    // Export closed trades as CSV
    async function exportTradesCSV() {
        try {
            const res = await fetch(`${apiBase}/api/trades?limit=1000`);
            const trades = await res.json();
            
            if (trades.length === 0) {
                alert("No trades found to export.");
                return;
            }

            let csvContent = "Symbol,Side,Quantity,Price,Commission,Realized_PnL,Time,Broker\n";
            trades.forEach(t => {
                csvContent += `${t.symbol},${t.side},${t.quantity},${t.price},${t.commission},${t.realized_pnl},${t.executed_at},${t.broker}\n`;
            });

            const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.setAttribute("href", url);
            link.setAttribute("download", `trades_export_${Date.now()}.csv`);
            link.style.visibility = "hidden";
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        } catch (e) {
            console.error("Failed to export trades CSV", e);
        }
    }

    // Live price widget
    function updatePriceWidget(tick) {
        const { symbol, close } = tick;
        const prevPrice = activePrices[symbol];
        activePrices[symbol] = close;

        let card = document.getElementById(`price-card-${symbol}`);
        if (!card) {
            card = document.createElement("div");
            card.id = `price-card-${symbol}`;
            card.className = "price-card";
            card.innerHTML = `
                <div class="symbol">${symbol}</div>
                <div class="price" id="price-val-${symbol}">$${close.toFixed(2)}</div>
                <div class="change text-green" id="price-change-${symbol}">+0.00%</div>
            `;
            document.getElementById("prices-row").appendChild(card);
        } else {
            const priceVal = document.getElementById(`price-val-${symbol}`);
            const priceChange = document.getElementById(`price-change-${symbol}`);
            priceVal.textContent = `$${close.toFixed(2)}`;

            if (prevPrice) {
                const diff = close - prevPrice;
                const percent = (diff / prevPrice) * 100;
                priceChange.textContent = `${percent >= 0 ? '+' : ''}${percent.toFixed(2)}%`;
                if (diff >= 0) {
                    priceChange.className = "change text-green";
                    priceVal.style.color = "var(--accent-green)";
                } else {
                    priceChange.className = "change text-red";
                    priceVal.style.color = "var(--accent-red)";
                }
                setTimeout(() => { priceVal.style.color = ""; }, 300);
            }
        }

        recalculateGainersLosers();
    }

    function recalculateGainersLosers() {
        // Automatically rank and display gainers and losers
        const tickers = Object.keys(activePrices).map(sym => {
            const priceEl = document.getElementById(`price-change-${sym}`);
            const pct = priceEl ? parseFloat(priceEl.textContent) : 0.0;
            return { symbol: sym, pct };
        });

        tickers.sort((a, b) => b.pct - a.pct);
        
        const gainers = tickers.filter(t => t.pct > 0).slice(0, 3);
        const losers = [...tickers].reverse().filter(t => t.pct < 0).slice(0, 3);

        const gContainer = document.getElementById("top-gainers");
        gContainer.innerHTML = gainers.length > 0 ? "" : "-";
        gainers.forEach(g => {
            gContainer.innerHTML += `<div style="display:flex; justify-content:space-between"><span>${g.symbol}</span><span class="text-green">+${g.pct.toFixed(2)}%</span></div>`;
        });

        const lContainer = document.getElementById("top-losers");
        lContainer.innerHTML = losers.length > 0 ? "" : "-";
        losers.forEach(l => {
            lContainer.innerHTML += `<div style="display:flex; justify-content:space-between"><span>${l.symbol}</span><span class="text-red">${l.pct.toFixed(2)}%</span></div>`;
        });
    }

    // Logs rendering & filtering
    function matchesFilter(sender, topic, filter) {
        const send = sender.toLowerCase();
        const top = topic.toLowerCase();
        
        if (filter === "system") return send.includes("system") || send.includes("health");
        if (filter === "strategy") return send.includes("strategy") || top.includes("signal");
        if (filter === "execution") return send.includes("execution") || top.includes("order");
        if (filter === "risk") return send.includes("risk") || top.includes("safety");
        if (filter === "broker") return send.includes("broker") || send.includes("kotak");
        if (filter === "paper") return send.includes("paper");
        return false;
    }

    function renderLogsFromMemory() {
        const terminal = document.getElementById("terminal-logs");
        terminal.innerHTML = "";
        
        const filtered = activeLogFilter === "all" 
            ? allLogs 
            : allLogs.filter(l => matchesFilter(l.sender, l.topic, activeLogFilter));

        filtered.slice(-100).forEach(l => {
            const entry = document.createElement("div");
            entry.className = "log-entry";
            entry.innerHTML = `
                <span class="log-time">[${new Date(l.timestamp).toLocaleTimeString()}]</span>
                <span class="log-sender">&lt;${l.sender}&gt;</span>
                <span class="log-topic" style="${getTopicStyle(l.topic)}">[${l.topic.toUpperCase()}]</span>
                <span class="log-data">${l.dataStr}</span>
            `;
            terminal.appendChild(entry);
        });
        terminal.scrollTop = terminal.scrollHeight;
    }

    function appendTerminalLog(sender, topic, dataStr, customStyle = "") {
        const terminal = document.getElementById("terminal-logs");
        const entry = document.createElement("div");
        entry.className = "log-entry";
        const time = new Date().toLocaleTimeString();
        entry.innerHTML = `
            <span class="log-time">[${time}]</span>
            <span class="log-sender">&lt;${sender}&gt;</span>
            <span class="log-topic" style="${customStyle}">[${topic.toUpperCase()}]</span>
            <span class="log-data">${dataStr}</span>
        `;
        terminal.appendChild(entry);
        terminal.scrollTop = terminal.scrollHeight;

        if (terminal.children.length > 100) {
            terminal.removeChild(terminal.firstChild);
        }
    }

    function getTopicStyle(topic) {
        switch (topic) {
            case "market_data": return "color: var(--text-muted); font-size: 0.75rem;";
            case "order_request": return "color: var(--accent-blue);";
            case "order_approved": return "color: var(--accent-green); font-weight: bold;";
            case "order_rejected": return "color: var(--accent-red); font-weight: bold;";
            case "order_filled": return "color: var(--accent-green); font-weight: bold; text-shadow: 0 0 5px rgba(0,245,160,0.3)";
            case "risk_alert": return "color: var(--accent-orange); font-weight: bold;";
            case "execution_failed": return "color: var(--accent-red); font-weight: bold;";
            case "employee_activated": return "color: var(--accent-blue); font-weight: bold;";
            default: return "";
        }
    }
});
