// Globals to maintain app state
let appConfig = {};
let unnamedClusters = [];
let currentClusterIndex = 0;
let currentClusterFaces = [];
let existingClusterNames = [];
let currentFaceIdForMove = null;

// Multi-selection & Undo Stack states
let selectedFaceIds = [];
let lastSelectedFaceId = null;
let undoStack = [];

// Collapsible log panel height management
const logToggleBtn = document.getElementById('btn-toggle-logs');
const logPanel = document.getElementById('log-panel');
const logContent = document.getElementById('log-content');

// Screens
const screenSettings = document.getElementById('screen-settings');
const screenProgress = document.getElementById('screen-progress');
const screenLabeling = document.getElementById('screen-labeling');

// Inputs & Buttons (Settings)
const inputSourceDir = document.getElementById('input-source-dir');
const inputDestDir = document.getElementById('input-dest-dir');
const btnBrowseSource = document.getElementById('btn-browse-source');
const btnBrowseDest = document.getElementById('btn-browse-dest');
const btnStartPipeline = document.getElementById('btn-start-pipeline');
const chkResetDb = document.getElementById('chk-reset-db');

// Progress Screen Elements
const progressBarFill = document.getElementById('progress-bar-fill');
const progressPercent = document.getElementById('progress-percent');
const progressStageTitle = document.getElementById('progress-stage-title');
const progressStageSubtitle = document.getElementById('progress-stage-subtitle');

// Labeling Screen Elements
const labelingClusterId = document.getElementById('labeling-cluster-id');
const labelingPhotoCount = document.getElementById('labeling-photo-count');
const currentClusterIndexSpan = document.getElementById('current-cluster-index');
const totalClustersCountSpan = document.getElementById('total-clusters-count');
const facesGrid = document.getElementById('faces-grid');
const inputClusterName = document.getElementById('input-cluster-name');
const btnSkipCluster = document.getElementById('btn-skip-cluster');
const btnSaveCluster = document.getElementById('btn-save-cluster');
const autocompleteList = document.getElementById('autocomplete-list');
const btnUndo = document.getElementById('btn-undo');

// Floating Selection Toolbar Elements
const selectionToolbar = document.getElementById('selection-toolbar');
const selectedCountValue = document.getElementById('selected-count-value');
const btnDeleteSelected = document.getElementById('btn-delete-selected');
const btnMoveSelected = document.getElementById('btn-move-selected');
const btnClearSelection = document.getElementById('btn-clear-selection');

// Preview Modal Elements
const previewModal = document.getElementById('preview-modal');
const previewImage = document.getElementById('preview-image');
const previewSvgOverlay = document.getElementById('preview-svg-overlay');
const spotlightCircle = document.getElementById('spotlight-circle');
const spotlightBorder = document.getElementById('spotlight-border');
const btnClosePreview = document.getElementById('btn-close-preview');

// Move Modal Elements
const moveModal = document.getElementById('move-modal');
const inputMoveSearch = document.getElementById('input-move-search');
const moveClusterList = document.getElementById('move-cluster-list');
const btnCancelMove = document.getElementById('btn-cancel-move');
const btnConfirmMove = document.getElementById('btn-confirm-move');

// Theme Switcher
const themeToggleBtn = document.getElementById('theme-toggle');

/* ==========================================================================
   INITIALIZATION & BOOTSTRAP
   ========================================================================== */
window.addEventListener('DOMContentLoaded', async () => {
    // 1. Get initial configuration from backend
    try {
        appConfig = await eel.get_initial_config()();
        if (appConfig) {
            inputSourceDir.value = appConfig.source_dir || '';
            inputDestDir.value = appConfig.dest_dir || '';
            
            if (appConfig.device === 'cuda') {
                document.getElementById('device-cuda').checked = true;
            } else {
                document.getElementById('device-cpu').checked = true;
            }
            chkResetDb.checked = appConfig.reset_db || false;
        }
    } catch (err) {
        console.error("Помилка ініціалізації конфігу:", err);
    }

    // 2. Load existing names for autocomplete
    loadExistingNames();

    // 3. Register Event Listeners & Initialize interactions
    setupEventListeners();
    setupHotkeys();
    setupMarqueeSelection();
});

