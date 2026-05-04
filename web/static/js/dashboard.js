// ========================================
// Dashboard Functions
// ========================================

async function openDashboard() {
    const modal = document.getElementById('dashboardModal');
    const content = document.getElementById('dashboardContent');

    // Show modal
    modal.style.display = 'flex';

    // Load crawls
    try {
        const response = await fetch('/api/crawls/list');
        const data = await response.json();

        if (!data.success) {
            content.innerHTML = `<p style="color: #ef4444;">Error loading crawls: ${data.error}</p>`;
            return;
        }

        const crawls = data.crawls || [];

        if (crawls.length === 0) {
            content.innerHTML = `<p style="text-align: center; color: #9ca3af;">No saved crawls found.</p>`;
            return;
        }

        // Build table
        let html = `
            <table class="data-table" style="width: 100%; table-layout: fixed;">
                <thead>
                    <tr>
                        <th style="width: 180px;">Date</th>
                        <th style="width: 200px;">Domain</th>
                        <th style="width: 80px;">URLs</th>
                        <th style="width: 70px;">Age</th>
                        <th style="width: 100px;">Status</th>
                        <th style="width: 280px;">Actions</th>
                    </tr>
                </thead>
                <tbody>
        `;

        crawls.forEach(crawl => {
            const date = new Date(crawl.started_at).toLocaleString();
            const domain = crawl.base_domain || crawl.base_url;
            const status = crawl.status || 'unknown';
            const statusColor = status === 'completed' ? '#10b981' : status === 'running' ? '#3b82f6' : status === 'paused' ? '#f59e0b' : '#6b7280';

            // Calculate age
            const ageMs = Date.now() - new Date(crawl.started_at).getTime();
            const ageDays = Math.floor(ageMs / (1000 * 60 * 60 * 24));
            let ageText, ageColor;
            if (ageDays === 0) {
                ageText = 'Today';
                ageColor = '#10b981';
            } else if (ageDays === 1) {
                ageText = '1 day';
                ageColor = '#10b981';
            } else if (ageDays <= 30) {
                ageText = `${ageDays} days`;
                ageColor = '#9ca3af';
            } else if (ageDays <= 60) {
                ageText = `${ageDays} days`;
                ageColor = '#f59e0b';
            } else {
                ageText = `${ageDays} days`;
                ageColor = '#ef4444';
            }

            html += `
                <tr>
                    <td>${date}</td>
                    <td>${domain}</td>
                    <td>${crawl.urls_crawled || 0}</td>
                    <td><span style="color: ${ageColor};">${ageText}</span></td>
                    <td><span style="color: ${statusColor};">${status}</span></td>
                    <td style="white-space: nowrap;">
                        <button class="btn btn-primary" style="margin-right: 5px; padding: 6px 12px; font-size: 13px;" onclick="loadCrawlFromDashboard(${crawl.id})">Load</button>
                        <button class="btn btn-secondary" style="margin-right: 5px; padding: 6px 12px; font-size: 13px;" onclick="resumeCrawlFromDashboard(${crawl.id})">Resume</button>
                        <button class="btn btn-danger" style="padding: 6px 12px; font-size: 13px;" onclick="deleteCrawlFromDashboard(${crawl.id})">Delete</button>
                    </td>
                </tr>
            `;
        });

        html += `
                </tbody>
            </table>
        `;

        content.innerHTML = html;

    } catch (error) {
        console.error('Error loading dashboard:', error);
        content.innerHTML = `<p style="color: #ef4444;">Error loading crawls.</p>`;
    }
}

function closeDashboard() {
    document.getElementById('dashboardModal').style.display = 'none';
}

async function loadCrawlFromDashboard(crawlId) {
    if (!confirm('Load this crawl? Any unsaved current data will be lost.')) return;

    try {
        const response = await fetch(`/api/crawls/${crawlId}/load`, {
            method: 'POST'
        });
        const data = await response.json();

        if (!data.success) {
            alert('Error: ' + (data.error || data.message));
            return;
        }

        // Redirect — main page will detect 'loading' status and start polling
        window.location.href = '/';

    } catch (error) {
        console.error('Error loading crawl:', error);
        alert('Error loading crawl');
    }
}

async function resumeCrawlFromDashboard(crawlId) {
    if (!confirm('Resume this crawl? Any unsaved current data will be lost.')) return;

    try {
        const response = await fetch(`/api/crawls/${crawlId}/resume`, {
            method: 'POST'
        });
        const data = await response.json();

        if (!data.success) {
            alert('Error: ' + (data.error || data.message));
            return;
        }

        // Use redirect pattern so page fully reinitializes and polls correctly
        sessionStorage.setItem('force_ui_refresh', 'true');
        window.location.href = '/';

    } catch (error) {
        console.error('Error resuming crawl:', error);
        alert('Error resuming crawl');
    }
}

async function deleteCrawlFromDashboard(crawlId) {
    if (!confirm('Delete this crawl permanently? This cannot be undone.')) return;

    try {
        const response = await fetch(`/api/crawls/${crawlId}/delete`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.success) {
            showNotification('Crawl deleted', 'success');
            // Reload dashboard
            openDashboard();
        } else {
            alert('Error deleting crawl: ' + data.error);
        }
    } catch (error) {
        console.error('Error deleting crawl:', error);
        alert('Error deleting crawl');
    }
}
