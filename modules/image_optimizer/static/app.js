
const form = document.getElementById("optimizer-form");
const fileInput = document.getElementById("image");
const dropzone = document.getElementById("dropzone");
const previewWrap = document.getElementById("preview-wrap");
const canvas = document.getElementById("crop-canvas");
const ctx = canvas.getContext("2d");
const fileName = document.getElementById("file-name");
const details = document.getElementById("original-details");
const cropDetails = document.getElementById("crop-details");
const cropEnabled = document.getElementById("crop_enabled");
const widthInput = document.getElementById("width");
const heightInput = document.getElementById("height");
const lockAspect = document.getElementById("lock_aspect");
const format = document.getElementById("format");
const outputName = document.getElementById("output_name");
const extensionPreview = document.getElementById("extension-preview");
const quality = document.getElementById("quality");
const qualityOutput = document.getElementById("quality-output");
const target = document.getElementById("target_kb");
const optimize = document.getElementById("optimize");
const submit = document.getElementById("submit");
const status = document.getElementById("status");

let image = new Image();
let originalWidth = 0;
let originalHeight = 0;
let changing = false;
let dragging = false;
let cropRatio = null;
let crop = {x: 0, y: 0, width: 0, height: 0};
let startPoint = {x: 0, y: 0};

quality.addEventListener("input", () => qualityOutput.value = `${quality.value}%`);

function selectedExtension() {
  return format.value === "JPG" ? ".jpg" : `.${format.value.toLowerCase()}`;
}

function updateExtensionPreview() {
  extensionPreview.textContent = selectedExtension();
}

format.addEventListener("change", updateExtensionPreview);
updateExtensionPreview();

function displayScale() {
  return {
    x: originalWidth / canvas.width,
    y: originalHeight / canvas.height
  };
}

function resetCrop() {
  crop = {x: 0, y: 0, width: originalWidth, height: originalHeight};
  updateCropDetails();
  drawCanvas();
}

function updateCropDetails() {
  if (!originalWidth) return;
  cropDetails.textContent = cropEnabled.checked
    ? `Crop: ${Math.round(crop.width)} × ${Math.round(crop.height)}px at ${Math.round(crop.x)}, ${Math.round(crop.y)}`
    : "Crop: full image";
}

function fitCanvas() {
  // Floor the measurement: a container that reports 0 (hidden, or not yet
  // laid out) must not produce a negative ratio. Belt and braces alongside
  // unhiding first — a preview that silently disappears is hard to diagnose.
  const stage = document.querySelector(".preview-stage");
  const measured = stage ? stage.clientWidth - 20 : 0;
  const maxWidth = Math.min(640, Math.max(280, measured));
  const maxHeight = 420;
  const ratio = Math.min(maxWidth / originalWidth, maxHeight / originalHeight, 1);
  canvas.width = Math.max(1, Math.round(originalWidth * ratio));
  canvas.height = Math.max(1, Math.round(originalHeight * ratio));
}

function drawCanvas() {
  if (!originalWidth) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

  if (!cropEnabled.checked) return;

  const scale = displayScale();
  const x = crop.x / scale.x;
  const y = crop.y / scale.y;
  const w = crop.width / scale.x;
  const h = crop.height / scale.y;

  ctx.save();
  ctx.fillStyle = "rgba(0,0,0,.48)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.clearRect(x, y, w, h);
  ctx.drawImage(
    image,
    crop.x, crop.y, crop.width, crop.height,
    x, y, w, h
  );
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  ctx.setLineDash([7, 5]);
  ctx.strokeRect(x, y, w, h);
  ctx.restore();
}

function setFile(file) {
  if (!file) return;
  const url = URL.createObjectURL(file);
  image = new Image();
  image.onload = () => {
    originalWidth = image.naturalWidth;
    originalHeight = image.naturalHeight;
    // Unhide first: fitCanvas() measures .preview-stage, and a hidden element
    // reports clientWidth 0. That made the ratio negative and collapsed the
    // canvas to 1x1 — a blank grey box with correct metadata beside it, which
    // is exactly what it looked like.
    previewWrap.classList.remove("hidden");
    fitCanvas();
    widthInput.value = originalWidth;
    heightInput.value = originalHeight;
    fileName.textContent = file.name;
    const originalStem = file.name.replace(/\.[^/.]+$/, "");
    outputName.value = `${originalStem}-resized`;
    details.textContent = `${originalWidth} × ${originalHeight}px · ${(file.size / 1024).toFixed(1)} KB`;
    resetCrop();
    URL.revokeObjectURL(url);
  };
  image.src = url;
}