// Load existing cluster names from db
async function loadExistingNames() {
    try {
        existingClusterNames = await eel.get_existing_names()();
    } catch (err) {
        console.error("Не вдалося завантажити існуючі імена:", err);
    }
}

function setupEventListeners() {
    // Disable native HTML5 drag-and-drop ghosting globally
    document.addEventListener('dragstart', (e) => {
        e.preventDefault();
    });

    // Theme switching
    themeToggleBtn.addEventListener('click', () => {
        const body = document.body;
        if (body.classList.contains('dark-theme')) {
            body.classList.replace('dark-theme', 'light-theme');
        } else {
            body.classList.replace('light-theme', 'dark-theme');
        }
    });

    // Directory Selection
    btnBrowseSource.addEventListener('click', async () => {
        const selected = await eel.select_directory(inputSourceDir.value)();
        if (selected) inputSourceDir.value = selected;
    });

    btnBrowseDest.addEventListener('click', async () => {
        const selected = await eel.select_directory(inputDestDir.value)();
        if (selected) inputDestDir.value = selected;
    });

    // Start Pipeline
    btnStartPipeline.addEventListener('click', () => {
        if (!inputSourceDir.value || !inputDestDir.value) {
            alert("Будь ласка, оберіть вихідну та цільову папки.");
            return;
        }

        const device = document.querySelector('input[name="device-select"]:checked').value;
        const resetDb = chkResetDb.checked;

        appConfig.source_dir = inputSourceDir.value;
        appConfig.dest_dir = inputDestDir.value;
        appConfig.device = device;
        appConfig.reset_db = resetDb;

        switchScreen('screen-progress');
        eel.start_pipeline(appConfig)();
    });

    // Toggle Console Logs
    logToggleBtn.addEventListener('click', () => {
        const isOpen = logPanel.classList.contains('open');
        if (isOpen) {
            logPanel.classList.remove('open');
            logToggleBtn.classList.remove('active');
            logToggleBtn.querySelector('span').innerText = 'Показати деталі';
        } else {
            logPanel.classList.add('open');
            logToggleBtn.classList.add('active');
            logToggleBtn.querySelector('span').innerText = 'Приховати деталі';
            setTimeout(() => {
                logContent.scrollTop = logContent.scrollHeight;
            }, 50);
        }
    });

    // Header Undo Button
    btnUndo.addEventListener('click', () => {
        undoLastAction();
    });

    // Skip/Save Cluster Actions
    btnSkipCluster.addEventListener('click', () => {
        pushUndoAction({
            type: 'skip_cluster',
            index: currentClusterIndex
        });
        nextCluster();
    });

    btnSaveCluster.addEventListener('click', async () => {
        const name = inputClusterName.value.trim();
        if (!name) {
            alert("Будь ласка, введіть ім'я для збереження або натисніть 'Пропустити'");
            return;
        }

        const clusterId = unnamedClusters[currentClusterIndex];
        const faceIds = currentClusterFaces.map(f => f.id);
        
        const success = await eel.save_cluster_name(clusterId, name)();
        if (success) {
            pushUndoAction({
                type: 'name_cluster',
                clusterId: clusterId,
                name: name,
                faceIds: faceIds,
                index: currentClusterIndex
            });

            if (!existingClusterNames.includes(name)) {
                existingClusterNames.push(name);
                existingClusterNames.sort();
            }
            nextCluster();
        } else {
            alert("Не вдалося зберегти ім'я кластера.");
        }
    });

    // Autocomplete events
    inputClusterName.addEventListener('input', () => {
        showAutocomplete(inputClusterName.value.trim());
    });

    // Hide autocomplete on click outside
    document.addEventListener('click', (e) => {
        if (!inputClusterName.contains(e.target) && !autocompleteList.contains(e.target)) {
            autocompleteList.classList.add('hidden');
        }
    });

    // Close preview modal
    btnClosePreview.addEventListener('click', () => {
        previewModal.classList.add('hidden');
        previewImage.src = '';
    });

    previewModal.addEventListener('click', (e) => {
        if (e.target === previewModal) {
            previewModal.classList.add('hidden');
            previewImage.src = '';
        }
    });

    // Move Modal events
    btnCancelMove.addEventListener('click', () => {
        moveModal.classList.add('hidden');
        currentFaceIdForMove = null;
    });

    inputMoveSearch.addEventListener('input', () => {
        renderMoveClusterOptions(inputMoveSearch.value.trim());
    });

    btnConfirmMove.addEventListener('click', async () => {
        const selectedOption = moveClusterList.querySelector('.cluster-option-item.selected');
        if (!selectedOption || !currentFaceIdForMove) return;

        const targetCluster = selectedOption.dataset.name;

        // Path for BULK move
        if (currentFaceIdForMove === 'BULK') {
            const affectedFaces = selectedFaceIds.map(id => {
                const face = currentClusterFaces.find(f => f.id === id);
                return { id: face.id, filepath: face.filepath, bbox: face.bbox };
            });

            pushUndoAction({
                type: 'bulk_move',
                clusterId: unnamedClusters[currentClusterIndex],
                targetCluster: targetCluster,
                faces: affectedFaces
            });

            // Perform moves sequentially
            for (let faceId of selectedFaceIds) {
                await eel.update_face_cluster(faceId, targetCluster)();
                currentClusterFaces = currentClusterFaces.filter(f => f.id !== faceId);
                const card = document.querySelector(`.face-card[data-id="${faceId}"]`);
                if (card) card.remove();
            }

            labelingPhotoCount.innerText = currentClusterFaces.length;
            clearSelection();
            moveModal.classList.add('hidden');
            currentFaceIdForMove = null;

            if (currentClusterFaces.length === 0) {
                nextCluster();
            }
            return;
        }

        // Path for SINGLE move
        const faceId = currentFaceIdForMove;
        const success = await eel.update_face_cluster(faceId, targetCluster)();
        if (success) {
            pushUndoAction({
                type: 'move_face',
                faceId: faceId,
                fromCluster: unnamedClusters[currentClusterIndex],
                toCluster: targetCluster
            });

            currentClusterFaces = currentClusterFaces.filter(f => f.id !== faceId);
            const card = document.querySelector(`.face-card[data-id="${faceId}"]`);
            if (card) {
                card.style.transform = 'scale(0.8)';
                card.style.opacity = '0';
                setTimeout(() => {
                    card.remove();
                    labelingPhotoCount.innerText = currentClusterFaces.length;
                    if (currentClusterFaces.length === 0) {
                        nextCluster();
                    }
                }, 300);
            }
            moveModal.classList.add('hidden');
            currentFaceIdForMove = null;
        } else {
            alert("Не вдалося перенести обличчя.");
        }
    });

    // Floating Selection Toolbar actions
    btnClearSelection.addEventListener('click', () => {
        clearSelection();
    });

    btnDeleteSelected.addEventListener('click', async () => {
        if (selectedFaceIds.length === 0) return;

        const affectedFaces = selectedFaceIds.map(id => {
            const face = currentClusterFaces.find(f => f.id === id);
            return { id: face.id, filepath: face.filepath, bbox: face.bbox };
        });

        pushUndoAction({
            type: 'bulk_delete',
            clusterId: unnamedClusters[currentClusterIndex],
            faces: affectedFaces
        });

        for (let faceId of selectedFaceIds) {
            await eel.update_face_cluster(faceId, 'Noise')();
            currentClusterFaces = currentClusterFaces.filter(f => f.id !== faceId);
            const card = document.querySelector(`.face-card[data-id="${faceId}"]`);
            if (card) {
                card.style.transform = 'scale(0.8)';
                card.style.opacity = '0';
                card.remove(); // Direct removal for clean bulk transition
            }
        }

        labelingPhotoCount.innerText = currentClusterFaces.length;
        clearSelection();

        if (currentClusterFaces.length === 0) {
            nextCluster();
        }
    });

    btnMoveSelected.addEventListener('click', () => {
        if (selectedFaceIds.length === 0) return;
        openMoveModal('BULK');
    });

    // Clear selection if background grid is clicked
    facesGrid.addEventListener('click', (e) => {
        if (e.target === facesGrid) {
            clearSelection();
        }
    });
}

