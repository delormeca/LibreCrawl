// Application State
let crawlState = {
    isRunning: false,
    isPaused: false,
    startTime: null,
    baseUrl: null,
    urls: [],
    links: [],
    issues: [],
    stats: {
        discovered: 0,
        crawled: 0,
        depth: 0,
        speed: 0
    },
    filters: {
        active: null,
        urlSearch: '',
        issueFilter: 'all',
        linksFilter: {
            internalStatusCode: 'all',
            externalStatusCode: 'all',
            internalPlacement: 'all',
            externalPlacement: 'all',
            internalSearch: '',
            externalSearch: ''
        }
    }
};

// Incremental polling instance
let incrementalPoller = null;

// Virtual Scrollers
let virtualScrollers = {
    overview: null,
    internal: null,
    external: null,
    internalLinks: null,
    externalLinks: null,
    issues: null,
    content: null
};

// Initialize application
document.addEventListener('DOMContentLoaded', async function() {
    await initializeApp();
});

async function initializeApp() {
    // Load plugins first (before tabs are initialized)
    if (window.LibreCrawlPlugin && window.LibreCrawlPlugin.loader) {
        await window.LibreCrawlPlugin.loader.loadAllPlugins();
        window.LibreCrawlPlugin.loader.initializePlugins();
    }

    // Setup event listeners
    setupEventListeners();

    // Initialize tables
    initializeTables();

    // Load user info
    loadUserInfo();

    // Check if embeddings exist from a previous crawl
    checkEmbeddingsExist();

    // Check server for active session (loading, running, or completed crawl)
    try {
        const statusResponse = await fetch('/api/crawl_status');
        const statusData = await statusResponse.json();

        if (statusData.status === 'loading') {
            // A DB load is in progress — start incremental polling
            console.log('Detected loading state, starting incremental polling');

            clearAllTables();
            resetStats();

            crawlState.urls = [];
            crawlState.links = [];
            crawlState.issues = [];
            crawlState.stats = statusData.stats || {};
            crawlState.baseUrl = statusData.stats?.baseUrl || '';

            if (crawlState.baseUrl) {
                document.getElementById('urlInput').value = crawlState.baseUrl;
            }

            // Process first batch of data
            if (statusData.urls && statusData.urls.length > 0) {
                statusData.urls.forEach(url => addUrlToTable(url));
            }

            // Initialize incremental poller — reset first to clear any stale data
            incrementalPoller = new IncrementalPoller();
            incrementalPoller.allUrls = statusData.urls || [];
            incrementalPoller.allLinks = statusData.links || [];
            incrementalPoller.allIssues = statusData.issues || [];
            incrementalPoller.lastUrlCount = incrementalPoller.allUrls.length;
            incrementalPoller.lastLinkCount = incrementalPoller.allLinks.length;
            incrementalPoller.lastIssueCount = incrementalPoller.allIssues.length;

            // Show progress and start polling
            crawlState.isRunning = true;
            crawlState.isLoading = true;  // Flag to bypass IncrementalPoller in pollCrawlProgress
            crawlState.isPaused = false;
            showProgress();
            updateStatus('Loading crawl from database...');
            updateCrawlButtons();
            pollCrawlProgress();

        } else if (statusData.status !== 'idle' && statusData.urls && statusData.urls.length > 0) {
            // Running or completed crawl — restore UI (existing auto-reconnect logic)
            console.log('Auto-reconnect: server has crawl data, restoring UI');
            clearAllTables();
            resetStats();

            crawlState.urls = [];
            crawlState.stats = statusData.stats || {};
            crawlState.baseUrl = statusData.stats?.baseUrl || '';

            if (crawlState.baseUrl) {
                document.getElementById('urlInput').value = crawlState.baseUrl;
            }

            statusData.urls.forEach(url => addUrlToTable(url));

            if (statusData.links && statusData.links.length > 0) {
                crawlState.pendingLinks = statusData.links;
            }
            if (statusData.issues && statusData.issues.length > 0) {
                crawlState.pendingIssues = statusData.issues;
            }

            updateStatsDisplay();
            updateFilterCounts();
            updateStatusCodesTable();

            if (statusData.status === 'running') {
                crawlState.isRunning = true;
                crawlState.isPaused = false;
                crawlState.startTime = new Date();
                if (!incrementalPoller) incrementalPoller = new IncrementalPoller();
                incrementalPoller.lastUrlCount = statusData.urls.length;
                incrementalPoller.allUrls = statusData.urls;
                showProgress();
                pollCrawlProgress();
                updateStatus('Reconnected — crawl in progress');
            } else {
                updateStatus(`Restored: ${statusData.urls.length} URLs from previous crawl`);
            }

            updateCrawlButtons();
        }
    } catch (e) {
        // Silent — server may have no data, this is fine on a fresh session
    }

    // Set initial focus
    document.getElementById('urlInput').focus();

    console.log('LibreCrawl initialized');
}

function setupEventListeners() {
    // URL input enter key
    document.getElementById('urlInput').addEventListener('keypress', handleUrlKeypress);

    // Update timer every second when crawling
    setInterval(updateTimer, 1000);

    // Warn before closing tab while crawl is actively running
    // The server continues crawling either way — this just prevents accidental disconnects
    window.addEventListener('beforeunload', function(e) {
        if (crawlState.isRunning && !crawlState.isPaused) {
            e.preventDefault();
            e.returnValue = '';
            return '';
        }
    });
}

function handleUrlKeypress(event) {
    if (event.key === 'Enter' && !crawlState.isRunning) {
        toggleCrawl();
    }
}

function toggleCrawl() {
    if (!crawlState.isRunning) {
        startCrawl();
    } else if (crawlState.isPaused) {
        resumeCrawl();
    } else {
        pauseCrawl();
    }
}

function startCrawl() {
    const urlInput = document.getElementById('urlInput');
    let url = urlInput.value.trim();

    if (!url) {
        alert('Please enter a URL to crawl');
        urlInput.focus();
        return;
    }

    // Normalize the URL - add protocol if missing
    url = normalizeUrl(url);

    if (!isValidUrl(url)) {
        alert('Please enter a valid URL or domain');
        urlInput.focus();
        return;
    }

    // Update the input field with the normalized URL
    urlInput.value = url;

    crawlState.isRunning = true;
    crawlState.isPaused = false;
    crawlState.startTime = new Date();
    crawlState.baseUrl = url;

    // Initialize incremental poller for new crawl
    if (!incrementalPoller) {
        incrementalPoller = new IncrementalPoller();
    }
    incrementalPoller.reset();

    // Update UI
    updateCrawlButtons();
    showProgress();
    updateStatus('Starting crawl...');

    // Clear previous data
    clearAllTables();
    resetStats();

    // Start the actual crawling via Python backend
    startPythonCrawl(url);
}

function pauseCrawl() {
    crawlState.isPaused = true;
    updateCrawlButtons();
    updateStatus('Crawl paused');

    // Pause Python crawler
    fetch('/api/pause_crawl', {
        method: 'POST'
    }).catch(error => {
        console.error('Error pausing crawl:', error);
    });
}

function resumeCrawl() {
    crawlState.isPaused = false;
    updateCrawlButtons();
    updateStatus('Resuming crawl...');

    // Resume Python crawler
    fetch('/api/resume_crawl', {
        method: 'POST'
    }).catch(error => {
        console.error('Error resuming crawl:', error);
    });
}

function stopCrawl() {
    crawlState.isRunning = false;
    crawlState.isPaused = false;

    // Update UI
    updateCrawlButtons();
    hideProgress();
    updateStatus('Crawl stopped');

    // Stop Python crawler
    stopPythonCrawl();
}

function toggleSitemapInput(event) {
    event.preventDefault();
    const container = document.getElementById('sitemapInputContainer');
    const toggle = document.getElementById('sitemapToggle');
    const isHidden = container.style.display === 'none';
    container.style.display = isHidden ? 'block' : 'none';
    toggle.textContent = isHidden ? '− Hide sitemap URLs' : '+ Add sitemap URLs';
}

function clearCrawlData() {
    if (crawlState.isRunning) {
        if (!confirm('A crawl is currently running. Stop the crawl and clear all data?')) {
            return;
        }
        stopCrawl();
    }

    // Clear all data
    clearAllTables();
    resetStats();
    crawlState.urls = [];
    crawlState.links = [];
    crawlState.issues = [];
    crawlState.baseUrl = null;
    crawlState.filters.active = null;
    crawlState.pendingLinks = null;
    crawlState.pendingIssues = null;
    updateStatusCodesTable();

    // Clear issues and reset badge
    window.currentIssues = [];
    updateIssuesTable([]);  // This will also clear the badge

    // Reset issue filter counts
    document.getElementById('issues-all-count').textContent = '(0)';
    document.getElementById('issues-error-count').textContent = '(0)';
    document.getElementById('issues-warning-count').textContent = '(0)';
    document.getElementById('issues-info-count').textContent = '(0)';

    // Clear visualization
    if (typeof window.clearVisualization === 'function') {
        window.clearVisualization();
    }

    // Notify plugins of data clear (send empty data)
    if (window.LibreCrawlPlugin && window.LibreCrawlPlugin.loader) {
        window.LibreCrawlPlugin.loader.notifyDataUpdate({
            urls: [],
            links: [],
            issues: [],
            stats: { discovered: 0, crawled: 0, depth: 0, speed: 0 }
        });
    }

    // Clear filter states
    document.querySelectorAll('.filter-item').forEach(item => {
        item.classList.remove('active');
    });

    // Reset the "All Issues" filter to active
    document.querySelector('[data-filter="all"]')?.classList.add('active');

    // Update UI
    updateStatus('Data cleared');
    hideProgress();
    updateCrawlButtons(); // Update save/load button states

    // Reset URL input
    document.getElementById('urlInput').value = '';
    document.getElementById('urlInput').focus();

    // Clear sitemap textarea
    const sitemapTextarea = document.getElementById('sitemapTextarea');
    if (sitemapTextarea) sitemapTextarea.value = '';
}

function startPythonCrawl(url) {
    const cvMode = document.getElementById('contentVectorizationMode')?.checked || false;
    const lgMode = document.getElementById('linkgraphMode')?.checked || false;

    // Collect user-provided sitemap URLs
    const sitemapText = document.getElementById('sitemapTextarea')?.value || '';
    const sitemapUrls = sitemapText.split('\n').map(u => u.trim()).filter(u => u.length > 0);

    // Call Python backend to start crawling
    fetch('/api/start_crawl', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ url: url, contentVectorizationMode: cvMode, linkgraphMode: lgMode, sitemapUrls: sitemapUrls })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            updateStatus('Crawling in progress...');
            // Refresh user info to update crawl count
            loadUserInfo();
            // Start polling for updates
            pollCrawlProgress();
        } else {
            updateStatus('Error: ' + data.error);
            stopCrawl();
        }
    })
    .catch(error => {
        console.error('Error starting crawl:', error);
        updateStatus('Error starting crawl');
        stopCrawl();
    });
}

function stopPythonCrawl() {
    fetch('/api/stop_crawl', {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        console.log('Crawl stopped:', data);
    })
    .catch(error => {
        console.error('Error stopping crawl:', error);
    });
}