function pointerToImage(event) {
  const rect = canvas.getBoundingClientRect();
  const scale = displayScale();
  return {
    x: Math.max(0, Math.min(originalWidth, (event.clientX - rect.left) * canvas.width / rect.width * scale.x)),
    y: Math.max(0, Math.min(originalHeight, (event.clientY - rect.top) * canvas.height / rect.height * scale.y))
  };
}

canvas.addEventListener("pointerdown", event => {
  if (!cropEnabled.checked || !originalWidth) return;
  dragging = true;
  startPoint = pointerToImage(event);
  crop = {x: startPoint.x, y: startPoint.y, width: 1, height: 1};
  canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener("pointermove", event => {
  if (!dragging) return;
  const point = pointerToImage(event);
  let dx = point.x - startPoint.x;
  let dy = point.y - startPoint.y;

  if (cropRatio) {
    const directionX = dx < 0 ? -1 : 1;
    const directionY = dy < 0 ? -1 : 1;
    let width = Math.abs(dx);
    let height = width / cropRatio;
    if (height > Math.abs(dy)) {
      height = Math.abs(dy);
      width = height * cropRatio;
    }
    dx = width * directionX;
    dy = height * directionY;
  }

  crop.x = Math.max(0, Math.min(startPoint.x, startPoint.x + dx));
  crop.y = Math.max(0, Math.min(startPoint.y, startPoint.y + dy));
  crop.width = Math.max(1, Math.min(originalWidth - crop.x, Math.abs(dx)));
  crop.height = Math.max(1, Math.min(originalHeight - crop.y, Math.abs(dy)));
  updateCropDetails();
  drawCanvas();
});

canvas.addEventListener("pointerup", event => {
  dragging = false;
  if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
});

cropEnabled.addEventListener("change", () => {
  updateCropDetails();
  drawCanvas();
});

document.querySelectorAll("[data-crop-ratio]").forEach(button => {
  button.addEventListener("click", () => {
    cropRatio = button.dataset.cropRatio === "free" ? null : Number(button.dataset.cropRatio);
    cropEnabled.checked = true;

    if (cropRatio) {
      let width = originalWidth;
      let height = width / cropRatio;
      if (height > originalHeight) {
        height = originalHeight;
        width = height * cropRatio;
      }
      crop = {
        x: (originalWidth - width) / 2,
        y: (originalHeight - height) / 2,
        width,
        height
      };
    }
    updateCropDetails();
    drawCanvas();
  });
});

document.getElementById("reset-crop").addEventListener("click", () => {
  cropRatio = null;
  resetCrop();
});

fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
["dragenter","dragover"].forEach(evt => dropzone.addEventListener(evt, e => {
  e.preventDefault(); dropzone.classList.add("drag");
}));
["dragleave","drop"].forEach(evt => dropzone.addEventListener(evt, e => {
  e.preventDefault(); dropzone.classList.remove("drag");
}));
dropzone.addEventListener("drop", e => {
  const file = e.dataTransfer.files[0];
  if (!file) return;
  const dt = new DataTransfer();
  dt.items.add(file);
  fileInput.files = dt.files;
  setFile(file);
});

widthInput.addEventListener("input", () => {
  if (!lockAspect.checked || changing || !originalWidth) return;
  changing = true;
  const sourceW = cropEnabled.checked ? crop.width : originalWidth;
  const sourceH = cropEnabled.checked ? crop.height : originalHeight;
  heightInput.value = Math.max(1, Math.round(Number(widthInput.value || sourceW) * sourceH / sourceW));
  changing = false;
});
heightInput.addEventListener("input", () => {
  if (!lockAspect.checked || changing || !originalHeight) return;
  changing = true;
  const sourceW = cropEnabled.checked ? crop.width : originalWidth;
  const sourceH = cropEnabled.checked ? crop.height : originalHeight;
  widthInput.value = Math.max(1, Math.round(Number(heightInput.value || sourceH) * sourceW / sourceH));
  changing = false;
});

document.querySelectorAll("[data-scale]").forEach(btn => btn.addEventListener("click", () => {
  if (!originalWidth) return;
  const scale = Number(btn.dataset.scale);
  const sourceW = cropEnabled.checked ? crop.width : originalWidth;
  const sourceH = cropEnabled.checked ? crop.height : originalHeight;
  widthInput.value = Math.round(sourceW * scale);
  heightInput.value = Math.round(sourceH * scale);
}));
document.querySelectorAll("[data-size]").forEach(btn => btn.addEventListener("click", () => {
  const [w,h] = btn.dataset.size.split("x");
  widthInput.value = w;
  heightInput.value = h;
}));

window.addEventListener("resize", () => {
  if (!originalWidth) return;
  fitCanvas();
  drawCanvas();
});

form.addEventListener("submit", async e => {
  e.preventDefault();
  if (!fileInput.files[0]) return;

  status.className = "";
  status.textContent = "Processing image…";
  submit.disabled = true;

  const body = new FormData();
  body.append("image", fileInput.files[0]);
  body.append("width", widthInput.value);
  body.append("height", heightInput.value);
  body.append("crop_enabled", String(cropEnabled.checked));
  body.append("crop_x", Math.round(crop.x));
  body.append("crop_y", Math.round(crop.y));
  body.append("crop_width", Math.round(crop.width));
  body.append("crop_height", Math.round(crop.height));
  body.append("lock_aspect", String(lockAspect.checked));
  body.append("format", format.value);
  body.append("output_name", outputName.value.trim());
  body.append("quality", quality.value);
  body.append("target_kb", target.value);
  body.append("optimize", String(optimize.checked));

  try {
    const response = await fetch("/tools/image/process", {method:"POST", body});
    if (!response.ok) {
      const payload = await response.json().catch(() => ({error:"Processing failed."}));
      throw new Error(payload.error || "Processing failed.");
    }
    const blob = await response.blob();

    // Save to the client's gallery instead of downloading, if asked.
    const target = document.querySelector('input[name="saveTarget"]:checked');
    if (target && target.value === "gallery") {
      const client = (document.getElementById("optClient") || {}).value || "";
      if (!client.trim()) {
        throw new Error("Pick a client, or choose Download instead.");
      }
      const fd = new FormData();
      fd.append("file", blob, (outputName.value || "image") + "." + format.value.toLowerCase());
      fd.append("company", client.trim());
      const up = await fetch("/tools/seo-images/api/save-one", {method:"POST", body: fd});
      const res = await up.json().catch(() => ({}));
      if (!up.ok || res.error) {
        // Don't lose the work — hand them the file rather than an error.
        const fallback = document.createElement("a");
        fallback.href = URL.createObjectURL(blob);
        fallback.download = (outputName.value || "image") + "." + format.value.toLowerCase();
        document.body.appendChild(fallback); fallback.click(); fallback.remove();
        throw new Error((res.error || "Could not save to the gallery") +
                        " — downloaded it instead so nothing is lost.");
      }
      status.textContent = `Complete · ${(blob.size / 1024).toFixed(1)} KB saved to ${client}'s gallery`;
      return;
    }

    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/i);
    const filename = match ? match[1] : `smart1-image.${format.value.toLowerCase()}`;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
    status.textContent = `Complete · ${(blob.size / 1024).toFixed(1)} KB downloaded`;
  } catch (error) {
    status.className = "error";
    status.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});


/* Show the client box only when saving to a gallery, and reuse the Hub's
   client lookup rather than a second list that can drift. */
(function () {
  var radios = document.querySelectorAll('input[name="saveTarget"]');
  var row = document.getElementById("galleryRow");
  var box = document.getElementById("optClient");
  var drop = document.getElementById("optClientDrop");
  if (!radios.length || !row) return;

  radios.forEach(function (r) {
    r.addEventListener("change", function () {
      row.hidden = r.value !== "gallery" || !r.checked;
      var btn = document.getElementById("submit");
      if (btn) btn.textContent = (r.value === "gallery" && r.checked)
        ? "Resize & Save to gallery" : "Resize & Optimize";
    });
  });

  var timer = null;
  if (!box) return;
  box.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(function () {
      var q = box.value.trim();
      if (q.length < 2) { drop.style.display = "none"; return; }
      fetch("/tools/io/api/clients?q=" + encodeURIComponent(q), {credentials:"same-origin"})
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var list = d.clients || [];
          if (!list.length) { drop.style.display = "none"; return; }
          drop.innerHTML = list.map(function (c) {
            return '<div data-n="' + (c.name || "").replace(/"/g, "&quot;") +
              '" style="padding:8px 10px;cursor:pointer">' + (c.name || "") + "</div>";
          }).join("");
          drop.style.display = "block";
          drop.querySelectorAll("[data-n]").forEach(function (el) {
            el.onmousedown = function (ev) {
              ev.preventDefault();
              box.value = el.dataset.n;
              drop.style.display = "none";
            };
          });
        }).catch(function () { drop.style.display = "none"; });
    }, 220);
  });
  box.addEventListener("blur", function () {
    setTimeout(function () { drop.style.display = "none"; }, 150);
  });
})();
