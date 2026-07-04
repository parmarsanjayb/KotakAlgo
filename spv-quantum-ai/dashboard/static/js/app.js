// SPV Quantum AI Operating System - Dashboard Controller

document.addEventListener("DOMContentLoaded", () => {
    // API endpoint paths
    const apiBase = window.location.origin;
    const wsUri = `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;

    // Elements
    const connectionStatus = document.getElementById("connection-status");
    const indicator = document.getElementById("status-indicator");
    const terminalLogs = document.getElementById("terminal-logs");
    const tradesBody = document.getElementById("trades-body");
    const ordersBody = document.getElementById("orders-body");
    const orderForm = document.getElementById("order-form");

    // Paper Trading Elements
    const paperStatusVal = document.getElementById("paper-status-val");
    const paperSessionVal = document.getElementById("paper-session-val");
    const paperMetrics = document.getElementById("paper-metrics");
    const paperCapVal = document.getElementById("paper-cap-val");
    const paperPnlVal = document.getElementById("paper-pnl-val");
    const paperWinVal = document.getElementById("paper-win-val");
    const paperTradesVal = document.getElementById("paper-trades-val");
    const paperSetupForm = document.getElementById("paper-setup-form");
    const btnPaperStart = document.getElementById("btn-paper-start");
    const btnPaperStop = document.getElementById("btn-paper-stop");

    // Track active prices
    const prices = {};

    // Initial state fetching
    fetchInitialData();

    // Setup WebSocket
    let ws;
    function connectWs() {
        console.log("Connecting WebSocket...");
        ws = new WebSocket(wsUri);

        ws.onopen = () => {
            console.log("WebSocket connected.");
            connectionStatus.textContent = "CONNECTED";
            indicator.classList.remove("disconnected");
            appendTerminalLog("system", "websocket", "WebSocket stream established successfully.");
        };

        ws.onmessage = (event) => {
            const payload = JSON.parse(event.data);
            handleBusEvent(payload);
        };

        ws.onclose = () => {
            console.warn("WebSocket closed. Attempting reconnect in 3s...");
            connectionStatus.textContent = "DISCONNECTED";
            indicator.classList.add("disconnected");
            appendTerminalLog("system", "websocket", "WebSocket disconnected. Reconnecting...", "color: var(--accent-red)");
            setTimeout(connectWs, 3000);
        };

        ws.onerror = (err) => {
            console.error("WebSocket error:", err);
        };
    }

    connectWs();

    // Event routing
    function handleBusEvent(event) {
        const { topic, sender, timestamp, data } = event;
        const formattedTime = new Date(timestamp).toLocaleTimeString();

        // Print to live log terminal
        appendTerminalLog(sender, topic, JSON.stringify(data), getTopicStyle(topic));

        // Trigger updates based on event category
        if (topic === "market_data") {
            updatePriceWidget(data);
        } else if (topic === "order_filled") {
            appendLiveTrade(data, formattedTime);
            refreshOrders(); // Refresh order table
        } else if (topic === "order_approved") {
            refreshOrders();
        } else if (topic === "order_rejected" || topic === "risk_alert") {
            highlightRiskNotification(data);
        } else if (topic && topic.startsWith("paper_")) {
            fetchPaperStatus();
            if (topic === "paper_order_filled") {
                appendLiveTrade({
                    order_id: data.order_id,
                    symbol: data.symbol,
                    side: data.side,
                    price: data.price,
                    quantity: data.quantity,
                    broker: "PaperBroker"
                }, formattedTime);
                refreshOrders();
            }
        }
    }

    // Refresh orders table
    async function refreshOrders() {
        try {
            const res = await fetch(`${apiBase}/api/orders?limit=15`);
            const orders = await res.json();
            renderOrdersTable(orders);
        } catch (e) {
            console.error("Failed to refresh orders table", e);
        }
    }

    // Initial data fetcher
    async function fetchInitialData() {
        try {
            const [ordersRes, tradesRes] = await Promise.all([
                fetch(`${apiBase}/api/orders?limit=15`),
                fetch(`${apiBase}/api/trades?limit=15`),
                fetchPaperStatus()
            ]);
            
            const orders = await ordersRes.json();
            const trades = await tradesRes.json();

            renderOrdersTable(orders);
            renderTradesTable(trades);
        } catch (e) {
            console.error("Failed fetching initial dashboard tables", e);
        }
    }

    // Update Paper UI representation
    function updatePaperUI(status) {
        if (status.is_running) {
            paperStatusVal.textContent = "ACTIVE";
            paperStatusVal.style.color = "var(--accent-green)";
            paperSessionVal.textContent = status.session_id;
            paperCapVal.textContent = `$${status.virtual_capital.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            
            paperPnlVal.textContent = `${status.virtual_pnl >= 0 ? '+' : ''}$${status.virtual_pnl.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            if (status.virtual_pnl > 0) {
                paperPnlVal.style.color = "var(--accent-green)";
            } else if (status.virtual_pnl < 0) {
                paperPnlVal.style.color = "var(--accent-red)";
            } else {
                paperPnlVal.style.color = "var(--text-primary)";
            }
            
            paperWinVal.textContent = `${(status.win_rate * 100).toFixed(1)}%`;
            paperTradesVal.textContent = status.trades_executed;

            paperMetrics.style.display = "grid";
            paperSetupForm.style.display = "none";
            btnPaperStop.style.display = "block";
        } else {
            paperStatusVal.textContent = "INACTIVE";
            paperStatusVal.style.color = "var(--accent-red)";
            paperSessionVal.textContent = "-";
            paperMetrics.style.display = "none";
            paperSetupForm.style.display = "block";
            btnPaperStop.style.display = "none";
        }
    }

    // Get current paper trading status
    async function fetchPaperStatus() {
        try {
            const res = await fetch(`${apiBase}/api/paper/status`);
            const status = await res.json();
            updatePaperUI(status);
            return status;
        } catch (e) {
            console.error("Failed to fetch paper trading status", e);
        }
    }

    // Set up paper button click handlers
    btnPaperStart.addEventListener("click", async () => {
        const capital = parseFloat(document.getElementById("paper-capital").value);
        const latency = parseFloat(document.getElementById("paper-latency").value);
        const slippage = parseFloat(document.getElementById("paper-slippage").value);

        const payload = {
            initial_capital: capital,
            latency_ms: latency,
            slippage_pct: slippage / 100.0
        };

        try {
            const res = await fetch(`${apiBase}/api/paper/start`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.status === "SUCCESS") {
                appendTerminalLog("paper_trading_engine", "session_started", `Started virtual paper session: ${data.session_id}`, "color: var(--accent-green)");
                await fetchPaperStatus();
            } else {
                alert("Failed to start session: " + data.message);
            }
        } catch (err) {
            console.error("Failed starting paper trading:", err);
        }
    });

    btnPaperStop.addEventListener("click", async () => {
        try {
            const res = await fetch(`${apiBase}/api/paper/stop`, {
                method: "POST"
            });
            const data = await res.json();
            if (data.status === "SUCCESS") {
                appendTerminalLog("paper_trading_engine", "session_stopped", "Stopped virtual paper trading session.", "color: var(--accent-red)");
                await fetchPaperStatus();
            }
        } catch (err) {
            console.error("Failed stopping paper trading:", err);
        }
    });

    // Render tables
    function renderOrdersTable(orders) {
        ordersBody.innerHTML = "";
        orders.forEach(o => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td style="font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;">${o.id}</td>
                <td><b>${o.symbol}</b></td>
                <td><span style="color: ${o.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)'}">${o.side}</span></td>
                <td>${o.type}</td>
                <td>${o.price ? '$' + o.price.toFixed(2) : 'MARKET'}</td>
                <td>${o.quantity}</td>
                <td><span style="color: ${getStatusColor(o.status)}">${o.status}</span></td>
                <td>${o.broker}</td>
            `;
            ordersBody.appendChild(tr);
        });
    }

    function renderTradesTable(trades) {
        tradesBody.innerHTML = "";
        trades.forEach(t => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td style="font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;">${t.id}</td>
                <td><b>${t.symbol}</b></td>
                <td><span style="color: ${t.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)'}">${t.side}</span></td>
                <td>$${t.price.toFixed(2)}</td>
                <td>${t.quantity}</td>
                <td>$${t.commission.toFixed(4)}</td>
                <td>${new Date(t.executed_at).toLocaleTimeString()}</td>
                <td>${t.broker}</td>
            `;
            tradesBody.appendChild(tr);
        });
    }

    function appendLiveTrade(trade, timeStr) {
        const tr = document.createElement("tr");
        tr.style.animation = "fadeIn 0.5s ease";
        tr.innerHTML = `
            <td style="font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;">${trade.order_id}</td>
            <td><b>${trade.symbol}</b></td>
            <td><span style="color: ${trade.side === 'BUY' ? 'var(--accent-green)' : 'var(--accent-red)'}">${trade.side}</span></td>
            <td>$${trade.price.toFixed(2)}</td>
            <td>${trade.quantity}</td>
            <td>-</td>
            <td>${timeStr}</td>
            <td>${trade.broker}</td>
        `;
        tradesBody.insertBefore(tr, tradesBody.firstChild);
        if (tradesBody.children.length > 15) {
            tradesBody.removeChild(tradesBody.lastChild);
        }
    }

    // Update Price widget cards
    function updatePriceWidget(tick) {
        const { symbol, close } = tick;
        const prevPrice = prices[symbol];
        prices[symbol] = close;

        let card = document.getElementById(`price-card-${symbol}`);
        if (!card) {
            // Create element card if not existing
            card = document.createElement("div");
            card.id = `price-card-${symbol}`;
            card.className = "price-card";
            card.innerHTML = `
                <div class="symbol">${symbol}</div>
                <div class="price" id="price-val-${symbol}">$${close.toFixed(2)}</div>
                <div class="change change-up" id="price-change-${symbol}">+0.00%</div>
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
                    priceChange.className = "change change-up";
                    priceVal.style.color = "var(--accent-green)";
                } else {
                    priceChange.className = "change change-down";
                    priceVal.style.color = "var(--accent-red)";
                }
                // Transition price back to default white after 300ms
                setTimeout(() => { priceVal.style.color = ""; }, 300);
            }
        }
    }

    // Scroll logger helper
    function appendTerminalLog(sender, topic, dataStr, customStyle = "") {
        const entry = document.createElement("div");
        entry.className = "log-entry";
        const time = new Date().toLocaleTimeString();
        entry.innerHTML = `
            <span class="log-time">[${time}]</span>
            <span class="log-sender">&lt;${sender}&gt;</span>
            <span class="log-topic" style="${customStyle}">[${topic.toUpperCase()}]</span>
            <span class="log-data">${dataStr}</span>
        `;
        terminalLogs.appendChild(entry);
        terminalLogs.scrollTop = terminalLogs.scrollHeight;

        if (terminalLogs.children.length > 100) {
            terminalLogs.removeChild(terminalLogs.firstChild);
        }
    }

    // Form execution submission
    orderForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const symbol = document.getElementById("order-symbol").value;
        const side = document.getElementById("order-side").value;
        const quantity = parseFloat(document.getElementById("order-qty").value);
        const priceVal = document.getElementById("order-price").value;
        const type = document.getElementById("order-type").value;

        const payload = {
            symbol: symbol,
            side: side,
            quantity: quantity,
            type: type,
            price: priceVal ? parseFloat(priceVal) : null
        };

        try {
            const res = await fetch(`${apiBase}/api/order`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            console.log("Order placement response:", data);
        } catch (err) {
            console.error("Order submission failed:", err);
            appendTerminalLog("web_api", "error", `Order placement failed: ${err.message}`, "color: var(--accent-red)");
        }
    });

    // Helper aesthetics styles mapping
    function getTopicStyle(topic) {
        switch (topic) {
            case "market_data": return "color: var(--text-muted); font-size: 0.75rem;";
            case "order_request": return "color: var(--accent-blue);";
            case "order_approved": return "color: var(--accent-green); font-weight: bold;";
            case "order_rejected": return "color: var(--accent-red); font-weight: bold;";
            case "order_filled": return "color: var(--accent-green); font-weight: bold; text-shadow: 0 0 5px rgba(0,245,160,0.3)";
            case "risk_alert": return "color: var(--accent-orange); font-weight: bold; background: rgba(255,159,10,0.1); border-radius: 4px; padding: 1px 4px;";
            case "execution_failed": return "color: var(--accent-red); font-weight: bold;";
            case "paper_trade_started": return "color: var(--accent-blue); font-weight: bold;";
            case "paper_order_placed": return "color: var(--accent-orange); font-weight: bold;";
            case "paper_order_filled": return "color: var(--accent-green); font-weight: bold;";
            case "paper_trade_closed": return "color: var(--accent-blue); font-weight: bold;";
            case "paper_trade_stopped": return "color: var(--accent-red); font-weight: bold;";
            default: return "";
        }
    }

    function getStatusColor(status) {
        switch (status) {
            case "FILLED": return "var(--accent-green)";
            case "PENDING": return "var(--accent-orange)";
            case "CANCELLED": return "var(--text-secondary)";
            case "REJECTED": return "var(--accent-red)";
            default: return "var(--text-primary)";
        }
    }

    function highlightRiskNotification(data) {
        // Aesthetic flash on log container
        terminalLogs.style.borderColor = "var(--accent-red)";
        setTimeout(() => { terminalLogs.style.borderColor = ""; }, 500);
    }
});
