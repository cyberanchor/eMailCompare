// ==========================================================================
// Utility Functions
// ==========================================================================

/**
 * Escapes HTML characters in text to prevent XSS and formats newlines as <br>.
 * @param {string|null|undefined} text - The text to escape.
 * @returns {string} - Escaped HTML text.
 */
function escapeHtml(text) {
    if (text === null || text === undefined) {
        console.warn('escapeHtml: Received null or undefined text, returning "-"');
        return '-';
    }
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML.replace(/\n/g, '<br>');
}

// ==========================================================================
// Data Loading and Table Population
// ==========================================================================

/**
 * Loads data from data.json and populates the table.
 */
async function loadData() {
    console.log('loadData: Starting data load');
    try {
        const response = await fetch('data.json', {
            cache: 'default' // Use browser cache for performance
        });
        if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}`);
        }
        const data = await response.json();
        console.log('loadData: Data loaded successfully', data);
        populateTable(data);
        generateDownloads(data);
    } catch (error) {
        console.error('loadData: Error loading JSON data:', error);
        document.getElementById('tableBody').innerHTML = '<tr><td colspan="18">Error loading data</td></tr>';
    }
}

/**
 * Populates the table with data from JSON.
 * @param {Array} domainsData - Array of domain objects from JSON.
 */
function populateTable(domainsData) {
    console.log('populateTable: Populating table with data', domainsData);
    const tbody = document.getElementById('tableBody');
    if (!tbody) {
        console.error('populateTable: Table body element not found');
        return;
    }
    tbody.innerHTML = ''; // Clear existing content
    domainsData.forEach((domain, index) => {
        try {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td class="fixed-column copyable ${domain.flag.color !== 'none' ? 'bg-' + domain.flag.color : ''}" onclick="copyText(this)">${escapeHtml(domain.flag.value)}</td>
                <td class="fixed-column copyable ${domain.Domain.color !== 'none' ? 'bg-' + domain.Domain.color : ''}" onclick="copyText(this)"><a href="https://${escapeHtml(domain.Domain.value)}" target="_blank">${escapeHtml(domain.Domain.value)}</a></td>
                <td class="copyable ${domain['2nd domain'].color !== 'none' ? 'bg-' + domain['2nd domain'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['2nd domain'].value)}</td>
                <td class="copyable ${domain['A Record'].color !== 'none' ? 'bg-' + domain['A Record'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['A Record'].value)}</td>
                <td class="copyable ${domain['MX Record'].color !== 'none' ? 'bg-' + domain['MX Record'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['MX Record'].value)}</td>
                <td class="copyable ${domain['Domain crated'].color !== 'none' ? 'bg-' + domain['Domain crated'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['Domain crated'].value)}</td>
                <td class="copyable ${domain['Domain expired'].color !== 'none' ? 'bg-' + domain['Domain expired'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['Domain expired'].value)}</td>
                <td class="${domain['Company owner'].color !== 'none' ? 'bg-' + domain['Company owner'].color : ''}"><div class="resizable-box copyable" onclick="copyText(this, 'content')"><div class="content">${escapeHtml(domain['Company owner'].value)}</div><div class="resize-handle"></div></div></td>
                <td class="copyable ${domain['Intelligence Alliance'].color !== 'none' ? 'bg-' + domain['Intelligence Alliance'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['Intelligence Alliance'].value)}</td>
                <td class="copyable ${domain['Jurisdiction (based on server location)'].color !== 'none' ? 'bg-' + domain['Jurisdiction (based on server location)'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['Jurisdiction (based on server location)'].value)}</td>
                <td class="${domain['Site description'].color !== 'none' ? 'bg-' + domain['Site description'].color : ''}"><div class="resizable-box copyable" onclick="copyText(this, 'content')"><div class="content">${escapeHtml(domain['Site description'].value)}</div><div class="resize-handle"></div></div></td>
                <td class="${domain['ToS'].color !== 'none' ? 'bg-' + domain['ToS'].color : ''}"><div class="resizable-box copyable" onclick="copyText(this, 'content')"><div class="content">${escapeHtml(domain['ToS'].value)}</div><div class="resize-handle"></div></div></td>
                <td class="${domain['Privacy policy'].color !== 'none' ? 'bg-' + domain['Privacy policy'].color : ''}"><div class="resizable-box copyable" onclick="copyText(this, 'content')"><div class="content">${escapeHtml(domain['Privacy policy'].value)}</div><div class="resize-handle"></div></div></td>
                <td class="copyable ${domain['Email(receive a verification link)'].color !== 'none' ? 'bg-' + domain['Email(receive a verification link)'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['Email(receive a verification link)'].value)}</td>
                <td class="copyable ${domain['Phone(receive s sms code)'].color !== 'none' ? 'bg-' + domain['Phone(receive s sms code)'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['Phone(receive s sms code)'].value)}</td>
                <td class="copyable ${domain['Price'].color !== 'none' ? 'bg-' + domain['Price'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['Price'].value)}</td>
                <td class="copyable ${domain['Personal Info(first name, last name, adress, etc..)'].color !== 'none' ? 'bg-' + domain['Personal Info(first name, last name, adress, etc..)'].color : ''}" onclick="copyText(this)">${escapeHtml(domain['Personal Info(first name, last name, adress, etc..)'].value)}</td>
                <td class="${domain['Note'].color !== 'none' ? 'bg-' + domain['Note'].color : ''}"><div class="resizable-box copyable" onclick="copyText(this, 'content')"><div class="content">${escapeHtml(domain['Note'].value)}</div><div class="resize-handle"></div></div></td>
            `;
            tbody.appendChild(row);
            console.log(`populateTable: Added row ${index + 1} successfully`);
        } catch (error) {
            console.error(`populateTable: Error adding row ${index + 1}:`, error);
        }
    });
    initResizable();
}

