const state = { videos: [], candidates: [], jobs: {}, selectedVideoId: null, reviewVideoId: null, audioSettings: { mic_audio_ordinal: -1 }, storageSettings: null, clipFolderDrafts: new Map(), combineSelection: new Set(), combineVideoId: null, chapterSelections: new Map() };
let hadActiveJob = false;
let lmStudioState = null;
let lmStudioBusy = false;
let pendingHateCandidate = null;
let folderBrowserTarget = null;
let folderBrowserCurrent = "";
let folderBrowserParent = null;
let folderBrowserFolders = [];
const AUTO_BUILD_MODES = ["0", "3", "5", "all"];
let autoBuildMode = localStorage.getItem("clipmaker-auto-build") || "0";
if (!AUTO_BUILD_MODES.includes(autoBuildMode)) autoBuildMode = "0";

const $ = (selector) => document.querySelector(selector);
const formatHours = (seconds = 0) => `${(seconds / 3600).toFixed(1)}h`;
const formatBytes = (bytes = 0) => `${(bytes / 1024 ** 3).toFixed(0)} GB`;
const formatTime = (seconds = 0) => {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  return `${hours ? `${hours}:` : ""}${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

function humanReason(reason = "") {
  if (reason.startsWith("OBS-flagged moment")) return "You flagged this area while recording, so it was checked with extra context before the marker.";
  if (reason.startsWith("Excited audio plus transcript")) return "A strong reaction and funny dialogue landed in the same moment.";
  if (reason.startsWith("Excited audio; transcript")) return "A big reaction landed here, though the dialogue signal was less certain.";
  if (reason.startsWith("Audio spike")) return "A noticeable reaction was detected across the available audio tracks.";
  if (reason.startsWith("Likely music")) return "This sounds repetitive or music-heavy, so it was ranked lower.";
  if (reason.startsWith("Combined context + payoff")) return "Combined from the setup and payoff clips you selected.";
  return reason;
}

function isCombinedCandidate(candidate) {
  return String(candidate?.reason || "").startsWith("Combined context + payoff");
}

function jobsByRecency() {
  return Object.values(state.jobs).sort(
    (a, b) => (b.updated_at || b.completed_at || b.started_at || 0) - (a.updated_at || a.completed_at || a.started_at || 0),
  );
}

function currentJob() {
  const jobs = jobsByRecency();
  return jobs.find((job) => job.status === "running")
    || jobs.find((job) => job.status === "error")
    || jobs[0]
    || null;
}

function renderActivity() {
  const panel = $("#activity-panel");
  const job = currentJob();
  const status = job?.status || "idle";
  const percent = Math.max(0, Math.min(100, Number(job?.progress || 0)));
  panel.dataset.state = status;
  $("#activity-title").textContent = job?.title || "Ready when you are";
  $("#activity-detail").textContent = job?.detail || "Find a moment or build the next cuts. Everything runs locally on this PC.";
  $("#activity-stage").textContent = job?.stage || "Nothing running";
  $("#activity-count").textContent = job?.total > 1 ? `Item ${job.current || 1} of ${job.total}` : "";
  $("#activity-percent").textContent = status === "running" ? `${percent}%` : status === "error" ? "Failed" : status === "complete" ? "Done" : "Ready";
  $("#activity-progress").style.width = `${status === "idle" ? 0 : percent}%`;
  panel.querySelector(".progress-track").setAttribute("aria-valuenow", String(percent));
  $("#job-status").textContent = status === "running" ? "Working" : status === "error" ? "Needs attention" : status === "complete" ? "Finished" : "Ready";

  const busy = jobsByRecency().some((item) => item.status === "running");
  $("#batch-button").disabled = busy;
  $("#prepare-button").disabled = busy;
  $("#scan-button").disabled = busy;
}

function renderLmStudioState() {
  const control = $("#lmstudio-control");
  const loaded = Boolean(lmStudioState?.model_loaded);
  const serverUp = Boolean(lmStudioState?.server_up);
  const fullyGaming = !serverUp && Array.isArray(lmStudioState?.loaded_models) && lmStudioState.loaded_models.length === 0;
  const unavailable = !lmStudioState?.ok;
  control.dataset.state = unavailable ? "error" : loaded && serverUp ? "ai" : fullyGaming ? "gaming" : "partial";
  $("#lmstudio-status").textContent = unavailable ? "LM Studio control unavailable" : loaded && serverUp ? "AI Mode · Qwen loaded" : fullyGaming ? "Gaming Mode · GPU released" : "LM Studio partially active";
  $("#lmstudio-detail").textContent = unavailable ? (lmStudioState?.error || "AI Council controller is offline") : "One shared Qwen model for all Clip Maker AI";
  $("#lmstudio-load").disabled = lmStudioBusy || unavailable || (loaded && serverUp);
  $("#lmstudio-gaming").disabled = lmStudioBusy || unavailable || fullyGaming;
}

async function loadLmStudioState() {
  if (lmStudioBusy) return;
  try {
    lmStudioState = await api("/api/lmstudio");
  } catch (error) {
    lmStudioState = { ok: false, error: error.message };
  }
  renderLmStudioState();
}

async function setLmStudioMode(mode) {
  lmStudioBusy = true;
  const action = mode === "gaming" ? "Unloading every LM Studio model…" : "Starting LM Studio and loading Qwen…";
  $("#lmstudio-status").textContent = action;
  $("#lmstudio-detail").textContent = mode === "gaming" ? "Verifying lms ps is empty before claiming the GPU is free." : "This can take a minute while the model loads into VRAM.";
  $("#lmstudio-load").disabled = true;
  $("#lmstudio-gaming").disabled = true;
  try {
    const result = await api("/api/lmstudio/" + mode, { method: "POST" });
    lmStudioState = result.state || await api("/api/lmstudio");
  } catch (error) {
    lmStudioState = { ok: false, error: error.message };
  } finally {
    lmStudioBusy = false;
    renderLmStudioState();
  }
}

async function loadStats() {  const stats = await api("/api/stats");
  const pending = stats.candidates?.pending || 0;
  const approved = stats.candidates?.approved || 0;
  const rejected = stats.candidates?.rejected || 0;
  $("#stats").innerHTML = [
    ["Unique VODs", stats.unique_videos || 0],
    ["Recording time", formatHours(stats.duration_sec)],
    ["Duplicates skipped", `${stats.duplicates || 0} · ${formatBytes(stats.duplicate_bytes)}`],
    ["VODs analyzed", stats.analyzed || 0],
    ["Review queue", `${pending} to review · ${approved} liked · ${rejected} passed`],
  ].map(([label, value]) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`).join("");
}