function pollCrawlProgress() {
    if (!crawlState.isRunning) return;

    // During DB loading, fetch directly (server manages offsets, we only need delta)
    // During live crawl, use IncrementalPoller for its offset tracking
    let fetchPromise;
    if (crawlState.isLoading) {
        fetchPromise = fetch('/api/crawl_status').then(response => response.json());
    } else if (incrementalPoller) {
        fetchPromise = incrementalPoller.fetchUpdate();
    } else {
        fetchPromise = fetch('/api/crawl_status').then(response => response.json());
    }

    fetchPromise
        .then(data => {
            // Skip updateCrawlData during DB loading — the loading branch
            // accumulates links/issues incrementally, updateCrawlData would replace them.
            // Also skip when loading just completed (isLoading flag still true) to preserve accumulated data.
            if (data.status !== 'loading' && !crawlState.isLoading) {
                updateCrawlData(data);
            }

            // Update bottom status bar based on current state
            if (data.is_running_pagespeed) {
                updateStatus('Running PageSpeed analysis...');
            } else if (data.status === 'running') {
                const stats = data.stats || {};
                const crawled = stats.crawled || 0;
                const discovered = stats.discovered || 0;
                const speed = stats.speed || 0;
                const sitemapCount = stats.sitemap_url_count || 0;

                if (discovered === 0 && crawled === 0) {
                    updateStatus('Parsing sitemaps...');
                } else if (discovered > 0 && crawled === 0) {
                    const sitemapNote = sitemapCount > 0 ? `Sitemap: ${sitemapCount} URLs locked in. ` : '';
                    updateStatus(`${sitemapNote}${discovered} URLs discovered. Starting crawl...`);
                } else {
                    const rate = speed > 0 ? speed.toFixed(2) : '—';
                    const sitemapNote = sitemapCount > 0 ? ` (${sitemapCount} from sitemap)` : '';
                    updateStatus(`Crawling... ${crawled}/${discovered} URLs — ${rate} URLs/sec${sitemapNote}`);
                }
            }

            // Update visualization if visualization tab is active
            const vizTab = document.getElementById('visualization-tab');
            if (vizTab && vizTab.classList.contains('active') && typeof loadVisualizationData === 'function') {
                loadVisualizationData();
            }

            if (data.status === 'demo_stopped' || data.demo_stopped) {
                stopCrawl();
                updateStatus('Demo limit reached — crawl data saved');
                showDemoLimitNotification();
                if (typeof loadVisualizationData === 'function') {
                    loadVisualizationData();
                }
            } else if (data.status === 'loading') {
                // DB load in progress — process delta directly (not through updateCrawlData)
                crawlState.stats = data.stats || crawlState.stats;
                updateStatsDisplay();

                // Append new URLs to table
                if (data.urls && data.urls.length > 0) {
                    data.urls.forEach(url => addUrlToTable(url));
                }

                // Accumulate links and issues (don't re-render — defer to tab switch)
                if (data.links && data.links.length > 0) {
                    crawlState.links = crawlState.links.concat(data.links);
                    crawlState.pendingLinks = crawlState.links;
                    if (isLinksTabActive()) {
                        updateLinksTable(crawlState.links);
                    }
                }
                if (data.issues && data.issues.length > 0) {
                    crawlState.issues = crawlState.issues.concat(data.issues);
                    crawlState.pendingIssues = crawlState.issues;
                    if (isIssuesTabActive()) {
                        updateIssuesTable(crawlState.issues);
                    }
                }

                updateFilterCounts();
                updateProgress(data.progress || 0);
                const pct = Math.round(data.progress || 0);
                updateStatus(`Loading crawl from database... ${pct}%`);
                setTimeout(pollCrawlProgress, 500); // Poll faster for loads
            } else if (crawlState.isRunning && data.status !== 'completed') {
                setTimeout(pollCrawlProgress, 1000); // Poll every second
            } else if (data.status === 'completed') {
                // Finalize accumulated data from DB loading
                if (crawlState.isLoading) {
                    // Process last batch
                    if (data.links && data.links.length > 0) {
                        crawlState.links = crawlState.links.concat(data.links);
                    }
                    if (data.issues && data.issues.length > 0) {
                        crawlState.issues = crawlState.issues.concat(data.issues);
                    }
                    if (data.urls && data.urls.length > 0) {
                        data.urls.forEach(url => addUrlToTable(url));
                    }
                    // Load accumulated links/issues into their tables
                    crawlState.pendingLinks = crawlState.links;
                    crawlState.pendingIssues = crawlState.issues;
                    console.log(`DB load complete: ${crawlState.links.length} links, ${crawlState.issues.length} issues accumulated`);
                }
                // Clear loading flag
                crawlState.isLoading = false;
                stopCrawl();
                updateStatus('Crawl completed');
                // Update visualization one final time when crawl completes
                if (typeof loadVisualizationData === 'function') {
                    loadVisualizationData();
                }
                // Notify plugins that crawl is complete
                if (window.LibreCrawlPlugin && window.LibreCrawlPlugin.loader) {
                    window.LibreCrawlPlugin.loader.notifyCrawlComplete({
                        urls: crawlState.urls,
                        links: crawlState.links,
                        issues: crawlState.issues,
                        stats: crawlState.stats
                    });
                }
                // Start embedding progress polling if content vectorization was on
                const cvMode = document.getElementById('contentVectorizationMode')?.checked || false;
                if (cvMode) {
                    pollEmbeddingProgress();
                }
            }
        })
        .catch(error => {
            console.error('Error polling crawl status:', error);
            // Continue polling even if there's an error (common on large crawls)
            if (crawlState.isRunning) {
                setTimeout(pollCrawlProgress, 1000);
            }
        });
}

function pollEmbeddingProgress() {
    fetch('/api/embed_status')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'embedding') {
                document.getElementById('progressContainer').style.display = 'flex';
                document.getElementById('progressText').textContent = `Embedding ${data.current}/${data.total} pages...`;
                const pct = data.total > 0 ? (data.current / data.total * 100) : 0;
                document.getElementById('progressFill').style.width = pct + '%';
                setTimeout(pollEmbeddingProgress, 1000);
            } else if (data.status === 'done') {
                const msg = data.failed > 0
                    ? `Embedding complete. ${data.current} pages embedded, ${data.failed} failed.`
                    : `Embedding complete. ${data.current} pages embedded.`;
                document.getElementById('progressText').textContent = msg;
                updateStatus(msg);
                showEmbeddingsTab();
                loadEmbeddingsList();
            }
        });
}

function showEmbeddingsTab() {
    const btn = document.getElementById('embeddingsTabBtn');
    if (btn) btn.style.display = '';
}

function loadEmbeddingsList() {
    fetch('/api/embeddings_list')
        .then(r => r.json())
        .then(data => {
            const tbody = document.querySelector('#embeddingsTable tbody');
            const empty = document.getElementById('embeddingsEmpty');
            const count = document.getElementById('embeddingsCount');
            if (!data.pages || data.pages.length === 0) {
                tbody.innerHTML = '';
                empty.style.display = 'block';
                count.textContent = '';
                return;
            }
            empty.style.display = 'none';
            count.textContent = `${data.total} pages embedded`;
            tbody.innerHTML = data.pages.map(p => `
                <tr>
                    <td style="word-break:break-all; max-width:400px;" title="${p.url}">${p.url}</td>
                    <td>${p.title || ''}</td>
                    <td>${p.token_count || 0}</td>
                </tr>
            `).join('');
        });
}

function checkEmbeddingsExist() {
    fetch('/api/embed_status')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'done' && data.current > 0) {
                showEmbeddingsTab();
                loadEmbeddingsList();
            }
        });
}

function updateCrawlData(data) {
    // Update statistics
    crawlState.stats = data.stats || crawlState.stats;
    updateStatsDisplay();

    // Update memory statistics
    if (data.memory && data.memory_data) {
        updateMemoryDisplay(data.memory, data.memory_data);
    }

    // Update tables with new URLs
    if (data.urls) {
        data.urls.forEach(url => {
            addUrlToTable(url);
        });
    }

    // Update links tables only if Links tab is active to improve performance
    if (data.links) {
        // Always store links data in crawlState
        crawlState.links = data.links;
        if (isLinksTabActive()) {
            updateLinksTable(data.links);
        } else {
            // Store in pendingLinks for lazy loading when switching to tab
            crawlState.pendingLinks = data.links;
        }
    }

    // Update issues table only if Issues tab is active
    if (data.issues) {
        // Always store issues data in crawlState
        crawlState.issues = data.issues;
        if (isIssuesTabActive()) {
            updateIssuesTable(data.issues);
        } else {
            // Store in pendingIssues for lazy loading when switching to tab
            crawlState.pendingIssues = data.issues;
        }
    }

    // Update filter counts
    updateFilterCounts();

    // Update status codes table (respecting active filter)
    updateStatusCodesTable(crawlState.filters.active);

    // Update progress and status text
    updateProgress(data.progress || 0);
    updateProgressText(data);

    // Update PageSpeed results if available
    if (data.stats && data.stats.pagespeed_results) {
        displayPageSpeedResults(data.stats.pagespeed_results);
    }

    // Notify plugins of data update
    if (window.LibreCrawlPlugin && window.LibreCrawlPlugin.loader) {
        window.LibreCrawlPlugin.loader.notifyDataUpdate({
            urls: crawlState.urls,
            links: crawlState.links,
            issues: crawlState.issues,
            stats: crawlState.stats
        });
    }
}

function updateProgressText(data) {
    const progressText = document.getElementById('progressText');
    if (!progressText) return;

    if (data.is_running_pagespeed) {
        progressText.textContent = 'Running PageSpeed analysis...';
    } else if (data.status === 'completed') {
        progressText.textContent = 'Crawl completed';
    } else if (data.status === 'running') {
        const stats = data.stats || crawlState.stats;
        if (stats.crawled === 0) {
            progressText.textContent = 'Starting crawl...';
        } else if (stats.discovered > stats.crawled) {
            progressText.textContent = `Crawling... (${stats.crawled}/${stats.discovered} URLs)`;
        } else {
            progressText.textContent = `Finishing up... (${stats.crawled} URLs crawled)`;
        }
    } else {
        progressText.textContent = 'Initializing...';
    }
}

function updateStatsDisplay() {
    document.getElementById('discoveredCount').textContent = crawlState.stats.discovered;
    document.getElementById('crawledCount').textContent = crawlState.stats.crawled;
    document.getElementById('crawlDepth').textContent = crawlState.stats.depth;
    document.getElementById('crawlSpeed').textContent = crawlState.stats.speed + ' URLs/sec';
}

function updateMemoryDisplay(memoryData, memoryDataSizes) {
    if (!memoryData || !memoryDataSizes) return;

    // Actual data size (deep measurement)
    const dataMB = memoryDataSizes.total_deep_mb || 0;
    document.getElementById('memCurrent').textContent = dataMB.toFixed(1) + ' MB';

    // KB per URL (actual data)
    const kbPerUrl = memoryDataSizes.avg_per_url_kb || 0;
    document.getElementById('memPeak').textContent = kbPerUrl.toFixed(1) + ' KB/URL';

    // Estimate for 1M URLs (data only)
    const estimate1M = (kbPerUrl * 1000000) / 1024; // Convert to MB
    const estimate1MDisplay = estimate1M > 1024
        ? (estimate1M / 1024).toFixed(1) + ' GB'
        : estimate1M.toFixed(0) + ' MB';
    document.getElementById('memEstimate1M').textContent = estimate1MDisplay;

    // System available
    const availableMB = memoryData.system?.available_mb || 0;
    document.getElementById('memAvailable').textContent = availableMB.toFixed(0) + ' MB';
}

