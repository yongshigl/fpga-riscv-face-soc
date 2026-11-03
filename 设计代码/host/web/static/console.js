"use strict";
const words = {
  preview: "\u9884\u89c8",
  recognition: "\u8bc6\u522b",
  metrics: "\u6027\u80fd",
  source: "\u56fe\u50cf\u6765\u6e90",
  infer: "\u8bc6\u522b",
  inferOnboard: "\u677f\u5185\u5feb\u901f\u8bc6\u522b",
  load: "\u52a0\u8f7d\u6a21\u578b",
  upload: "\u6253\u5f00\u56fe\u50cf",
  pause: "\u6682\u505c\u9884\u89c8",
  resume: "\u7ee7\u7eed\u9884\u89c8",
  reconnect: "\u91cd\u65b0\u8fde\u63a5",
  export: "\u5bfc\u51fa\u62a5\u544a",
  stereo: "\u53cc\u76ee\u753b\u9762",
  waitingFrame: "\u7b49\u5f85\u753b\u9762",
  crc: "CRC \u9519\u8bef",
  decode: "\u89e3\u7801\u9519\u8bef",
  selectedImage: "\u5f53\u524d\u56fe\u50cf",
  firmware: "\u56fa\u4ef6",
  cpuClock: "CPU \u65f6\u949f",
  aiClock: "AI \u65f6\u949f",
  recognitionResult: "\u8bc6\u522b\u7ed3\u679c",
  noResult: "\u6682\u65e0\u7ed3\u679c",
  totalTime: "\u5904\u7406\u8017\u65f6",
  aiTime: "FPGA \u63a8\u7406",
  transferTime: "\u56fe\u50cf\u4f20\u8f93",
  faces: "\u4eba\u8138",
  cpuResults: "CPU \u677f\u6d4b\u7ed3\u679c",
  boardMeasured: "\u65f6\u5e8f\u901a\u8fc7 / CRC \u6b63\u786e",
  configuration: "\u914d\u7f6e",
  runtime: "\u8fd0\u884c\u65f6\u95f4",
  scoreScope:
    "CoreMark/LUT \u4f7f\u7528\u5b8c\u6574\u8bc4\u5206 SoC \u7684\u5e03\u7ebf\u540e LUT \u6570\u3002\u65e0 Cache \u4e0e\u6709 Cache \u7248\u672c\u5206\u522b\u6807\u6ce8\uff1b\u8fd9\u4e9b\u6570\u636e\u4e0d\u5305\u542b\u6444\u50cf\u5934\u548c CNN \u7684\u8d44\u6e90\u3002\u57fa\u51c6\u7248\u672c\u4e3a CoreMark v1.01\u3002",
  hardwareModels: "CNN \u786c\u4ef6\u9a8c\u8bc1",
  model: "\u6a21\u578b",
  layers: "\u5c42\u6570",
  scheduleTime: "CPU \u8c03\u5ea6\u4e0e\u7b49\u5f85",
  verification: "\u6821\u9a8c",
  hostAccuracy: "\u4e3b\u673a\u6574\u6570\u53c2\u8003",
  boardAccuracy: "FPGA / RISC-V \u677f\u6d4b",
  historicalBoardAccuracy: "FPGA / RISC-V \u5386\u53f2\u677f\u6d4b",
  accuracyScope:
    "LFW DevTest\uff0c\u4e0e\u677f\u4e0a\u51c6\u786e\u7387\u5206\u5f00\u8bb0\u5f55",
  gallery: "\u4eba\u8138\u5e93",
  enroll: "\u767b\u8bb0\u5f53\u524d\u4eba\u8138",
  emptyGallery: "\u4eba\u8138\u5e93\u4e3a\u7a7a",
  clearGallery: "\u6e05\u7a7a\u4eba\u8138\u5e93",
  boardTask: "\u677f\u4e0a\u4efb\u52a1",
  volatile: "\u6613\u5931\u914d\u7f6e",
  connecting: "\u8fde\u63a5\u4e2d",
  online: "\u5df2\u8fde\u63a5",
  offline: "\u672a\u8fde\u63a5",
  ready: "\u6a21\u578b\u5df2\u5c31\u7eea",
  cameraOnly: "\u6444\u50cf\u5934\u4e13\u7528\u4f4d\u6d41",
  awaitModels: "\u6a21\u578b\u672a\u52a0\u8f7d",
  idle: "\u7a7a\u95f2",
  queued: "\u6392\u961f\u4e2d",
  running: "\u6267\u884c\u4e2d",
  done: "\u5b8c\u6210",
  failed: "\u5931\u8d25",
  age: "\u5e27\u9f84\u4f30\u8ba1",
  stale: "\u753b\u9762\u5df2\u505c\u6b62",
  unknown: "\u672a\u767b\u8bb0",
  threshold: "\u5339\u914d\u9608\u503c",
  confidence: "\u68c0\u6d4b",
  cosine: "\u76f8\u4f3c\u5ea6",
  pendingValidation: "\u7b49\u5f85\u677f\u6d4b",
  exact: "\u9010\u5b57\u8282\u4e00\u81f4",
  historicalExact: "历史逐字节验证",
  name: "\u59d3\u540d",
  remove: "\u79fb\u9664",
  submitted: "\u4efb\u52a1\u5df2\u52a0\u5165\u961f\u5217",
  uploaded: "\u672c\u5730\u56fe\u50cf",
  noFaces: "\u672a\u68c0\u6d4b\u5230\u4eba\u8138",
  continuousStart: "开始连续识别",
  continuousStop: "停止连续识别",
  continuousStopped: "连续识别已关闭",
  continuousRunning: "连续识别",
  continuousFinishing: "当前任务结束后停止",
  restoringModels: "正在恢复模型与人脸库",
  restoreFailed: "模型恢复失败",
  queueTime: "排队",
  inputAge: "输入帧龄估计",
  track: "跟踪",
  liveness: "活体",
  liveMetrics: "实时识别指标",
  liveMedian: "延迟中位数",
  liveRange: "最近窗口",
  detectionRate: "检出率",
  liveNote:
    "跟踪编号与活体提示在 PC 侧计算，仅供参考，不参与识别判定。",
};
const $ = (id) => document.getElementById(id);
const state = {
  status: null,
  file: null,
  objectUrl: null,
  resultTime: null,
  galleryKey: null,
  view: "preview",
  busy: false,
  lastError: null,
  seenJobs: new Set(),
  continuousBusy: false,
  continuousStopReason: null,
};
function icons() {
  if (window.lucide) lucide.createIcons();
}
document
  .querySelectorAll("[data-text]")
  .forEach((node) => (node.textContent = words[node.dataset.text]));