async function loadVideos() {
  state.videos = await api("/api/videos");
  renderVideos();
}

function renderVideos() {
  const needle = $("#video-search").value.trim().toLowerCase();
  const rows = state.videos.filter((video) => video.rel_path.toLowerCase().includes(needle));
  $("#video-list").innerHTML = rows.map((video) => {
    const reviewing = Number(video.id) === Number(state.reviewVideoId) ? " reviewing" : "";
    const clipCount = Number(video.candidate_count || 0);
    return '<button class="video-row' + reviewing + '" data-video-id="' + video.id + '"><span><strong>' + escapeHtml(video.name) + '</strong><small>' + formatHours(video.duration_sec) + ' · ' + (video.size_bytes / 1024 ** 3).toFixed(1) + ' GB · ' + video.status + '</small></span><span class="vod-row-side"><span class="track-count">' + video.audio_streams.length + ' audio</span>' + ((video.chapters || []).length ? '<small class="chapter-count">&#9873; ' + video.chapters.length + ' flags</small>' : '') + (clipCount ? '<small>' + clipCount + ' clips</small>' : '') + '</span></button>';
  }).join("") || `<div class="empty">No matching recordings.</div>`;
  document.querySelectorAll(".video-row[data-video-id]").forEach((button) => {
    button.addEventListener("click", () => selectVideo(button.dataset.videoId));
  });
}

function selectedVideo() {
  return state.videos.find((video) => Number(video.id) === Number(state.selectedVideoId));
}

function clipFolderDraft(video) {
  const key = Number(video.id);
  if (!state.clipFolderDrafts.has(key)) state.clipFolderDrafts.set(key, video.clip_folder_name || "");
  return state.clipFolderDrafts.get(key);
}

function renderClipFolderDestination(video) {
  const name = clipFolderDraft(video) || video.clip_folder_name || "VOD";
  const root = state.storageSettings?.output_dir || "Finished clips folder";
  $("#vod-clip-folder-destination").textContent = "Future renders save to " + root + "\\" + name + (Number(video.candidate_count || 0) ? ". Existing rendered files will not be moved." : ". You can rename it before analysis.");
}

function reviewVideo() {
  return state.videos.find((video) => Number(video.id) === Number(state.reviewVideoId));
}

function renderReviewFilter() {
  const banner = $("#vod-review-filter");
  const video = reviewVideo();
  banner.hidden = !video;
  if (video) $("#vod-review-filter-label").textContent = "Showing only clips from " + video.name;
  const unbuilt = state.candidates.filter((item) => !item.active_variant).length;
  ["#build-vod-3", "#build-vod-5", "#build-vod-all"].forEach((selector) => {
    const button = $(selector);
    button.disabled = !video || unbuilt === 0;
  });
  renderVideos();
}

function renderAutoBuildMode() {
  const button = $("#auto-build-vod");
  const labels = { "0": "Off", "3": "Best 3", "5": "Best 5", all: "All candidates" };
  button.dataset.mode = autoBuildMode;
  button.textContent = "Auto-build after analysis: " + labels[autoBuildMode];
}

function cycleAutoBuildMode() {
  const index = AUTO_BUILD_MODES.indexOf(autoBuildMode);
  autoBuildMode = AUTO_BUILD_MODES[(index + 1) % AUTO_BUILD_MODES.length];
  localStorage.setItem("clipmaker-auto-build", autoBuildMode);
  renderAutoBuildMode();
}