function updateCrawlButtons() {
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const clearBtn = document.getElementById('clearBtn');
    const saveCrawlBtn = document.getElementById('saveCrawlBtn');
    const loadCrawlBtn = document.getElementById('loadCrawlBtn');

    if (crawlState.isRunning) {
        if (crawlState.isPaused) {
            startBtn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M8 5v14l11-7z"/>
                </svg>
                Resume
            `;
        } else {
            startBtn.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <rect x="6" y="4" width="4" height="16"/>
                    <rect x="14" y="4" width="4" height="16"/>
                </svg>
                Pause
            `;
        }
        startBtn.disabled = false;
        stopBtn.disabled = false;
        clearBtn.disabled = false;
        saveCrawlBtn.disabled = true; // Disable during crawl
        loadCrawlBtn.disabled = true; // Disable during crawl
    } else {
        startBtn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <path d="M8 5v14l11-7z"/>
            </svg>
            Start
        `;
        startBtn.disabled = false;
        stopBtn.disabled = true;
        clearBtn.disabled = false;

        // Save button: only enabled if crawl is completed and has data
        const hasData = crawlState.stats.crawled > 0;
        saveCrawlBtn.disabled = !hasData;

        // Load button: only enabled if no current crawl data
        loadCrawlBtn.disabled = hasData;
    }
}

function showProgress() {
    document.getElementById('progressContainer').style.display = 'flex';
}

function hideProgress() {
    document.getElementById('progressContainer').style.display = 'none';
}

function updateProgress(percentage) {
    document.getElementById('progressFill').style.width = percentage + '%';
}

function updateStatus(message) {
    document.getElementById('statusText').textContent = message;
}

function showDemoLimitNotification() {
    // Remove existing demo notification if any
    const existing = document.getElementById('demoLimitNotification');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'demoLimitNotification';
    overlay.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.6); z-index: 10000;
        display: flex; align-items: center; justify-content: center;
    `;

    const box = document.createElement('div');
    box.style.cssText = `
        background: #1a1a2e; border: 1px solid #f59e0b; border-radius: 12px;
        padding: 32px 40px; max-width: 480px; text-align: center;
        color: #e0e0e0; box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    `;
    box.innerHTML = `
        <div style="font-size: 28px; margin-bottom: 12px;">&#9888;</div>
        <h2 style="color: #f59e0b; margin: 0 0 12px; font-size: 18px;">Demo Memory Limit Reached</h2>
        <p style="margin: 0 0 16px; line-height: 1.5; font-size: 14px;">
            This user has reached the 1.5 GB per-user memory limit.<br>
            Your crawl data has been saved automatically.<br><br>
            <strong>This is a free demo and is not intended for production use.</strong>
        </p>
        <button id="demoLimitDismiss" style="
            background: #f59e0b; color: #000; border: none; padding: 10px 28px;
            border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 14px;
        ">OK</button>
    `;

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    document.getElementById('demoLimitDismiss').addEventListener('click', function() {
        overlay.remove();
    });
}

function updateTimer() {
    if (crawlState.isRunning && crawlState.startTime) {
        const elapsed = new Date() - crawlState.startTime;
        const minutes = Math.floor(elapsed / 60000);
        const seconds = Math.floor((elapsed % 60000) / 1000);
        document.getElementById('crawlTime').textContent =
            `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    }
}

// Table Management
function initializeTables() {
    // Clear any existing data first
    clearAllTables();
    // Initialize virtual scrollers for all tables
    initializeVirtualScrollers();
    // Initialize column resizers and column copy after virtual scrollers
    setTimeout(() => {
        if (window.initializeColumnResizers) {
            initializeColumnResizers();
        }
        setupColumnCopy();
        setupColumnSort();
    }, 100);
}

function initializeVirtualScrollers() {
    try {
        // Overview table
        const overviewContainer = document.querySelector('#overview-tab .table-container');
        if (overviewContainer && overviewContainer.querySelector('tbody')) {
            virtualScrollers.overview = new VirtualScroller(overviewContainer, {
                rowHeight: 100,
                buffer: 25,
                renderRow: renderOverviewRow
            });
            console.log('Overview virtual scroller initialized');
        }

        // Internal URLs table
        const internalContainer = document.querySelector('#internal-tab .table-container');
        if (internalContainer && internalContainer.querySelector('tbody')) {
            virtualScrollers.internal = new VirtualScroller(internalContainer, {
                rowHeight: 80,
                buffer: 25,
                renderRow: renderInternalRow
            });
            console.log('Internal virtual scroller initialized');
        }

        // External URLs table
        const externalContainer = document.querySelector('#external-tab .table-container');
        if (externalContainer && externalContainer.querySelector('tbody')) {
            virtualScrollers.external = new VirtualScroller(externalContainer, {
                rowHeight: 80,
                buffer: 25,
                renderRow: renderExternalRow
            });
            console.log('External virtual scroller initialized');
        }

        // Internal Links table
        const internalLinksContainer = document.querySelector('#links-tab .internal-links-container');
        if (internalLinksContainer && internalLinksContainer.querySelector('tbody')) {
            virtualScrollers.internalLinks = new VirtualScroller(internalLinksContainer, {
                rowHeight: 80,
                buffer: 25,
                renderRow: renderInternalLinkRow
            });
            console.log('Internal links virtual scroller initialized');
        }

        // External Links table
        const externalLinksContainer = document.querySelector('#links-tab .external-links-container');
        if (externalLinksContainer && externalLinksContainer.querySelector('tbody')) {
            virtualScrollers.externalLinks = new VirtualScroller(externalLinksContainer, {
                rowHeight: 80,
                buffer: 25,
                renderRow: renderExternalLinkRow
            });
            console.log('External links virtual scroller initialized');
        }

        // Issues table
        const issuesContainer = document.querySelector('#issues-tab .table-container');
        if (issuesContainer && issuesContainer.querySelector('tbody')) {
            virtualScrollers.issues = new VirtualScroller(issuesContainer, {
                rowHeight: 80,
                buffer: 25,
                renderRow: renderIssueRow
            });
            console.log('Issues virtual scroller initialized');
        }

        // Content table
        const contentContainer = document.querySelector('#content-tab .table-container');
        if (contentContainer && contentContainer.querySelector('tbody')) {
            virtualScrollers.content = new VirtualScroller(contentContainer, {
                rowHeight: 100,
                buffer: 25,
                renderRow: renderContentRow
            });
            console.log('Content virtual scroller initialized');
        }
    } catch (error) {
        console.error('Error initializing virtual scrollers:', error);
    }
}

function isLinksTabActive() {
    const linksTab = document.getElementById('links-tab');
    return linksTab && linksTab.classList.contains('active');
}

function isIssuesTabActive() {
    const issuesTab = document.getElementById('issues-tab');
    return issuesTab && issuesTab.classList.contains('active');
}

function updateLinksTable(links) {
    // Create a lookup map of URL statuses from crawled URLs
    const urlStatusMap = new Map();
    if (crawlState.urls && crawlState.urls.length > 0) {
        crawlState.urls.forEach(url => {
            urlStatusMap.set(url.url, url.status_code);
        });
    }

    // Remove duplicates from links array (extra safety check)
    const uniqueLinks = [];
    const seenLinks = new Set();
    links.forEach(link => {
        const key = `${link.source_url}|${link.target_url}`;
        if (!seenLinks.has(key)) {
            seenLinks.add(key);

            // Update target status with actual crawled status if available
            const crawledStatus = urlStatusMap.get(link.target_url);
            if (crawledStatus) {
                link.target_status = crawledStatus;
            }

            uniqueLinks.push(link);
        }
    });

    // Store unfiltered links in crawlState
    crawlState.links = uniqueLinks;

    // Apply filters and update virtual scrollers
    applyLinksFilter();

    console.log(`Links loaded: ${crawlState.links.filter(l => l.is_internal).length} internal, ${crawlState.links.filter(l => !l.is_internal).length} external`);
}

function applyLinksFilter() {
    if (!crawlState.links || crawlState.links.length === 0) return;

    // Separate internal and external links
    let internalLinks = crawlState.links.filter(link => link.is_internal);
    let externalLinks = crawlState.links.filter(link => !link.is_internal);

    // Apply status code filter for internal links
    const internalStatusFilter = crawlState.filters.linksFilter.internalStatusCode;
    if (internalStatusFilter && internalStatusFilter !== 'all') {
        internalLinks = internalLinks.filter(link => {
            if (!link.target_status) return false;
            const status = parseInt(link.target_status);
            switch (internalStatusFilter) {
                case '2xx': return status >= 200 && status < 300;
                case '3xx': return status >= 300 && status < 400;
                case '4xx': return status >= 400 && status < 500;
                case '5xx': return status >= 500;
                default: return true;
            }
        });
    }

    // Apply placement filter for internal links (matches placement_detail for granularity)
    const internalPlacementFilter = crawlState.filters.linksFilter.internalPlacement;
    if (internalPlacementFilter && internalPlacementFilter !== 'all') {
        internalLinks = internalLinks.filter(link =>
            (link.placement_detail || link.placement || 'body') === internalPlacementFilter
        );
    }

    // Apply search filter for internal links (with column mode)
    const internalSearch = crawlState.filters.linksFilter.internalSearch.toLowerCase();
    if (internalSearch) {
        const searchMode = document.getElementById('internalLinkSearchMode')?.value || 'all';
        internalLinks = internalLinks.filter(link => {
            switch (searchMode) {
                case 'target': return link.target_url.toLowerCase().includes(internalSearch);
                case 'source': return link.source_url.toLowerCase().includes(internalSearch);
                case 'anchor': return (link.anchor_text && link.anchor_text.toLowerCase().includes(internalSearch));
                default: return (
                    link.source_url.toLowerCase().includes(internalSearch) ||
                    link.target_url.toLowerCase().includes(internalSearch) ||
                    (link.anchor_text && link.anchor_text.toLowerCase().includes(internalSearch))
                );
            }
        });
    }

    // Apply status code filter for external links
    const externalStatusFilter = crawlState.filters.linksFilter.externalStatusCode;
    if (externalStatusFilter && externalStatusFilter !== 'all') {
        externalLinks = externalLinks.filter(link => {
            if (!link.target_status) return false;
            const status = parseInt(link.target_status);
            switch (externalStatusFilter) {
                case '2xx': return status >= 200 && status < 300;
                case '3xx': return status >= 300 && status < 400;
                case '4xx': return status >= 400 && status < 500;
                case '5xx': return status >= 500;
                default: return true;
            }
        });
    }

    // Apply placement filter for external links (matches placement_detail for granularity)
    const externalPlacementFilter = crawlState.filters.linksFilter.externalPlacement;
    if (externalPlacementFilter && externalPlacementFilter !== 'all') {
        externalLinks = externalLinks.filter(link =>
            (link.placement_detail || link.placement || 'body') === externalPlacementFilter
        );
    }

    // Apply search filter for external links
    const externalSearch = crawlState.filters.linksFilter.externalSearch.toLowerCase();
    if (externalSearch) {
        externalLinks = externalLinks.filter(link =>
            link.source_url.toLowerCase().includes(externalSearch) ||
            link.target_url.toLowerCase().includes(externalSearch) ||
            (link.target_domain && link.target_domain.toLowerCase().includes(externalSearch))
        );
    }

    // Update virtual scrollers with filtered data
    if (virtualScrollers.internalLinks) {
        virtualScrollers.internalLinks.setData(internalLinks);
    }

    if (virtualScrollers.externalLinks) {
        virtualScrollers.externalLinks.setData(externalLinks);
    }
}

function filterInternalLinks(filterType) {
    crawlState.filters.linksFilter.internalStatusCode = filterType;
    applyLinksFilter();
}

function filterExternalLinks(filterType) {
    crawlState.filters.linksFilter.externalStatusCode = filterType;
    applyLinksFilter();
}

function searchInternalLinks(searchText) {
    crawlState.filters.linksFilter.internalSearch = searchText;
    applyLinksFilter();
}

function searchExternalLinks(searchText) {
    crawlState.filters.linksFilter.externalSearch = searchText;
    applyLinksFilter();
}

function filterInternalPlacement(value) {
    crawlState.filters.linksFilter.internalPlacement = value;
    applyLinksFilter();
}

function filterExternalPlacement(value) {
    crawlState.filters.linksFilter.externalPlacement = value;
    applyLinksFilter();
}

function updateIssuesTable(issues) {
    if (!issues || !Array.isArray(issues)) {
        issues = [];
    }

    // Store issues globally for filtering
    window.currentIssues = issues;

    const emptyState = document.getElementById('issuesEmptyState');
    const issuesTable = document.getElementById('issuesTable');

    // Count by type
    let errorCount = 0;
    let warningCount = 0;
    let infoCount = 0;

    issues.forEach(issue => {
        if (issue.type === 'error') errorCount++;
        else if (issue.type === 'warning') warningCount++;
        else if (issue.type === 'info') infoCount++;
    });

    // Update filter counts
    document.getElementById('issues-all-count').textContent = `(${issues.length})`;
    document.getElementById('issues-error-count').textContent = `(${errorCount})`;
    document.getElementById('issues-warning-count').textContent = `(${warningCount})`;
    document.getElementById('issues-info-count').textContent = `(${infoCount})`;

    // Show/hide empty state
    if (issues.length === 0) {
        if (emptyState) emptyState.style.display = 'block';
        if (issuesTable) issuesTable.style.display = 'none';
    } else {
        if (emptyState) emptyState.style.display = 'none';
        if (issuesTable) issuesTable.style.display = 'table';

        // Use virtual scroller for issues
        if (virtualScrollers.issues) {
            virtualScrollers.issues.setData(issues);
        }
    }

    // Update issue count in tab button (find the button, not the tab content)
    const issuesTabButton = Array.from(document.querySelectorAll('.tab-btn')).find(btn => btn.textContent.includes('Issues'));
    if (issuesTabButton) {
        const totalIssues = issues.length;
        if (totalIssues > 0) {
            let badgeColor = '#3b82f6';
            if (errorCount > 0) badgeColor = '#ef4444';
            else if (warningCount > 0) badgeColor = '#f59e0b';

            issuesTabButton.innerHTML = `Issues <span style="background: ${badgeColor}; color: white; padding: 2px 6px; border-radius: 12px; font-size: 12px;">${totalIssues}</span>`;
        } else {
            issuesTabButton.innerHTML = 'Issues';
        }
    }
}

function clearAllTables() {
    // Clear virtual scrollers if they exist
    if (virtualScrollers.overview) {
        virtualScrollers.overview.clear();
    }
    if (virtualScrollers.internal) {
        virtualScrollers.internal.clear();
    }
    if (virtualScrollers.external) {
        virtualScrollers.external.clear();
    }
    if (virtualScrollers.internalLinks) {
        virtualScrollers.internalLinks.clear();
    }
    if (virtualScrollers.externalLinks) {
        virtualScrollers.externalLinks.clear();
    }
    if (virtualScrollers.issues) {
        virtualScrollers.issues.clear();
    }
    if (virtualScrollers.content) {
        virtualScrollers.content.clear();
    }

    // Clear status codes table (not virtualized)
    const statusCodesBody = document.getElementById('statusCodesTableBody');
    if (statusCodesBody) statusCodesBody.innerHTML = '';

    crawlState.urls = [];

    console.log('All tables cleared');
}

function formatAnalyticsInfo(analytics) {
    const detected = [];
    if (analytics.gtag || analytics.ga4_id) detected.push('GA4');
    if (analytics.google_analytics) detected.push('GA');
    if (analytics.gtm_id) detected.push('GTM');
    if (analytics.facebook_pixel) detected.push('FB');
    if (analytics.hotjar) detected.push('HJ');
    if (analytics.mixpanel) detected.push('MP');

    return detected.length > 0 ? detected.join(', ') : '';
}

// Scroller-to-table mapping for column copy
const columnCopyMap = {
    overviewTable: { scroller: 'overview', extractor: extractOverviewColumn },
    internalTable: { scroller: 'internal', extractor: extractSimpleColumn },
    externalTable: { scroller: 'external', extractor: extractSimpleColumn },
    issuesTable: { scroller: 'issues', extractor: extractIssueColumn },
    contentTable: { scroller: 'content', extractor: extractContentColumn },
    internalLinksTable: { scroller: 'internalLinks', extractor: extractInternalLinkColumn },
    externalLinksTable: { scroller: 'externalLinks', extractor: extractExternalLinkColumn },
};

// Add visible copy buttons to every column header + a "copy all" button
function setupColumnCopy() {
    document.querySelectorAll('.data-table').forEach(table => {
        const entry = columnCopyMap[table.id];
        if (!entry) return;

        const headerRow = table.querySelector('thead tr');
        if (!headerRow) return;

        // "Copy all" button — first cell, copies all columns as TSV
        const copyAllBtn = document.createElement('button');
        copyAllBtn.className = 'col-copy-btn col-copy-all';
        copyAllBtn.title = 'Copy all columns (tab-separated)';
        copyAllBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
        copyAllBtn.onclick = (e) => {
            e.stopPropagation();
            copyAllColumns(table.id, entry);
        };
        headerRow.children[0].insertBefore(copyAllBtn, headerRow.children[0].firstChild);

        // Per-column copy button in each header
        Array.from(headerRow.children).forEach((th, colIndex) => {
            const btn = document.createElement('button');
            btn.className = 'col-copy-btn';
            btn.title = 'Copy this column';
            btn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
            btn.onclick = (e) => {
                e.stopPropagation();
                copySingleColumn(table.id, entry, colIndex, th.textContent.trim());
            };
            th.appendChild(btn);
        });
    });
}

function copySingleColumn(tableId, entry, colIndex, headerText) {
    const scroller = virtualScrollers[entry.scroller];
    if (!scroller || !scroller.data || scroller.data.length === 0) {
        showNotification('No data to copy', 'info');
        return;
    }
    const values = [headerText, ...scroller.data.map(item => entry.extractor(item, colIndex))];
    navigator.clipboard.writeText(values.join('\n'));
    showNotification(`Copied ${scroller.data.length} rows from "${headerText}"`, 'success');
}

function copyAllColumns(tableId, entry) {
    const scroller = virtualScrollers[entry.scroller];
    if (!scroller || !scroller.data || scroller.data.length === 0) {
        showNotification('No data to copy', 'info');
        return;
    }
    const table = document.getElementById(tableId);
    const headers = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
    const colCount = headers.length;

    const rows = [headers.join('\t')];
    scroller.data.forEach(item => {
        const cols = [];
        for (let i = 0; i < colCount; i++) {
            cols.push(entry.extractor(item, i));
        }
        rows.push(cols.join('\t'));
    });
    navigator.clipboard.writeText(rows.join('\n'));
    showNotification(`Copied ${scroller.data.length} rows (all columns)`, 'success');
}

// Column sorting — click header text to cycle asc → desc → reset
let sortState = { tableId: null, colIndex: null, direction: null };

function setupColumnSort() {
    document.querySelectorAll('.data-table thead th').forEach(th => {
        th.addEventListener('click', (e) => {
            // Ignore clicks on copy buttons and resize grips
            if (e.target.closest('.col-copy-btn') || e.target.closest('.col-copy-all') ||
                e.target.classList.contains('column-resize-grip')) return;

            const table = th.closest('.data-table');
            const entry = columnCopyMap[table.id];
            if (!entry) return;

            const scroller = virtualScrollers[entry.scroller];
            if (!scroller || !scroller.data || scroller.data.length === 0) return;

            const colIndex = Array.from(th.parentElement.children).indexOf(th);

            // Cycle sort direction
            if (sortState.tableId === table.id && sortState.colIndex === colIndex) {
                if (sortState.direction === 'asc') sortState.direction = 'desc';
                else if (sortState.direction === 'desc') { sortState.direction = null; sortState.tableId = null; }
            } else {
                sortState = { tableId: table.id, colIndex, direction: 'asc' };
            }

            // Clear sort indicators from all headers in this table
            table.querySelectorAll('thead th .sort-arrow').forEach(el => el.remove());

            if (sortState.direction) {
                // Add sort indicator
                const arrow = document.createElement('span');
                arrow.className = 'sort-arrow';
                arrow.textContent = sortState.direction === 'asc' ? ' ▲' : ' ▼';
                arrow.style.fontSize = '10px';
                th.appendChild(arrow);

                // Sort data
                const sorted = [...scroller.data].sort((a, b) => {
                    const valA = entry.extractor(a, colIndex);
                    const valB = entry.extractor(b, colIndex);
                    const numA = Number(valA), numB = Number(valB);
                    const isNum = !isNaN(numA) && !isNaN(numB) && valA !== '' && valB !== '';
                    let cmp = isNum ? numA - numB : String(valA).localeCompare(String(valB));
                    return sortState.direction === 'desc' ? -cmp : cmp;
                });
                scroller.setData(sorted);
            } else {
                // Reset — reapply filters to get original order
                reapplyCurrentFilters(entry.scroller);
            }
        });
    });
}

function reapplyCurrentFilters(scrollerName) {
    if (scrollerName === 'overview' || scrollerName === 'internal' || scrollerName === 'external') {
        filterVirtualScrollerData(scrollerName, crawlState.filters.active);
    } else if (scrollerName === 'internalLinks' || scrollerName === 'externalLinks') {
        applyLinksFilter();
    } else if (scrollerName === 'issues') {
        filterIssues(crawlState.filters.issueFilter || 'all');
    } else if (scrollerName === 'content') {
        updateContentTable();
    }
}

function extractOverviewColumn(urlData, colIndex) {
    const fields = [
        urlData.url, urlData.status_code, urlData.title || '',
        urlData.meta_description || '', urlData.h1 || '', urlData.word_count || 0,
        urlData.response_time || 0, formatAnalyticsInfo(urlData.analytics || {}),
        Object.keys(urlData.og_tags || {}).length || '', (urlData.json_ld || []).length || '',
        `${urlData.internal_links || 0}/${urlData.external_links || 0}`,
        (urlData.images || []).length || '', urlData.javascript_rendered ? 'JS' : '', ''
    ];
    return fields[colIndex] ?? '';
}

function extractSimpleColumn(urlData, colIndex) {
    const fields = [urlData.url, urlData.status_code, urlData.content_type || '', urlData.size || 0, urlData.title || ''];
    return fields[colIndex] ?? '';
}

function extractIssueColumn(issue, colIndex) {
    const fields = [issue.url, issue.type, issue.category, issue.issue, issue.details];
    return fields[colIndex] ?? '';
}

function extractContentColumn(urlData, colIndex) {
    const fields = [urlData.url, urlData.title || '', urlData.h1 || '', urlData.word_count || 0, urlData.body_text || ''];
    return fields[colIndex] ?? '';
}

function extractInternalLinkColumn(link, colIndex) {
    const fields = [link.source_url, link.target_url, link.target_status || '', link.anchor_text || '', link.placement || ''];
    return fields[colIndex] ?? '';
}

function extractExternalLinkColumn(link, colIndex) {
    const fields = [link.source_url, link.target_url, link.target_status || '', link.target_domain || '', link.placement || ''];
    return fields[colIndex] ?? '';
}

function addUrlToTable(urlData) {
    // Check if URL already exists to prevent duplicates
    const existingUrl = crawlState.urls.find(u => u.url === urlData.url);
    if (existingUrl) {
        return; // Skip duplicate
    }

    crawlState.urls.push(urlData);

    // Update virtual scrollers with new data
    if (virtualScrollers.overview) {
        virtualScrollers.overview.appendData([urlData]);
    }

    if (urlData.is_internal && virtualScrollers.internal) {
        virtualScrollers.internal.appendData([urlData]);
    } else if (!urlData.is_internal && virtualScrollers.external) {
        virtualScrollers.external.appendData([urlData]);
    }

    // Content tab — append to scroller if active, otherwise defer to tab switch
    if (virtualScrollers.content && document.getElementById('content-tab')?.classList.contains('active')) {
        virtualScrollers.content.appendData([urlData]);
    }

    // Reapply current filter if one is active
    if (crawlState.filters.active) {
        applyFilter(crawlState.filters.active);
    }
}

function addRowToTable(tableBodyId, rowData) {
    const tbody = document.getElementById(tableBodyId);
    const row = tbody.insertRow();

    rowData.forEach(cellData => {
        const cell = row.insertCell();
        // Check if cellData contains HTML (specifically our button)
        if (typeof cellData === 'string' && cellData.includes('<button')) {
            cell.innerHTML = cellData;
        } else {
            cell.textContent = cellData;
        }
    });
}

// Tab Management
function switchTab(tabName) {
    // Remove active class from all tabs and panes
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));

    // Add active class to selected tab button (find by tabName, not event.target,
    // because clicking a badge <span> inside the button gives wrong target)
    const tabBtn = Array.from(document.querySelectorAll('.tab-btn')).find(
        btn => btn.getAttribute('onclick')?.includes(`'${tabName}'`)
    );
    if (tabBtn) tabBtn.classList.add('active');
    document.getElementById(tabName + '-tab').classList.add('active');

    // Defer pending-data loads to next frame so the container has layout dimensions
    // (virtual scrollers need non-zero container height to render correctly)
    requestAnimationFrame(() => {
        // Load pending links data if switching to Links tab
        if (tabName === 'links' && crawlState.pendingLinks) {
            updateLinksTable(crawlState.pendingLinks);
            crawlState.pendingLinks = null;
        }

        // Load pending issues data if switching to Issues tab
        if (tabName === 'issues' && crawlState.pendingIssues) {
            updateIssuesTable(crawlState.pendingIssues);
            crawlState.pendingIssues = null;
        }

        // Load content data if switching to Content tab
        if (tabName === 'content') {
            updateContentTable();
        }

        // Force virtual scroller viewport update for newly visible tabs
        if (tabName === 'links') {
            if (virtualScrollers.internalLinks) virtualScrollers.internalLinks.updateViewport();
            if (virtualScrollers.externalLinks) virtualScrollers.externalLinks.updateViewport();
        } else {
            const scrollerName = tabName === 'issues' ? 'issues' :
                                 tabName === 'overview' ? 'overview' :
                                 tabName === 'internal' ? 'internal' :
                                 tabName === 'external' ? 'external' :
                                 tabName === 'content' ? 'content' : null;
            if (scrollerName && virtualScrollers[scrollerName]) {
                virtualScrollers[scrollerName].updateViewport();
            }
        }
    });

    // Initialize visualization if switching to Visualization tab
    if (tabName === 'visualization' && typeof initVisualization === 'function') {
        // Small delay to ensure the tab is visible before initializing
        setTimeout(() => {
            initVisualization();
        }, 100);
    }

    // Handle plugin tabs
    const pluginTab = document.getElementById(`${tabName}-tab`);
    if (pluginTab && pluginTab.classList.contains('plugin-tab')) {
        handlePluginTabSwitch(tabName);
    }
}

// Handle plugin tab activation
function handlePluginTabSwitch(tabName) {
    if (!window.LibreCrawlPlugin || !window.LibreCrawlPlugin.loader) {
        return;
    }

    const loader = window.LibreCrawlPlugin.loader;

    // Deactivate previously active plugin
    if (loader.activePluginId && loader.activePluginId !== tabName) {
        loader.deactivatePlugin(loader.activePluginId);
    }

    // Activate the new plugin
    loader.activatePlugin(tabName, {
        urls: crawlState.urls,
        links: crawlState.links,
        issues: crawlState.issues,
        stats: crawlState.stats
    });
}

// Issue Filtering
function filterIssues(filterType) {
    // Store the active filter
    crawlState.filters.issueFilter = filterType;

    // Update active button state and colors
    document.querySelectorAll('#issues-tab .filter-item').forEach(btn => {
        btn.classList.remove('active');
        const filter = btn.getAttribute('data-filter');

        if (filter === filterType) {
            btn.classList.add('active');
            // Set active state colors
            if (filter === 'all') {
                btn.style.background = '#374151';
                btn.style.borderColor = '#4b5563';
                btn.style.color = 'white';
            } else if (filter === 'error') {
                btn.style.background = 'rgba(239, 68, 68, 0.2)';
                btn.style.borderColor = 'rgba(239, 68, 68, 0.5)';
            } else if (filter === 'warning') {
                btn.style.background = 'rgba(245, 158, 11, 0.2)';
                btn.style.borderColor = 'rgba(245, 158, 11, 0.5)';
            } else if (filter === 'info') {
                btn.style.background = 'rgba(59, 130, 246, 0.2)';
                btn.style.borderColor = 'rgba(59, 130, 246, 0.5)';
            }
        } else {
            // Reset inactive state colors
            if (filter === 'all') {
                btn.style.background = 'transparent';
                btn.style.borderColor = '#4b5563';
                btn.style.color = '#9ca3af';
            } else if (filter === 'error') {
                btn.style.background = 'rgba(239, 68, 68, 0.1)';
                btn.style.borderColor = 'rgba(239, 68, 68, 0.3)';
            } else if (filter === 'warning') {
                btn.style.background = 'rgba(245, 158, 11, 0.1)';
                btn.style.borderColor = 'rgba(245, 158, 11, 0.3)';
            } else if (filter === 'info') {
                btn.style.background = 'rgba(59, 130, 246, 0.1)';
                btn.style.borderColor = 'rgba(59, 130, 246, 0.3)';
            }
        }
    });

    // Filter issues data and update virtual scroller
    if (window.currentIssues && virtualScrollers.issues) {
        let filteredIssues = window.currentIssues;

        if (filterType !== 'all') {
            filteredIssues = window.currentIssues.filter(issue => issue.type === filterType);
        }

        virtualScrollers.issues.setData(filteredIssues);
    }
}

// Filter Management
function toggleFilter(filterType) {
    const filterItems = document.querySelectorAll('.filter-item');
    filterItems.forEach(item => item.classList.remove('active'));

    event.currentTarget.classList.add('active');
    crawlState.filters.active = filterType;

    // Apply filter to tables
    applyFilter(filterType);
}

function applyFilter(filterType) {
    // Set current filter as active
    crawlState.filters.active = filterType;

    // Filter the data arrays and update virtual scrollers
    filterVirtualScrollerData('overview', filterType);
    filterVirtualScrollerData('internal', filterType);
    filterVirtualScrollerData('external', filterType);

    // Update Status Codes table with filtered data
    updateStatusCodesTable(filterType);

    console.log('Applied filter:', filterType);
}

// Global URL search across Overview, Internal, External, Content tabs
function searchUrls(searchText) {
    crawlState.filters.urlSearch = searchText.toLowerCase();
    // Reapply all filters (which now include search)
    filterVirtualScrollerData('overview', crawlState.filters.active);
    filterVirtualScrollerData('internal', crawlState.filters.active);
    filterVirtualScrollerData('external', crawlState.filters.active);
    // Also update content tab if visible
    if (virtualScrollers.content) {
        let data = crawlState.urls;
        if (crawlState.filters.urlSearch) {
            data = data.filter(u =>
                u.url.toLowerCase().includes(crawlState.filters.urlSearch) ||
                (u.title && u.title.toLowerCase().includes(crawlState.filters.urlSearch)) ||
                (u.h1 && u.h1.toLowerCase().includes(crawlState.filters.urlSearch))
            );
        }
        virtualScrollers.content.setData(data);
    }
    updateStatusCodesTable(crawlState.filters.active);
}

function clearActiveFilters() {
    crawlState.filters.active = null;
    crawlState.filters.urlSearch = '';
    const searchInput = document.getElementById('globalUrlSearch');
    if (searchInput) searchInput.value = '';

    // Reset all virtual scrollers to show full data
    if (virtualScrollers.overview) {
        virtualScrollers.overview.setData(crawlState.urls);
    }
    if (virtualScrollers.internal) {
        const internalUrls = crawlState.urls.filter(url => url.is_internal);
        virtualScrollers.internal.setData(internalUrls);
    }
    if (virtualScrollers.external) {
        const externalUrls = crawlState.urls.filter(url => !url.is_internal);
        virtualScrollers.external.setData(externalUrls);
    }

    // Reset Status Codes table to show all data
    updateStatusCodesTable();
}

function filterVirtualScrollerData(scrollerName, filterType) {
    const scroller = virtualScrollers[scrollerName];
    if (!scroller) return;

    let filteredData = crawlState.urls;

    // Apply base filter for internal/external tables
    if (scrollerName === 'internal') {
        filteredData = filteredData.filter(url => url.is_internal);
    } else if (scrollerName === 'external') {
        filteredData = filteredData.filter(url => !url.is_internal);
    }

    // Apply user-selected filter
    if (filterType) {
        filteredData = filteredData.filter(url => {
            switch (filterType) {
                case 'internal':
                    return isInternalURL(url.url);
                case 'external':
                    return !isInternalURL(url.url);
                case '2xx':
                    return url.status_code >= 200 && url.status_code < 300;
                case '3xx':
                    return url.status_code >= 300 && url.status_code < 400;
                case '4xx':
                    return url.status_code >= 400 && url.status_code < 500;
                case '5xx':
                    return url.status_code >= 500;
                case 'html':
                    return (url.content_type || '').toLowerCase().includes('html');
                case 'css':
                    return (url.content_type || '').toLowerCase().includes('css');
                case 'js':
                    return (url.content_type || '').toLowerCase().includes('javascript');
                case 'images':
                    return (url.content_type || '').toLowerCase().includes('image');
                default:
                    return true;
            }
        });
    }

    // Apply URL search text
    const searchText = crawlState.filters.urlSearch;
    if (searchText) {
        filteredData = filteredData.filter(url =>
            url.url.toLowerCase().includes(searchText) ||
            (url.title && url.title.toLowerCase().includes(searchText)) ||
            (url.h1 && url.h1.toLowerCase().includes(searchText))
        );
    }

    scroller.setData(filteredData);
}

// Legacy function - kept for compatibility but no longer used
function filterTable(tableBodyId, filterType) {
    // This function is deprecated in favor of filterVirtualScrollerData
    // Kept for backwards compatibility only
}

function isInternalURL(url) {
    if (!url || !crawlState.baseUrl) return false;
    try {
        const urlObj = new URL(url);
        const baseObj = new URL(crawlState.baseUrl);

        // Normalize domains by removing www prefix for comparison
        const urlDomain = urlObj.hostname.replace('www.', '');
        const baseDomain = baseObj.hostname.replace('www.', '');

        return urlDomain === baseDomain;
    } catch (e) {
        return false;
    }
}

function isStatusCodeRange(statusText, min, max) {
    const status = parseInt(statusText);
    return status >= min && status <= max;
}

function isContentType(contentType, type) {
    if (!contentType) return false;
    return contentType.toLowerCase().includes(type.toLowerCase());
}

function updateFilterCounts() {
    // Count URLs by type and update filter counts
    const counts = {
        internal: 0,
        external: 0,
        '2xx': 0,
        '3xx': 0,
        '4xx': 0,
        '5xx': 0,
        html: 0,
        css: 0,
        js: 0,
        images: 0
    };

    crawlState.urls.forEach(url => {
        // Count by internal/external using corrected logic
        if (isInternalURL(url.url)) counts.internal++;
        else counts.external++;

        // Count by status code
        const statusCode = parseInt(url.status_code);
        if (statusCode >= 200 && statusCode < 300) counts['2xx']++;
        else if (statusCode >= 300 && statusCode < 400) counts['3xx']++;
        else if (statusCode >= 400 && statusCode < 500) counts['4xx']++;
        else if (statusCode >= 500) counts['5xx']++;

        // Count by content type
        const contentType = url.content_type || '';
        if (contentType.includes('html')) counts.html++;
        else if (contentType.includes('css')) counts.css++;
        else if (contentType.includes('javascript')) counts.js++;
        else if (contentType.includes('image')) counts.images++;
    });

    // Update DOM
    Object.keys(counts).forEach(key => {
        const element = document.getElementById(key + '-count');
        if (element) {
            element.textContent = counts[key];
        }
    });
}

function updateStatusCodesTable(filterType = null) {
    const tbody = document.getElementById('statusCodesTableBody');
    if (!tbody) return;

    // Count status codes, respecting current filter
    const statusCounts = {};
    let filteredUrls = crawlState.urls;

    // Apply filter if specified
    if (filterType === 'internal') {
        filteredUrls = crawlState.urls.filter(url => isInternalURL(url.url));
    } else if (filterType === 'external') {
        filteredUrls = crawlState.urls.filter(url => !isInternalURL(url.url));
    } else if (filterType === '2xx') {
        filteredUrls = crawlState.urls.filter(url => {
            const status = parseInt(url.status_code);
            return status >= 200 && status < 300;
        });
    } else if (filterType === '3xx') {
        filteredUrls = crawlState.urls.filter(url => {
            const status = parseInt(url.status_code);
            return status >= 300 && status < 400;
        });
    } else if (filterType === '4xx') {
        filteredUrls = crawlState.urls.filter(url => {
            const status = parseInt(url.status_code);
            return status >= 400 && status < 500;
        });
    } else if (filterType === '5xx') {
        filteredUrls = crawlState.urls.filter(url => {
            const status = parseInt(url.status_code);
            return status >= 500;
        });
    } else if (filterType === 'html') {
        filteredUrls = crawlState.urls.filter(url => (url.content_type || '').includes('html'));
    } else if (filterType === 'css') {
        filteredUrls = crawlState.urls.filter(url => (url.content_type || '').includes('css'));
    } else if (filterType === 'js') {
        filteredUrls = crawlState.urls.filter(url => (url.content_type || '').includes('javascript'));
    } else if (filterType === 'images') {
        filteredUrls = crawlState.urls.filter(url => (url.content_type || '').includes('image'));
    }

    let totalUrls = filteredUrls.length;

    filteredUrls.forEach(url => {
        const statusCode = url.status_code;
        if (statusCounts[statusCode]) {
            statusCounts[statusCode]++;
        } else {
            statusCounts[statusCode] = 1;
        }
    });

    // Clear existing rows
    tbody.innerHTML = '';

    // Add rows for each status code
    Object.keys(statusCounts).sort((a, b) => parseInt(a) - parseInt(b)).forEach(statusCode => {
        const count = statusCounts[statusCode];
        const percentage = totalUrls > 0 ? ((count / totalUrls) * 100).toFixed(1) : 0;
        const statusText = getStatusCodeText(parseInt(statusCode));

        addRowToTable('statusCodesTableBody', [
            statusCode,
            statusText,
            count,
            percentage + '%'
        ]);
    });
}

function getStatusCodeText(statusCode) {
    if (statusCode >= 200 && statusCode < 300) {
        return 'Success';
    } else if (statusCode >= 300 && statusCode < 400) {
        return 'Redirect';
    } else if (statusCode >= 400 && statusCode < 500) {
        return 'Client Error';
    } else if (statusCode >= 500) {
        return 'Server Error';
    } else if (statusCode === 0) {
        return 'Failed/Timeout';
    } else {
        return 'Unknown';
    }
}

function resetStats() {
    crawlState.stats = {
        discovered: 0,
        crawled: 0,
        depth: 0,
        speed: 0
    };
    updateStatsDisplay();
}

// Utility Functions
function normalizeUrl(input) {
    // Remove any whitespace
    input = input.trim();

    // If it already has a protocol, return as-is
    if (input.match(/^https?:\/\//i)) {
        return input;
    }

    // If it looks like a domain or IP, add https://
    if (input.match(/^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]*\.([a-zA-Z]{2,}|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})/) ||
        input.match(/^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/) ||
        input.match(/^localhost(:[0-9]+)?$/i) ||
        input.match(/^[a-zA-Z0-9-]+\.(com|org|net|edu|gov|mil|int|co|io|dev|app|tech|info|biz|name|pro|museum|aero|coop|travel|jobs|mobi|tel|asia|cat|post|xxx|local|test)$/i)) {
        return 'https://' + input;
    }

    // If it doesn't match common patterns, try adding https:// anyway
    return 'https://' + input;
}

function isValidUrl(string) {
    try {
        const url = new URL(string);
        // Check if it has a valid protocol and hostname
        return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname.length > 0;
    } catch (_) {
        return false;
    }
}

// This is defined in settings.js - no need to redefine here

async function logout() {
    try {
        const response = await fetch('/api/logout', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (data.success) {
            // Redirect to login page
            window.location.href = '/login';
        } else {
            console.error('Logout failed:', data.message);
            // Still redirect even if logout fails
            window.location.href = '/login';
        }
    } catch (error) {
        console.error('Logout error:', error);
        // Redirect anyway
        window.location.href = '/login';
    }
}

async function loadUserInfo() {
    try {
        const response = await fetch('/api/user/info');
        const data = await response.json();

        if (data.success && data.user) {
            const user = data.user;
            const userInfoElement = document.getElementById('userInfo');

            if (user.tier === 'guest') {
                // Show crawls remaining for guests
                const remaining = user.crawls_remaining;
                userInfoElement.textContent = `Guest (${remaining}/3 crawls remaining)`;
                userInfoElement.style.color = remaining === 0 ? '#dc2626' : '#6b7280';
            } else {
                // Show username and tier for registered users
                userInfoElement.textContent = `${user.username} (${user.tier})`;
                userInfoElement.style.color = '#6b7280';
            }
        }
    } catch (error) {
        console.error('Error loading user info:', error);
    }
}

async function exportData() {
    try {
        // Get current settings to determine export format and fields
        const settingsResponse = await fetch('/api/get_settings');
        const settingsData = await settingsResponse.json();

        if (!settingsData.success) {
            showNotification('Failed to get export settings', 'error');
            return;
        }

        const settings = settingsData.settings;
        const exportFormat = settings.exportFormat || 'csv';
        const exportFields = settings.exportFields || ['url', 'status_code', 'title', 'meta_description', 'h1'];

        // Check if there's data to export - always fetch fresh data from backend
        let hasData = false;
        let exportUrls = [];
        let exportLinks = [];
        let exportIssues = [];

        // Always fetch from backend to ensure we have the latest data including links
        const status = await fetch('/api/crawl_status');
        const statusData = await status.json();

        if (statusData.urls && statusData.urls.length > 0) {
            hasData = true;
            exportUrls = statusData.urls;
            exportLinks = statusData.links || [];
            exportIssues = statusData.issues || [];
        } else if (crawlState.urls && crawlState.urls.length > 0) {
            // Fallback to local state if backend has no data (e.g., loaded crawl)
            hasData = true;
            exportUrls = crawlState.urls;
            // Get links and issues from stored state
            exportLinks = crawlState.links || [];
            exportIssues = crawlState.issues || window.currentIssues || [];
        }

        if (!hasData) {
            showNotification('No crawl data to export', 'error');
            return;
        }

        showNotification('Preparing export...', 'info');

        // Request export from backend, including local data if available
        const exportResponse = await fetch('/api/export_data', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                format: exportFormat,
                fields: exportFields,
                // Send local data if we have it (for loaded crawls)
                localData: {
                    urls: exportUrls,
                    links: exportLinks,
                    issues: exportIssues
                }
            })
        });

        const exportData = await exportResponse.json();

        if (!exportData.success) {
            showNotification(exportData.error || 'Export failed', 'error');
            return;
        }

        // Check if we have multiple files to download
        if (exportData.multiple_files && exportData.files) {
            // Download each file separately
            exportData.files.forEach((file, index) => {
                setTimeout(() => {
                    const blob = new Blob([file.content], { type: file.mimetype });
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.style.display = 'none';
                    a.href = url;
                    a.download = file.filename;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);
                }, index * 500); // Delay between downloads to avoid browser blocking
            });

            showNotification(`Exporting ${exportData.files.length} files...`, 'success');
        } else {
            // Single file download (original logic)
            const blob = new Blob([exportData.content], { type: exportData.mimetype });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = exportData.filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            showNotification(`Export complete: ${exportData.filename}`, 'success');
        }

    } catch (error) {
        console.error('Export error:', error);
        showNotification('Export failed', 'error');
    }
}

function exportAll() {
    // Check if embeddings exist for current crawl
    fetch('/api/embed_status')
        .then(r => r.json())
        .then(data => {
            const expEmbed = document.getElementById('exp-embeddings');
            if (expEmbed) {
                expEmbed.disabled = !(data.status === 'done' && data.current > 0);
            }
        });
    // Show the export modal
    const modal = document.getElementById('exportAllModal');
    if (modal) {
        modal.style.display = 'flex';
    }
}

function closeExportAllModal() {
    const modal = document.getElementById('exportAllModal');
    if (modal) {
        modal.style.display = 'none';
    }
}

async function runExportAll() {
    // Read checkbox states
    const opts = {
        urls:       document.getElementById('exp-urls').checked,
        body_text:  document.getElementById('exp-body-text').checked,
        links:      document.getElementById('exp-links').checked,
        issues:     document.getElementById('exp-issues').checked,
        images:     document.getElementById('exp-images').checked,
        embeddings: document.getElementById('exp-embeddings')?.checked || false,
        linkgraph:  document.getElementById('exp-linkgraph')?.checked || false,
    };

    // Must pick at least one
    if (!opts.urls && !opts.body_text && !opts.links && !opts.issues && !opts.images && !opts.embeddings && !opts.linkgraph) {
        showNotification('Select at least one export option', 'error');
        return;
    }

    closeExportAllModal();

    try {
        let exportUrls = [];
        let exportLinks = [];
        let exportIssues = [];

        const status = await fetch('/api/crawl_status');
        const statusData = await status.json();

        if (statusData.urls && statusData.urls.length > 0) {
            exportUrls = statusData.urls;
            exportLinks = statusData.links || [];
            exportIssues = statusData.issues || [];
        } else if (crawlState.urls && crawlState.urls.length > 0) {
            exportUrls = crawlState.urls;
            exportLinks = crawlState.links || [];
            exportIssues = crawlState.issues || window.currentIssues || [];
        }

        if (!exportUrls.length) {
            showNotification('No crawl data to export', 'error');
            return;
        }

        showNotification('Preparing export (ZIP)...', 'info');

        const response = await fetch('/api/export_all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                options: opts,
                localData: {
                    urls: exportUrls,
                    links: exportLinks,
                    issues: exportIssues
                }
            })
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            showNotification(err.error || 'Export failed', 'error');
            return;
        }

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        const cd = response.headers.get('Content-Disposition') || '';
        const match = cd.match(/filename=(.+)/);
        a.download = match ? match[1] : 'librecrawl_export_all.zip';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showNotification('Export downloaded (ZIP)', 'success');
    } catch (error) {
        console.error('Export All error:', error);
        showNotification('Export failed', 'error');
    }
}

// Helper function to escape HTML for safe display
function escapeHtml(text) {
    if (!text) return text;
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showUrlDetails(url) {
    // Find the URL data
    const urlData = crawlState.urls.find(u => u.url === url);
    if (!urlData) {
        showNotification('URL data not found', 'error');
        return;
    }

    // Escape all user-controlled text fields to prevent HTML injection
    const safeUrl = escapeHtml(url);
    const safeTitle = escapeHtml(urlData.title) || 'N/A';
    const safeH1 = escapeHtml(urlData.h1) || 'N/A';
    const safeMetaDesc = escapeHtml(urlData.meta_description) || 'N/A';
    const safeLang = escapeHtml(urlData.lang) || 'N/A';
    const safeCharset = escapeHtml(urlData.charset) || 'N/A';
    const safeCanonical = escapeHtml(urlData.canonical_url) || 'N/A';
    const safeRobots = escapeHtml(urlData.robots) || 'N/A';
    const safeContentType = escapeHtml(urlData.content_type) || 'N/A';
    const safeGa4Id = escapeHtml(urlData.analytics?.ga4_id) || 'N/A';
    const safeGtmId = escapeHtml(urlData.analytics?.gtm_id) || 'N/A';

    // Create modal content
    const modalContent = `
        <div class="details-modal-overlay" onclick="closeUrlDetails()">
            <div class="details-modal" onclick="event.stopPropagation()">
                <div class="details-header">
                    <h3>Comprehensive URL Analysis</h3>
                    <button class="close-btn" onclick="closeUrlDetails()">×</button>
                </div>
                <div class="details-content">
                    <div class="details-url">${safeUrl}</div>

                    <div class="details-sections">
                        <div class="details-section">
                            <h4>🔍 Basic SEO</h4>
                            <div class="details-grid">
                                <div><strong>Title:</strong> ${safeTitle}</div>
                                <div><strong>H1:</strong> ${safeH1}</div>
                                <div><strong>Meta Description:</strong> ${safeMetaDesc}</div>
                                <div><strong>Word Count:</strong> ${urlData.word_count || 0}</div>
                                <div><strong>Language:</strong> ${safeLang}</div>
                                <div><strong>Charset:</strong> ${safeCharset}</div>
                                <div><strong>Canonical URL:</strong> ${safeCanonical}</div>
                                <div><strong>Robots Meta:</strong> ${safeRobots}</div>
                            </div>
                        </div>

                        <div class="details-section">
                            <h4>📊 Analytics & Tracking</h4>
                            <div class="details-grid">
                                <div><strong>Google Analytics:</strong> ${urlData.analytics?.google_analytics ? '✅ Yes' : '❌ No'}</div>
                                <div><strong>GA4/Gtag:</strong> ${urlData.analytics?.gtag ? '✅ Yes' : '❌ No'}</div>
                                <div><strong>GA4 ID:</strong> ${safeGa4Id}</div>
                                <div><strong>GTM ID:</strong> ${safeGtmId}</div>
                                <div><strong>Facebook Pixel:</strong> ${urlData.analytics?.facebook_pixel ? '✅ Yes' : '❌ No'}</div>
                                <div><strong>Hotjar:</strong> ${urlData.analytics?.hotjar ? '✅ Yes' : '❌ No'}</div>
                                <div><strong>Mixpanel:</strong> ${urlData.analytics?.mixpanel ? '✅ Yes' : '❌ No'}</div>
                            </div>
                        </div>

                        <div class="details-section">
                            <h4>📱 Social Media</h4>
                            <div class="details-grid">
                                <div><strong>OpenGraph Tags:</strong> ${Object.keys(urlData.og_tags || {}).length} found</div>
                                <div><strong>Twitter Cards:</strong> ${Object.keys(urlData.twitter_tags || {}).length} found</div>
                            </div>
                            ${Object.keys(urlData.og_tags || {}).length > 0 ? `
                                <div class="details-subsection">
                                    <h5>OpenGraph Tags:</h5>
                                    ${Object.entries(urlData.og_tags || {}).map(([key, value]) =>
                                        `<div><strong>og:${escapeHtml(key)}:</strong> ${escapeHtml(value)}</div>`
                                    ).join('')}
                                </div>
                            ` : ''}
                            ${Object.keys(urlData.twitter_tags || {}).length > 0 ? `
                                <div class="details-subsection">
                                    <h5>Twitter Cards:</h5>
                                    ${Object.entries(urlData.twitter_tags || {}).map(([key, value]) =>
                                        `<div><strong>twitter:${escapeHtml(key)}:</strong> ${escapeHtml(value)}</div>`
                                    ).join('')}
                                </div>
                            ` : ''}
                        </div>

                        <div class="details-section">
                            <h4>🔗 Links & Structure</h4>
                            <div class="details-grid">
                                <div><strong>Internal Links:</strong> ${urlData.internal_links || 0}</div>
                                <div><strong>External Links:</strong> ${urlData.external_links || 0}</div>
                                <div><strong>Images:</strong> ${(urlData.images || []).length}</div>
                                <div><strong>H2 Tags:</strong> ${(urlData.h2 || []).length}</div>
                                <div><strong>H3 Tags:</strong> ${(urlData.h3 || []).length}</div>
                            </div>
                        </div>

                        <div class="details-section">
                            <h4>⚡ Performance</h4>
                            <div class="details-grid">
                                <div><strong>Status Code:</strong> ${urlData.status_code}</div>
                                <div><strong>Response Time:</strong> ${urlData.response_time || 0}ms</div>
                                <div><strong>Content Type:</strong> ${safeContentType}</div>
                                <div><strong>Size:</strong> ${urlData.size || 0} bytes</div>
                            </div>
                        </div>

                        ${(urlData.linked_from && urlData.linked_from.length > 0) ? `
                        <div class="details-section">
                            <h4>🔗 Linked From</h4>
                            <div class="details-grid">
                                <div><strong>Found on ${urlData.linked_from.length} page${urlData.linked_from.length !== 1 ? 's' : ''}:</strong></div>
                            </div>
                            <div class="details-subsection">
                                <ul style="list-style: none; padding: 0; margin: 10px 0;">
                                    ${urlData.linked_from.slice(0, 20).map(sourceUrl => {
                                        const escapedUrl = escapeHtml(sourceUrl);
                                        return `<li style="padding: 5px 0; word-break: break-all;"><a href="${escapedUrl}" target="_blank" style="color: #8a9a5c; text-decoration: none;">${escapedUrl}</a></li>`;
                                    }).join('')}
                                    ${urlData.linked_from.length > 20 ? `<li style="padding: 5px 0; font-style: italic; color: #9ca3af;">... and ${urlData.linked_from.length - 20} more</li>` : ''}
                                </ul>
                            </div>
                        </div>
                        ` : ''}

                        <div class="details-section">
                            <h4>🏗️ Structured Data</h4>
                            <div class="details-grid">
                                <div><strong>JSON-LD Scripts:</strong> ${(urlData.json_ld || []).length}</div>
                                <div><strong>Schema.org Items:</strong> ${(urlData.schema_org || []).length}</div>
                            </div>
                            ${(urlData.json_ld || []).length > 0 ? `
                                <div class="details-subsection">
                                    <h5>JSON-LD Data:</h5>
                                    <pre class="json-preview">${escapeHtml(JSON.stringify(urlData.json_ld, null, 2))}</pre>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Add modal to page
    document.body.insertAdjacentHTML('beforeend', modalContent);
}

function closeUrlDetails() {
    const modal = document.querySelector('.details-modal-overlay');
    if (modal) {
        modal.remove();
    }
}

function displayPageSpeedResults(results) {
    const container = document.getElementById('pagespeedResults');
    if (!container || !results || results.length === 0) {
        return;
    }

    container.innerHTML = '';

    results.forEach(pageResult => {
        const pageCard = document.createElement('div');
        pageCard.className = 'pagespeed-page-card';

        const mobile = pageResult.mobile || {};
        const desktop = pageResult.desktop || {};

        pageCard.innerHTML = `
            <div class="pagespeed-page-header">
                <h4 class="pagespeed-page-url">${pageResult.url}</h4>
                <span class="pagespeed-analysis-date">Analyzed: ${pageResult.analysis_date}</span>
            </div>

            <div class="pagespeed-results-grid">
                <div class="pagespeed-device-result">
                    <h5>📱 Mobile</h5>
                    ${mobile.success ? `
                        <div class="pagespeed-score ${getScoreClass(mobile.performance_score)}">
                            ${mobile.performance_score || 'N/A'}
                        </div>
                        <div class="pagespeed-metrics">
                            <div class="metric">
                                <span class="metric-label">FCP:</span>
                                <span class="metric-value">${mobile.metrics?.first_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">LCP:</span>
                                <span class="metric-value">${mobile.metrics?.largest_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">CLS:</span>
                                <span class="metric-value">${mobile.metrics?.cumulative_layout_shift || 'N/A'}</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">SI:</span>
                                <span class="metric-value">${mobile.metrics?.speed_index || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">TTI:</span>
                                <span class="metric-value">${mobile.metrics?.time_to_interactive || 'N/A'}s</span>
                            </div>
                        </div>
                    ` : `
                        <div class="pagespeed-error">
                            Error: ${mobile.error || 'Analysis failed'}
                        </div>
                    `}
                </div>

                <div class="pagespeed-device-result">
                    <h5>🖥️ Desktop</h5>
                    ${desktop.success ? `
                        <div class="pagespeed-score ${getScoreClass(desktop.performance_score)}">
                            ${desktop.performance_score || 'N/A'}
                        </div>
                        <div class="pagespeed-metrics">
                            <div class="metric">
                                <span class="metric-label">FCP:</span>
                                <span class="metric-value">${desktop.metrics?.first_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">LCP:</span>
                                <span class="metric-value">${desktop.metrics?.largest_contentful_paint || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">CLS:</span>
                                <span class="metric-value">${desktop.metrics?.cumulative_layout_shift || 'N/A'}</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">SI:</span>
                                <span class="metric-value">${desktop.metrics?.speed_index || 'N/A'}s</span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">TTI:</span>
                                <span class="metric-value">${desktop.metrics?.time_to_interactive || 'N/A'}s</span>
                            </div>
                        </div>
                    ` : `
                        <div class="pagespeed-error">
                            Error: ${desktop.error || 'Analysis failed'}
                        </div>
                    `}
                </div>
            </div>
        `;

        container.appendChild(pageCard);
    });
}

function getScoreClass(score) {
    if (!score) return 'score-unknown';
    if (score >= 90) return 'score-good';
    if (score >= 50) return 'score-needs-improvement';
    return 'score-poor';
}

// Save/Load Crawl Functions
async function saveCrawl() {
    try {
        if (crawlState.stats.crawled === 0) {
            showNotification('No crawl data to save', 'error');
            return;
        }

        // Get current crawl data from backend or use local state
        let urls = crawlState.urls;
        let links = crawlState.links;
        let issues = crawlState.issues;
        let stats = crawlState.stats;

        // Try to get fresh data from backend if available
        try {
            const status = await fetch('/api/crawl_status');
            const crawlData = await status.json();
            if (crawlData.urls && crawlData.urls.length > 0) {
                urls = crawlData.urls;
                links = crawlData.links || links;
                issues = crawlData.issues || issues;
                // Update stats to include latest PageSpeed results if available
                if (crawlData.stats) {
                    stats = crawlData.stats;
                }
            }
        } catch (e) {
            console.log('Using local state for save:', e);
        }

        // Add metadata
        const saveData = {
            timestamp: new Date().toISOString(),
            baseUrl: crawlState.baseUrl,
            stats: stats,
            urls: urls,
            links: links,
            issues: issues,
            version: '1.1'
        };

        // Create and download the file
        const blob = new Blob([JSON.stringify(saveData, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;

        // Generate filename with domain and timestamp
        const domain = crawlState.baseUrl ? new URL(crawlState.baseUrl).hostname : 'crawl';
        const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
        a.download = `librecrawl_${domain}_${timestamp}.json`;

        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);

        showNotification('Crawl saved successfully', 'success');

    } catch (error) {
        console.error('Save error:', error);
        showNotification('Failed to save crawl', 'error');
    }
}

function loadCrawl() {
    // Create file input
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.json';
    fileInput.style.display = 'none';

    fileInput.addEventListener('change', async function(event) {
        const file = event.target.files[0];
        if (!file) return;

        try {
            const text = await file.text();
            const saveData = JSON.parse(text);

            // Validate save data
            if (!saveData.version || !saveData.urls || !saveData.stats) {
                showNotification('Invalid crawl file format', 'error');
                return;
            }

            // Clear current data
            clearAllTables();
            resetStats();

            // Load the data
            crawlState.baseUrl = saveData.baseUrl;
            crawlState.stats = saveData.stats;
            crawlState.urls = [];
            crawlState.links = saveData.links || [];
            crawlState.issues = saveData.issues || [];

            // Update UI
            document.getElementById('urlInput').value = saveData.baseUrl || '';
            updateStatsDisplay();

            // Populate tables with loaded data
            if (saveData.urls && saveData.urls.length > 0) {
                console.log(`Loading ${saveData.urls.length} URLs...`);

                // Clear crawlState.urls first to avoid duplicate check issues
                crawlState.urls = [];

                // Add URLs to tables (addUrlToTable will handle adding to crawlState.urls)
                saveData.urls.forEach(url => {
                    // Debug: check if url has is_internal flag
                    if (url.is_internal === undefined) {
                        console.warn('URL missing is_internal flag:', url.url);
                        // Try to determine is_internal based on domain
                        if (crawlState.baseUrl) {
                            try {
                                const urlDomain = new URL(url.url).hostname.replace('www.', '');
                                const baseDomain = new URL(crawlState.baseUrl).hostname.replace('www.', '');
                                url.is_internal = urlDomain === baseDomain;
                            } catch (e) {
                                url.is_internal = false;
                            }
                        }
                    }
                    addUrlToTable(url);
                });

                console.log(`Added ${crawlState.urls.length} URLs to state`);
                console.log('Sample URL data:', crawlState.urls[0]);
            }

            // Load links data
            if (saveData.links && saveData.links.length > 0) {
                console.log(`Loading ${saveData.links.length} links...`);
                crawlState.pendingLinks = saveData.links;
                // If Links tab is currently active, load them immediately
                if (isLinksTabActive()) {
                    updateLinksTable(saveData.links);
                }
            }

            // Load issues data if present - filter them based on current exclusion settings
            if (saveData.issues && saveData.issues.length > 0) {
                console.log(`Loading ${saveData.issues.length} issues...`);

                // Filter issues using current exclusion patterns
                try {
                    const filterResponse = await fetch('/api/filter_issues', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ issues: saveData.issues })
                    });
                    const filterData = await filterResponse.json();

                    const filteredIssues = filterData.success ? filterData.issues : saveData.issues;
                    console.log(`Filtered to ${filteredIssues.length} issues after exclusions`);

                    crawlState.issues = filteredIssues;
                    crawlState.pendingIssues = filteredIssues;

                    // If Issues tab is currently active, load them immediately
                    if (isIssuesTabActive()) {
                        updateIssuesTable(filteredIssues);
                    } else {
                        // Update the badge count even if tab is not active
                        const issuesTabButton = Array.from(document.querySelectorAll('.tab-btn')).find(btn => btn.textContent.includes('Issues'));
                        if (issuesTabButton) {
                            const errorCount = filteredIssues.filter(i => i.type === 'error').length;
                            const warningCount = filteredIssues.filter(i => i.type === 'warning').length;
                            let badgeColor = '#3b82f6';
                            if (errorCount > 0) badgeColor = '#ef4444';
                            else if (warningCount > 0) badgeColor = '#f59e0b';
                            issuesTabButton.innerHTML = `Issues <span style="background: ${badgeColor}; color: white; padding: 2px 6px; border-radius: 12px; font-size: 12px;">${filteredIssues.length}</span>`;
                        }
                    }
                } catch (error) {
                    console.error('Failed to filter issues:', error);
                    // Fall back to unfiltered issues if filtering fails
                    crawlState.issues = saveData.issues;
                    crawlState.pendingIssues = saveData.issues;
                    if (isIssuesTabActive()) {
                        updateIssuesTable(saveData.issues);
                    }
                }
            }

            // Update all secondary data
            updateFilterCounts();
            updateStatusCodesTable();
            updateCrawlButtons();

            // Display PageSpeed results if available
            if (saveData.stats && saveData.stats.pagespeed_results) {
                console.log(`Loading ${saveData.stats.pagespeed_results.length} PageSpeed results...`);
                displayPageSpeedResults(saveData.stats.pagespeed_results);
            }

            // Force refresh of all tables
            setTimeout(() => {
                console.log('Force refreshing tables...');
                const overviewCount = document.getElementById('overviewTableBody').children.length;
                const internalCount = document.getElementById('internalTableBody').children.length;
                const externalCount = document.getElementById('externalTableBody').children.length;
                console.log(`Table counts - Overview: ${overviewCount}, Internal: ${internalCount}, External: ${externalCount}`);
            }, 100);

            // Update visualization if it exists and has been initialized
            if (typeof window.updateVisualizationFromLoadedData === 'function') {
                window.updateVisualizationFromLoadedData(saveData.urls, saveData.links);
            }

            // Notify plugins of loaded data
            if (window.LibreCrawlPlugin && window.LibreCrawlPlugin.loader) {
                window.LibreCrawlPlugin.loader.notifyDataUpdate({
                    urls: crawlState.urls,
                    links: crawlState.links,
                    issues: crawlState.issues,
                    stats: crawlState.stats
                });
            }

            showNotification(`Crawl loaded: ${saveData.stats.crawled} URLs from ${new Date(saveData.timestamp).toLocaleDateString()}`, 'success');

        } catch (error) {
            console.error('Load error:', error);
            showNotification('Failed to load crawl file', 'error');
        }
    });

    // Trigger file selection
    document.body.appendChild(fileInput);
    fileInput.click();
    document.body.removeChild(fileInput);
}

// ========================================
// Virtual Scroller Render Functions
// ========================================

// Helper: create a URL cell with copy + open-in-new-tab icons
function createUrlCell(url) {
    const cell = document.createElement('td');
    cell.style.wordBreak = 'break-all';
    cell.title = url;

    const wrapper = document.createElement('span');
    wrapper.className = 'url-cell';

    const text = document.createElement('span');
    text.className = 'url-cell-text';
    text.textContent = url;
    wrapper.appendChild(text);

    const actions = document.createElement('span');
    actions.className = 'url-cell-actions';

    const copyBtn = document.createElement('button');
    copyBtn.className = 'url-action-btn';
    copyBtn.title = 'Copy URL';
    copyBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>';
    copyBtn.onclick = (e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(url);
        showNotification('URL copied', 'success');
    };
    actions.appendChild(copyBtn);

    const openBtn = document.createElement('button');
    openBtn.className = 'url-action-btn';
    openBtn.title = 'Open in new tab';
    openBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>';
    openBtn.onclick = (e) => {
        e.stopPropagation();
        window.open(url, '_blank');
    };
    actions.appendChild(openBtn);

    wrapper.appendChild(actions);
    cell.appendChild(wrapper);
    return cell;
}

function renderOverviewRow(row, urlData, index) {
    const analyticsInfo = formatAnalyticsInfo(urlData.analytics || {});
    const ogTagsCount = Object.keys(urlData.og_tags || {}).length;
    const jsonLdCount = (urlData.json_ld || []).length;
    const linksInfo = `${urlData.internal_links || 0}/${urlData.external_links || 0}`;
    const imagesCount = (urlData.images || []).length;
    const jsRendered = urlData.javascript_rendered ? '✅ JS' : '';

    // URL cell with action icons
    row.appendChild(createUrlCell(urlData.url));

    const cells = [
        urlData.status_code,
        urlData.title || '',
        (urlData.meta_description || '').substring(0, 50) + (urlData.meta_description && urlData.meta_description.length > 50 ? '...' : ''),
        urlData.h1 || '',
        urlData.word_count || 0,
        urlData.response_time || 0,
        analyticsInfo,
        ogTagsCount > 0 ? `${ogTagsCount} tags` : '',
        jsonLdCount > 0 ? `${jsonLdCount} scripts` : '',
        linksInfo,
        imagesCount > 0 ? `${imagesCount} images` : '',
        jsRendered,
        `<button class="details-btn" onclick="showUrlDetails('${urlData.url.replace(/'/g, "\\'")}')">📊 Details</button>`
    ];
    // Full values for tooltips (meta_description not truncated)
    const titles = [null, urlData.title, urlData.meta_description, urlData.h1, null, null, null, null, null, null, null, null, null];

    cells.forEach((cellData, i) => {
        const cell = document.createElement('td');
        if (typeof cellData === 'string' && cellData.includes('<button')) {
            cell.innerHTML = cellData;
        } else {
            cell.textContent = cellData;
            cell.title = titles[i] || String(cellData);
        }
        row.appendChild(cell);
    });
}

function renderInternalRow(row, urlData, index) {
    row.appendChild(createUrlCell(urlData.url));

    const cells = [
        urlData.status_code,
        urlData.content_type || '',
        urlData.size || 0,
        urlData.title || ''
    ];

    cells.forEach(cellData => {
        const cell = document.createElement('td');
        cell.textContent = cellData;
        cell.title = String(cellData);
        row.appendChild(cell);
    });
}

function renderExternalRow(row, urlData, index) {
    row.appendChild(createUrlCell(urlData.url));

    const cells = [
        urlData.status_code,
        urlData.content_type || '',
        urlData.size || 0,
        urlData.title || ''
    ];

    cells.forEach(cellData => {
        const cell = document.createElement('td');
        cell.textContent = cellData;
        cell.title = String(cellData);
        row.appendChild(cell);
    });
}

function placementBadge(placementDetail, placement) {
    const detail = placementDetail || placement || 'body';
    const label = detail.split(/[-_]/).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
    const cssClass = 'placement-' + detail;
    return `<span class="placement-badge ${cssClass}">${label}</span>`;
}

function renderInternalLinkRow(row, link, index) {
    const statusBadge = link.target_status ? `<span class="status-badge status-${Math.floor(link.target_status / 100)}xx">${link.target_status}</span>` : '';
    const placementHtml = placementBadge(link.placement_detail, link.placement);

    row.appendChild(createUrlCell(link.source_url));
    row.appendChild(createUrlCell(link.target_url));

    const remaining = [statusBadge, link.anchor_text || '', placementHtml];
    const remainingTitles = [link.target_status || '', link.anchor_text || '', link.placement || 'body'];
    remaining.forEach((html, i) => {
        const td = document.createElement('td');
        td.innerHTML = html;
        td.title = String(remainingTitles[i]);
        row.appendChild(td);
    });
}

function renderExternalLinkRow(row, link, index) {
    const statusBadge = link.target_status ? `<span class="status-badge status-${Math.floor(link.target_status / 100)}xx">${link.target_status}</span>` : '';
    const placementHtml = placementBadge(link.placement_detail, link.placement);

    row.appendChild(createUrlCell(link.source_url));
    row.appendChild(createUrlCell(link.target_url));

    const remaining = [statusBadge, link.target_domain || '', placementHtml];
    const remainingTitles = [link.target_status || '', link.target_domain || '', link.placement || 'body'];
    remaining.forEach((html, i) => {
        const td = document.createElement('td');
        td.innerHTML = html;
        td.title = String(remainingTitles[i]);
        row.appendChild(td);
    });
}

function renderIssueRow(row, issue, index) {
    row.setAttribute('data-issue-type', issue.type);

    // Set row style based on issue type
    if (issue.type === 'error') {
        row.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
    } else if (issue.type === 'warning') {
        row.style.backgroundColor = 'rgba(245, 158, 11, 0.1)';
    } else {
        row.style.backgroundColor = 'rgba(59, 130, 246, 0.1)';
    }

    // Create type indicator
    let typeIcon = '';
    let typeColor = '';
    if (issue.type === 'error') {
        typeIcon = '❌';
        typeColor = '#ef4444';
    } else if (issue.type === 'warning') {
        typeIcon = '⚠️';
        typeColor = '#f59e0b';
    } else {
        typeIcon = 'ℹ️';
        typeColor = '#3b82f6';
    }

    row.appendChild(createUrlCell(issue.url));

    const cells = [
        `<span style="color: ${typeColor};">${typeIcon}</span> ${issue.type}`,
        issue.category,
        issue.issue,
        issue.details
    ];
    cells.forEach((html, i) => {
        const td = document.createElement('td');
        if (i === 3) { td.style.wordBreak = 'break-word'; td.title = issue.details; }
        td.innerHTML = html;
        row.appendChild(td);
    });
}

function renderContentRow(row, urlData, index) {
    row.appendChild(createUrlCell(urlData.url));

    const title = document.createElement('td');
    title.textContent = urlData.title || '';
    title.title = urlData.title || '';
    row.appendChild(title);

    const h1 = document.createElement('td');
    h1.textContent = urlData.h1 || '';
    h1.title = urlData.h1 || '';
    row.appendChild(h1);

    const words = document.createElement('td');
    words.textContent = urlData.word_count || 0;
    row.appendChild(words);

    const body = document.createElement('td');
    const bodyText = urlData.body_text || '';
    const preview = bodyText.substring(0, 200) + (bodyText.length > 200 ? '...' : '');
    body.textContent = preview;
    body.title = 'Click to expand';
    body.style.cursor = bodyText.length > 200 ? 'pointer' : 'default';
    body.style.whiteSpace = 'normal';
    body.style.maxHeight = '60px';
    body.style.overflow = 'hidden';
    body.style.lineHeight = '1.4';
    body.style.fontSize = '12px';
    if (bodyText.length > 200) {
        body.addEventListener('click', () => {
            if (body._expanded) {
                body.textContent = preview;
                body.style.maxHeight = '60px';
                body._expanded = false;
            } else {
                body.textContent = bodyText;
                body.style.maxHeight = 'none';
                body._expanded = true;
            }
        });
    }
    row.appendChild(body);
}

function updateContentTable() {
    const urls = crawlState.urls || [];
    const emptyState = document.getElementById('contentEmptyState');
    const table = document.getElementById('contentTable');

    if (urls.length === 0) {
        if (emptyState) emptyState.style.display = 'block';
        if (table) table.style.display = 'none';
    } else {
        if (emptyState) emptyState.style.display = 'none';
        if (table) table.style.display = 'table';
        if (virtualScrollers.content) {
            virtualScrollers.content.setData(urls);
        }
    }
}

function toggleContentVectorizationMode(enabled) {
    const seoSettings = ['enablePageSpeed', 'enableDuplicationCheck'];
    seoSettings.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.disabled = enabled;
            el.closest('.setting-group')?.classList.toggle('disabled-setting', enabled);
        }
    });

    document.getElementById('cvApiKeyGroup').style.display = enabled ? 'block' : 'none';

    // Check max URLs for scale warning
    const maxUrls = parseInt(document.getElementById('maxUrls')?.value || '0');
    document.getElementById('cvScaleWarning').style.display = (enabled && maxUrls > 5000) ? 'block' : 'none';

    // Validate API key server-side
    if (enabled) {
        checkOpenAIKey();
        // Uncheck linkgraph mode (mutually exclusive)
        const lgEl = document.getElementById('linkgraphMode');
        if (lgEl) { lgEl.checked = false; toggleLinkgraphMode(false); }
    } else {
        document.getElementById('cvModeWarning').style.display = 'none';
    }
}

