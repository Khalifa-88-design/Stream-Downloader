const $ = id => document.getElementById(id);

let currentUrl = "";
let currentMode = "video";
let jobTimer = null;

document.querySelectorAll(".segment").forEach(button => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".segment").forEach(x => x.classList.remove("active"));
    button.classList.add("active");
    currentMode = button.dataset.mode;
    $("quality").disabled = currentMode === "audio";
  });
});

$("analyzeBtn").addEventListener("click", analyze);
$("url").addEventListener("keydown", e => {
  if (e.key === "Enter") analyze();
});

$("closeError").addEventListener("click", hideError);

$("retryBtn").addEventListener("click", () => {
  hideError();
  if (currentUrl) analyze(currentUrl);
});

$("openUrlBtn").addEventListener("click", () => {
  if (currentUrl) window.open(currentUrl, "_blank", "noopener,noreferrer");
});

$("openDulo").addEventListener("click", () => {
  window.open("https://dulo.cx/", "_blank", "noopener,noreferrer");
});

async function analyze(urlOverride = null) {
  const url = (urlOverride || $("url").value).trim();

  if (!url) {
    showError("Invalid URL", "Please enter a complete HTTP or HTTPS URL.", "invalid_url");
    return;
  }

  currentUrl = url;
  hideError();
  $("analyzeBtn").disabled = true;
  $("analyzeBtn").textContent = "Analyzing...";
  $("downloadBtn").disabled = true;
  $("downloadState").textContent = "Analyzing";

  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url})
    });

    const data = await response.json();

    if (!data.ok) {
      showError("Unable to analyze URL", data.error, data.error_code, true);
      return;
    }

    $("preview").classList.add("hidden");
    $("previewData").classList.remove("hidden");

    $("thumb").src = data.thumbnail || "";
    $("thumb").style.display = data.thumbnail ? "block" : "none";
    $("title").textContent = data.title || "Untitled";
    $("uploader").textContent = data.uploader || "Unknown uploader";
    $("duration").textContent = formatDuration(data.duration);
    $("site").textContent = data.site || "Supported source";
    $("siteBadge").textContent = data.site || "Supported";

    $("quality").innerHTML = '<option value="best">Best available</option>';

    (data.qualities || []).forEach(q => {
      const option = document.createElement("option");
      option.value = q;
      option.textContent = `${q}p`;
      $("quality").appendChild(option);
    });

    $("downloadBtn").disabled = false;
    $("downloadState").textContent = "Ready";
    setProgress(0, "Ready", "—", "—");

  } catch (error) {
    showError("Connection error", error.message, "network", true);
  } finally {
    $("analyzeBtn").disabled = false;
    $("analyzeBtn").textContent = "Analyze";
  }
}

$("downloadBtn").addEventListener("click", startDownload);

async function startDownload() {
  if (!currentUrl) return;

  clearInterval(jobTimer);
  hideError();

  $("downloadBtn").disabled = true;
  $("downloadState").textContent = "Starting";
  $("fileLink").classList.add("hidden");
  setProgress(0, "Starting", "—", "—");

  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        url: currentUrl,
        quality: currentMode === "audio" ? "best" : $("quality").value,
        mode: currentMode
      })
    });

    const data = await response.json();

    if (!data.ok) {
      showError("Unable to start download", data.error, data.error_code, true);
      $("downloadBtn").disabled = false;
      return;
    }

    poll(data.job_id);

  } catch (error) {
    showError("Connection error", error.message, "network", true);
    $("downloadBtn").disabled = false;
  }
}

function poll(jobId) {
  jobTimer = setInterval(async () => {
    try {
      const response = await fetch(`/api/progress/${jobId}`);
      const data = await response.json();

      if (!data.ok) {
        throw new Error(data.error || "Download job not found.");
      }

      setProgress(
        data.percent || 0,
        prettyStatus(data.status),
        data.speed || "—",
        data.eta || "—"
      );

      if (data.status === "completed") {
        clearInterval(jobTimer);
        $("downloadState").textContent = "Completed";
        $("downloadBtn").disabled = false;

        if (data.file) {
          $("fileLink").href = `/downloads/${encodeURIComponent(data.file)}`;
          $("fileLink").classList.remove("hidden");
        }
      }

      if (data.status === "error") {
        clearInterval(jobTimer);
        $("downloadBtn").disabled = false;
        $("downloadState").textContent = "Error";

        showError(
          errorTitle(data.error_code),
          data.error || "Download failed.",
          data.error_code,
          true
        );
      }

    } catch (error) {
      clearInterval(jobTimer);
      $("downloadBtn").disabled = false;
      showError("Progress error", error.message, "network", true);
    }
  }, 900);
}

function errorTitle(code) {
  if (code === "unsupported") return "Unsupported website";
  if (code === "timeout") return "Connection timeout";
  if (code === "format") return "Format unavailable";
  if (code === "ffmpeg") return "FFmpeg required";
  return "Download failed";
}

function showError(title, message, code = "", allowRetry = false) {
  $("errorTitle").textContent = title;
  $("errorMessage").textContent = cleanMessage(message);
  $("errorBox").classList.remove("hidden");

  const open = code === "unsupported";
  $("openUrlBtn").classList.toggle("hidden", !open);
  $("retryBtn").classList.toggle("hidden", !allowRetry);
}

function cleanMessage(message) {
  return String(message || "")
    .replace(/\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, "")
    .replace(/\n+/g, " ")
    .trim();
}

function hideError() {
  $("errorBox").classList.add("hidden");
}

function setProgress(percent, label, speed, eta) {
  $("bar").style.width = `${Math.max(0, Math.min(100, percent))}%`;
  $("percent").textContent = `${Math.round(percent)}%`;
  $("progressLabel").textContent = label;
  $("speed").textContent = speed;
  $("eta").textContent = eta;
}

function prettyStatus(status) {
  return {
    starting: "Starting",
    downloading: "Downloading",
    processing: "Processing",
    completed: "Completed",
    error: "Error"
  }[status] || status;
}

function formatDuration(seconds) {
  if (!seconds) return "Duration unavailable";

  seconds = Number(seconds);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);

  return h
    ? `${h}h ${m}m ${s}s`
    : `${m}m ${s}s`;
}