$("identity-name").placeholder = words.name;
$("uploaded-option").textContent = words.uploaded;
icons();
function switchView(view) {
  state.view = view;
  document
    .querySelectorAll(".view")
    .forEach((node) => (node.hidden = node.id !== `view-${view}`));
  document.querySelectorAll(".tab").forEach((node) => {
    const active = node.dataset.view === view;
    node.classList.toggle("active", active);
    node.setAttribute("aria-selected", String(active));
  });
}
document
  .querySelectorAll(".tab")
  .forEach((button) =>
    button.addEventListener("click", () => switchView(button.dataset.view)),
  );
let toastTimer;
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 4500);
}
function text(id, value) {
  $(id).textContent = value;
}
function ms(value) {
  return Number.isFinite(value)
    ? `${value.toFixed(value < 100 ? 1 : 0)} ms`
    : "--";
}
async function job(action, extra = {}) {
  if (state.busy) return;
  state.busy = true;
  try {
    let body, headers;
    if (
      state.file &&
      (action === "infer" || action === "enroll") &&
      $("source").value === "uploaded"
    ) {
      body = new FormData();
      body.append("image", state.file);
      body.append("action", action);
      if (extra.name) body.append("name", extra.name);
    } else {
      headers = { "Content-Type": "application/json" };
      body = JSON.stringify({
        action,
        source: action === "infer_onboard" ? "camera0" : $("source").value,
        ...extra,
      });
    }
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers,
      body,
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || `HTTP ${response.status}`);
    if (action === "infer" || action === "infer_onboard") switchView("recognition");
    await poll();
  } catch (error) {
    toast(error.message);
  } finally {
    state.busy = false;
  }
}
$("infer").addEventListener("click", () => job(
  $("source").value === "camera0" && state.status?.capabilities.camera_coherent_capture ? "infer_onboard" : "infer",
));
$("infer-onboard").addEventListener("click", () => job("infer_onboard"));
$("load-models").addEventListener("click", () => job("load_models"));
$("enroll").addEventListener("click", () =>
  job("enroll", { name: $("identity-name").value }),
);
$("identity-name").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !$("enroll").disabled)
    job("enroll", { name: $("identity-name").value });
});
$("clear-gallery").addEventListener("click", () => job("clear_gallery"));
$("reconnect").addEventListener("click", () => job("reconnect"));
$("preview-toggle").addEventListener("click", () =>
  job("preview", { index: state.status.preview_enabled ? 0 : 1 }),
);
async function continuous(enabled, reason) {
  if (state.continuousBusy) {
    if (!enabled) state.continuousStopReason = reason || "user_stopped";
    return;
  }
  state.continuousBusy = true;
  if (state.status) renderContinuous(state.status);
  try {
    const response = await fetch("/api/continuous", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, source: $("source").value, reason }),
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || `HTTP ${response.status}`);
    if (enabled) switchView("recognition");
    await poll();
  } catch (error) {
    toast(error.message);
  } finally {
    state.continuousBusy = false;
    if (state.status) renderContinuous(state.status);
    if (state.continuousStopReason) {
      const stopReason = state.continuousStopReason;
      state.continuousStopReason = null;
      continuous(false, stopReason);
    }
  }
}
$("continuous-toggle").addEventListener("click", () =>
  continuous(!state.status?.continuous?.enabled),
);
function sourceChanged() {
  // A pending status response may predate the start acknowledgement.
  continuous(false, "source_changed");
  updateSource();
}
$("source").addEventListener("change", sourceChanged);
$("image-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) {
    toast("Image exceeds 10 MB");
    return;
  }
  state.file = file;
  if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
  state.objectUrl = URL.createObjectURL(file);
  $("uploaded-option").hidden = false;
  $("source").value = "uploaded";
  sourceChanged();
});
document.querySelector(".file-button").addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    $("image-file").click();
  }
});
function updateSource() {
  const source = $("source").value;
  const image = $("input-preview");
  const camera = source.startsWith("camera")
    ? state.status?.cameras[Number(source.slice(-1))]
    : null;
  const url =
    source === "uploaded"
      ? state.objectUrl
      : source.startsWith("camera")
        ? state.status?.connected && camera?.received_monotonic != null
          ? `/stream/${source.slice(-1)}`
          : null
        : `/image/${source}`;
  if (url) {
    if (image.getAttribute("src") !== url) image.src = url;
  } else {
    image.removeAttribute("src");
    image.hidden = true;
    $("input-empty").hidden = false;
  }
  text("input-name", $("source").selectedOptions[0].textContent);
}
$("input-preview").addEventListener("load", () => {
  $("input-preview").hidden = false;
  $("input-empty").hidden = true;
});
$("input-preview").addEventListener("error", () => {
  $("input-preview").hidden = true;
  $("input-empty").hidden = false;
});
for (let camera = 0; camera < 2; camera++) {
  const image = $(`camera${camera}`);
  image.addEventListener("load", () => {
    image.hidden = false;
    $(`empty${camera}`).hidden = true;
  });
  image.addEventListener("error", () => {
    image.hidden = true;
    $(`empty${camera}`).hidden = false;
  });
}
function renderGallery(gallery) {
  const key = JSON.stringify(gallery);
  if (key === state.galleryKey) return;
  state.galleryKey = key;
  const container = $("gallery");
  container.replaceChildren();
  text("gallery-count", `${gallery.length} / 16`);
  if (!gallery.length) {
    const empty = document.createElement("div");
    empty.className = "empty-gallery";
    empty.innerHTML = '<i data-lucide="users"></i>';
    const label = document.createElement("span");
    label.textContent = words.emptyGallery;
    empty.append(label);
    container.append(empty);
  }
  for (const entry of gallery) {
    const row = document.createElement("div");
    row.className = "gallery-entry";
    const image = document.createElement("img");
    image.src = `/image/gallery-${entry.id}`;
    image.alt = entry.name;
    const name = document.createElement("span");
    name.className = "gallery-name";
    name.textContent = entry.name;
    const remove = document.createElement("button");
    remove.className = "icon-button";
    remove.dataset.tip = "remove";
    remove.setAttribute("aria-label", `${words.remove} ${entry.name}`);
    remove.innerHTML = '<i data-lucide="x"></i>';
    remove.addEventListener("click", () =>
      job("remove_identity", { index: entry.index }),
    );
    row.append(image, name, remove);
    container.append(row);
  }
  icons();
}
// 板内快速识别先发布计时结果，再用约 1.4 s 回读 640×360 画面并标注；
// 这段时间里 /image/result 还是 404。加载失败就每 0.5 s 重取一次，最多 10 次。
$("result-image").addEventListener("error", () => {
  if ((state.resultRetries ?? 0) >= 10) return;
  state.resultRetries = (state.resultRetries ?? 0) + 1;
  setTimeout(() => {
    $("result-image").src = `/image/result?t=${Date.now()}`;
  }, 500);
});
function renderResult(result) {
  if (!result || state.resultTime === result.measured_utc) return;
  state.resultTime = result.measured_utc;
  state.resultRetries = 0;
  $("result-image").src = `/image/result?t=${Date.now()}`;
  $("result-image").hidden = false;
  $("result-empty").hidden = true;
  text(
    "result-time",
    new Date(result.measured_utc).toLocaleTimeString("zh-CN"),
  );
  text("total-time", ms(result.total_ms));
  const ai =
    result.detection.accelerator_ms +
    result.faces.reduce((sum, face) => sum + face.accelerator_ms, 0);
  const transfer =
    result.detection.upload_ms +
    result.detection.download_ms +
    result.faces.reduce((sum, face) => sum + face.upload_ms, 0);
  text("ai-time", ms(ai));
  text("transfer-time", ms(transfer));
  text("queue-time", ms(result.queue_wait_ms));
  text("input-age", ms(result.camera_input?.camera_estimated_age_at_selection_ms));
  text("input-sequence", result.camera_input
    ? `CAM ${result.camera_input.camera} / #${result.camera_input.camera_sequence}` : "");
  text("threshold", `${words.threshold} ${result.match_threshold.toFixed(4)}`);
  const container = $("faces");
  container.replaceChildren();
  if (!result.faces.length) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.textContent = words.noFaces;
    container.append(empty);
  }
  for (const face of result.faces) {
    const row = document.createElement("div");
    row.className = "face-result";
    const identity = document.createElement("span");
    identity.textContent =
      face.identity === "Unknown" ? words.unknown : face.identity;
    if (face.identity === "Unknown") identity.className = "unknown";
    const confidence = document.createElement("span");
    confidence.textContent = `${words.confidence} ${(face.confidence * 100).toFixed(1)}%`;
    const cosine = document.createElement("span");
    cosine.textContent = `${words.cosine} ${face.cosine.toFixed(3)}`;
    row.append(identity, confidence, cosine);
    if (face.track_id != null) {
      const track = document.createElement("span");
      track.className = "muted";
      track.textContent = `${words.track} #${face.track_id}`;
      row.append(track);
    }
    const liveness = face.liveness?.score;
    if (Number.isFinite(liveness)) {
      const badge = document.createElement("span");
      badge.className = liveness >= 0.5 ? "live" : "muted";
      badge.textContent = `${words.liveness} ${(liveness * 100).toFixed(0)}%`;
      const signals = [
        face.liveness.detail != null ? `detail ${face.liveness.detail}` : null,
        face.liveness.specular != null ? `specular ${face.liveness.specular}` : null,
        face.liveness.motion != null ? `motion ${face.liveness.motion}` : null,
      ].filter(Boolean);
      badge.title = signals.join(" / ");
      if (signals.length) badge.dataset.tip = badge.title;
      row.append(badge);
    }
    container.append(row);
  }
}
function continuousReason(reason) {
  const known = {
    "Continuous recognition stopped": words.continuousStopped,
    "Image source changed; continuous recognition stopped": "图像来源已改变，连续识别已停止",
    "Manual board task requested; continuous recognition stopped": "已让位给手动操作，连续识别已停止",
    "Camera preview is paused; continuous recognition stopped": "预览已暂停，连续识别已停止",
    "Board disconnected; continuous recognition stopped": "开发板已断开，连续识别已停止",
    "Board or models are unavailable; continuous recognition stopped": "开发板或模型未就绪，连续识别已停止",
  };
  return known[reason] || (reason?.startsWith("Recognition failed: ")
    ? `识别失败，连续识别已停止：${reason.slice(20)}` : reason) || words.continuousStopped;
}
function renderContinuous(status) {
  const value = status.continuous || { enabled: false };
  const active = status.jobs.find((job) => job.id === status.active_job);
  const queued = status.jobs.some((job) => job.status === "queued");
  const canStart = status.connected && status.models_ready && status.preview_enabled
    && status.capabilities.camera_preview && ["camera0", "camera1"].includes($("source").value)
    && !active && !queued;
  $("continuous-toggle").disabled = state.continuousBusy || (!value.enabled && !canStart);
  $("continuous-toggle").setAttribute("aria-pressed", String(value.enabled));
  text("continuous-label", value.enabled ? words.continuousStop : words.continuousStart);
  text("continuous-state", value.enabled
    ? `${words.continuousRunning} / CAM ${value.source.slice(-1)} / ${active ? words.running : words.waitingFrame}`
    : `${continuousReason(value.reason)}${value.active_job ? ` / ${words.continuousFinishing}` : ""}`);
}
function renderLiveMetrics(metrics) {
  if (!metrics || !metrics.samples) {
    text("live-median", "--");
    text("live-range", "");
    text("live-detection", "--");
    text("live-samples", "0");
    return;
  }
  text("live-samples", String(metrics.samples));
  text("live-median", ms(metrics.median_ms));
  text("live-range", `${ms(metrics.min_ms)} – ${ms(metrics.max_ms)}`);
  text("live-detection", Number.isFinite(metrics.detection_rate)
    ? `${(metrics.detection_rate * 100).toFixed(0)}%` : "--");
}
function renderStatus(status) {
  if (!state.status && status.continuous?.enabled) $("source").value = status.continuous.source;
  state.status = status;
  text(
    "connection-state",
    status.connected
      ? words.online
      : status.mode === "connecting"
        ? words.connecting
        : words.offline,
  );
  $("connection-dot").className =
    `dot ${status.connected ? "online" : "offline"}`;
  const active = status.jobs.find((job) => job.id === status.active_job);
  const manualQueued = status.jobs.some((job) => job.status === "queued" && !job.automatic);
  const manualAvailable = (!active || active.trigger === "continuous") && !manualQueued;
  const ready = status.connected && status.models_ready && manualAvailable;
  $("infer").disabled = !ready;
  $("infer-onboard").disabled = !ready || !status.capabilities.camera_tensor;
  $("enroll").disabled = !ready;
  $("load-models").disabled = !status.board || !manualAvailable;
  $("clear-gallery").disabled = !ready || !status.gallery.length;
  $("preview-toggle").disabled =
    !status.capabilities.camera_preview || !manualAvailable;
  $("preview-toggle").dataset.tip = status.preview_enabled ? "pause" : "resume";
  const previewIcon = status.preview_enabled ? "pause" : "play";
  if ($("preview-toggle").dataset.icon !== previewIcon) {
    $("preview-toggle").dataset.icon = previewIcon;
    $("preview-toggle").innerHTML = `<i data-lucide="${previewIcon}"></i>`;
    icons();
  }
  const execution = status.models_ready
    ? words.ready
    : status.model_restore?.state === "loading"
      ? words.restoringModels
      : status.model_restore?.state === "failed"
        ? words.restoreFailed
        : status.mode === "camera"
          ? words.cameraOnly
          : status.board
            ? words.awaitModels
            : words.offline;
  text("execution-state", execution);
  text("serial-port", status.port);
  text("serial-baud", String(status.baud || 921600));
  renderContinuous(status);
  text(
    "hardware-kind",
    status.board
      ? status.capabilities.camera_face_decode
        ? "RV32I + MUL · 板上解码"
        : "RV32I + MUL"
      : status.mode === "camera"
        ? "OV5640 \u00d7 2"
        : "--",
  );
  text(
    "preview-state",
    status.mode === "camera"
      ? status.cameras.some((camera) => camera.format === 5)
        ? "OV5640 / JPEG"
        : "RGB565 / JPEG-LS"
      : status.capabilities.camera_preview
        ? "OV5640 / JPEG"
        : execution,
  );
  text("icache-size", status.board ? "8 KiB / 2-way" : "--");
  text("dcache-size", status.board ? "4 KiB / 2-way" : "--");
  const firmware =
    status.board?.firmware_version ??
    status.cameras.find((camera) => camera.sensor)?.sensor.build_id;
  text("firmware", firmware ? `0x${firmware.toString(16)}` : "--");
  text(
    "cpu-hz",
    status.board ? `${(status.board.cpu_hz / 1e6).toFixed(0)} MHz` : "--",
  );
  text(
    "ai-hz",
    status.board ? `${(status.board.ai_hz / 1e6).toFixed(0)} MHz` : "--",
  );
  text("crc", status.crc_errors);
  text("decode", status.decode_errors);
  text("footer-state", `${execution}  /  ${status.port}`);
  $("error").hidden = !status.error;
  $("error").textContent = status.error || "";
  for (const camera of status.cameras) {
    const index = camera.camera;
    text(`fps${index}`, camera.fps.toFixed(2));
    text(`sequence${index}`, `#${camera.sequence || 0}`);
    text(
      `age${index}`,
      camera.stale_seconds > 8
        ? words.stale
        : camera.estimated_frame_age_ms
          ? `${words.age} ${ms(camera.estimated_frame_age_ms)}`
          : "",
    );
    text(`size${index}`, camera.width ? `${camera.width} \u00d7 ${camera.height}` : "--");
    const hasImage = status.connected && camera.received_monotonic != null;
    const image = $(`camera${index}`);
    const url = `/stream/${index}`;
    if (hasImage) {
      if (image.getAttribute("src") !== url) image.src = url;
    } else {
      image.removeAttribute("src");
    }
    image.hidden = !hasImage;
    $(`empty${index}`).hidden = hasImage;
    if (camera.sensor) {
      const sensor = camera.sensor;
      text(
        `sensor${index}`,
        `${sensor.chip_id.toString(16).toUpperCase()} / NACK ${sensor.nacks}`,
      );
      $(`sensor${index}`).className =
        `sensor ${sensor.verified ? "good" : "bad"}`;
    } else {
      text(`sensor${index}`, "--");
      $(`sensor${index}`).className = "sensor";
    }
  }
  const source = $("source").value;
  if (source.startsWith("camera")) updateSource();
  text(
    "format",
    status.cameras.some((camera) => camera.format)
      ? status.cameras.find((camera) => camera.format).format === 5
        ? "JPEG / 640 \u00d7 360"
        : "JPEG-LS / RGB565"
      : "--",
  );
  renderGallery(status.gallery);
  renderResult(status.result);
  renderLiveMetrics(status.app_metrics);
  const latest = active || status.jobs.at(-1);
  text("job-state", latest ? words[latest.status] : words.idle);
  $("job-progress").hidden = !active;
  $("job-progress").value = latest?.progress || 0;
  text(
    "job-detail",
    latest
      ? `${words[latest.action] || { load_models: words.load, clear_gallery: words.clearGallery, remove_identity: words.remove, preview: words.preview, reconnect: words.reconnect }[latest.action] || latest.action}${latest.stage ? ` / ${latest.stage}` : ""}${active ? ` / ${latest.progress.toFixed(0)}%` : ""}`
      : "",
  );
  for (const job of status.jobs) {
    if (
      (job.status === "done" || job.status === "failed") &&
      !state.seenJobs.has(job.id)
    ) {
      state.seenJobs.add(job.id);
      if (job.status === "failed") toast(job.error);
      else if (job.action === "enroll") {
        $("identity-name").value = "";
        toast(`${job.name} / ${words.done}`);
      }
    }
  }
  document
    .querySelectorAll(".gallery-entry button")
    .forEach((button) => (button.disabled = !ready));
}
let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw Error(`HTTP ${response.status}`);
    renderStatus(await response.json());
  } catch (error) {
    text("connection-state", words.offline);
    $("connection-dot").className = "dot offline";
  } finally {
    polling = false;
  }
}
function cell(row, value, css = "") {
  const node = document.createElement("td");
  node.textContent = value;
  if (css) node.className = css;
  row.append(node);
}
async function metrics() {
  try {
    const response = await fetch("/api/metrics");
    if (!response.ok) return;
    const data = await response.json();
    const container = $("cpu-results");
    container.replaceChildren();
    const bestByCache = [0, 0];
    for (const record of data.cpu) {
      const group = Number(Boolean(record.icache_bytes && record.dcache_bytes));
      bestByCache[group] = Math.max(bestByCache[group], record.coremark_per_lut_post_route);
    }
    const records = data.cpu.slice().sort((a, b) =>
      Number(Boolean(b.icache_bytes && b.dcache_bytes)) - Number(Boolean(a.icache_bytes && a.dcache_bytes)) ||
      b.coremark_per_lut_post_route - a.coremark_per_lut_post_route);
    for (const record of records) {
      const row = document.createElement("tr");
      const cached = Boolean(record.icache_bytes && record.dcache_bytes);
      if (record.coremark_per_lut_post_route === bestByCache[Number(cached)]) row.className = "best";
      const label = record.project.replace("riscv_", "").replaceAll("_", " ");
      const cache = cached ? `I ${record.icache_bytes / 1024} KiB / D ${record.dcache_bytes / 1024} KiB` : "no Cache";
      cell(row, `${label} / ${record.pipeline_stages ?? 5}\u7ea7 / ${cache}`);
      cell(row, Number((record.clock_hz / 1e6).toFixed(2)).toString());
      cell(row, record.coremark_per_mhz.toFixed(4));
      cell(row, record.coremark_per_second.toFixed(2));
      cell(row, String(record.lut_post_route));
      cell(row, record.coremark_per_lut_post_route.toFixed(6));
      cell(row, `${record.seconds.toFixed(2)} s`);
      cell(row, "PASS", "valid");
      container.append(row);
    }
    const models = $("model-results");
    models.replaceChildren();
    text(
      "hardware-validation",
      data.hardware ? (data.hardware_matches_running_identity ? words.exact : words.historicalExact) : words.pendingValidation,
    );
    if (data.hardware) {
      for (const model of data.hardware.models) {
        const row = document.createElement("tr");
        cell(row, model.name);
        cell(row, model.layers.length);
        cell(row, ms(model.accelerator_ms));
        cell(row, ms(model.cpu_schedule_ms));
        cell(
          row,
          model.all_bytes_identical ? "PASS" : "FAIL",
          model.all_bytes_identical ? "valid" : "",
        );
        models.append(row);
      }
    }
    text("host-accuracy", "--");
    if (data.host_accuracy) {
      const accuracy =
        data.host_accuracy.accuracy ?? data.host_accuracy.metrics?.accuracy;
      if (Number.isFinite(accuracy))
        text("host-accuracy", `${(accuracy * 100).toFixed(1)}%`);
    }
    text("board-accuracy", data.board_accuracy
      ? `${(data.board_accuracy.accuracy * 100).toFixed(1)}%` : words.pendingValidation);
    document.querySelector('[data-text="boardAccuracy"]').textContent =
      data.board_accuracy && !data.board_accuracy.matches_hardware
        ? words.historicalBoardAccuracy : words.boardAccuracy;
    text("board-accuracy-scope", data.board_accuracy
      ? `LFW DevTest / ${data.board_accuracy.pairs} pairs / ${words.threshold} ${data.board_accuracy.threshold.toFixed(4)}`
      : "LFW DevTest / 1000 pairs");
  } catch (error) {
    console.error(error);
  }
}
const tooltip = $("tooltip");
document.addEventListener("mouseover", (event) => {
  const target = event.target.closest("[data-tip]");
  if (!target) return;
  tooltip.textContent = words[target.dataset.tip] || target.dataset.tip;
  target.setAttribute("aria-label", tooltip.textContent);
  tooltip.hidden = false;
  const rect = target.getBoundingClientRect();
  tooltip.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - tooltip.offsetWidth - 8))}px`;
  tooltip.style.top = `${rect.bottom + 7}px`;
});
document.addEventListener("mouseout", (event) => {
  if (event.target.closest("[data-tip]")) tooltip.hidden = true;
});
document
  .querySelectorAll("[data-tip]")
  .forEach((node) =>
    node.setAttribute(
      "aria-label",
      words[node.dataset.tip] || node.dataset.tip,
    ),
  );
poll();
metrics();
updateSource();
setInterval(poll, 750);
setInterval(metrics, 30000);