function toggleLinkgraphMode(enabled) {
    // Mutually exclusive with content vectorization
    if (enabled) {
        const cvEl = document.getElementById('contentVectorizationMode');
        if (cvEl && cvEl.checked) {
            cvEl.checked = false;
            toggleContentVectorizationMode(false);
        }
    }
    // Disable SEO-only settings when linkgraph is on
    const seoSettings = ['enablePageSpeed', 'enableDuplicationCheck'];
    seoSettings.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.disabled = enabled;
            el.closest('.setting-group')?.classList.toggle('disabled-setting', enabled);
        }
    });
}

function exportLinkgraphJSON() {
    closeExportAllModal();
    updateStatus('Generating LinkGraph JSON...');
    fetch('/api/export_data', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ format: 'linkgraph-json' })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success && data.content) {
            const blob = new Blob([data.content], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = data.filename || 'linkgraph.json';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            updateStatus('LinkGraph JSON downloaded.');
        } else {
            updateStatus('Export failed: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(err => {
        console.error('LinkGraph export error:', err);
        updateStatus('Export failed: ' + err.message);
    });
}

function checkOpenAIKey() {
    const warning = document.getElementById('cvModeWarning');
    fetch('/api/check_openai_key')
        .then(r => r.json())
        .then(data => {
            if (data.valid) {
                warning.textContent = 'API key verified.';
                warning.style.borderColor = 'rgba(106,122,64,0.3)';
                warning.style.background = 'rgba(106,122,64,0.15)';
                warning.style.color = '#c8d9a0';
            } else {
                warning.textContent = data.error || 'No API key configured. Enter one above.';
                warning.style.borderColor = 'rgba(239,68,68,0.3)';
                warning.style.background = 'rgba(239,68,68,0.15)';
                warning.style.color = '#ef4444';
            }
            warning.style.display = 'block';
        });
}

function saveOpenAIKey() {
    const key = document.getElementById('openaiApiKey').value.trim();
    fetch('/api/set_openai_key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key })
    })
    .then(r => r.json())
    .then(() => {
        checkOpenAIKey();
        if (key) {
            document.getElementById('openaiApiKey').value = '';
            document.getElementById('openaiApiKey').placeholder = 'sk-...saved';
        }
    });
}