// ==========================================================================
// Sorting Functions
// ==========================================================================

// Variables for tracking sort state
let sortDirection = 1; // 1 for ascending, -1 for descending
let lastSortedColumn = -1; // Index of the last sorted column

/**
 * Sorts the table by the specified column index.
 * @param {number} columnIndex - The index of the column to sort.
 */
function sortTable(columnIndex) {
    console.log(`sortTable: Sorting column ${columnIndex}`);
    const tbody = document.getElementById('tableBody');
    if (!tbody) {
        console.error('sortTable: Table body not found');
        return;
    }
    const rows = Array.from(tbody.getElementsByTagName('tr'));
    
    if (lastSortedColumn === columnIndex) {
        sortDirection *= -1; // Reverse direction if same column
    } else {
        sortDirection = 1; // Reset to ascending for new column
    }
    lastSortedColumn = columnIndex;

    try {
        rows.sort((a, b) => {
            const aValue = a.cells[columnIndex].textContent;
            const bValue = b.cells[columnIndex].textContent;
            return aValue.localeCompare(bValue, undefined, { numeric: true }) * sortDirection;
        });
        rows.forEach(row => tbody.appendChild(row));
        console.log(`sortTable: Column ${columnIndex} sorted successfully`);
    } catch (error) {
        console.error('sortTable: Error sorting table:', error);
    }
}

// ==========================================================================
// Search Functionality
// ==========================================================================

/**
 * Filters table rows based on search query.
 * @param {string} query - The search query entered by the user.
 */
function searchTable(query) {
    console.log(`searchTable: Searching with query "${query}"`);
    const tbody = document.getElementById('tableBody');
    if (!tbody) {
        console.error('searchTable: Table body not found');
        return;
    }
    const rows = Array.from(tbody.getElementsByTagName('tr'));
    query = query.toLowerCase();

    try {
        rows.forEach(row => {
            const text = Array.from(row.cells).map(cell => cell.textContent.toLowerCase()).join(' ');
            row.style.display = text.includes(query) ? '' : 'none';
        });
        console.log('searchTable: Search completed');
    } catch (error) {
        console.error('searchTable: Error during search:', error);
    }
}

