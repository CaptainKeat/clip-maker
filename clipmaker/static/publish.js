const publishState = {
  exports: [],
  integrations: [],
  configured: false,
  scheduled: [],
};

const publishApi = async (path, options = {}) => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
};

const publishEscape = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");

function updatePublishSelection() {
  const selected = document.querySelectorAll(".export-check:checked").length;
  const destinations = document.querySelectorAll(".destination-check:checked").length;
  document.querySelector("#selected-export-count").textContent = `${selected} selected`;
  document.querySelector("#schedule-selected").disabled = !selected || !destinations;
  document.querySelector("#generate-ai-captions").disabled = !selected;
  const all = document.querySelectorAll(".export-check");
  const selectAll = document.querySelector("#select-all-exports");
  selectAll.checked = Boolean(all.length) && selected === all.length;
  selectAll.indeterminate = selected > 0 && selected < all.length;
}

function renderPublishTab() {
  const status = document.querySelector("#postiz-status");
  status.textContent = publishState.configured ? "Postiz connected" : "Postiz unavailable";
  status.dataset.connected = String(publishState.configured);

  const destinations = document.querySelector("#destination-list");
  destinations.innerHTML = publishState.integrations.length
    ? publishState.integrations.map((item) => `
        <label class="destination-option">
          <input class="destination-check" type="checkbox" value="${publishEscape(item.id)}">
          <span><strong>${publishEscape(item.name)}</strong><small>${publishEscape(item.platform)}</small></span>
        </label>
      `).join("")
    : '<p class="empty-inline">No connected Postiz destinations were found.</p>';

  const list = document.querySelector("#export-list");
  list.innerHTML = publishState.exports.length
    ? publishState.exports.map((item) => `
        <label class="export-row">
          <input class="export-check" type="checkbox" value="${item.candidate_id}">
          <video controls preload="metadata" src="${publishEscape(item.url)}"></video>
          <span>
            <strong>${publishEscape(item.video_name)}</strong>
            <small>Attempt ${item.attempt} · ${publishEscape(item.recipe_label)}</small>
            <small>${item.publish_count ? `Scheduled ${item.publish_count} time(s)` : "Not scheduled yet"}</small>
          </span>
          <textarea class="per-reel-caption" data-candidate-id="${item.candidate_id}" rows="4" placeholder="Generate or write a caption">${publishEscape(item.caption_draft || "")}</textarea>
        </label>
      `).join("")
    : '<div class="empty">Like a reel and it will appear here.</div>';

  document.querySelectorAll(".export-check, .destination-check")
    .forEach((input) => input.addEventListener("change", updatePublishSelection));
  updatePublishSelection();
}

function renderScheduledTab() {
  const list = document.querySelector("#scheduled-list");
  document.querySelector("#scheduled-count").textContent = publishState.scheduled.length + " sent";
  list.innerHTML = publishState.scheduled.length ? publishState.scheduled.map((item) => {
    const scheduled = new Date(item.scheduled_for);
    const when = Number.isNaN(scheduled.getTime()) ? item.scheduled_for : scheduled.toLocaleString();
    return `<article class="scheduled-row"><video controls preload="metadata" src="${publishEscape(item.url)}"></video><div><strong>${publishEscape(item.video_name)}</strong><small>Attempt ${item.attempt} · ${publishEscape(item.recipe_label)}</small><small>Scheduled for ${publishEscape(when)}</small><small>Recorded by Clip Maker ${publishEscape(item.created_at)}</small></div><span class="scheduled-platform">${publishEscape(item.platform)}</span></article>`;
  }).join("") : '<div class="empty">Nothing has been sent to Postiz yet.</div>';
}

async function loadScheduledTab() {
  try {
    publishState.scheduled = await publishApi("/api/scheduled");
  } catch (error) {
    publishState.scheduled = [];
    document.querySelector("#scheduled-list").innerHTML = '<div class="empty">Could not load scheduler history: ' + publishEscape(error.message) + '</div>';
    return;
  }
  renderScheduledTab();
}

async function loadPublishTab() {  const message = document.querySelector("#publish-message");
  try {
    publishState.exports = await publishApi("/api/exports");
  } catch (error) {
    publishState.exports = [];
    message.textContent = error.message;
  }
  try {
    const postiz = await publishApi("/api/postiz/integrations");
    publishState.integrations = postiz.integrations || [];
    publishState.configured = Boolean(postiz.configured);
  } catch (error) {
    publishState.configured = false;
    publishState.integrations = [];
    message.textContent = "Reels are available, but Postiz is unavailable: " + error.message;
  }
  renderPublishTab();
}

