const recordButton = document.querySelector("#record");
const recordLabel = document.querySelector("#record-label");
const statusEl = document.querySelector("#status");
const transcriptEl = document.querySelector("#transcript");
const copyButton = document.querySelector("#copy");
const clearButton = document.querySelector("#clear");
const engineEl = document.querySelector("#engine");

let mediaRecorder = null;
let chunks = [];
let recording = false;
let stopPromise = null;

function setStatus(message, tone = "") {
  statusEl.textContent = message;
  statusEl.className = `status ${tone}`.trim();
}

function setRecording(active) {
  recording = active;
  recordButton.classList.toggle("is-recording", active);
  recordButton.setAttribute("aria-pressed", String(active));
  recordLabel.textContent = active ? "Release to transcribe" : "Hold to record";
}

async function ensureRecorder() {
  if (mediaRecorder) return mediaRecorder;

  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("This browser cannot access the microphone.");
  }
  if (!window.MediaRecorder) {
    throw new Error("This browser cannot record audio. Try Safari, Chrome, or Edge.");
  }

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const preferred = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
  ].find((type) => MediaRecorder.isTypeSupported(type));

  mediaRecorder = new MediaRecorder(stream, preferred ? { mimeType: preferred } : undefined);
  mediaRecorder.addEventListener("dataavailable", (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  });
  return mediaRecorder;
}

async function startRecording() {
  if (recording) return;
  try {
    const recorder = await ensureRecorder();
    chunks = [];
    recorder.start();
    setRecording(true);
    setStatus("Recording...");
  } catch (error) {
    setStatus(error.message || "Could not start recording.", "error");
  }
}

async function stopRecording() {
  if (!recording || stopPromise) return;
  const recorder = await ensureRecorder();

  stopPromise = new Promise((resolve) => {
    recorder.addEventListener("stop", resolve, { once: true });
  });

  recorder.stop();
  setRecording(false);
  setStatus("Transcribing...");
  await stopPromise;
  stopPromise = null;

  const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
  chunks = [];

  if (blob.size < 1000) {
    setStatus("Recording was too short.", "error");
    return;
  }

  await transcribe(blob);
}

async function transcribe(blob) {
  const form = new FormData();
  const extension = blob.type.includes("mp4") ? "mp4" : "webm";
  form.append("file", blob, `recording.${extension}`);

  try {
    const response = await fetch("/api/transcribe", {
      method: "POST",
      body: form,
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Transcription failed.");
    }

    transcriptEl.value = payload.text || "";
    copyButton.disabled = !transcriptEl.value.trim();
    setStatus(payload.text ? "Transcript ready." : "No speech detected.", payload.text ? "ok" : "");
  } catch (error) {
    setStatus(error.message || "Transcription failed.", "error");
  }
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    const status = await response.json();
    const model = status.transcribe_command ? "custom command" : status.engine;
    engineEl.textContent = `${model} · ${status.language}`;
  } catch {
    engineEl.textContent = "offline";
  }
}

recordButton.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  recordButton.setPointerCapture(event.pointerId);
  startRecording();
});

recordButton.addEventListener("pointerup", (event) => {
  event.preventDefault();
  stopRecording();
});

recordButton.addEventListener("pointercancel", () => {
  stopRecording();
});

recordButton.addEventListener("keydown", (event) => {
  if ((event.code === "Space" || event.code === "Enter") && !event.repeat) {
    event.preventDefault();
    startRecording();
  }
});

recordButton.addEventListener("keyup", (event) => {
  if (event.code === "Space" || event.code === "Enter") {
    event.preventDefault();
    stopRecording();
  }
});

copyButton.addEventListener("click", async () => {
  const text = transcriptEl.value.trim();
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
    setStatus("Copied to clipboard.", "ok");
  } catch {
    transcriptEl.select();
    document.execCommand("copy");
    setStatus("Copied to clipboard.", "ok");
  }
});

clearButton.addEventListener("click", () => {
  transcriptEl.value = "";
  copyButton.disabled = true;
  setStatus("Ready");
});

transcriptEl.addEventListener("input", () => {
  copyButton.disabled = !transcriptEl.value.trim();
});

loadStatus();