/* ==========================================================================
   NAVIGATION
   ========================================================================== */
function switchScreen(screenId) {
    const screens = [screenSettings, screenProgress, screenLabeling];
    screens.forEach(s => {
        if (s.id === screenId) {
            s.classList.add('active');
        } else {
            s.classList.remove('active');
        }
    });
}

/* ==========================================================================
   EEL EXPOSED CALLBACKS
   ========================================================================== */
eel.expose(update_progress);
function update_progress(percent, title, subtitle) {
    progressBarFill.style.width = percent + '%';
    progressPercent.innerText = percent + '%';
    if (title) progressStageTitle.innerText = title;
    if (subtitle) progressStageSubtitle.innerText = subtitle;
}

eel.expose(append_log);
function append_log(text) {
    const line = document.createElement('div');
    line.innerText = text;
    logContent.appendChild(line);
    logContent.scrollTop = logContent.scrollHeight;
}

eel.expose(on_pipeline_done);
function on_pipeline_done(clusters) {
    unnamedClusters = clusters || [];
    currentClusterIndex = 0;
    undoStack = []; // Reset undo stack upon new run
    updateUndoButtonVisibility();

    if (unnamedClusters.length === 0) {
        append_log("[Інфо] Немає нерозмічених кластерів. Запуск завершального кроку організації...");
        update_progress(10, "Етап 5: Організація", "Організація файлів...");
        switchScreen('screen-progress');
        eel.start_organizer(appConfig)();
    } else {
        switchScreen('screen-labeling');
        totalClustersCountSpan.innerText = unnamedClusters.length;
        loadCluster(currentClusterIndex);
    }
}