async function setReviewVideo(videoId, showAllStatuses = true) {
  if (Number(state.reviewVideoId) !== Number(videoId)) clearCombineSelection();
  state.reviewVideoId = Number(videoId);
  if (showAllStatuses) $("#status-filter").value = "all";
  renderReviewFilter();
  await loadCandidates();
  $(".queue-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function vodPreviewJob(videoId) {
  return state.jobs["vod-preview-" + videoId] || null;
}

function renderMicTrackOptions(video) {
  const select = $("#mic-track-select");
  const options = [
    '<option value="-1">Auto-detect speakers</option>',
    ...video.audio_streams.map((track, index) => {
      const ordinal = Number(track.ordinal ?? index);
      const title = track.title ? " ? " + escapeHtml(track.title) : "";
      return '<option value="' + ordinal + '">Track ' + (ordinal + 1) + title + '</option>';
    }),
  ];
  select.innerHTML = options.join("");
  const configured = String(state.audioSettings.mic_audio_ordinal ?? -1);
  select.value = [...select.options].some((option) => option.value === configured) ? configured : "-1";
  $("#mic-track-control").hidden = video.audio_streams.length < 2;
}

async function loadAudioSettings() {
  state.audioSettings = await api("/api/settings/audio");
  if (selectedVideo()) renderMicTrackOptions(selectedVideo());
}

function renderStorageSettings() {
  const settings = state.storageSettings;
  if (!settings) return;
  $("#source-folder").value = settings.source_root || "";
  $("#output-folder").value = settings.output_dir || "";
  $("#source-folder-state").textContent = settings.source_exists ? "Folder found" : "Folder not found";
  $("#source-folder-state").className = "folder-state " + (settings.source_exists ? "valid" : "invalid");
  $("#output-folder-state").textContent = settings.output_exists ? "Folder ready" : "Folder will be created when saved";
  $("#output-folder-state").className = "folder-state " + (settings.output_exists ? "valid" : "");
  $("#storage-summary").textContent = (settings.source_root || "Choose VODs") + "  ->  " + (settings.output_dir || "Choose clips");
}

async function loadStorageSettings() {
  state.storageSettings = await api("/api/settings/folders");
  renderStorageSettings();
}

async function loadFolderBrowser(path = "") {
  const list = $("#folder-browser-list");
  $("#folder-browser-message").className = "folder-browser-message";
  $("#folder-browser-message").textContent = "Loading folders...";
  list.innerHTML = '<div class="empty">Loading...</div>';
  try {
    const result = await api("/api/settings/folders/list", { method: "POST", body: JSON.stringify({ path }) });
    folderBrowserCurrent = result.drives ? "" : result.current;
    folderBrowserParent = result.parent;
    folderBrowserFolders = result.folders || [];
    $("#folder-browser-path").value = result.current;
    $("#folder-browser-up").disabled = result.drives || folderBrowserParent === null;
    $("#folder-browser-choose").disabled = result.drives;
    list.innerHTML = folderBrowserFolders.map((folder, index) => '<button class="folder-browser-item" type="button" data-folder-index="' + index + '" role="option"><span class="folder-icon">' + (folder.drive ? '&#128187;' : '&#128193;') + '</span><span>' + escapeHtml(folder.name) + '</span></button>').join("") || '<div class="empty">This folder has no subfolders.</div>';
    list.querySelectorAll("[data-folder-index]").forEach((button) => button.addEventListener("click", () => loadFolderBrowser(folderBrowserFolders[Number(button.dataset.folderIndex)].path)));
    $("#folder-browser-message").textContent = result.drives ? "Choose a drive." : folderBrowserFolders.length + " folder" + (folderBrowserFolders.length === 1 ? "" : "s");
  } catch (error) {
    folderBrowserFolders = [];
    list.innerHTML = '<div class="empty">Unable to open this location.</div>';
    $("#folder-browser-message").className = "folder-browser-message error";
    $("#folder-browser-message").textContent = error.message;
  }
}

function closeFolderBrowser() {
  $("#folder-browser-modal").hidden = true;
  folderBrowserTarget = null;
}

function pickStorageFolder(kind) {
  folderBrowserTarget = kind;
  const input = $(kind === "source" ? "#source-folder" : "#output-folder");
  $("#folder-browser-title").textContent = kind === "source" ? "Choose the VOD folder" : "Choose the finished clips folder";
  $("#folder-browser-modal").hidden = false;
  loadFolderBrowser(input.value.trim()).catch(() => loadFolderBrowser(""));
}

async function saveMicTrack(value) {
  state.audioSettings = await api("/api/settings/audio", {
    method: "POST",
    body: JSON.stringify({ mic_audio_ordinal: Number(value) }),
  });
}

function chapterSelection(video) {
  const key = Number(video.id);
  if (!state.chapterSelections.has(key)) {
    state.chapterSelections.set(key, new Set((video.chapters || []).map((chapter) => Number(chapter.index))));
  }
  return state.chapterSelections.get(key);
}

function renderVodChapters(video) {
  const chapters = video.chapters || [];
  const panel = $("#vod-chapter-panel");
  panel.hidden = chapters.length === 0;
  if (!chapters.length) return;
  const selected = chapterSelection(video);
  $("#vod-chapter-count").textContent = selected.size + " of " + chapters.length + " will guide analysis";
  const duration = Math.max(1, Number(video.duration_sec || 1));
  $("#vod-chapter-timeline").innerHTML = chapters.map((chapter) => {
    const checked = selected.has(Number(chapter.index));
    const left = Math.max(0.5, Math.min(99.5, Number(chapter.start_sec || 0) / duration * 100));
    return '<button type="button" class="vod-chapter-flag' + (checked ? ' selected' : '') + '" data-chapter-index="' + chapter.index + '" style="left:' + left + '%" title="' + escapeHtml(chapter.title || 'OBS flag') + ' at ' + formatTime(chapter.start_sec) + '"><span>&#9873;</span></button>';
  }).join("");
  $("#vod-chapter-list").innerHTML = chapters.map((chapter) => {
    const checked = selected.has(Number(chapter.index)) ? " checked" : "";
    return '<label><input type="checkbox" data-chapter-index="' + chapter.index + '"' + checked + '><span><strong>' + escapeHtml(chapter.title || ('OBS flag ' + (Number(chapter.index) + 1))) + '</strong><small>' + formatTime(chapter.start_sec) + ' in the VOD</small></span></label>';
  }).join("");
  panel.querySelectorAll('[data-chapter-index]').forEach((control) => {
    control.addEventListener('click', (event) => {
      const index = Number(event.currentTarget.dataset.chapterIndex);
      if (selected.has(index)) selected.delete(index); else selected.add(index);
      renderVodChapters(video);
    });
  });
}
function renderSelectedVideo() {
  const modal = $("#vod-preview-modal");
  const video = selectedVideo();
  modal.hidden = !video;
  if (!video) return;
  const job = vodPreviewJob(video.id);
  $("#vod-preview-title").textContent = video.name;
  $("#vod-clip-folder-name").value = clipFolderDraft(video);
  renderClipFolderDestination(video);
  renderMicTrackOptions(video);
  renderVodChapters(video);
  $("#vod-preview-meta").textContent = formatHours(video.duration_sec) + " · " + (video.size_bytes / 1024 ** 3).toFixed(1) + " GB · " + video.audio_streams.length + " audio track" + (video.audio_streams.length === 1 ? "" : "s");
  $("#analyze-selected-vod").disabled = job?.status === "running";
  $("#analyze-selected-vod").textContent = video.status === "analyzed" ? "Analyze this VOD again" : "Analyze this VOD";
  $("#view-vod-clips").hidden = Number(video.candidate_count || 0) === 0;
  $("#retry-vod-preview").hidden = job?.status !== "error";
  const media = $("#vod-preview-media");
  if (video.preview_url) {
    const currentPlayer = media.querySelector("video");
    if (!currentPlayer || Number(currentPlayer.dataset.videoId) !== Number(video.id)) media.innerHTML = '<video data-video-id="' + video.id + '" controls autoplay preload="metadata" src="' + video.preview_url + '"></video>';
  } else if (job?.status === "error") {
    media.innerHTML = '<div class="vod-preview-placeholder failed"><strong>Preview failed</strong><span>' + escapeHtml(job.detail || "The preview could not be created.") + '</span></div>';
  } else {
    const percent = Math.max(2, Math.min(99, Number(job?.progress || 2)));
    media.innerHTML = '<div class="vod-preview-placeholder"><strong>Preparing a 45-second sample…</strong><span>' + escapeHtml(job?.detail || "Taking a lightweight sample from the middle of this recording.") + '</span><div class="mini-progress"><i style="width:' + percent + '%"></i></div></div>';
  }
}

async function requestVodPreview(videoId) {
  try {
    await api("/api/videos/" + videoId + "/preview", { method: "POST" });
    hadActiveJob = true;
    await pollJobs();
  } catch (error) {
    alert(error.message);
  }
}

async function selectVideo(videoId) {
  state.selectedVideoId = Number(videoId);
  renderSelectedVideo();
  if (!selectedVideo()?.preview_url) await requestVodPreview(videoId);
}

function closeVodPreview() {
  const player = $("#vod-preview-media video");
  if (player) player.pause();
  state.selectedVideoId = null;
  renderSelectedVideo();
}

function openHateFeedback(candidate) {
  pendingHateCandidate = candidate;
  $("#hate-feedback-form").reset();
  $("#hate-feedback-message").textContent = "";
  $("#hate-feedback-clip").textContent = candidate.video_name + " ? " + Math.round(Number(candidate.end_sec) - Number(candidate.start_sec)) + "s reel";
  $("#hate-feedback-modal").hidden = false;
  window.setTimeout(() => $("#hate-feedback-text").focus(), 0);
}

function closeHateFeedback() {
  pendingHateCandidate = null;
  $("#hate-feedback-modal").hidden = true;
}

async function analyzeVideo(videoId, button, chapterIndices = null) {
  if (button) button.disabled = true;
  try {
    const result = await api("/api/analyze", { method: "POST", body: JSON.stringify({
      video_id: Number(videoId),
      auto_build: autoBuildMode,
      chapter_indices: chapterIndices,
      clip_folder_name: $("#vod-clip-folder-name").value,
    }) });
    const video = state.videos.find((item) => Number(item.id) === Number(videoId));
    if (video && result.clip_folder_name) {
      video.clip_folder_name = result.clip_folder_name;
      state.clipFolderDrafts.set(Number(videoId), result.clip_folder_name);
    }
    hadActiveJob = true;
    await pollJobs();
  } catch (error) {
    alert(error.message);
    if (button) button.disabled = false;
  }
}

async function buildSelectedVod(limit, button) {
  if (!state.reviewVideoId) return;
  if (button) button.disabled = true;
  try {
    await api("/api/prepare-batch", {
      method: "POST",
      body: JSON.stringify({ video_id: Number(state.reviewVideoId), limit }),
    });
    hadActiveJob = true;
    await pollJobs();
  } catch (error) {
    alert(error.message);
    if (button) button.disabled = false;
  }
}

async function loadCandidates() {
  const status = $("#status-filter").value;
  const params = new URLSearchParams({ status });
  if (state.reviewVideoId) params.set("video_id", String(state.reviewVideoId));
  state.candidates = await api("/api/candidates?" + params.toString());
  renderCandidates();
  renderReviewFilter();
}

function escapeHtml(value = "") {
  const node = document.createElement("div");
  node.textContent = value;
  return node.innerHTML;
}

async function loadTasteExamples() {
  const examples = await api("/api/taste/examples");
  $("#taste-count").textContent = `${examples.filter((item) => item.status === "ready").length} ready`;
  $("#taste-list").innerHTML = examples.map((item) => `
    <article class="taste-row" data-state="${item.status}">
      <div><strong>${escapeHtml(item.creator || item.source_title || "Comedy reference")}</strong><span>${escapeHtml(item.status === "ready" ? `${item.transcript_chars} transcript characters learned` : item.error || item.status)}</span></div>
      <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">Source</a>
    </article>
  `).join("") || `<div class="empty">No references yet. Add a few clips that make you lose it.</div>`;
}

function actionButton(action, label, className = "") {
  return `<button data-action="${action}" class="${className}">${label}</button>`;
}

function updateCombineToolbar() {
  const count = state.combineSelection.size;
  $("#combine-count").textContent = `${count} selected`;
  $("#combine-selected").disabled = count < 2;
  $("#combine-selected").textContent = count >= 2 ? `Combine ${count} clips` : "Combine selected";
  $("#clear-combine").disabled = count === 0;
  $("#combine-help").textContent = count
    ? "The new reel keeps the selected context and payoff in chronological order."
    : "Check 2 or 3 finished clips from the same VOD.";
}

function clearCombineSelection() {
  state.combineSelection.clear();
  state.combineVideoId = null;
  document.querySelectorAll('[data-role="combine"]').forEach((input) => { input.checked = false; });
  document.querySelectorAll(".candidate.combine-selected").forEach((item) => item.classList.remove("combine-selected"));
  updateCombineToolbar();
}

function toggleCombineCandidate(candidate, checked, input, article) {
  if (checked) {
    if (state.combineVideoId !== null && Number(state.combineVideoId) !== Number(candidate.video_id)) {
      input.checked = false;
      alert("Choose clips from the same VOD so their context stays in the right order.");
      return;
    }
    if (state.combineSelection.size >= 3) {
      input.checked = false;
      alert("Choose up to 3 clips for one clean reel.");
      return;
    }
    state.combineVideoId = Number(candidate.video_id);
    state.combineSelection.add(Number(candidate.id));
    article.classList.add("combine-selected");
  } else {
    state.combineSelection.delete(Number(candidate.id));
    article.classList.remove("combine-selected");
    if (!state.combineSelection.size) state.combineVideoId = null;
  }
  updateCombineToolbar();
}

async function combineSelectedClips(button) {
  const candidateIds = [...state.combineSelection];
  if (candidateIds.length < 2) return;
  button.disabled = true;
  try {
    await api("/api/combine", { method: "POST", body: JSON.stringify({ candidate_ids: candidateIds }) });
    clearCombineSelection();
    $("#status-filter").value = "all";
    hadActiveJob = true;
    await pollJobs();
  } catch (error) {
    alert(error.message);
    updateCombineToolbar();
  }
}

function renderCandidates() {
  const container = $("#candidate-list");
  container.innerHTML = "";
  if (!state.candidates.length) {
    const video = reviewVideo();
    const message = video ? "No clips from " + escapeHtml(video.name) + " in this lane yet." : "Nothing in this lane yet.";
    container.innerHTML = '<div class="empty">' + message + '</div>';
    updateCombineToolbar();
    return;
  }
  const visibleCandidates = [...state.candidates].sort((left, right) => {
    const combinedOrder = Number(isCombinedCandidate(right)) - Number(isCombinedCandidate(left));
    return combinedOrder || (Number(right.score) - Number(left.score)) || (Number(right.id) - Number(left.id));
  });
  for (const candidate of visibleCandidates) {
    const node = $("#candidate-template").content.cloneNode(true);
    const article = node.querySelector(".candidate");
    const variant = candidate.active_variant;
    const combineControl = article.querySelector(".combine-check");
    const combineInput = article.querySelector('[data-role="combine"]');
    combineControl.hidden = !variant?.url;
    combineInput.checked = state.combineSelection.has(Number(candidate.id));
    article.classList.toggle("combine-selected", combineInput.checked);
    combineInput.addEventListener("change", () => toggleCombineCandidate(candidate, combineInput.checked, combineInput, article));
    article.dataset.id = candidate.id;
    const isCombined = isCombinedCandidate(candidate);
    article.classList.toggle("combined-reel", isCombined);
    article.querySelector(".score").textContent = `${Math.round(candidate.score * 100)}% moment score`;
    const duration = Math.max(0, Number(candidate.end_sec) - Number(candidate.start_sec));
    article.querySelector(".timecode").textContent = `${formatTime(candidate.start_sec)}?${formatTime(candidate.end_sec)} ? ${Math.round(duration)}s`;
    article.querySelector("h3").textContent = isCombined ? `COMBINED REEL · ${candidate.video_name}` : candidate.video_name;
    article.querySelector(".reason").textContent = humanReason(candidate.reason);
    article.querySelector(".transcript").textContent = candidate.transcript || "The first cut will listen for dialogue and time the captions automatically.";
    const actions = article.querySelector(".candidate-actions");
    const destination = article.querySelector(".candidate-destination");
    const publishCount = Number(candidate.publish_count || 0);
    if (publishCount) {
      const platforms = String(candidate.publish_platforms || "").split(",").filter(Boolean).join(", ");
      destination.textContent = "Sent to Scheduler · " + publishCount + (platforms ? " · " + platforms : "");
      destination.dataset.state = "scheduled";
      destination.hidden = false;
    } else if (candidate.status === "approved") {
      destination.textContent = "Saved to Liked ✓";
      destination.dataset.state = "liked";
      destination.hidden = false;
    } else if (variant?.decision === "better_moment") {
      destination.textContent = "Saved as good edit, weak moment";
      destination.dataset.state = "feedback";
      destination.hidden = false;
    } else if (candidate.status === "rejected") {
      destination.textContent = "Saved to Passed";
      destination.dataset.state = "passed";
      destination.hidden = false;
    }

    if (variant?.url) {
      article.querySelector(".candidate-media").innerHTML = `<video controls preload="metadata" src="${variant.url}"></video>`;
      article.querySelector(".attempt-badge").textContent = isCombined ? `Combined reel · Attempt ${variant.attempt}` : `Attempt ${variant.attempt}`;
      article.querySelector(".recipe-detail").textContent = `${variant.recipe_label} · ${variant.caption_preset} captions · ${variant.audio_preset} audio · source track ${variant.audio_ordinal + 1}`;
      if (["liked", "hated", "better_moment"].includes(variant.decision)) article.classList.add(variant.decision);
      actions.innerHTML = [
        actionButton("like", variant.decision === "liked" ? "Liked ✓" : "Like", "like"),
        actionButton("better-moment", variant.decision === "better_moment" ? "Feedback saved ✓" : "Good edit, weak moment", "secondary weak-moment"),
        actionButton("hate", variant.decision === "hated" ? "Hated" : "Hate", "danger"),
        actionButton("retry", "Try again", "secondary retry"),
        actionButton("edit", "Edit clip", "secondary edit-clip"),
        actionButton("open", "Open file", "secondary"),
      ].join("");
    } else if (candidate.preview_url) {
      article.querySelector(".candidate-media").innerHTML = `<video controls preload="metadata" src="${candidate.preview_url}"></video>`;
      actions.innerHTML = actionButton("prepare", "Make the final cut");
    } else {
      actions.innerHTML = actionButton("prepare", "Make first cut");
    }
    article.querySelectorAll("[data-action]").forEach((button) => {
      button.addEventListener("click", () => candidateAction(candidate, button.dataset.action, button));
    });
    container.appendChild(node);
  }
  updateCombineToolbar();
  updateCandidateJobs();
}

function candidateJob(candidateId) {
  return jobsByRecency().find(
    (job) => Number(job.candidate_id) === Number(candidateId)
      && (job.status === "running" || job.status === "error"),
  );
}

function updateCandidateJobs() {
  document.querySelectorAll(".candidate[data-id]").forEach((article) => {
    const job = candidateJob(article.dataset.id);
    const panel = article.querySelector(".candidate-job");
    if (!panel) return;
    article.classList.toggle("working", job?.status === "running");
    article.classList.toggle("failed", job?.status === "error");
    if (!job) {
      panel.hidden = true;
      return;
    }
    const percent = Math.max(0, Math.min(100, Number(job.progress || 0)));
    panel.hidden = false;
    panel.dataset.state = job.status;
    panel.querySelector("strong").textContent = job.status === "error" ? "This reel failed" : job.stage || "Making the reel";
    panel.querySelector("span").textContent = job.status === "error" ? job.detail : `${percent}% · ${job.detail}`;
    panel.querySelector("i").style.width = `${percent}%`;
    article.querySelectorAll("button").forEach((button) => { button.disabled = job.status === "running"; });
  });
}

async function candidateAction(candidate, action, button) {
  button.disabled = true;
  try {
    if (action === "hate") {
      openHateFeedback(candidate);
    } else if (["like", "better-moment"].includes(action)) {
      const decision = action === "better-moment" ? "better_moment" : action;
      if (action === "like") $("#status-filter").value = "all";
      await api(`/api/candidates/${candidate.id}/decision`, { method: "POST", body: JSON.stringify({ decision }) });
      await Promise.all([loadCandidates(), loadStats()]);
    } else if (action === "prepare" || action === "retry") {
      await api(`/api/candidates/${candidate.id}/${action}`, { method: "POST" });
      hadActiveJob = true;
      button.textContent = action === "retry" ? "Making another version..." : "Starting the reel...";
      await pollJobs();
    } else if (action === "edit") {
      await window.clipEditor?.open(candidate.id);
    } else if (action === "open") {
      window.open(candidate.active_variant.url, "_blank");
    }
  } catch (error) {
    alert(error.message);
  } finally {
    if (action !== "prepare" && action !== "retry") button.disabled = false;
  }
}

async function pollJobs() {
  try {
    state.jobs = await api("/api/jobs");
    const active = jobsByRecency().find((job) => job.status === "running");
    renderActivity();
    updateCandidateJobs();
    renderSelectedVideo();
    if (active) hadActiveJob = true;
    else if (hadActiveJob) {
      hadActiveJob = false;
      await Promise.all([loadStats(), loadVideos(), loadCandidates()]);
      renderSelectedVideo();
      renderActivity();
    }
  } catch (error) {
    $("#job-status").textContent = error.message;
  }
}

$("#lmstudio-load").addEventListener("click", () => setLmStudioMode("ai"));
$("#lmstudio-gaming").addEventListener("click", () => setLmStudioMode("gaming"));
$("#batch-button").addEventListener("click", async () => {
  await api("/api/analyze-batch", { method: "POST", body: JSON.stringify({ limit: 10 }) });
  hadActiveJob = true;
  await pollJobs();
});
$("#prepare-button").addEventListener("click", async () => {
  await api("/api/prepare-batch", { method: "POST", body: JSON.stringify({ limit: 3 }) });
  hadActiveJob = true;
  await pollJobs();
});
$("#scan-button").addEventListener("click", async () => {
  await api("/api/scan", { method: "POST" });
  hadActiveJob = true;
  await pollJobs();
});
$("#video-search").addEventListener("input", renderVideos);
$("#mic-track-select").addEventListener("change", (event) => saveMicTrack(event.currentTarget.value));
$("#auto-build-vod").addEventListener("click", cycleAutoBuildMode);
$("#build-vod-3").addEventListener("click", (event) => buildSelectedVod(3, event.currentTarget));
$("#build-vod-5").addEventListener("click", (event) => buildSelectedVod(5, event.currentTarget));
$("#build-vod-all").addEventListener("click", (event) => buildSelectedVod("all", event.currentTarget));
$("#combine-selected").addEventListener("click", (event) => combineSelectedClips(event.currentTarget));
$("#clear-combine").addEventListener("click", clearCombineSelection);
$("#close-vod-preview").addEventListener("click", closeVodPreview);
$("#vod-preview-modal").addEventListener("click", (event) => { if (event.target.id === "vod-preview-modal") closeVodPreview(); });
$("#close-hate-feedback").addEventListener("click", closeHateFeedback);
$("#cancel-hate-feedback").addEventListener("click", closeHateFeedback);
$("#hate-feedback-modal").addEventListener("click", (event) => {
  if (event.target.id === "hate-feedback-modal") closeHateFeedback();
});
$("#hate-feedback-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!pendingHateCandidate) return;
  const reasonTags = [...document.querySelectorAll('input[name="hate-reason"]:checked')].map((input) => input.value);
  const reason = $("#hate-feedback-text").value.trim();
  if (!reasonTags.length && !reason) {
    $("#hate-feedback-message").textContent = "Pick a reason or tell the AI what went wrong.";
    return;
  }
  const button = $("#submit-hate-feedback");
  button.disabled = true;
  $("#hate-feedback-message").textContent = "Saving what the AI should learn?";
  try {
    await api("/api/candidates/" + pendingHateCandidate.id + "/decision", {
      method: "POST",
      body: JSON.stringify({ decision: "hate", reason, reason_tags: reasonTags }),
    });
    closeHateFeedback();
    await Promise.all([loadCandidates(), loadStats()]);
  } catch (error) {
    $("#hate-feedback-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$("#vod-clip-folder-name").addEventListener("input", (event) => {
  const video = selectedVideo();
  if (!video) return;
  state.clipFolderDrafts.set(Number(video.id), event.target.value);
  renderClipFolderDestination(video);
});
$("#analyze-selected-vod").addEventListener("click", async (event) => {
  const video = selectedVideo();
  if (!video) return;
  await setReviewVideo(video.id, true);
  await analyzeVideo(video.id, event.currentTarget, [...chapterSelection(video)]);
  closeVodPreview();
});
$("#view-vod-clips").addEventListener("click", async () => {
  const video = selectedVideo();
  if (!video) return;
  await setReviewVideo(video.id, true);
  closeVodPreview();
});
$("#clear-vod-filter").addEventListener("click", async () => {
  state.reviewVideoId = null;
  renderReviewFilter();
  await loadCandidates();
});
$("#select-all-vod-chapters").addEventListener("click", () => {
  const video = selectedVideo();
  if (!video) return;
  state.chapterSelections.set(Number(video.id), new Set((video.chapters || []).map((chapter) => Number(chapter.index))));
  renderVodChapters(video);
});
$("#clear-vod-chapters").addEventListener("click", () => {
  const video = selectedVideo();
  if (!video) return;
  state.chapterSelections.set(Number(video.id), new Set());
  renderVodChapters(video);
});$("#retry-vod-preview").addEventListener("click", () => requestVodPreview(state.selectedVideoId));
document.addEventListener("keydown", (event) => { if (event.key !== "Escape") return; if (!$("#hate-feedback-modal").hidden) closeHateFeedback(); else if (state.selectedVideoId) closeVodPreview(); });
$("#status-filter").addEventListener("change", loadCandidates);

$("#taste-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#taste-import");
  const urls = $("#taste-urls").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  button.disabled = true;
  $("#taste-message").textContent = "Starting the local reference import…";
  try {
    await api("/api/taste/import", {
      method: "POST",
      body: JSON.stringify({ urls, creator: $("#taste-creator").value, notes: $("#taste-notes").value }),
    });
    $("#taste-message").textContent = "Importing and transcribing. Progress is shown in Reel Workshop.";
    hadActiveJob = true;
    await pollJobs();
  } catch (error) {
    $("#taste-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("#browse-source-folder").addEventListener("click", () => pickStorageFolder("source"));
$("#browse-output-folder").addEventListener("click", () => pickStorageFolder("output"));
$("#folder-browser-close").addEventListener("click", closeFolderBrowser);
$("#folder-browser-cancel").addEventListener("click", closeFolderBrowser);
$("#folder-browser-modal").addEventListener("click", (event) => { if (event.target.id === "folder-browser-modal") closeFolderBrowser(); });
$("#folder-browser-this-pc").addEventListener("click", () => loadFolderBrowser(""));
$("#folder-browser-up").addEventListener("click", () => loadFolderBrowser(folderBrowserParent || ""));
$("#folder-browser-choose").addEventListener("click", () => {
  if (!folderBrowserTarget || !folderBrowserCurrent) return;
  const input = $(folderBrowserTarget === "source" ? "#source-folder" : "#output-folder");
  input.value = folderBrowserCurrent;
  $("#storage-message").className = "storage-message";
  $("#storage-message").textContent = "Folder selected. Press Save folders to apply it.";
  closeFolderBrowser();
});
$("#storage-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#save-storage-folders");
  const previousSource = state.storageSettings?.source_root || "";
  button.disabled = true;
  $("#storage-message").className = "storage-message";
  $("#storage-message").textContent = "Checking and saving folders...";
  try {
    state.storageSettings = await api("/api/settings/folders", {
      method: "POST",
      body: JSON.stringify({ source_root: $("#source-folder").value, output_dir: $("#output-folder").value }),
    });
    renderStorageSettings();
    const sourceChanged = previousSource.toLowerCase() !== state.storageSettings.source_root.toLowerCase();
    $("#storage-message").textContent = sourceChanged
      ? "Saved. Press Rescan library when you are ready to import VODs from this folder."
      : "Saved. New finished clips will use this clips folder.";
    await Promise.all([loadStats(), loadVideos(), loadCandidates()]);
  } catch (error) {
    $("#storage-message").className = "storage-message error";
    $("#storage-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

renderAutoBuildMode();
Promise.all([loadStats(), loadVideos(), loadCandidates(), loadTasteExamples(), loadLmStudioState(), loadAudioSettings(), loadStorageSettings(), pollJobs()]);
setInterval(pollJobs, 1200);
setInterval(loadLmStudioState, 15000);
