"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const MAX_BYTES = 10 * 1024 * 1024;
  const STORAGE_KEY = "cellula_task5_dataset_id";
  const AUDIO_EXTENSIONS = /\.(wav|mp3|m4a|ogg|flac|webm|aac|opus)$/i;
  const state = {
    dataset: null, busy: false, audio: null, audioUrl: null,
    recording: false, micPending: false, stream: null, recorder: null,
    recordTimer: null, stopTimer: null, startedAt: 0,
  };

  function element(tag, className = "", text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function notice(message, kind = "info") {
    $("notice").textContent = message;
    $("notice").className = `notice ${kind}`;
    $("notice").hidden = !message;
  }

  function rememberDataset(id) {
    try {
      if (id) sessionStorage.setItem(STORAGE_KEY, id);
      else sessionStorage.removeItem(STORAGE_KEY);
    } catch (_) { /* Storage may be disabled; the current page still works. */ }
  }

  function controls() {
    const locked = state.busy || state.recording || state.micPending;
    document.querySelectorAll("[data-lock]").forEach((node) => { node.disabled = locked; });
    $("question").disabled = locked || !state.dataset;
    $("send-button").disabled = locked || !state.dataset;
    $("analyze-audio").disabled = locked || !state.dataset || !state.audio;
    $("record-button").disabled = state.busy || state.micPending || !state.dataset ||
      !(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
    $("messages").setAttribute("aria-busy", String(state.busy));
    $("composer-dataset").textContent = state.dataset
      ? `${state.dataset.row_count.toLocaleString()} rows ready` : "No dataset selected";
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      $("record-label").textContent = "Microphone unavailable";
    }
  }

  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(path, { ...options, cache: "no-store" });
    } catch (error) {
      if (error.name === "AbortError" || error.name === "TimeoutError") {
        throw new Error("The API connection check timed out. Confirm that Uvicorn is running.");
      }
      throw new Error("Could not reach the API. Confirm that Uvicorn is running, then try again.");
    }
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error(`The server returned an unexpected response (HTTP ${response.status}).`); }
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => `${(item.loc || []).join(".")}: ${item.msg}`).join("; ")
        : (typeof data.detail === "string" ? data.detail : `Request failed (HTTP ${response.status}).`);
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  async function checkHealth() {
    $("api-status-text").textContent = "Connecting…";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const data = await request("/health", { signal: controller.signal });
      if (data.status !== "ok") throw new Error("Unexpected health response");
      $("api-status").classList.remove("offline");
      $("api-status-text").textContent = "API connected";
    } catch (_) {
      $("api-status").classList.add("offline");
      $("api-status-text").textContent = "API unavailable · retry";
    } finally { clearTimeout(timer); }
  }

  function table(columns, rows) {
    const wrapper = element("div", "table-scroll");
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", "Scrollable data table");
    const node = element("table", "data-table");
    const header = element("tr");
    columns.forEach((column) => {
      const cell = element("th", "", column);
      cell.scope = "col";
      header.append(cell);
    });
    const head = element("thead"); head.append(header);
    const body = element("tbody");
    rows.forEach((row) => {
      const line = element("tr");
      columns.forEach((column) => {
        const value = row[column];
        const cell = element("td", value == null ? "null-cell" : typeof value === "number" ? "numeric" : "", value == null ? "—" : value);
        cell.dir = "auto";
        line.append(cell);
      });
      body.append(line);
    });
    node.append(head, body); wrapper.append(node);
    return wrapper;
  }

  function clearHistory() {
    $("messages").replaceChildren();
    $("thread-toolbar").hidden = true;
    $("empty-state").hidden = false;
  }

  function selectDataset(info, name = "Saved dataset") {
    state.dataset = info;
    rememberDataset(info.dataset_id);
    $("dataset-card").hidden = false;
    $("dataset-name").textContent = name;
    $("dataset-rows").textContent = info.row_count.toLocaleString();
    $("dataset-columns").textContent = info.columns.length.toLocaleString();
    $("active-id").value = info.dataset_id;
    $("dataset-id").value = info.dataset_id;
    const schema = element("div", "schema-list");
    info.columns.forEach((column) => { schema.append(element("span", "", `${column.name} · ${column.type}`)); });
    $("preview-content").replaceChildren(schema, table(info.columns.map((column) => column.name), info.preview));
    $("dataset-preview").hidden = false;
    $("dataset-preview").open = true;
    $("empty-hint").textContent = "Your dataset is ready. Ask a complete question about its columns, or record your question below.";
    clearHistory();
    controls();
  }

  async function uploadDataset(event) {
    event.preventDefault();
    if (state.busy || state.recording || state.micPending) return;
    const file = $("csv-file").files[0];
    if (!file) return notice("Choose a CSV file first.", "error");
    if (!/\.csv$/i.test(file.name)) return notice("Choose a file with the .csv extension.", "error");
    if (!file.size || file.size > MAX_BYTES) return notice("Use a nonempty CSV file up to 10 MiB.", "error");
    state.busy = true; controls();
    $("upload-button").textContent = "Uploading…";
    notice("Importing your dataset…");
    try {
      const form = new FormData(); form.append("file", file, file.name);
      const data = await request("/datasets", { method: "POST", body: form });
      selectDataset(data, file.name);
      notice(`Dataset ready: ${data.row_count.toLocaleString()} rows and ${data.columns.length} columns.`, "success");
    } catch (error) { notice(error.message, "error"); }
    finally {
      state.busy = false; controls();
      $("upload-button").textContent = "Upload dataset ↗";
    }
  }

  async function loadDataset(id, restoring = false) {
    id = id.trim();
    if (!/^[0-9a-f]{32}$/.test(id)) return notice("Enter a dataset ID containing 32 lowercase hexadecimal characters.", "error");
    state.busy = true; controls(); notice("Loading saved dataset…");
    try {
      const info = await request(`/datasets/${encodeURIComponent(id)}`);
      selectDataset(info);
      notice(restoring ? "Your saved dataset is ready. Previous questions are not restored." : "Saved dataset loaded.", "success");
    } catch (error) {
      if (restoring) rememberDataset(null);
      notice(error.message, "error");
    } finally { state.busy = false; controls(); }
  }

  function setMode(mode) {
    if (state.busy || state.recording || state.micPending) return;
    for (const name of ["text", "voice"]) {
      const active = mode === name;
      $(`${name}-panel`).hidden = !active;
      $(`${name}-tab`).classList.toggle("active", active);
      $(`${name}-tab`).setAttribute("aria-selected", String(active));
      $(`${name}-tab`).tabIndex = active ? 0 : -1;
    }
  }

  function questionMessage(text) {
    $("empty-state").hidden = true;
    $("thread-toolbar").hidden = false;
    const bubble = element("div", "user-message", text); bubble.dir = "auto";
    $("messages").append(bubble);
    return bubble;
  }

  function pendingCard(text) {
    const card = element("article", "answer-card");
    const row = element("div", "loading-row"); row.setAttribute("role", "status");
    const spinner = element("span", "spinner"); spinner.setAttribute("aria-hidden", "true");
    row.append(spinner, element("span", "", text)); card.append(row);
    $("messages").append(card);
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return card;
  }

  async function copyText(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      const old = button.textContent; button.textContent = "Copied";
      setTimeout(() => { button.textContent = old; }, 1700);
    } catch (_) { notice("Automatic copying is unavailable. Select and copy the text manually."); }
  }

  function renderAnswer(card, data) {
    card.replaceChildren();
    const heading = element("div", "answer-heading");
    heading.append(element("span", "answer-mark", "✓"), element("span", "", data.status === "cannot_answer" ? "Question needs different data" : "Analysis complete"));
    card.append(heading);
    if (data.transcription) {
      const transcript = element("div", "transcript-note");
      transcript.append(element("span", "transcript-label", "TRANSCRIPT"));
      const words = element("span", "", data.transcription.text); words.dir = "auto";
      transcript.append(words); card.append(transcript);
    }
    if (data.status === "cannot_answer") {
      const message = element("p", "result-warning", data.message || "Ask a clear question supported by this dataset.");
      message.dir = "auto"; card.append(message);
      card.append(element("p", "result-meta", "No SQL was executed for this request."));
      return;
    }
    if (typeof data.explanation === "string" && data.explanation.trim()) {
      const explanation = element("p", "explanation", data.explanation); explanation.dir = "auto";
      card.append(explanation);
    } else if (data.explanation_note) {
      card.append(element("p", "result-warning", data.explanation_note));
    }
    if (data.truncated) card.append(element("p", "result-warning", "Partial results: a row or size limit was reached. Only the returned rows are shown."));
    if (Array.isArray(data.columns) && data.columns.length) {
      if (data.rows?.length) card.append(table(data.columns, data.rows));
      else card.append(element("p", "explanation", "The query returned no matching rows."));
    }
    card.append(element("p", "result-meta", `${data.row_count ?? 0} result row${data.row_count === 1 ? "" : "s"} returned${data.truncated ? " · partial" : ""}`));
    if (data.sql) {
      const details = element("details", "query-details");
      details.append(element("summary", "", "View executed SQL"));
      const pre = element("pre"); pre.dir = "ltr"; pre.append(element("code", "", data.sql));
      const tools = element("div", "query-tools");
      const copy = element("button", "text-button", "Copy SQL"); copy.type = "button";
      copy.addEventListener("click", () => copyText(data.sql, copy));
      tools.append(copy); details.append(tools, pre); card.append(details);
    }
  }

  function renderFailure(card, error) {
    card.replaceChildren(element("p", "result-warning", error.message));
    card.append(element("p", "result-meta", "The request did not complete. Check the message before trying again."));
  }

  async function analyzeText(event) {
    event.preventDefault();
    if (state.busy || state.recording || state.micPending) return;
    const question = $("question").value.trim();
    if (!state.dataset) return notice("Upload or load a dataset first.", "error");
    if (!question) return notice("Enter a question about your dataset.", "error");
    if (question.length > 2000) return notice("Keep your question within 2,000 characters.", "error");
    state.busy = true; controls(); notice("");
    questionMessage(question);
    const card = pendingCard("Generating a query and analyzing your data…");
    try {
      const data = await request("/analyze/text", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset_id: state.dataset.dataset_id, question }),
      });
      renderAnswer(card, data);
      $("question").value = "";
    } catch (error) { renderFailure(card, error); }
    finally { state.busy = false; controls(); }
  }

  function clearAudio() {
    $("audio-player").pause();
    if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
    state.audio = null; state.audioUrl = null;
    $("audio-player").removeAttribute("src"); $("audio-player").load();
    $("audio-file").value = ""; $("audio-review").hidden = true;
    controls();
  }

  function setAudio(file) {
    clearAudio();
    if (!AUDIO_EXTENSIONS.test(file.name)) return notice("Choose a supported audio format such as .m4a, .wav, .mp3, or .webm.", "error");
    if (!file.size || file.size > MAX_BYTES) return notice("Use a nonempty audio file up to 10 MiB.", "error");
    state.audio = file; state.audioUrl = URL.createObjectURL(file);
    $("audio-player").src = state.audioUrl;
    $("audio-name").textContent = `${file.name} · ${(file.size / 1024).toFixed(1)} KiB`;
    $("audio-review").hidden = false;
    controls();
  }

  function releaseMicrophone() {
    clearInterval(state.recordTimer); clearTimeout(state.stopTimer);
    state.recordTimer = null; state.stopTimer = null;
    state.stream?.getTracks().forEach((track) => track.stop());
    state.stream = null;
  }

  function stopRecording() {
    if (state.recorder?.state !== "inactive") state.recorder?.stop();
  }

  async function toggleRecording() {
    if (state.recording) { stopRecording(); return; }
    if (state.busy || state.micPending || !state.dataset) return;
    state.micPending = true; controls(); notice("Allow microphone access in your browser to record a question.");
    let localStream = null;
    try {
      localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      state.stream = localStream;
      if (document.hidden) throw new Error("Recording was cancelled because this tab is no longer active.");
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
        .find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = mime ? new MediaRecorder(localStream, { mimeType: mime }) : new MediaRecorder(localStream);
      state.recorder = recorder;
      const chunks = []; let bytes = 0; let failed = false;
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) { chunks.push(event.data); bytes += event.data.size; }
        if (bytes > MAX_BYTES) { failed = true; stopRecording(); notice("The recording exceeded 10 MiB. Record a shorter question.", "error"); }
      });
      recorder.addEventListener("error", () => {
        failed = true; state.recording = false; releaseMicrophone();
        $("record-button").classList.remove("recording"); $("record-label").textContent = "Record a question";
        controls(); notice("Recording failed. Try uploading an audio file instead.", "error");
      });
      recorder.addEventListener("stop", () => {
        releaseMicrophone(); state.recording = false; state.recorder = null;
        $("record-button").classList.remove("recording"); $("record-label").textContent = "Record a question";
        $("audio-hint").textContent = "Up to 60 seconds · 10 MiB. You can review the recording before analysis.";
        if (!failed) {
          const type = recorder.mimeType || chunks[0]?.type || "";
          const extension = type.includes("webm") ? "webm" : type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : null;
          if (extension) {
            setAudio(new File(chunks, `question.${extension}`, { type }));
            if (state.audio) notice("Recording ready. Listen to it, then choose Analyze recording.", "success");
          } else notice("This browser recorded an unsupported audio format. Upload a supported file instead.", "error");
        }
        controls();
      });
      recorder.start(250);
      state.recording = true; state.micPending = false; state.startedAt = Date.now();
      $("record-button").classList.add("recording"); $("record-label").textContent = "Stop recording";
      $("audio-hint").textContent = "Recording… 0 seconds. Recording stops automatically at 55 seconds.";
      state.recordTimer = setInterval(() => {
        $("audio-hint").textContent = `Recording… ${Math.floor((Date.now() - state.startedAt) / 1000)} seconds. Stops automatically at 55 seconds.`;
      }, 500);
      state.stopTimer = setTimeout(stopRecording, 55_000);
      notice(""); controls();
    } catch (error) {
      localStream?.getTracks().forEach((track) => track.stop());
      releaseMicrophone(); state.recording = false; state.recorder = null;
      const denied = ["NotAllowedError", "PermissionDeniedError"].includes(error.name);
      notice(denied ? "Microphone access was denied. Allow it in your browser, or upload an audio file." : (error.message || "Could not start the microphone. Upload an audio file instead."), "error");
    } finally { state.micPending = false; controls(); }
  }

  async function analyzeAudio() {
    if (state.busy || state.recording || state.micPending) return;
    if (!state.dataset) return notice("Upload or load a dataset first.", "error");
    if (!state.audio) return notice("Record a question or choose an audio file first.", "error");
    // Keep the file for this request while clearing the composer immediately.
    const audio = state.audio;
    const datasetId = state.dataset.dataset_id;
    state.busy = true; controls(); notice("");
    const language = $("audio-language").value;
    const bubble = questionMessage(`Voice question · ${language === "ar" ? "Arabic" : language === "en" ? "English" : "Automatic language detection"}`);
    clearAudio();
    const card = pendingCard("Transcribing your recording and analyzing the dataset… The first transcription may take longer while the speech model loads.");
    try {
      const form = new FormData();
      form.append("file", audio, audio.name);
      form.append("dataset_id", datasetId);
      form.append("language", language);
      const data = await request("/analyze/audio", { method: "POST", body: form });
      if (data.transcription?.text) bubble.textContent = data.transcription.text;
      renderAnswer(card, data);
    } catch (error) { renderFailure(card, error); }
    finally { state.busy = false; controls(); }
  }

  $("upload-form").addEventListener("submit", uploadDataset);
  $("csv-file").addEventListener("change", () => { $("csv-name").textContent = $("csv-file").files[0]?.name || "UTF-8 · up to 10 MiB"; });
  $("load-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!state.busy && !state.recording && !state.micPending) loadDataset($("dataset-id").value);
  });
  $("copy-id").addEventListener("click", () => copyText($("active-id").value, $("copy-id")));
  $("api-status").addEventListener("click", checkHealth);
  $("clear-history").addEventListener("click", clearHistory);
  $("text-tab").addEventListener("click", () => setMode("text"));
  $("voice-tab").addEventListener("click", () => setMode("voice"));
  document.querySelectorAll(".mode-tab").forEach((tab) => {
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const target = event.key === "Home" ? "text" : event.key === "End" ? "voice" : tab.id === "text-tab" ? "voice" : "text";
      setMode(target); $(`${target}-tab`).focus();
    });
  });
  document.querySelectorAll("[data-question]").forEach((button) => {
    button.addEventListener("click", () => {
      setMode("text"); $("question").value = button.dataset.question;
      if (state.dataset) $("question").focus();
      else notice("Upload or load a dataset first. The example question is ready below.");
    });
  });
  $("question-form").addEventListener("submit", analyzeText);
  $("question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault(); $("question-form").requestSubmit();
    }
  });
  $("choose-audio").addEventListener("click", () => $("audio-file").click());
  $("audio-file").addEventListener("change", () => {
    const file = $("audio-file").files[0];
    if (file) setAudio(file);
  });
  $("discard-audio").addEventListener("click", clearAudio);
  $("record-button").addEventListener("click", toggleRecording);
  $("analyze-audio").addEventListener("click", analyzeAudio);
  window.addEventListener("pagehide", () => {
    stopRecording(); releaseMicrophone();
    if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
  });

  controls();
  checkHealth();
  let previousId;
  try { previousId = sessionStorage.getItem(STORAGE_KEY); } catch (_) { /* Optional. */ }
  if (previousId && /^[0-9a-f]{32}$/.test(previousId)) loadDataset(previousId, true);
})();
