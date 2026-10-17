document.addEventListener("DOMContentLoaded", () => {
  const input = document.querySelector('input[name="images"]');
  const container = document.querySelector("#previews");
  const dropzone = document.querySelector("#images-dropzone");
  if (!input || !container || !dropzone) return;

  const supportedTypes = new Set(["image/jpeg", "image/png"]);
  const maxBytes = Number(dropzone.dataset.maxMb || 0) * 1024 * 1024;
  const render = () => {
    container.replaceChildren();
    [...input.files].forEach((file) => {
      const card = document.createElement("div");
      card.className = "preview-card";
      const label = document.createElement("span");
      label.textContent = file.name;
      if (!supportedTypes.has(file.type) || (maxBytes && file.size > maxBytes)) {
        card.textContent = !supportedTypes.has(file.type)
          ? `${file.name}: formato no admitido; elegí JPG o PNG.`
          : `${file.name}: supera el máximo de ${dropzone.dataset.maxMb} MB.`;
        card.classList.add("error");
        container.append(card);
        return;
      }
      const image = document.createElement("img");
      const objectUrl = URL.createObjectURL(file);
      image.src = objectUrl;
      image.alt = file.name;
      image.addEventListener("load", () => URL.revokeObjectURL(objectUrl), { once: true });
      card.append(image, label);
      container.append(card);
    });
  };

  input.addEventListener("change", render);
  ["dragenter", "dragover"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("is-dragging");
  }));
  ["dragleave", "drop"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-dragging");
  }));
  dropzone.addEventListener("drop", (event) => {
    if (!event.dataTransfer?.files?.length) return;
    const dataTransfer = new DataTransfer();
    [...event.dataTransfer.files].forEach((file) => dataTransfer.items.add(file));
    input.files = dataTransfer.files;
    render();
  });

  input.form?.addEventListener("submit", (event) => {
    if (input.form.querySelector('input[name="delete_image"]:checked') && !window.confirm("Las imágenes marcadas se eliminarán al guardar el borrador. ¿Continuar?")) {
      event.preventDefault();
    }
  });
});