document.querySelectorAll(".view-tab").forEach((button) => {
  button.addEventListener("click", async () => {
    const view = button.dataset.view;
    document.querySelectorAll(".view-tab").forEach((item) => item.classList.toggle("active", item === button));
    document.querySelectorAll(".app-view").forEach((panel) => { panel.hidden = panel.id !== `${view}-view`; });
    if (view === "publish") await loadPublishTab();
    if (view === "scheduled") await loadScheduledTab();
    if (view === "taste") await loadTasteExamples();
  });
});

document.querySelector("#select-all-exports").addEventListener("change", (event) => {
  document.querySelectorAll(".export-check").forEach((input) => {
    input.checked = event.target.checked;
  });
  updatePublishSelection();
});

document.querySelector("#publish-type").addEventListener("change", (event) => {
  const scheduled = event.target.value === "schedule";
  document.querySelector("#start-at-label").hidden = !scheduled;
  document.querySelector("#interval-label").hidden = !scheduled;
});

async function waitForCaptionJob(jobId) {
  const message = document.querySelector("#publish-message");
  const timer = setInterval(async () => {
    try {
      const jobs = await publishApi("/api/jobs");
      const job = jobs[jobId];
      if (!job || job.status === "running") return;
      clearInterval(timer);
      if (job.status === "error") {
        message.textContent = "Caption generation failed: " + job.detail;
      } else {
        message.textContent = "AI captions are ready. Edit anything you want before scheduling.";
        await loadPublishTab();
      }
    } catch (error) {
      clearInterval(timer);
      message.textContent = error.message;
    }
  }, 1200);
}

document.querySelector("#generate-ai-captions").addEventListener("click", async () => {
  const selected = [...document.querySelectorAll(".export-check:checked")].map((item) => Number(item.value));
  const button = document.querySelector("#generate-ai-captions");
  const message = document.querySelector("#publish-message");
  button.disabled = true;
  message.textContent = "Starting unique captions for the selected reels...";
  try {
    const result = await publishApi("/api/captions/generate", {
      method: "POST",
      body: JSON.stringify({ candidate_ids: selected, tone: document.querySelector("#caption-tone").value }),
    });
    message.textContent = "Writing captions locally. Progress is shown in Reel Workshop.";
    waitForCaptionJob(result.job_id);
  } catch (error) {
    message.textContent = "Could not generate captions: " + error.message;
    updatePublishSelection();
  }
});

document.querySelector("#publish-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#schedule-selected");
  const message = document.querySelector("#publish-message");
  const scheduleType = document.querySelector("#publish-type").value;
  const startValue = document.querySelector("#publish-start").value;
  const body = {
    candidate_ids: [...document.querySelectorAll(".export-check:checked")].map((item) => Number(item.value)),
    integration_ids: [...document.querySelectorAll(".destination-check:checked")].map((item) => item.value),
    caption_template: document.querySelector("#publish-caption").value,
    captions: Object.fromEntries([...document.querySelectorAll(".export-check:checked")].map((item) => [item.value, document.querySelector(".per-reel-caption[data-candidate-id=" + item.value + "]").value])),
    schedule_type: scheduleType,
    start_at: scheduleType === "schedule" && startValue ? new Date(startValue).toISOString() : null,
    interval_hours: Number(document.querySelector("#publish-interval").value || 24),
  };
  button.disabled = true;
  message.textContent = "Sending the selected reels to Postiz…";
  try {
    await publishApi("/api/postiz/schedule", {
      method: "POST",
      body: JSON.stringify(body),
    });
    message.textContent = "Publishing job started. Progress is shown in Reel Workshop.";
  } catch (error) {
    message.textContent = `Could not schedule: ${error.message}`;
    updatePublishSelection();
  }
});

const defaultStart = new Date(Date.now() + 60 * 60 * 1000);
defaultStart.setMinutes(0, 0, 0);
document.querySelector("#publish-start").value = new Date(
  defaultStart.getTime() - defaultStart.getTimezoneOffset() * 60000,
).toISOString().slice(0, 16);