eel.expose(on_organizer_done);
function on_organizer_done() {
    update_progress(100, "Все готово!", "Архів успішно відсортовано.");
    append_log("=========================================");
    append_log("ОБРОБКА ПОВНІСТЮ ЗАВЕРШЕНА.");
    append_log("Ви можете закрити програму.");
    append_log("=========================================");
}

eel.expose(on_pipeline_error);
function on_pipeline_error(errorMsg) {
    update_progress(100, "Помилка", "Сталася помилка у фоновому конвеєрі");
    append_log(`[ПОМИЛКА] ${errorMsg}`);
    alert(`Помилка обробки: ${errorMsg}`);
}

/* ==========================================================================
   LABELING WORKFLOW
   ========================================================================== */
async function loadCluster(index) {
    if (index < 0 || index >= unnamedClusters.length) return;
    
    const clusterId = unnamedClusters[index];
    labelingClusterId.innerText = `#${clusterId}`;
    currentClusterIndexSpan.innerText = index + 1;
    inputClusterName.value = '';
    autocompleteList.classList.add('hidden');
    facesGrid.innerHTML = '';
    
    // Clear any active selections
    clearSelection();
    
    // Fetch faces
    try {
        currentClusterFaces = await eel.get_cluster_faces(clusterId)();
        labelingPhotoCount.innerText = currentClusterFaces.length;

        // Render card layouts
        currentClusterFaces.forEach(face => {
            const card = document.createElement('div');
            card.className = 'face-card';
            card.dataset.id = face.id;
            card.draggable = false;

            const img = document.createElement('img');
            img.src = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><rect width="100" height="100" fill="%231a2030"/><circle cx="50" cy="50" r="10" fill="%23616e7f"/></svg>';
            img.draggable = false;
            card.appendChild(img);

            // Checkmark indicator badge
            const checkBadge = document.createElement('div');
            checkBadge.className = 'face-card-check';
            checkBadge.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
            card.appendChild(checkBadge);

            // Create actions overlay
            const overlay = document.createElement('div');
            overlay.className = 'face-card-overlay';

            // ✕ Delete / Noise Button
            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'card-action-btn delete-btn';
            deleteBtn.title = 'Видалити / Шум';
            deleteBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`;
            deleteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                deleteFace(face.id);
            });

            // ➔ Move Button
            const moveBtn = document.createElement('button');
            moveBtn.className = 'card-action-btn move-btn';
            moveBtn.title = 'Перенести в іншу групу';
            moveBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>`;
            moveBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                openMoveModal(face.id);
            });

            // 👁 Preview Button
            const previewBtn = document.createElement('button');
            previewBtn.className = 'card-action-btn preview-btn';
            previewBtn.title = 'Показати оригінал';
            previewBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8Z"></path></svg>`;
            previewBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                openPreview(face.filepath, face.bbox);
            });

            overlay.appendChild(deleteBtn);
            overlay.appendChild(moveBtn);
            overlay.appendChild(previewBtn);
            card.appendChild(overlay);
            
            // Selection event listener (Single click)
            card.addEventListener('click', (e) => {
                // If user clicked any of the action buttons inside overlay, ignore selection toggling
                if (e.target.closest('.card-action-btn')) return;
                toggleFaceSelection(face.id, e.shiftKey);
            });

            // Double click to preview directly
            card.addEventListener('dblclick', (e) => {
                if (e.target.closest('.card-action-btn')) return;
                openPreview(face.filepath, face.bbox);
            });

            facesGrid.appendChild(card);

            // Progressive Image Loading
            loadFaceThumbnail(face.filepath, face.bbox, img);
        });

    } catch (err) {
        console.error("Помилка завантаження облич кластера:", err);
    }
}

