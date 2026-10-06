/* ── Element refs ── */
  const fileInput          = document.getElementById("fileInput");
  const dropZone           = document.getElementById("dropZone");
  const runBtn             = document.getElementById("runBtn");
  const fileChip           = document.getElementById("fileChip");
  const fileName           = document.getElementById("fileName");
  const fileSize           = document.getElementById("fileSize");
  const audioMeta          = document.getElementById("audioMeta");
  const metaDuration       = document.getElementById("metaDuration");
  const metaFormat         = document.getElementById("metaFormat");
  const metaChannels       = document.getElementById("metaChannels");
  const metaSampleRate     = document.getElementById("metaSampleRate");
  const uploadBar         = document.getElementById("uploadBar");
  const uploadText        = document.getElementById("uploadText");
  const analysisBar       = document.getElementById("analysisBar");
  const analysisText      = document.getElementById("analysisText");
  const statusText        = document.getElementById("statusText");
  const spinner           = document.getElementById("spinner");
  const resultMeta        = document.getElementById("resultMeta");
  const transcriptEditor  = document.getElementById("transcriptEditor");
  const saveTranscriptBtn = document.getElementById("saveTranscriptBtn");
  const transcriptSaveHint= document.getElementById("transcriptSaveHint");
  const formatBtn         = document.getElementById("formatBtn");
  const formatStatus      = document.getElementById("formatStatus");
  const notesMeta         = document.getElementById("notesMeta");
  const notesEditor       = document.getElementById("notesEditor");
  const saveNotesBtn      = document.getElementById("saveNotesBtn");
  const notesSaveHint     = document.getElementById("notesSaveHint");
  const downloadTranscriptBtn = document.getElementById("downloadTranscriptBtn");
  const downloadNotesBtn = document.getElementById("downloadNotesBtn");

  /* ── State ── */
  const state = { file: null, jobId: null, eventSource: null, pollTimer: null, finished: false, result: [], loadedTitle: null, upload: null, pollBusy: false };
  const isHttp = location.protocol === "http:" || location.protocol === "https:";

  /* ── Tabs ── */
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    });
  });
  function switchTab(name) { document.querySelector(`[data-tab="${name}"]`).click(); }

  /* ── Utils ── */
  const setUploadProgress   = v => { const c = clamp(v); uploadBar.style.width = c + "%"; uploadText.textContent = c + "%"; };
  const setAnalysisProgress = v => { const c = clamp(v); analysisBar.style.width = c + "%"; analysisText.textContent = c + "%"; };
  const clamp = v => Math.max(0, Math.min(100, Math.round(v)));

  function setStatus(msg, loading = false) {
    statusText.textContent = msg;
    spinner.classList.toggle("on", loading);
  }
  function formatBytes(b) {
    if (!b) return "0 B";
    const u = ["B", "KB", "MB", "GB"]; let s = b, i = 0;
    while (s >= 1024 && i < u.length - 1) { s /= 1024; i++; }
    return s.toFixed(i === 0 ? 0 : 1) + " " + u[i];
  }
  function formatTime(sec) {
    const t = Math.max(0, Math.floor(sec));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
    return `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  }
  function escHtml(t) {
    return String(t).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
  }
  function fileBaseName() {
    if (state.loadedTitle) return state.loadedTitle;
    if (state.file) return state.file.name.replace(/\.[^.]+$/, "");
    return state.jobId || "meeting";
  }
  function buildTranscriptMd(items) {
    if (!items.length) return "";
    const lines = items.map(item =>
      `**[${item.speaker}]** \`${formatTime(item.start)} – ${formatTime(item.end)}\`\n\n${item.text}`
    );
    return `# STT 스크립트\n\n---\n\n` + lines.join("\n\n---\n\n");
  }
  function downloadMd(filename, content) {
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Allow the browser to consume the Blob before releasing it.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function syncDownloads() {
    downloadTranscriptBtn.disabled = !state.jobId || !transcriptEditor.value.trim();
    downloadNotesBtn.disabled = !state.jobId || !notesEditor.value.trim();
  }

  function showHint(el, msg, cls = "") {
    el.textContent = msg;
    el.className = "tab-hint" + (cls ? " " + cls : "");
  }

  async function readResponse(response) {
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${response.status}`);
    return body;
  }

  /* ── Audio metadata ── */
  function formatDuration(sec) {
    if (!isFinite(sec) || sec < 0) return "-";
    const t = Math.floor(sec);
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
    return `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  }

  function getFormatLabel(file) {
    const ext = file.name.split(".").pop().toLowerCase();
    const map = { mp3:"MP3", wav:"WAV", m4a:"M4A", aac:"AAC", ogg:"OGG",
                  flac:"FLAC", webm:"WebM", opus:"Opus", wma:"WMA", aiff:"AIFF" };
    return map[ext] || ext.toUpperCase() || file.type.split("/")[1]?.toUpperCase() || "-";
  }

  async function loadAudioMeta(file) {
    // 1) Duration via <audio> element (fast, non-blocking)
    const duration = await new Promise(resolve => {
      const url = URL.createObjectURL(file);
      const audio = new Audio();
      audio.preload = "metadata";
      audio.onloadedmetadata = () => { URL.revokeObjectURL(url); resolve(audio.duration); };
      audio.onerror            = () => { URL.revokeObjectURL(url); resolve(null); };
      audio.src = url;
    });
    if (state.file !== file) return;
    metaDuration.textContent = duration != null ? formatDuration(duration) : "-";

    // 2) Channels + sampleRate via AudioContext (heavier; skip if > 80 MB)
    if (file.size > 80 * 1024 * 1024) {
      metaChannels.textContent   = "-";
      metaSampleRate.textContent = "-";
      return;
    }
    try {
      const ctx     = new (window.AudioContext || window.webkitAudioContext)();
      const buf     = await file.arrayBuffer();
      const decoded = await ctx.decodeAudioData(buf);
      ctx.close();
      if (state.file !== file) return;
      const ch = decoded.numberOfChannels;
      metaChannels.textContent   = ch === 1 ? "모노" : ch === 2 ? "스테레오" : `${ch}ch`;
      metaSampleRate.textContent = `${decoded.sampleRate.toLocaleString()} Hz`;
    } catch (_) {
      metaChannels.textContent   = "-";
      metaSampleRate.textContent = "-";
    }
  }

  /* ── Reset ── */
  function clearResults() {
    transcriptEditor.value = "";
    resultMeta.textContent = "STT 완료 후 스크립트가 표시됩니다.";
    saveTranscriptBtn.disabled = true;
    showHint(transcriptSaveHint, "");
    formatBtn.disabled = true;
    showHint(formatStatus, "");
    notesEditor.value = "";
    saveNotesBtn.disabled = true;
    notesMeta.textContent = "STT 완료 후 AI 정리를 시작할 수 있습니다.";
    showHint(notesSaveHint, "");
    state.result = [];
    // audioMeta 그리드는 파일 선택 상태를 유지하므로 초기화하지 않음
    state.loadedTitle = null;
    syncDownloads();
  }
  function resetTracking() {
    if (state.eventSource) { state.eventSource.close(); state.eventSource = null; }
    if (state.pollTimer)   { clearInterval(state.pollTimer); state.pollTimer = null; }
  }

  /* ── File selection ── */
  function onFileSelected(file) {
    if (!file) return;
    if (state.upload || (state.jobId && !state.finished)) {
      setStatus("현재 음성 처리가 끝난 뒤 새 파일을 선택해주세요.", true);
      return;
    }
    resetTracking();
    state.jobId = null;
    state.file = file;
    fileName.textContent = file.name;
    fileSize.textContent = formatBytes(file.size);
    fileChip.classList.add("show");

    // Reset meta fields and show grid immediately
    metaDuration.textContent   = "…";
    metaFormat.textContent     = getFormatLabel(file);
    metaChannels.textContent   = "…";
    metaSampleRate.textContent = "…";
    audioMeta.classList.add("show");

    runBtn.disabled = false;
    state.finished = false;
    setUploadProgress(0);
    setAnalysisProgress(0);
    setStatus("업로드 준비 완료");
    clearResults();

    loadAudioMeta(file); // async, updates fields when ready
  }

  /* ── Server events ── */
  function handleServerEvent(payload) {
    if (!payload || state.finished || (payload.job_id && payload.job_id !== state.jobId)) return;

    if (payload.type === "status" || payload.status) {
      if (typeof payload.progress === "number") setAnalysisProgress(payload.progress);
      if (payload.stage) setStatus(payload.stage, payload.status !== "done" && payload.status !== "error");
    }
    if (payload.type === "transcript_info") {
      const dur = payload.duration_sec ?? 0, cnt = payload.segment_count ?? 0;
      resultMeta.textContent = `${cnt}개 구간 · 총 ${formatTime(dur)}`;
    }
    if (payload.type === "done" || (payload.status === "done" && Array.isArray(payload.result))) {
      state.finished = true;
      runBtn.disabled = !state.file;
      projectsBtn.disabled = false;
      setAnalysisProgress(100);
      setStatus("STT 완료");
      resetTracking();
      state.result = payload.result || [];
      transcriptEditor.value = buildTranscriptMd(state.result);
      resultMeta.textContent = `${state.result.length}개 발화 · 편집 후 저장 가능`;
      saveTranscriptBtn.disabled = !transcriptEditor.value.trim();
      formatBtn.disabled = !transcriptEditor.value.trim();
      syncDownloads();
      switchTab("transcript");
      return;
    }
    if (payload.type === "error" || payload.status === "error") {
      state.finished = true;
      runBtn.disabled = !state.file;
      projectsBtn.disabled = false;
      setStatus("분석 실패");
      resultMeta.innerHTML = `<span class="err">${escHtml(payload.error || "오류가 발생했습니다.")}</span>`;
      resetTracking();
    }
  }

  /* ── Polling / SSE ── */
  async function fetchResult(jobId) {
    try {
      const r = await fetch(`/result/${jobId}`);
      const d = await readResponse(r);
      if (state.jobId === jobId && d.status === "done") handleServerEvent({ job_id: jobId, type: "done", status: "done", result: d.result || [] });
    } catch (e) {
      resultMeta.innerHTML = `<span class="err">결과 조회 실패: ${escHtml(e.message)}</span>`;
    }
  }
  function startPolling(jobId) {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      if (state.finished || state.jobId !== jobId || state.pollBusy) return;
      state.pollBusy = true;
      try {
        const r = await fetch(`/status/${jobId}`);
        const d = await readResponse(r);
        handleServerEvent(d);
        if (d.status === "done") await fetchResult(jobId);
        if (d.status === "error") resetTracking();
      } catch (e) {
        resultMeta.innerHTML = `<span class="err">상태 조회 실패: ${escHtml(e.message)}</span>`;
      } finally { state.pollBusy = false; }
    }, 2000);
  }
  function startSSE(jobId) {
    if (!window.EventSource) return;
    const src = new EventSource(`/events/${jobId}`);
    state.eventSource = src;
    src.onmessage = e => { try { handleServerEvent(JSON.parse(e.data)); } catch (_) {} };
    src.onerror   = () => { src.close(); state.eventSource = null; };
  }

  /* ── Upload & run ── */
  function uploadAndRun() {
    if (!isHttp) {
      setStatus("서버에서 열어야 합니다.");
      resultMeta.innerHTML = `<span class="err">uvicorn main:app --reload 실행 후 http://localhost:8000 접속</span>`;
      return;
    }
    if (!state.file) return;
    resetTracking();
    state.finished = false;
    setUploadProgress(0); setAnalysisProgress(0);
    setStatus("파일 업로드 중...", true);
    runBtn.disabled = true;
    projectsBtn.disabled = true;
    state.jobId = null;
    clearResults();

    const form = new FormData();
    form.append("file", state.file);
    const xhr = new XMLHttpRequest();
    state.upload = xhr;
    xhr.open("POST", "/upload");
    xhr.upload.onprogress = e => { if (e.lengthComputable) setUploadProgress((e.loaded / e.total) * 100); };
    xhr.onload = () => {
      state.upload = null;
      if (xhr.status < 200 || xhr.status >= 300) {
        runBtn.disabled = false;
        projectsBtn.disabled = false;
        setStatus("업로드 실패");
        let msg = `HTTP ${xhr.status}`;
        try { const b = JSON.parse(xhr.responseText); if (b.detail) msg = b.detail; } catch (_) {}
        resultMeta.innerHTML = `<span class="err">${escHtml(msg)}</span>`;
        return;
      }
      setUploadProgress(100);
      let payload;
      try { payload = JSON.parse(xhr.responseText); } catch (_) { runBtn.disabled = false; projectsBtn.disabled = false; setStatus("응답 파싱 실패"); return; }
      state.jobId = payload.job_id;
      setStatus("텍스트 변환 중...", true);
      startSSE(state.jobId);
      startPolling(state.jobId);
    };
    xhr.onerror = () => {
      state.upload = null;
      projectsBtn.disabled = false;
      runBtn.disabled = false;
      setStatus("네트워크 오류");
      resultMeta.innerHTML = `<span class="err">서버 연결 실패. 서버가 실행 중인지 확인하세요.</span>`;
    };
    xhr.send(form);
  }

  /* ── Save transcript ── */
  async function handleSaveTranscript() {
    const content = transcriptEditor.value;
    if (!content || !state.jobId) return;
    saveTranscriptBtn.disabled = true;
    showHint(transcriptSaveHint, "저장 중...");
    try {
      const resp = await fetch(`/projects/${state.jobId}/save-transcript`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      showHint(transcriptSaveHint, `저장됨 · ${data.path}`, "ok");
      setTimeout(() => showHint(transcriptSaveHint, ""), 4000);
    } catch (err) {
      showHint(transcriptSaveHint, `실패: ${err.message}`, "error");
    } finally {
      saveTranscriptBtn.disabled = false;
    }
  }

  /* ── AI format ── */
  async function handleFormat() {
    if (!state.jobId || !state.finished) return;
    formatBtn.disabled = true;
    showHint(formatStatus, "AI 분석 중...");
    try {
      const jobId = state.jobId;
      const resp = await fetch(`/format/${jobId}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript: transcriptEditor.value }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      if (data.job_id !== state.jobId) return;
      notesEditor.value = data.notes;
      saveNotesBtn.disabled = false;
      notesMeta.textContent = "직접 편집 후 저장할 수 있습니다.";
      showHint(formatStatus, "완료", "ok");
      syncDownloads();
      switchTab("notes");
    } catch (err) {
      showHint(formatStatus, `실패: ${err.message}`, "error");
    } finally {
      formatBtn.disabled = false;
    }
  }

  /* ── Save notes ── */
  async function handleSaveNotes() {
    const content = notesEditor.value;
    if (!content || !state.jobId) return;
    saveNotesBtn.disabled = true;
    showHint(notesSaveHint, "저장 중...");
    try {
      const resp = await fetch(`/projects/${state.jobId}/save-notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      showHint(notesSaveHint, `저장됨 · ${data.path}`, "ok");
      setTimeout(() => showHint(notesSaveHint, ""), 4000);
    } catch (err) {
      showHint(notesSaveHint, `실패: ${err.message}`, "error");
    } finally {
      saveNotesBtn.disabled = false;
    }
  }

  /* ── Projects modal ── */
  const projectsModal = document.getElementById("projectsModal");
  const projectsList  = document.getElementById("projectsList");
  const projectsBtn   = document.getElementById("projectsBtn");
  const closeModal    = document.getElementById("closeModal");

  function formatDurSec(sec) {
    if (!sec) return null;
    const t = Math.floor(sec), h = Math.floor(t/3600), m = Math.floor((t%3600)/60), s = t%60;
    if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
    return `${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
  }

  function renderProjectsList(projects) {
    if (!projects.length) {
      projectsList.innerHTML = '<p class="modal-empty">저장된 프로젝트가 없습니다.</p>';
      return;
    }
    projectsList.innerHTML = "";
    for (const p of projects) {
      const item = document.createElement("div");
      item.className = "project-item";
      const date = p.created_at
        ? new Date(p.created_at).toLocaleString("ko-KR", { dateStyle:"short", timeStyle:"short" })
        : "-";
      const dur = formatDurSec(p.duration_sec);
      const badges = [];
      if (dur)              badges.push(`<span class="badge badge-dur">${dur}</span>`);
      if (p.has_transcript) badges.push('<span class="badge">스크립트</span>');
      if (p.has_notes)      badges.push('<span class="badge badge-ai">AI 회의록</span>');
      item.innerHTML = `
        <div class="pi-name">${escHtml(p.original_name || p.title)}</div>
        <div class="pi-date">${date}</div>
        <div class="pi-badges">${badges.join("") || '<span class="badge">분석 완료</span>'}</div>
      `;
      item.addEventListener("click", () => loadProject(p.job_id));
      projectsList.appendChild(item);
    }
  }

  async function openProjectsModal() {
    projectsModal.classList.add("open");
    projectsList.innerHTML = '<p class="modal-empty">불러오는 중...</p>';
    try {
      const resp = await fetch("/projects");
      renderProjectsList(await readResponse(resp));
    } catch (e) {
      projectsList.innerHTML = `<p class="modal-empty err">로드 실패: ${escHtml(e.message)}</p>`;
    }
  }

  function closeProjectsModal() { projectsModal.classList.remove("open"); }

  async function loadProject(jobId) {
    try {
      const resp = await fetch(`/projects/${jobId}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      resetTracking();
      state.jobId       = jobId;
      state.finished    = true;
      state.result      = data.segments || [];
      state.loadedTitle = data.title || null;

      transcriptEditor.value = data.transcript ?? buildTranscriptMd(state.result);
      notesEditor.value      = data.notes || "";

      saveTranscriptBtn.disabled = !transcriptEditor.value;
      saveNotesBtn.disabled      = !data.notes;
      formatBtn.disabled         = !transcriptEditor.value.trim();

      resultMeta.textContent = `${data.original_name} · 불러옴`;
      notesMeta.textContent  = data.notes
        ? "직접 편집 후 저장할 수 있습니다."
        : "STT 완료 후 AI 정리를 시작할 수 있습니다.";

      setUploadProgress(100);
      setAnalysisProgress(100);
      setStatus("프로젝트 로드 완료");

      closeProjectsModal();
      syncDownloads();
      switchTab("transcript");
    } catch (e) {
      closeProjectsModal();
      alert(`프로젝트 로드 실패: ${e.message}`);
    }
  }

  projectsBtn.addEventListener("click", openProjectsModal);
  closeModal.addEventListener("click", closeProjectsModal);
  projectsModal.addEventListener("click", e => { if (e.target === projectsModal) closeProjectsModal(); });

  /* ── Event listeners ── */
  transcriptEditor.addEventListener("input", () => {
    syncDownloads();
    saveTranscriptBtn.disabled = !state.jobId || !transcriptEditor.value.trim();
    formatBtn.disabled = !state.finished || !state.jobId || !transcriptEditor.value.trim();
  });
  notesEditor.addEventListener("input", () => {
    syncDownloads();
    saveNotesBtn.disabled = !state.jobId || !notesEditor.value.trim();
  });
  downloadTranscriptBtn.addEventListener("click", () => downloadMd(fileBaseName() + "_transcript.md", transcriptEditor.value));
  downloadNotesBtn.addEventListener("click", () => downloadMd(fileBaseName() + "_meeting_notes.md", notesEditor.value));
  runBtn.addEventListener("click", uploadAndRun);
  saveTranscriptBtn.addEventListener("click", handleSaveTranscript);
  formatBtn.addEventListener("click", handleFormat);
  saveNotesBtn.addEventListener("click", handleSaveNotes);

  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", e => onFileSelected(e.target.files?.[0]));

  ["dragenter", "dragover"].forEach(t => dropZone.addEventListener(t, e => {
    e.preventDefault(); e.stopPropagation(); dropZone.classList.add("dragover");
  }));
  ["dragleave", "drop"].forEach(t => dropZone.addEventListener(t, e => {
    e.preventDefault(); e.stopPropagation(); dropZone.classList.remove("dragover");
  }));
  dropZone.addEventListener("drop", e => onFileSelected(e.dataTransfer?.files?.[0]));
  window.addEventListener("beforeunload", resetTracking);
  window.addEventListener("keydown", e => { if (e.key === "Escape") closeProjectsModal(); });

  if (!isHttp) {
    setStatus("서버에서 열어야 합니다.");
    resultMeta.innerHTML = `<span class="err">파일로 직접 열면 동작하지 않습니다. 서버 실행 후 http://localhost:8000 접속</span>`;
    runBtn.disabled = true;
  }