// ==========================================================================
// Copy Text Functionality
// ==========================================================================

/**
 * Copies text from an element to the clipboard.
 * @param {HTMLElement} element - The element to copy text from.
 * @param {string} [contentClass] - Optional class to target specific content within the element.
 */
function copyText(element, contentClass = '') {
    console.log('copyText: Copying text from element');
    try {
        const text = contentClass ? element.querySelector(`.${contentClass}`).textContent : element.textContent;
        navigator.clipboard.writeText(text).then(() => {
            element.style.backgroundColor = '#505050'; // Highlight effect
            setTimeout(() => {
                element.style.backgroundColor = ''; // Reset after 200ms
            }, 200);
            console.log(`copyText: Successfully copied "${text}"`);
        }).catch(err => {
            console.error('copyText: Failed to copy text:', err);
        });
    } catch (error) {
        console.error('copyText: Error processing copy:', error);
    }
}

// ==========================================================================
// Resizable Box Initialization
// ==========================================================================

/**
 * Initializes resizable functionality for elements with class "resizable-box".
 */
function initResizable() {
    console.log('initResizable: Initializing resizable boxes');
    const resizableBoxes = document.querySelectorAll('.resizable-box');
    if (resizableBoxes.length === 0) {
        console.warn('initResizable: No resizable boxes found');
    }
    resizableBoxes.forEach((box, index) => {
        try {
            let resizeHandle = box.querySelector('.resize-handle');
            if (!resizeHandle) {
                resizeHandle = document.createElement('div');
                resizeHandle.className = 'resize-handle';
                box.appendChild(resizeHandle);
            }
            let isResizing = false;
            let startX, startY, startWidth, startHeight;

            resizeHandle.addEventListener('mousedown', (e) => {
                e.stopPropagation();
                isResizing = true;
                startX = e.clientX;
                startY = e.clientY;
                startWidth = parseInt(document.defaultView.getComputedStyle(box).width, 10);
                startHeight = parseInt(document.defaultView.getComputedStyle(box).height, 10);
                console.log(`initResizable: Started resizing box ${index}`);

                document.addEventListener('mousemove', resize);
                document.addEventListener('mouseup', stopResize);
            });

            const resize = (e) => {
                if (!isResizing) return;
                const width = startWidth + (e.clientX - startX);
                const height = startHeight + (e.clientY - startY);
                box.style.width = Math.max(100, width) + 'px';
                box.style.height = Math.max(40, height) + 'px';
            };

            const stopResize = () => {
                isResizing = false;
                document.removeEventListener('mousemove', resize);
                document.removeEventListener('mouseup', stopResize);
                console.log(`initResizable: Stopped resizing box ${index}`);
            };
        } catch (error) {
            console.error(`initResizable: Error initializing box ${index}:`, error);
        }
    });
}

// ==========================================================================
// Download Functionality
// ==========================================================================

/**
 * Generates a downloadable text file from domain data.
 * @param {Array} data - Array of domain objects.
 */
function generateDownloads(data) {
    console.log('generateDownloads: Generating download file');
    try {
        const txtContent = data.map(d => d.Domain.value).join('\n');
        const txtBlob = new Blob([txtContent], { type: 'text/plain' });
        window.txtUrl = URL.createObjectURL(txtBlob);
        console.log('generateDownloads: Download file generated');
    } catch (error) {
        console.error('generateDownloads: Error generating download file:', error);
    }
}

/**
 * Triggers the download of the generated text file.
 */
function downloadAllMail() {
    console.log('downloadAllMail: Initiating download');
    try {
        const link = document.createElement('a');
        link.href = window.txtUrl;
        link.download = 'emails.txt';
        link.click();
        console.log('downloadAllMail: Download triggered');
    } catch (error) {
        console.error('downloadAllMail: Error triggering download:', error);
    }
}

// ==========================================================================
// Initialization
// ==========================================================================

// Load data when the page is fully loaded
window.onload = () => {
    console.log('Window onload: Initializing application');
    loadData();
};