async function loadFaceThumbnail(filepath, bbox, imgElement) {
    try {
        const base64Str = await eel.get_image_base64(filepath, bbox, true)();
        if (base64Str) {
            imgElement.src = `data:image/jpeg;base64,${base64Str}`;
        }
    } catch (err) {
        console.error("Помилка завантаження мініатюри обличчя:", err);
    }
}

async function deleteFace(faceId) {
    const success = await eel.update_face_cluster(faceId, 'Noise')();
    if (success) {
        pushUndoAction({
            type: 'delete_face',
            faceId: faceId,
            fromCluster: unnamedClusters[currentClusterIndex]
        });

        currentClusterFaces = currentClusterFaces.filter(f => f.id !== faceId);
        const card = document.querySelector(`.face-card[data-id="${faceId}"]`);
        if (card) {
            card.style.transform = 'scale(0.8)';
            card.style.opacity = '0';
            setTimeout(() => {
                card.remove();
                labelingPhotoCount.innerText = currentClusterFaces.length;
                if (currentClusterFaces.length === 0) {
                    nextCluster();
                }
            }, 300);
        }
    } else {
        alert("Не вдалося позначити обличчя як шум.");
    }
}

function nextCluster() {
    currentClusterIndex++;
    if (currentClusterIndex >= unnamedClusters.length) {
        append_log("[Інфо] Розмітка завершена. Запуск завершального кроку організації...");
        update_progress(10, "Етап 5: Організація", "Організація файлів...");
        switchScreen('screen-progress');
        eel.start_organizer(appConfig)();
    } else {
        loadCluster(currentClusterIndex);
    }
}

/* ==========================================================================
   MULTI-SELECTION LOGIC
   ========================================================================== */
function toggleFaceSelection(faceId, isShift) {
    const card = document.querySelector(`.face-card[data-id="${faceId}"]`);
    if (!card) return;

    if (isShift && lastSelectedFaceId) {
        // Range select via Shift-Click
        const cardElements = Array.from(facesGrid.querySelectorAll('.face-card'));
        const idx1 = cardElements.findIndex(c => parseInt(c.dataset.id) === lastSelectedFaceId);
        const idx2 = cardElements.findIndex(c => parseInt(c.dataset.id) === faceId);
        
        if (idx1 !== -1 && idx2 !== -1) {
            const start = Math.min(idx1, idx2);
            const end = Math.max(idx1, idx2);
            
            for (let i = start; i <= end; i++) {
                const c = cardElements[i];
                const id = parseInt(c.dataset.id);
                if (!selectedFaceIds.includes(id)) {
                    selectedFaceIds.push(id);
                    c.classList.add('selected');
                }
            }
        }
    } else {
        // Standard single select toggle
        if (selectedFaceIds.includes(faceId)) {
            selectedFaceIds = selectedFaceIds.filter(id => id !== faceId);
            card.classList.remove('selected');
        } else {
            selectedFaceIds.push(faceId);
            card.classList.add('selected');
            lastSelectedFaceId = faceId;
        }
    }
    updateSelectionToolbar();
}

function updateSelectionToolbar() {
    if (selectedFaceIds.length > 0) {
        selectedCountValue.innerText = selectedFaceIds.length;
        selectionToolbar.classList.remove('hidden');
    } else {
        selectionToolbar.classList.add('hidden');
    }
}

function clearSelection() {
    selectedFaceIds = [];
    lastSelectedFaceId = null;
    const cards = facesGrid.querySelectorAll('.face-card');
    cards.forEach(c => c.classList.remove('selected'));
    updateSelectionToolbar();
}

/* ==========================================================================
   MOUSE MARQUEE (DRAG-TO-SELECT BOX)
   ========================================================================== */
function setupMarqueeSelection() {
    const container = document.querySelector('.faces-grid-container');

    let startX, startY;
    let marquee = null;
    let isDragging = false;

    container.addEventListener('mousedown', (e) => {
        // Trigger on primary button click on the grid container or items (ignoring action overlays)
        if (e.button !== 0) return;
        if (e.target.closest('.card-action-btn') || e.target.closest('.autocomplete-dropdown') || e.target.closest('.modal-content-container')) return;

        isDragging = true;
        const rect = container.getBoundingClientRect();
        
        // Calculate coordinates relative to grid container including scroll height/width
        startX = e.clientX - rect.left + container.scrollLeft;
        startY = e.clientY - rect.top + container.scrollTop;

        // Clear select state on click empty background
        if (e.target === container || e.target === facesGrid) {
            clearSelection();
        }

        // Create box overlay element inside relative container
        marquee = document.createElement('div');
        marquee.className = 'selection-box';
        marquee.style.left = startX + 'px';
        marquee.style.top = startY + 'px';
        marquee.style.width = '0px';
        marquee.style.height = '0px';
        container.appendChild(marquee);

        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isDragging || !marquee) return;

        const rect = container.getBoundingClientRect();
        const currentX = e.clientX - rect.left + container.scrollLeft;
        const currentY = e.clientY - rect.top + container.scrollTop;

        const left = Math.min(startX, currentX);
        const top = Math.min(startY, currentY);
        const width = Math.abs(startX - currentX);
        const height = Math.abs(startY - currentY);

        marquee.style.left = left + 'px';
        marquee.style.top = top + 'px';
        marquee.style.width = width + 'px';
        marquee.style.height = height + 'px';

        // Select items intersecting the marquee boundaries
        const selectionRect = marquee.getBoundingClientRect();
        const cards = facesGrid.querySelectorAll('.face-card');
        
        cards.forEach(card => {
            const cardRect = card.getBoundingClientRect();
            const id = parseInt(card.dataset.id);

            const intersects = !(
                cardRect.right < selectionRect.left ||
                cardRect.left > selectionRect.right ||
                cardRect.bottom < selectionRect.top ||
                cardRect.top > selectionRect.bottom
            );

            if (intersects) {
                card.classList.add('selected');
            } else {
                card.classList.remove('selected');
            }
        });

        // Sync selectedFaceIds with the selected DOM classes
        selectedFaceIds = [];
        const selectedCards = facesGrid.querySelectorAll('.face-card.selected');
        selectedCards.forEach(c => {
            selectedFaceIds.push(parseInt(c.dataset.id));
        });

        updateSelectionToolbar();
    });

    const clearActiveMarquees = () => {
        isDragging = false;
        const boxes = container.querySelectorAll('.selection-box');
        boxes.forEach(b => b.remove());
        marquee = null;
    };

    document.addEventListener('mouseup', () => {
        if (isDragging) {
            clearActiveMarquees();
        }
    });

    window.addEventListener('blur', () => {
        if (isDragging) {
            clearActiveMarquees();
        }
    });
}

/* ==========================================================================
   UNDO (CTRL+Z) LOGIC
   ========================================================================== */
function pushUndoAction(action) {
    undoStack.push(action);
    if (undoStack.length > 5) {
        undoStack.shift();
    }
    updateUndoButtonVisibility();
}

function updateUndoButtonVisibility() {
    if (undoStack.length > 0) {
        btnUndo.classList.remove('hidden');
    } else {
        btnUndo.classList.add('hidden');
    }
}

async function undoLastAction() {
    if (undoStack.length === 0) return;
    const action = undoStack.pop();
    updateUndoButtonVisibility();

    if (action.type === 'delete_face' || action.type === 'move_face') {
        const success = await eel.update_face_cluster(action.faceId, action.fromCluster)();
        if (success) {
            loadCluster(currentClusterIndex);
        }
    } else if (action.type === 'bulk_delete' || action.type === 'bulk_move') {
        for (let face of action.faces) {
            await eel.update_face_cluster(face.id, action.clusterId)();
        }
        loadCluster(currentClusterIndex);
    } else if (action.type === 'name_cluster') {
        for (let faceId of action.faceIds) {
            await eel.update_face_cluster(faceId, action.clusterId)();
        }
        currentClusterIndex = action.index;
        loadCluster(currentClusterIndex);
    } else if (action.type === 'skip_cluster') {
        currentClusterIndex = action.index;
        loadCluster(currentClusterIndex);
    }
}

/* ==========================================================================
   SYSTEM HOTKEYS
   ========================================================================== */
function setupHotkeys() {
    document.addEventListener('keydown', (e) => {
        // Labeling hotkeys require screen to be active
        if (!screenLabeling.classList.contains('active')) return;

        // Escape checks to dismiss active modals
        if (!moveModal.classList.contains('hidden') || !previewModal.classList.contains('hidden')) {
            if (e.key === 'Escape') {
                moveModal.classList.add('hidden');
                previewModal.classList.add('hidden');
                previewImage.src = '';
            }
            return;
        }

        // Ctrl + Z triggers undo
        if (e.ctrlKey && (e.key.toLowerCase() === 'z' || e.code === 'KeyZ')) {
            e.preventDefault();
            undoLastAction();
            return;
        }

        const isInputFocused = document.activeElement === inputClusterName || document.activeElement === inputMoveSearch;

        // Enter submits cluster naming when input is focused
        if (isInputFocused) {
            if (e.key === 'Enter') {
                e.preventDefault();
                btnSaveCluster.click();
            }
            return;
        }

        // Action Hotkeys (space to skip, enter to save)
        if (e.key === 'Enter') {
            e.preventDefault();
            btnSaveCluster.click();
        } else if (e.key === ' ' || (e.ctrlKey && e.key === 'Enter')) {
            e.preventDefault();
            btnSkipCluster.click();
        }
    });
}

/* ==========================================================================
   AUTOCOMPLETE SUGGESTIONS
   ========================================================================== */
function showAutocomplete(query) {
    autocompleteList.innerHTML = '';
    
    if (!query) {
        autocompleteList.classList.add('hidden');
        return;
    }

    const filtered = existingClusterNames.filter(name => 
        name.toLowerCase().startsWith(query.toLowerCase())
    );

    if (filtered.length === 0) {
        autocompleteList.classList.add('hidden');
        return;
    }

    filtered.forEach(name => {
        const item = document.createElement('div');
        item.className = 'autocomplete-item';
        item.innerText = name;
        item.addEventListener('click', () => {
            inputClusterName.value = name;
            autocompleteList.classList.add('hidden');
        });
        autocompleteList.appendChild(item);
    });

    autocompleteList.classList.remove('hidden');
}

/* ==========================================================================
   SPOTLIGHT IMAGE PREVIEW
   ========================================================================== */
async function openPreview(filepath, bbox) {
    previewImage.src = '';
    previewSvgOverlay.classList.add('hidden');
    previewModal.classList.remove('hidden');

    try {
        const response = await eel.get_image_base64(filepath, bbox, false)();
        if (response && response.image) {
            previewImage.src = `data:image/jpeg;base64,${response.image}`;
            
            previewImage.onload = () => {
                if (response.bbox) {
                    const natWidth = previewImage.naturalWidth;
                    const natHeight = previewImage.naturalHeight;
                    
                    previewSvgOverlay.setAttribute('viewBox', `0 0 ${natWidth} ${natHeight}`);
                    
                    const box = response.bbox;
                    const width = box[2] - box[0];
                    const height = box[3] - box[1];
                    const cx = box[0] + width / 2;
                    const cy = box[1] + height / 2;
                    const r = Math.max(width, height) / 2 * 1.3;
                    
                    spotlightCircle.setAttribute('cx', cx);
                    spotlightCircle.setAttribute('cy', cy);
                    spotlightCircle.setAttribute('r', r);
                    
                    spotlightBorder.setAttribute('cx', cx);
                    spotlightBorder.setAttribute('cy', cy);
                    spotlightBorder.setAttribute('r', r);
                    
                    previewSvgOverlay.classList.remove('hidden');
                } else {
                    previewSvgOverlay.classList.add('hidden');
                }
            };
        }
    } catch (err) {
        console.error("Помилка відкриття попереднього перегляду:", err);
    }
}

/* ==========================================================================
   MOVE FACE TO ANOTHER CLUSTER
   ========================================================================== */
async function openMoveModal(faceId) {
    currentFaceIdForMove = faceId;
    inputMoveSearch.value = '';
    moveModal.classList.remove('hidden');
    btnConfirmMove.disabled = true;
    
    await loadExistingNames();
    renderMoveClusterOptions('');
}

function renderMoveClusterOptions(query) {
    moveClusterList.innerHTML = '';
    let targets = ['Noise', ...existingClusterNames];
    
    if (query) {
        targets = targets.filter(t => t.toLowerCase().includes(query.toLowerCase()));
    }
    
    if (targets.length === 0) {
        const emptyMsg = document.createElement('div');
        emptyMsg.style.padding = '12px';
        emptyMsg.style.color = 'var(--text-tertiary)';
        emptyMsg.style.textAlign = 'center';
        emptyMsg.innerText = 'Нічого не знайдено';
        moveClusterList.appendChild(emptyMsg);
        return;
    }
    
    targets.forEach(name => {
        const item = document.createElement('div');
        item.className = 'cluster-option-item';
        item.dataset.name = name;
        
        let displayLabel = name;
        if (name === 'Noise') {
            displayLabel = '✕ Шум / Невідомо';
            item.style.color = 'var(--accent-error)';
        }
        
        item.innerText = displayLabel;
        
        item.addEventListener('click', () => {
            const prevSelected = moveClusterList.querySelector('.cluster-option-item.selected');
            if (prevSelected) prevSelected.classList.remove('selected');
            
            item.classList.add('selected');
            btnConfirmMove.disabled = false;
        });
        
        moveClusterList.appendChild(item);
    });
}
